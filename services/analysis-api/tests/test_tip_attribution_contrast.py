"""Offline experiments exercise the agreed Provider -> fusion public seam."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from hakimi_analysis.orchestration import SkillRepository

ROOT = Path(__file__).resolve().parents[3]


def load_tool() -> ModuleType:
    path = ROOT / "services/analysis-api/scripts/tip_attribution_contrast.py"
    spec = importlib.util.spec_from_file_location("tip_attribution_contrast", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_baseline_trial_detects_the_observed_wrong_tip_without_touching_production() -> None:
    tool = load_tool()
    case = tool.load_case("ambiguous_transition")
    groups = case["groups"]
    groups[1]["accepted_tip_ids"] = ["visual-2-tip-1", "visual-2-tip-2"]
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        trial = tool.TipAttributionTrial(
            case_id="ambiguous_transition", arm="nested", http_client=client,
            api_key="synthetic", model_id="synthetic", base_url="https://ark.test",
            instructions=SkillRepository.load(ROOT / "skills").fusion_instructions,
        )
        receipt = await trial.run()
    assert receipt["status"] == "quality_failed"
    assert receipt["unexpected_tips"] == 1
    assert receipt["missing_required_tips"] == 0
    assert receipt["groups_match"] is True
    assert receipt["actions_parameters_ranges_match"] is True
    assert len(requests) == 1
    assert "坚持完成4组" not in json.dumps(receipt, ensure_ascii=False)


async def run_cell(
    arm: str, case_id: str, *, response_groups: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tool = load_tool()
    wire: dict[str, Any] = {}
    groups = tool.load_case(case_id)["groups"] if response_groups is None else response_groups

    def respond(request: httpx.Request) -> httpx.Response:
        assert not wire, "one request per cell"
        wire.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        receipt = await tool.TipAttributionTrial(
            case_id=case_id, arm=arm, http_client=client, api_key="synthetic",
            model_id="synthetic", base_url="https://ark.test",
            instructions=SkillRepository.load(ROOT / "skills").fusion_instructions,
        ).run()
    return receipt, wire


def observations(wire: dict[str, Any]) -> list[dict[str, Any]]:
    value: list[dict[str, Any]] = json.loads(
        wire["input"][0]["content"][0]["text"]
    )["observations"]
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["ambiguous_transition", "previous_action_closing_caption"])
async def test_contrast_preserves_evidence_schema_and_gold_without_leaking_tip_answers(
    case_id: str,
) -> None:
    baseline, a = await run_cell("nested", case_id)
    flat, b = await run_cell("flat", case_id)
    fixed, c = await run_cell("flat_fixed_groups", case_id)
    assert all(r["status"] == "passed" for r in [baseline, flat, fixed])
    original = observations(a)
    envelope = observations(b)[0]
    restored = []
    for action in envelope["actions"]:
        restored.append({**action, "tip_evidence": [
            {k: v for k, v in tip.items() if k != "upstream_observation_id"}
            for tip in envelope["tips"] if tip["upstream_observation_id"] == action["id"]
        ]})
    assert restored == original
    fixed_envelope = observations(c)[0]
    assert fixed_envelope["actions"] == envelope["actions"]
    assert fixed_envelope["tips"] == envelope["tips"]
    assert all("accepted_tip_ids" not in g for g in fixed_envelope["fixed_groups"])
    assert "fixed_groups" not in envelope
    assert a["text"] == b["text"] == c["text"], "identical output schema"
    assert a["model"] == b["model"] == c["model"]
    assert a["instructions"] == SkillRepository.load(ROOT / "skills").fusion_instructions
    assert b["instructions"].startswith(a["instructions"])
    assert c["instructions"].startswith(b["instructions"])
    assert baseline["case_sha256"] == flat["case_sha256"] == fixed["case_sha256"]
    assert len({r["request_sha256"] for r in [baseline, flat, fixed]}) == 3


@pytest.mark.asyncio
async def test_fixed_group_name_drift_is_reported_not_silently_corrected() -> None:
    groups = load_tool().load_case("ambiguous_transition")["groups"]
    groups[1]["name"] = "模型改名"
    receipt, _ = await run_cell("flat_fixed_groups", "ambiguous_transition", response_groups=groups)
    assert receipt["status"] == "fixed_group_drift"
    assert receipt["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["nested", "flat", "flat_fixed_groups"])
@pytest.mark.parametrize("mutation", ["empty", "wrong", "unknown", "duplicate", "cross_group"])
async def test_scoring_rejects_wrong_and_missing_tips_in_every_arm(arm: str, mutation: str) -> None:
    groups = load_tool().load_case("ambiguous_transition")["groups"]
    if mutation == "cross_group":
        groups[0]["accepted_tip_ids"] = ["visual-2-tip-2"]
        groups[1]["accepted_tip_ids"] = []
    else:
        groups[1]["accepted_tip_ids"] = {
            "empty": [], "wrong": ["visual-2-tip-1", "visual-2-tip-2"],
            "unknown": ["unknown"], "duplicate": ["visual-2-tip-2", "visual-2-tip-2"],
        }[mutation]
    receipt, _ = await run_cell(arm, "ambiguous_transition", response_groups=groups)
    assert receipt["status"] == "quality_failed"
    assert receipt["passed"] is False
    assert receipt["selections_match"] is False
    if mutation == "wrong":
        assert receipt["unexpected_tips"] == 1
        assert receipt["missing_required_tips"] == 0
    else:
        assert receipt["missing_required_tips"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "timeout", "invalid_json", "cancel"])
async def test_failures_do_not_retry_persist_content_or_allow_reuse(failure: str) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise TimeoutError("private error content")
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "invalid_json":
            return httpx.Response(200, json={"output_text": "private invalid response"})
        return httpx.Response(503, text="private provider response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        trial = load_tool().TipAttributionTrial(
            case_id="ambiguous_transition", arm="flat_fixed_groups", http_client=client,
            api_key="synthetic", model_id="synthetic", base_url="https://ark.test",
            instructions=SkillRepository.load(ROOT / "skills").fusion_instructions,
        )
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await trial.run()
        else:
            receipt = await trial.run()
            assert receipt["status"] == "unavailable"
            assert receipt["passed"] is False
            assert "private" not in json.dumps(receipt)
        with pytest.raises(RuntimeError, match="trial_already_used"):
            await trial.run()
    assert calls == 1
