import json

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.personalization import (
    ModelAdjustment,
    PlanAdjustmentRequest,
    PlanAdjustmentService,
    validate_adjustment,
)
from hakimi_analysis.providers.ark import ArkResponsesClient


def request_payload() -> dict[str, object]:
    return {
        "input_fingerprint": "adjustment-input-example",
        "plan_revision": 2,
        "intent": "more_challenging",
        "experience": "intermediate",
        "actions": [
            {
                "id": "row",
                "name": "划船",
                "mode": "reps",
                "confirmed": True,
                "sets": {"value": 3, "source": "video"},
                "reps": {"value": 10, "source": "rule"},
                "durationSeconds": {"value": None, "source": None},
                "restSeconds": {"value": 60, "source": "user"},
            },
        ],
    }


@pytest.mark.parametrize(
    "field,value,status",
    [("reps", 11, 200), ("reps", 14, 503), ("sets", 4, 503), ("restSeconds", 65, 503)],
)
async def test_model_authors_proposal_and_server_rejects_unsafe_changes(
    field: str,
    value: int,
    status: int,
) -> None:
    calls: list[dict[str, object]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "changes": [
                            {
                                "action_id": "row",
                                "field": field,
                                "value": value,
                                "reason": "这次希望增加挑战，每组增加一次，保留原休息。",
                            }
                        ],
                        "summary": "从一次的小幅增加开始。",
                    },
                    ensure_ascii=False,
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider:
        model = ArkResponsesClient(
            api_key="synthetic",
            model_id="existing",
            base_url="https://model.invalid",
            http_client=provider,
        )
        app = create_app(adjustment_service=PlanAdjustmentService(model))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.post("/api/v1/plan-adjustments", json=request_payload())
    assert len(calls) == 1
    assert calls[0]["store"] is False
    assert result.status_code == status
    if status == 200:
        assert result.json()["mode"] == "model"
        assert result.json()["changes"][0]["value"] == 11
        assert result.json()["input_fingerprint"] == "adjustment-input-example"
        assert result.headers["cache-control"] == "no-store"


async def test_missing_model_never_returns_a_rule_proposal_as_ai() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/api/v1/plan-adjustments", json=request_payload())
        assert result.status_code == 503


@pytest.mark.parametrize("case", ["locked", "safety_stop", "empty"])
async def test_protected_plan_does_not_require_a_model(case: str) -> None:
    payload = request_payload()
    if case == "empty":
        payload["actions"] = []
    elif case == "safety_stop":
        payload["safety_stop"] = True
    else:
        request = PlanAdjustmentRequest.model_validate(payload)
        request.actions[0].reps.source = "video"
        payload = request.model_dump()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        result = await client.post("/api/v1/plan-adjustments", json=payload)
    assert result.status_code == 200
    assert result.json()["mode"] == "protected"


async def test_safety_stop_skips_model_and_cross_origin_is_rejected() -> None:
    async def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not contact model")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as provider:
        model = ArkResponsesClient(
            api_key="synthetic",
            model_id="existing",
            base_url="https://model.invalid",
            http_client=provider,
        )
        app = create_app(adjustment_service=PlanAdjustmentService(model))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {**request_payload(), "safety_stop": True}
            result = await client.post("/api/v1/plan-adjustments", json=payload)
            assert result.json()["mode"] == "protected"
            assert result.json()["changes"] == []
            result = await client.post(
                "/api/v1/plan-adjustments",
                json=payload,
                headers={"origin": "https://untrusted.invalid"},
            )
            assert result.status_code == 403


@pytest.mark.parametrize(
    "case", ["whole_plan", "range", "feedback", "pending", "beginner", "tired", "duplicate"]
)
def test_model_decision_must_satisfy_context_and_whole_plan_guards(case: str) -> None:
    request = PlanAdjustmentRequest.model_validate(request_payload())
    action = request.actions[0]
    field, value = "reps", 11
    if case == "whole_plan":
        request.intent = "easier_to_finish"
        request.energy = "tired"
        action.restSeconds.source = "rule"
        field, value = "restSeconds", 90
    elif case == "range":
        action.reps_range = (8, 10)
    elif case == "feedback":
        action.feedback = "too_hard"
    elif case == "pending":
        action.confirmed = False
    elif case == "beginner":
        request.experience = "beginner"
    elif case == "tired":
        request.energy = "tired"
    changes = [{"action_id": "row", "field": field, "value": value, "reason": "合成建议"}]
    if case == "duplicate":
        changes *= 2
    with pytest.raises(ValueError):
        validate_adjustment(
            request, ModelAdjustment.model_validate({"changes": changes, "summary": "合成"})
        )


async def test_oversize_body_rejected_before_model() -> None:
    async def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not contact model")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as provider:
        model = ArkResponsesClient(
            api_key="synthetic",
            model_id="existing",
            base_url="https://model.invalid",
            http_client=provider,
        )
        app = create_app(adjustment_service=PlanAdjustmentService(model))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.post("/api/v1/plan-adjustments", content=b" " * 64_001)
            ).status_code == 413
