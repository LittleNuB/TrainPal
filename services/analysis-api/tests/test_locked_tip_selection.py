"""Two-stage pilot contract at the real Ark HTTP -> fusion seam; synthetic data only."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import (
    CandidateParameters,
    EvidenceType,
    SourceTip,
    SpeechSignal,
    VisualSegment,
)
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


@pytest.mark.asyncio
async def test_locked_group_selects_verbatim_reminder_in_second_request() -> None:
    requests: list[dict[str, Any]] = []
    original = SourceTip(
        text="不要耸肩，双手向两侧打开。",
        category="path",
        evidence={"type": "visual", "start_seconds": 12, "end_seconds": 14},
    )
    visual = VisualSegment(
        action_name="反向飞鸟",
        start_seconds=10,
        end_seconds=30,
        segment_role="teaching_demo",
        visual_cue="反向飞鸟完整教学",
        tips=[original],
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        requests.append(body)
        if len(requests) == 1:
            assert body["observations"][0]["tip_evidence"] == []
            result = {
                "groups": [
                    {
                        "member_ids": ["visual-1"],
                        "name": "反向飞鸟",
                        "relation": "same_demonstration",
                        "accepted_tip_ids": [],
                    }
                ]
            }
        else:
            target = body["locked_groups"][0]
            result = {
                "selections": [
                    {
                        "group_id": target["group_id"],
                        "accepted_tip_ids": target["allowed_tip_ids"],
                    }
                ]
            }
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            retry_delays=(),
        )
        result = await SemanticCandidateFusion(
            model=model,
            instructions="synthetic",
            tip_selection_model=model,
        ).run(source_id="synthetic", speech_signals=[], visual_segments=[visual])
    assert len(requests) == 2
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == [original]
    assert result.candidates[0].name == "反向飞鸟"
    assert result.candidates[0].segment.start_seconds == 10
    assert result.candidates[0].segment.end_seconds == 30
    assert visual.tips == [original]


async def run_pair(
    *,
    fault: str = "none",
    enabled: bool = True,
    visuals: list[VisualSegment] | None = None,
    groups: list[dict[str, Any]] | None = None,
    remaining_seconds: float | None = None,
    stale: dict[str, Any] | None = None,
    client_defaults: bool = False,
    speech: list[SpeechSignal] | None = None,
) -> tuple[SemanticFusionResult, list[dict[str, Any]]]:
    if visuals is None:
        visuals = [
            VisualSegment(
                action_name="反向飞鸟",
                start_seconds=10,
                end_seconds=30,
                segment_role="teaching_demo",
                sequence_label="动作一",
                visual_cue="完整教学",
                text_parameters=CandidateParameters(sets=4, reps=12),
                tips=[
                    SourceTip(
                        text="不要耸肩。",
                        category="path",
                        evidence={
                            "type": "visual",
                            "start_seconds": 12,
                            "end_seconds": 14,
                        },
                    )
                ],
            )
        ]
    if groups is None:
        groups = [
            {
                "member_ids": [f"visual-{index}"],
                "name": item.action_name,
                "relation": "same_demonstration",
                "accepted_tip_ids": [f"visual-{index}-tip-1"],
            }
            for index, item in enumerate(visuals, 1)
        ]
    requests: list[dict[str, Any]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})
        if fault in {"timeout", "shared_budget"}:
            await asyncio.Event().wait()
        if fault == "cancel":
            raise asyncio.CancelledError
        if fault == "provider":
            return httpx.Response(503)
        selections = [
            {
                "group_id": target["group_id"],
                "accepted_tip_ids": target["allowed_tip_ids"][:3],
            }
            for target in body["locked_groups"]
        ]
        if fault == "unknown_tip":
            selections[0]["accepted_tip_ids"] = ["invented"]
        elif fault == "cross_group":
            selections[0]["accepted_tip_ids"] = selections[1]["accepted_tip_ids"]
        elif fault == "duplicate_tip":
            selections[0]["accepted_tip_ids"] *= 2
        elif fault == "overlong":
            selections[0]["accepted_tip_ids"] *= 4
        elif fault == "wrong_type":
            selections[0]["accepted_tip_ids"] = "bad"
        elif fault == "unknown_group":
            selections[0]["group_id"] = "invented"
        elif fault == "duplicate_group":
            selections.append(selections[0])
        elif fault == "missing_group":
            selections.pop()
        elif fault == "action_mutation":
            selections[0]["name"] = "深蹲"
            selections[0]["parameters"] = {"sets": 100}
        elif fault == "empty":
            selections[0]["accepted_tip_ids"] = []
        result = stale if stale is not None else {"selections": selections}
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    options: dict[str, Any] = {} if client_defaults else {"retry_delays": ()}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            **options,
        )
        result = await SemanticCandidateFusion(
            model=model,
            instructions="synthetic",
            tip_selection_model=model if enabled else None,
            timeout_seconds=0.02 if fault == "timeout" else 20,
            allow_touching_teaching=True,
        ).run(
            source_id="synthetic",
            speech_signals=speech or [],
            visual_segments=visuals,
            remaining_seconds=remaining_seconds,
        )
    return result, requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "provider",
        "timeout",
        "unknown_tip",
        "duplicate_tip",
        "overlong",
        "wrong_type",
        "unknown_group",
        "duplicate_group",
        "missing_group",
        "action_mutation",
    ],
)
async def test_reminder_failure_preserves_locked_action(fault: str) -> None:
    result, requests = await run_pair(fault=fault)
    assert len(requests) == 2
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "反向飞鸟"
    assert candidate.parameters.sets == 4
    assert candidate.parameters.reps == 12
    assert candidate.segment.start_seconds == 10
    assert candidate.segment.end_seconds == 30
    assert candidate.tips == []
    assert result.warnings[-1].code == "semantic_fusion_unavailable"


@pytest.mark.asyncio
async def test_default_path_stays_single_call_and_retains_existing_review() -> None:
    result, requests = await run_pair(enabled=False)
    assert len(requests) == 1
    assert len(requests[0]["observations"][0]["tip_evidence"]) == 1
    assert result.candidates[0].tips[0].text == "不要耸肩。"


@pytest.mark.asyncio
async def test_empty_review_is_valid_and_phase_one_tip_selection_is_ignored() -> None:
    result, requests = await run_pair(fault="empty")
    assert len(requests) == 2
    assert result.candidates[0].tips == []
    assert not result.warnings


@pytest.mark.asyncio
async def test_cancel_propagates_instead_of_returning_success() -> None:
    with pytest.raises(asyncio.CancelledError):
        await run_pair(fault="cancel")


@pytest.mark.asyncio
async def test_reminder_selection_from_prior_request_is_rejected() -> None:
    _, requests = await run_pair()
    target = requests[1]["locked_groups"][0]
    result, later = await run_pair(
        stale={
            "selections": [
                {
                    "group_id": target["group_id"],
                    "accepted_tip_ids": target["allowed_tip_ids"],
                }
            ]
        }
    )
    assert later[1]["locked_groups"][0]["group_id"] != target["group_id"]
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []
    assert result.warnings


def demo(start: float, end: float, label: str = "1", reps: int = 12) -> VisualSegment:
    return VisualSegment(
        action_name="反向飞鸟",
        start_seconds=start,
        end_seconds=end,
        segment_role="teaching_demo",
        sequence_label=label,
        visual_cue="完整教学",
        text_parameters=CandidateParameters(sets=4, reps=reps),
        tips=[
            SourceTip(
                text="不要耸肩。",
                category="path",
                evidence={
                    "type": "visual",
                    "start_seconds": start + 1,
                    "end_seconds": start + 2,
                },
            )
        ],
    )


@pytest.mark.asyncio
async def test_total_budget_is_shared_and_second_timeout_does_not_undo_grouping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Control the external elapsed-time seam, not the event loop's timeout clock.
    # First-stage setup has 20s; its simulated elapsed time leaves 20ms for tips.
    ticks = iter((100.0, 119.98))
    monkeypatch.setattr(
        "hakimi_analysis.semantic_fusion.time", SimpleNamespace(monotonic=lambda: next(ticks))
    )
    async with asyncio.timeout(2):
        result, requests = await run_pair(fault="shared_budget", remaining_seconds=20)
    assert len(requests) == 2
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []
    assert result.warnings[-1].code == "semantic_fusion_unavailable"


@pytest.mark.asyncio
async def test_cross_group_choice_drops_only_that_group_not_other_valid_reminders() -> None:
    result, _ = await run_pair(fault="cross_group", visuals=[demo(10, 30), demo(40, 60, "2")])
    assert len(result.candidates) == 2
    assert result.candidates[0].tips == []
    assert result.candidates[1].tips[0].evidence.start_seconds == 41


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["different_number", "later", "uncertain", "preview"])
async def test_rejected_or_reference_groups_never_become_reminder_targets(reason: str) -> None:
    visuals = [
        demo(10, 30),
        demo(
            40 if reason in {"later", "preview"} else 15,
            60,
            "2" if reason == "different_number" else "1",
        ),
    ]
    groups: list[dict[str, Any]] = [
        {
            "member_ids": ["visual-1", "visual-2"],
            "name": "反向飞鸟",
            "relation": "uncertain" if reason == "uncertain" else "same_demonstration",
            "accepted_tip_ids": ["visual-1-tip-1"],
        }
    ]
    if reason == "preview":
        groups = [
            {
                "member_ids": ["visual-1"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "content_role": "preview",
                "related_member_id": "visual-2",
            },
            {"member_ids": ["visual-2"], "name": "反向飞鸟", "relation": "same_demonstration"},
        ]
    result, requests = await run_pair(visuals=visuals, groups=groups)
    if reason == "preview":
        assert len(requests[1]["locked_groups"]) == 1
        assert requests[1]["locked_groups"][0]["member_ids"] == ["visual-2"]
        assert len(result.candidates) == 1
        assert result.candidates[0].tips[0].evidence.start_seconds == 41
    else:
        assert len(requests) == 1
        assert len(result.candidates) == 2
        assert all(item.tips == [] and item.needs_confirmation for item in result.candidates)


@pytest.mark.asyncio
async def test_parameter_conflict_and_all_sources_survive_second_stage() -> None:
    visuals = [demo(10, 30), demo(20, 40, reps=15)]
    result, _ = await run_pair(
        visuals=visuals,
        groups=[
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
            }
        ],
    )
    assert len(result.candidates) == 1
    item = result.candidates[0]
    assert item.parameters.reps is None
    assert item.parameters.sets == 4
    assert item.needs_confirmation
    assert {alt.parameters.reps for alt in item.parameter_conflicts[0].alternatives} == {12, 15}
    assert [(e.start_seconds, e.end_seconds) for e in item.evidence] == [(10, 30), (20, 40)]
    assert item.tips[0].text == "不要耸肩。"


@pytest.mark.asyncio
async def test_no_tips_skips_extra_call() -> None:
    visual = demo(10, 30)
    visual.tips = []
    result, requests = await run_pair(visuals=[visual])
    assert len(requests) == 1
    assert len(result.candidates) == 1
    assert not result.warnings


@pytest.mark.asyncio
async def test_second_stage_never_retries_even_with_default_client() -> None:
    result, requests = await run_pair(fault="provider", client_defaults=True)
    assert len(requests) == 2
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []


@pytest.mark.asyncio
async def test_model_separated_variants_and_later_rounds_remain_separate() -> None:
    visuals = [demo(10, 30, "1左侧"), demo(15, 35, "1右侧"), demo(40, 60, "1第二轮")]
    result, requests = await run_pair(visuals=visuals)
    assert len(requests[1]["locked_groups"]) == 3
    assert len(result.candidates) == 3
    assert [item.segment.start_seconds for item in result.candidates] == [10, 15, 40]


@pytest.mark.asyncio
async def test_speech_reminder_keeps_full_negation_condition_and_source_time() -> None:
    original = SourceTip(
        text="做反向飞鸟时不要耸肩，左侧也保持这个姿势。",
        category="setup",
        evidence={"type": "speech", "start_seconds": 12, "end_seconds": 16},
    )
    signal = SpeechSignal(
        action_name="反向飞鸟",
        start_seconds=10,
        end_seconds=30,
        evidence_text="合成完整教学",
        segment_role="teaching_demo",
        tips=[original],
        sets=4,
        reps=12,
    )
    result, _ = await run_pair(
        speech=[signal],
        groups=[
            {
                "member_ids": ["speech-1", "visual-1"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
            }
        ],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].tips[0] == original
    assert result.candidates[0].needs_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["before", "after", "source"])
async def test_ineligible_source_tips_never_become_selectable(invalid: str) -> None:
    visual = demo(10, 30)
    if invalid == "before":
        visual.tips[0].evidence.start_seconds = 9
    elif invalid == "after":
        visual.tips[0].evidence.end_seconds = 31
    else:
        visual.tips[0].evidence.type = EvidenceType.SPEECH
    result, requests = await run_pair(visuals=[visual])
    assert len(requests) == 1
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []
