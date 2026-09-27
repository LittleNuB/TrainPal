"""Frozen synthetic contrasts, verified at the external HTTP model boundary."""

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]


async def cell(
    monkeypatch: pytest.MonkeyPatch, case_id: str, arm: str,
    mutation: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    monkeypatch.syspath_prepend(str(ROOT / "services/analysis-api/scripts"))
    tool = importlib.import_module("context_attribution_contrast")
    case = tool.load_context_case(case_id)
    groups = case["groups"]
    if mutation == "extra":
        groups[-1]["accepted_tip_ids"].insert(0, case["wrong_tip_id"])
    elif mutation == "empty":
        for group in groups:
            group["accepted_tip_ids"] = []
    elif mutation == "malformed":
        groups[-1]["accepted_tip_ids"] = "invalid"
    wire: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        assert not wire, "one request per cell"
        wire.update(json.loads(request.content))
        if mutation == "timeout":
            raise TimeoutError("private synthetic exception")
        if mutation == "cancel":
            raise asyncio.CancelledError
        return httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        row = await tool.ContextAttributionTrial(
            case_id=case_id, arm=arm, http_client=client,
            api_key="synthetic", model_id="synthetic", base_url="https://offline.test",
            instructions="fixed instructions",
        ).run()
    return row, wire


@pytest.mark.asyncio
async def test_spoken_contrast_changes_available_context_not_prompt_schema_or_tip_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, a = await cell(monkeypatch, "spoken_negation", "legacy_visible")
    new, b = await cell(monkeypatch, "spoken_negation", "complete_context")
    assert old["status"] == new["status"] == "passed"
    assert a["instructions"] == b["instructions"]
    assert a["text"] == b["text"] and a["model"] == b["model"]
    old_input = json.loads(a["input"][0]["content"][0]["text"])["observations"][0]
    new_input = json.loads(b["input"][0]["content"][0]["text"])["observations"][0]
    assert old_input["observations"] == new_input["observations"]
    assert old_input["evidence_context"]["records"] == []
    assert new_input["evidence_context"]["records"][0]["text"] == (
        "做反向飞鸟时，胸部贴住靠垫，不要耸肩。"
    )
    assert "accepted_tip_ids" not in json.dumps(new_input)
    assert old["case_sha256"] == new["case_sha256"]
    assert old["request_sha256"] != new["request_sha256"]
    assert "不要耸肩" not in json.dumps(new, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id", ["ambiguous_transition", "visual_tail_scope", "spoken_negation"],
)
@pytest.mark.parametrize("arm", ["legacy_visible", "complete_context"])
@pytest.mark.parametrize("mutation", [None, "extra", "empty", "malformed"])
async def test_frozen_answers_and_wrong_or_missing_tips_are_scored_before_paid_calls(
    monkeypatch: pytest.MonkeyPatch, case_id: str, arm: str, mutation: str | None,
) -> None:
    row, _ = await cell(monkeypatch, case_id, arm, mutation)
    if mutation is None:
        assert row["status"] == "passed"
    elif mutation == "malformed":
        assert row["status"] == "invalid_output_format"
    else:
        assert row["status"] == "quality_failed"
        if mutation == "extra":
            assert row["unexpected_tips"] == 1 and row["missing_required_tips"] == 0
        else:
            assert row["missing_required_tips"] > 0
    assert row["http_requests"] == 1


@pytest.mark.asyncio
async def test_visual_tail_is_the_only_observation_change_and_ambiguous_control_is_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identical_a, _ = await cell(monkeypatch, "ambiguous_transition", "legacy_visible")
    identical_b, _ = await cell(monkeypatch, "ambiguous_transition", "complete_context")
    assert identical_a["request_sha256"] == identical_b["request_sha256"]
    _, a = await cell(monkeypatch, "visual_tail_scope", "legacy_visible")
    _, b = await cell(monkeypatch, "visual_tail_scope", "complete_context")
    assert a["instructions"] == b["instructions"] and a["text"] == b["text"]
    aa, bb = [json.loads(w["input"][0]["content"][0]["text"])["observations"][0]
              for w in [a, b]]
    for old, new in zip(aa["observations"], bb["observations"], strict=True):
        assert len(old["description"]) == 600
        assert old["description"] == new["description"][:600]
        assert {k: v for k, v in old.items() if k != "description"} == {
            k: v for k, v in new.items() if k != "description"
        }


@pytest.mark.asyncio
async def test_timeout_is_technical_failure_and_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, _ = await cell(monkeypatch, "spoken_negation", "complete_context", "timeout")
    assert row["status"] == "unavailable"
    assert row["passed"] is False
    with pytest.raises(asyncio.CancelledError):
        await cell(monkeypatch, "spoken_negation", "complete_context", "cancel")
