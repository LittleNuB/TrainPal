"""Before-training tip ranking and deterministic scheduling; no workout-state writes."""

import asyncio
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Final, Literal, Protocol

from pydantic import Field, model_validator

from hakimi_analysis.models import Segment, SourceTip, StrictModel
from hakimi_analysis.source_tips import compact_text, safe_tips

CONTRACT_VERSION: Final = "training-reminders.v1.1"
SKILL_VERSION: Final = "1.0.0"
POLICY_VERSION: Final = "ReminderPolicy.v1"
ACTIVE_INTERVAL_SECONDS: Final = 30
END_QUIET_SECONDS: Final = 10
MAX_REMINDER_INPUT_BYTES: Final = 64_000


def serialize_reminder_input(payload: dict[str, object]) -> str:
    """One bounded representation for validation and the replaceable Provider wire."""
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_REMINDER_INPUT_BYTES:
        raise ValueError("reminder input exceeds its bounded capacity")
    return text


def _fingerprint_numbers(value: object) -> object:
    # Tag numbers before hashing to avoid Python/JS JSON formatting differences.
    # Keep arbitrary Python integers exact; browser callers reject unsafe integers.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return {"$integer": str(value)}
    if isinstance(value, float):
        if value.is_integer():
            return {"$integer": str(int(value))}
        return {"$float64": struct.pack(">d", value).hex()}
    if isinstance(value, dict):
        return {key: _fingerprint_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_fingerprint_numbers(item) for item in value]
    return value


class ReminderPreferences(StrictModel):
    automatic_speech: bool
    confirmed: bool


class ReviewedReminderTip(StrictModel):
    tip_id: str = Field(min_length=1, max_length=128)
    review_status: Literal["approved", "unknown", "rejected"] = "unknown"
    review_action_id: str = Field(min_length=1, max_length=128)
    review_action_revision: int = Field(ge=0)
    source_id: str = Field(min_length=1, max_length=128)
    tip: SourceTip


class ReminderAction(StrictModel):
    action_id: str = Field(min_length=1, max_length=128)
    action_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=120)
    confirmed: bool
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_segment: Segment | None = None
    mode: Literal["reps", "duration"]
    sets: int = Field(ge=1)
    reps: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    tips: list[ReviewedReminderTip] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_arrangement(self) -> "ReminderAction":
        if self.mode == "reps" and (self.reps is None or self.duration_seconds is not None):
            raise ValueError("reps arrangement requires reps and no duration")
        if self.mode == "duration" and (self.duration_seconds is None or self.reps is not None):
            raise ValueError("duration arrangement requires duration and no reps")
        if len({tip.tip_id for tip in self.tips}) != len(self.tips):
            raise ValueError("reminder tip IDs must be unique within an action")
        return self


class ReminderPlanRequest(StrictModel):
    plan_id: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(ge=0)
    preferences: ReminderPreferences
    actions: list[ReminderAction] = Field(max_length=200)

    @model_validator(mode="after")
    def validate_action_ids(self) -> "ReminderPlanRequest":
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("reminder action IDs must be unique")
        serialize_reminder_input(self.model_dump(mode="json"))
        return self


class ReminderRanking(StrictModel):
    action_id: str = Field(min_length=1, max_length=128)
    tip_ids: list[str] = Field(max_length=5)


class ReminderRankings(StrictModel):
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rankings: list[ReminderRanking] = Field(max_length=200)


class ScheduledReminder(StrictModel):
    action_id: str
    action_revision: int
    tip_id: str
    stage: Literal["prepare", "training"]
    trigger: Literal["action_prepare", "set_start", "active_elapsed"]
    set_number: int = Field(ge=1)
    active_seconds: int | None = Field(default=None, ge=1)
    priority: int = Field(ge=1, le=5)
    max_plays: Literal[1] = 1


class ReminderBudget(StrictModel):
    action_id: str
    utterance_limit: int = Field(ge=0, le=5)
    active_interval_seconds: Literal[30] = ACTIVE_INTERVAL_SECONDS
    end_quiet_seconds: Literal[10] = END_QUIET_SECONDS


class ReminderPlan(StrictModel):
    plan_id: str
    plan_revision: int
    input_fingerprint: str
    contract_version: Literal["training-reminders.v1.1"] = CONTRACT_VERSION
    skill_version: Literal["1.0.0"] = SKILL_VERSION
    policy_version: Literal["ReminderPolicy.v1"] = POLICY_VERSION
    mode: Literal["model", "rules", "disabled"]
    fallback_reason: (
        Literal[
            "model_unavailable",
            "model_failure",
            "model_timeout",
            "invalid_selection",
            "skill_unavailable",
        ]
        | None
    ) = None
    reminders: list[ScheduledReminder]
    budgets: list[ReminderBudget] = Field(default_factory=list, max_length=200)


class ReminderRankingModel(Protocol):
    async def rank_training_reminders(
        self, *, payload: dict[str, object], instructions: str
    ) -> ReminderRankings: ...


class TrainingReminderPlanner:
    def __init__(
        self,
        model: ReminderRankingModel | None = None,
        *,
        skill_path: Path | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("reminder timeout must be finite and positive")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._skill_path = skill_path or (
            Path(__file__).resolve().parents[4] / "skills/training-reminder-planning/SKILL.md"
        )

    async def plan(self, request: ReminderPlanRequest) -> ReminderPlan:
        request = ReminderPlanRequest.model_validate(request.model_dump())
        fingerprint = hashlib.sha256(
            json.dumps(
                _fingerprint_numbers(
                    {
                        "contract": CONTRACT_VERSION,
                        "skill": SKILL_VERSION,
                        "policy": POLICY_VERSION,
                        "input": request.model_dump(mode="json"),
                    }
                ),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if not request.preferences.confirmed or not request.preferences.automatic_speech:
            return ReminderPlan(
                plan_id=request.plan_id,
                plan_revision=request.plan_revision,
                input_fingerprint=fingerprint,
                mode="disabled",
                reminders=[],
            )
        actions = [
            action.model_copy(update={"tips": _eligible_tips(action)})
            for action in request.actions
            if action.confirmed
        ]
        actions = [
            action for action in actions if _schedule(action, [tip.tip_id for tip in action.tips])
        ]
        if not actions:
            return ReminderPlan(
                plan_id=request.plan_id,
                plan_revision=request.plan_revision,
                input_fingerprint=fingerprint,
                mode="disabled",
                reminders=[],
            )
        payload: dict[str, object] = {
            "input_fingerprint": fingerprint,
            "actions": [action.model_dump(mode="json") for action in actions],
        }
        serialize_reminder_input(payload)
        mode: Literal["model", "rules", "disabled"] = "rules"
        fallback_reason: (
            Literal[
                "model_unavailable",
                "model_failure",
                "model_timeout",
                "invalid_selection",
                "skill_unavailable",
            ]
            | None
        ) = "model_unavailable"
        ranks = {action.action_id: [tip.tip_id for tip in action.tips] for action in actions}
        if self._model is not None:
            try:
                instructions = self._skill_path.read_text(encoding="utf-8")
                if f"\nversion: {SKILL_VERSION}\n" not in instructions:
                    raise ValueError("reminder Skill version mismatch")
            except (OSError, ValueError):
                fallback_reason = "skill_unavailable"
            else:
                try:
                    async with asyncio.timeout(self._timeout_seconds):
                        selected = await self._model.rank_training_reminders(
                            payload=payload, instructions=instructions
                        )
                except TimeoutError:
                    fallback_reason = "model_timeout"
                except Exception:
                    # Only the replaceable external ranking boundary is guarded here.
                    # Cancellation is a BaseException and must propagate.
                    fallback_reason = "model_failure"
                else:
                    if _valid_rankings(selected, ranks, fingerprint):
                        ranks = {rank.action_id: rank.tip_ids for rank in selected.rankings}
                        mode = "model"
                        fallback_reason = None
                    else:
                        fallback_reason = "invalid_selection"
        reminders = [
            event for action in actions for event in _schedule(action, ranks[action.action_id])
        ]
        return ReminderPlan(
            plan_id=request.plan_id,
            plan_revision=request.plan_revision,
            input_fingerprint=fingerprint,
            mode=mode,
            fallback_reason=fallback_reason,
            reminders=reminders,
            budgets=[
                ReminderBudget(
                    action_id=action.action_id,
                    utterance_limit=sum(event.action_id == action.action_id for event in reminders),
                )
                for action in actions
            ],
        )


def _valid_rankings(
    selected: ReminderRankings, expected: dict[str, list[str]], fingerprint: str
) -> bool:
    if selected.input_fingerprint != fingerprint or len(selected.rankings) != len(expected):
        return False
    if {rank.action_id for rank in selected.rankings} != set(expected):
        return False
    return all(
        len(rank.tip_ids) == len(expected[rank.action_id])
        and set(rank.tip_ids) == set(expected[rank.action_id])
        for rank in selected.rankings
    )


def _eligible_tips(action: ReminderAction) -> list[ReviewedReminderTip]:
    source = action.source_segment
    if (
        source is None
        or not action.source_id
        or not (
            math.isfinite(source.start_seconds)
            and math.isfinite(source.end_seconds)
            and 0 <= source.start_seconds < source.end_seconds <= 300
        )
    ):
        return []
    result: list[ReviewedReminderTip] = []
    seen: set[str] = set()
    for tip in action.tips:
        evidence = tip.tip.evidence
        key = compact_text(tip.tip.text)
        if (
            tip.review_status != "approved"
            or tip.review_action_id != action.action_id
            or tip.review_action_revision != action.action_revision
            or tip.source_id != action.source_id
            or key in seen
            or not math.isfinite(evidence.start_seconds)
            or not math.isfinite(evidence.end_seconds)
            or not (
                source.start_seconds
                <= evidence.start_seconds
                < evidence.end_seconds
                <= source.end_seconds
            )
            or not safe_tips([tip.tip], limit=1)
        ):
            continue
        seen.add(key)
        result.append(tip)
    return result


def _schedule(action: ReminderAction, ranking: list[str]) -> list[ScheduledReminder]:
    tips = {tip.tip_id: tip for tip in action.tips}
    result: list[ScheduledReminder] = []
    setup = [tip_id for tip_id in ranking if tips[tip_id].tip.category == "setup"]
    if setup:
        result.append(
            ScheduledReminder(
                action_id=action.action_id,
                action_revision=action.action_revision,
                tip_id=setup[0],
                stage="prepare",
                trigger="action_prepare",
                set_number=1,
                priority=ranking.index(setup[0]) + 1,
            )
        )
    training = [tip_id for tip_id in ranking if tips[tip_id].tip.category != "setup"]
    slots: list[tuple[int, int]] = []
    # Work is bounded by the available tips, not potentially huge user set/duration values.
    for offset in range(0, len(training) * ACTIVE_INTERVAL_SECONDS, ACTIVE_INTERVAL_SECONDS):
        if action.mode == "reps" and offset > 0:
            break
        if action.mode == "duration" and (
            action.duration_seconds is None or offset >= action.duration_seconds - END_QUIET_SECONDS
        ):
            break
        for set_number in range(1, min(action.sets, len(training)) + 1):
            slots.append((set_number, offset))
            if len(slots) == len(training):
                break
        if len(slots) == len(training):
            break
    for tip_id, (set_number, offset) in zip(training, slots, strict=False):
        result.append(
            ScheduledReminder(
                action_id=action.action_id,
                action_revision=action.action_revision,
                tip_id=tip_id,
                stage="training",
                trigger="set_start" if offset == 0 else "active_elapsed",
                set_number=set_number,
                active_seconds=offset or None,
                priority=ranking.index(tip_id) + 1,
            )
        )
    return result
