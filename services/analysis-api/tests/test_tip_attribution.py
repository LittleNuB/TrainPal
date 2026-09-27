"""Synthetic source attribution checks at the fusion and Provider public seams."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import CandidateParameters, SourceTip, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


def tip(text: str, start: float, end: float) -> SourceTip:
    return SourceTip(
        text=text,
        category="path",
        evidence={"type": "visual", "start_seconds": start, "end_seconds": end},
    )


def next_action() -> VisualSegment:
    return VisualSegment(
        action_name="反向飞鸟",
        start_seconds=150,
        end_seconds=200,
        visual_cue="先出现上个动作的收尾字幕，随后进入反向飞鸟教学",
        sequence_label="第四个动作",
        segment_role="teaching_demo",
        tips=[tip("坚持完成4组", 150, 152), tip("双手朝两侧打开", 170, 172)],
    )


async def fuse(
    visuals: list[VisualSegment],
    groups: list[dict[str, Any]],
    *,
    status: int = 200,
    remaining_seconds: float | None = None,
    stall: bool = False,
) -> tuple[SemanticFusionResult, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(json.loads(payload["input"][0]["content"][0]["text"]))
        if stall:
            await asyncio.Event().wait()
        return httpx.Response(status, json={"output_text": json.dumps({"groups": groups})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test",
                model_id="test",
                base_url="https://test",
                http_client=client,
                retry_delays=(),
            ),
            instructions="synthetic attribution",
            timeout_seconds=0.01 if stall else 20,
        ).run(
            source_id="synthetic",
            speech_signals=[],
            visual_segments=visuals,
            remaining_seconds=remaining_seconds,
        )
    return result, requests


@pytest.mark.asyncio
async def test_previous_action_closing_caption_is_not_a_next_action_reminder() -> None:
    result, _ = await fuse(
        [next_action()],
        [
            {
                "member_ids": ["visual-1"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-2"],
            }
        ],
    )
    assert [item.text for item in result.candidates[0].tips] == ["双手朝两侧打开"]
    assert result.candidates[0].tips[0].evidence.start_seconds == 170


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["uncertain", "provider", "budget", "membership", "timeout"])
async def test_unreviewed_tips_never_escape_a_failed_fusion(failure: str) -> None:
    result, _ = await fuse(
        [next_action()],
        [
            {
                "member_ids": ["unknown" if failure == "membership" else "visual-1"],
                "name": "反向飞鸟",
                "relation": "uncertain" if failure == "uncertain" else "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-2"],
            }
        ],
        status=503 if failure == "provider" else 200,
        remaining_seconds=0 if failure == "budget" else None,
        stall=failure == "timeout",
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []
    assert result.candidates[0].needs_confirmation
    assert result.candidates[0].evidence[0].start_seconds == 150


@pytest.mark.asyncio
async def test_review_receives_source_tips_in_the_existing_single_model_request() -> None:
    visual = next_action()
    result, requests = await fuse(
        [visual],
        [
            {
                "member_ids": ["visual-1"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-2"],
            }
        ],
    )
    assert len(requests) == 1
    assert requests[0]["observations"][0]["tip_evidence"] == [
        {"id": "visual-1-tip-1", **visual.tips[0].model_dump(mode="json")},
        {"id": "visual-1-tip-2", **visual.tips[1].model_dump(mode="json")},
    ]
    assert result.candidates[0].tips == [visual.tips[1]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        None,
        [],
        ["unknown"],
        ["visual-1-tip-2", "visual-1-tip-2"],
        ["visual-1-tip-2", "visual-2-tip-1"],
    ],
)
async def test_missing_or_invalid_selection_does_not_silently_trust_upstream(
    selection: list[str] | None,
) -> None:
    group: dict[str, Any] = {
        "member_ids": ["visual-1"],
        "name": "反向飞鸟",
        "relation": "same_demonstration",
    }
    if selection is not None:
        group["accepted_tip_ids"] = selection
    result, _ = await fuse([next_action()], [group])
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["before", "after", "wrong_type"])
async def test_model_acceptance_cannot_override_original_tip_source_bounds(invalid: str) -> None:
    visual = next_action()
    evidence = visual.tips[1].evidence
    if invalid == "before":
        evidence.start_seconds = 149
    elif invalid == "after":
        evidence.end_seconds = 201
    else:
        visual.tips[1] = SourceTip(
            text="双手朝两侧打开",
            category="path",
            evidence={
                "type": "speech",
                "start_seconds": 170,
                "end_seconds": 172,
            },
        )
    result, _ = await fuse(
        [visual],
        [
            {
                "member_ids": ["visual-1"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-2"],
            }
        ],
    )
    assert result.candidates[0].tips == []


@pytest.mark.asyncio
async def test_known_tip_from_another_action_cannot_be_borrowed() -> None:
    earlier = VisualSegment(
        action_name="杠铃提拉",
        start_seconds=120,
        end_seconds=152,
        visual_cue="第3项收尾",
        sequence_label="3",
        segment_role="teaching_demo",
        tips=[tip("肘部向两侧打开", 140, 142)],
    )
    result, _ = await fuse(
        [earlier, next_action()],
        [
            {
                "member_ids": ["visual-1"],
                "name": "杠铃提拉",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-1"],
            },
            {
                "member_ids": ["visual-2"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-1"],
            },
        ],
    )
    assert len(result.candidates) == 2
    assert result.candidates[0].tips == earlier.tips
    assert result.candidates[1].tips == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["different_number", "later_execution"])
async def test_tip_selection_cannot_override_action_identity_guards(rejection: str) -> None:
    other = next_action().model_copy(deep=True)
    if rejection == "different_number":
        other.sequence_label = "第三个动作"
    else:
        other.start_seconds = 210
        other.end_seconds = 250
        other.tips = [tip("双手朝两侧打开", 220, 222)]
    result, _ = await fuse(
        [next_action(), other],
        [
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-2"],
            }
        ],
    )
    assert len(result.candidates) == 2
    assert all(item.needs_confirmation and not item.tips for item in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["preview", "recap"])
async def test_reference_only_group_cannot_supply_reminders(role: str) -> None:
    start = 0 if role == "preview" else 210
    reference = VisualSegment(
        action_name="反向飞鸟",
        start_seconds=start,
        end_seconds=start + 10,
        visual_cue="前后呼应的教学画面",
        segment_role="teaching_demo",
        sequence_label="4",
        tips=[tip("回顾两侧打开", start + 2, start + 4)],
    )
    result, _ = await fuse(
        [reference, next_action()],
        [
            {
                "member_ids": ["visual-1"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "content_role": role,
                "related_member_id": "visual-2",
                "accepted_tip_ids": ["visual-1-tip-1"],
            },
            {
                "member_ids": ["visual-2"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-2-tip-2"],
            },
        ],
    )
    assert len(result.candidates) == 1
    assert [item.text for item in result.candidates[0].tips] == ["双手朝两侧打开"]
    assert len(result.candidates[0].evidence) == 2


@pytest.mark.asyncio
async def test_parameter_conflict_retains_both_values_and_reviewed_tip() -> None:
    first, second = next_action(), next_action()
    first.text_parameters = CandidateParameters(mode="reps", reps=8)
    second.text_parameters = CandidateParameters(mode="reps", reps=10)
    result, _ = await fuse(
        [first, second],
        [
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": ["visual-2-tip-2"],
            }
        ],
    )
    candidate = result.candidates[0]
    assert len(result.candidates) == 1
    assert candidate.needs_confirmation
    assert {alt.parameters.reps for alt in candidate.parameter_conflicts[0].alternatives} == {8, 10}
    assert candidate.tips == [second.tips[1]]


@pytest.mark.asyncio
async def test_input_budget_failure_retains_candidates_without_unreviewed_tips() -> None:
    result, requests = await fuse([next_action() for _ in range(201)], [])
    assert len(result.candidates) == 201
    assert not requests
    assert all(not candidate.tips for candidate in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        ["visual-1-tip-1", "visual-1-tip-2", "visual-2-tip-1", "visual-2-tip-2"],
        None,
        "visual-1-tip-2",
        [123],
    ],
)
async def test_malformed_tip_selection_does_not_undo_valid_action_fusion(selection: Any) -> None:
    result, _ = await fuse(
        [next_action(), next_action()],
        [
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "反向飞鸟",
                "relation": "same_demonstration",
                "accepted_tip_ids": selection,
            }
        ],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].tips == []
    assert len(result.candidates[0].evidence) == 2
    assert result.warnings == []
