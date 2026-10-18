"""Model-authored plan proposals inside a deterministic, source-preserving envelope."""

import asyncio
import math
from typing import Literal, Protocol

from pydantic import Field, model_validator

from hakimi_analysis.models import StrictModel

AdjustmentField = Literal["sets", "reps", "durationSeconds", "restSeconds"]
FIELDS: tuple[AdjustmentField, ...] = ("sets", "reps", "durationSeconds", "restSeconds")


class AdjustmentValue(StrictModel):
    value: int | None = Field(default=None, ge=0, le=86_400, strict=True)
    source: Literal["video", "rule", "personalized", "user"] | None = None


class AdjustmentAction(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["reps", "duration"]
    confirmed: bool
    sets: AdjustmentValue
    reps: AdjustmentValue
    durationSeconds: AdjustmentValue
    restSeconds: AdjustmentValue
    feedback: Literal["too_hard", "just_right", "too_easy"] | None = None
    reps_range: tuple[int, int] | None = None


class PlanAdjustmentRequest(StrictModel):
    input_fingerprint: str = Field(min_length=1, max_length=100)
    plan_revision: int = Field(ge=0, strict=True)
    intent: Literal["easier_to_finish", "more_challenging", "shorter_session"]
    experience: Literal["beginner", "intermediate", "advanced"] | None = None
    available_minutes: float | None = Field(default=None, gt=0, le=1440)
    energy: Literal["usual", "tired"] = "usual"
    safety_stop: bool = False
    actions: list[AdjustmentAction] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_actions(self) -> "PlanAdjustmentRequest":
        if len({action.id for action in self.actions}) != len(self.actions):
            raise ValueError("duplicate actions")
        return self


class ModelAdjustmentChange(StrictModel):
    action_id: str = Field(min_length=1, max_length=100)
    field: AdjustmentField
    value: int = Field(ge=0, le=86_400, strict=True)
    reason: str = Field(min_length=1, max_length=180)


class ModelAdjustment(StrictModel):
    changes: list[ModelAdjustmentChange] = Field(max_length=200)
    summary: str = Field(min_length=1, max_length=240)


class PlanAdjustmentResponse(ModelAdjustment):
    input_fingerprint: str
    plan_revision: int
    mode: Literal["model", "protected"]


class AdjustmentModel(Protocol):
    async def propose_plan_adjustment(self, payload: dict[str, object]) -> ModelAdjustment: ...


def estimate_actions(actions: list[AdjustmentAction]) -> int:
    return sum(
        (action.sets.value or 0)
        * (
            (action.durationSeconds.value or 0)
            if action.mode == "duration"
            else (action.reps.value or 0) * 3
        )
        + max(0, (action.sets.value or 0) - 1) * (action.restSeconds.value or 0)
        for action in actions
        if action.confirmed
    )


def allowed_adjustments(request: PlanAdjustmentRequest) -> dict[str, dict[str, list[int]]]:
    intent = (
        "easier_to_finish"
        if request.energy == "tired" and request.intent == "more_challenging"
        else request.intent
    )
    options: dict[str, dict[str, list[int]]] = {}
    if request.safety_stop or (
        intent == "shorter_session"
        and request.available_minutes is not None
        and estimate_actions(request.actions) <= request.available_minutes * 60
    ):
        return options
    for action in request.actions:
        if not action.confirmed:
            continue
        if intent == "more_challenging" and action.feedback in {"too_hard", "just_right"}:
            continue
        if intent != "more_challenging" and action.feedback == "too_easy":
            continue
        if intent == "easier_to_finish" and action.feedback == "just_right":
            continue
        fields: dict[str, list[int]] = {}
        field = "durationSeconds" if action.mode == "duration" else "reps"
        current = getattr(action, field)
        eligible = current.source in {"rule", "personalized"} and current.value is not None
        if eligible:
            value = current.value
            minimum, maximum, step = (10, 90, 5) if field == "durationSeconds" else (5, 20, 1)
            delta = (
                min(10, math.floor(value * 0.2 / 5) * 5)
                if step == 5
                else min(2, math.floor(value * 0.2))
            )
            if minimum <= value <= maximum:
                sign = 1 if intent == "more_challenging" else -1
                values = [
                    value + sign * offset
                    for offset in range(step, delta + 1, step)
                    if minimum <= value + sign * offset <= maximum
                    and (
                        field != "reps"
                        or action.reps_range is None
                        or action.reps_range[0] <= value + sign * offset <= action.reps_range[1]
                    )
                ]
                if values:
                    fields[field] = values
        if (
            (intent == "shorter_session" or (intent == "easier_to_finish" and not eligible))
            and action.sets.source in {"rule", "personalized"}
            and action.sets.value is not None
            and 1 < action.sets.value <= 4
        ):
            fields["sets"] = [action.sets.value - 1]
        rest = action.restSeconds
        if (
            intent == "easier_to_finish"
            and (
                request.experience == "beginner"
                or request.energy == "tired"
                or action.feedback == "too_hard"
            )
            and rest.source in {"rule", "personalized"}
            and rest.value is not None
            and 30 <= rest.value < 180
        ):
            fields["restSeconds"] = list(range(rest.value + 5, min(180, rest.value + 30) + 1, 5))
        if fields:
            options[action.id] = fields
    return options


def validate_adjustment(request: PlanAdjustmentRequest, decision: ModelAdjustment) -> None:
    options = allowed_adjustments(request)
    changed = [action.model_copy(deep=True) for action in request.actions]
    by_id = {action.id: action for action in changed}
    seen: set[tuple[str, str]] = set()
    loads: set[str] = set()
    for change in decision.changes:
        key = (change.action_id, change.field)
        if key in seen or change.value not in options.get(change.action_id, {}).get(
            change.field, []
        ):
            raise ValueError("model changed a protected or out-of-bounds value")
        seen.add(key)
        if change.field != "restSeconds":
            if change.action_id in loads:
                raise ValueError("multiple load fields changed")
            loads.add(change.action_id)
        getattr(by_id[change.action_id], change.field).value = change.value
    if (
        request.intent == "more_challenging"
        and request.energy != "tired"
        and request.experience == "beginner"
        and len(loads) > sum(action.confirmed for action in request.actions) // 2
    ):
        raise ValueError("too many beginner load changes")
    before = estimate_actions(request.actions)
    after = estimate_actions(changed)
    if abs(after - before) > before * 0.2 + 1e-6:
        raise ValueError("whole-plan time boundary exceeded")


class PlanAdjustmentService:
    def __init__(self, model: AdjustmentModel | None) -> None:
        self.model = model

    async def propose(self, request: PlanAdjustmentRequest) -> PlanAdjustmentResponse:
        options = allowed_adjustments(request)
        if not options:
            return PlanAdjustmentResponse(
                input_fingerprint=request.input_fingerprint,
                plan_revision=request.plan_revision,
                mode="protected",
                changes=[],
                summary="当前方案保持不变，可以直接编辑动作数字。",
            )
        if self.model is None:
            raise ValueError("adjustment model unavailable")
        payload = request.model_dump(mode="json", exclude={"input_fingerprint", "plan_revision"})
        payload["actions"] = [
            action.model_dump(mode="json") for action in request.actions if action.confirmed
        ]
        payload["allowed_values"] = options
        payload["estimated_seconds"] = estimate_actions(request.actions)
        async with asyncio.timeout(25):
            decision = await self.model.propose_plan_adjustment(payload)
        validate_adjustment(request, decision)
        return PlanAdjustmentResponse(
            **decision.model_dump(),
            input_fingerprint=request.input_fingerprint,
            plan_revision=request.plan_revision,
            mode="model",
        )
