import json

import httpx
import pytest

from hakimi_analysis.models import CandidateParameters, SegmentRole, SpeechSignal, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


def teaching(start: float, end: float, label: str = "1") -> VisualSegment:
    return VisualSegment(
        action_name="器械反向飞鸟",
        start_seconds=start,
        end_seconds=end,
        sequence_label=label,
        segment_role=SegmentRole.TEACHING_DEMO,
        visual_cue="合成同次教学分块",
    )


async def fuse(
    visual: list[VisualSegment],
    speech: list[SpeechSignal] | None = None,
    groups: list[dict[str, object]] | None = None,
) -> SemanticFusionResult:
    speech = speech or []
    if groups is None:
        groups = [
            {
                "member_ids": [f"speech-{i}" for i in range(1, len(speech) + 1)]
                + [f"visual-{i}" for i in range(1, len(visual) + 1)],
                "name": "器械反向飞鸟",
                "relation": "same_demonstration",
            }
        ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})
        )
    ) as client:
        return await SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group supplied evidence; preserve distinct executions.",
        ).run(source_id="synthetic-chunks", speech_signals=speech, visual_segments=visual)


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_continuous_teaching_merges_without_expanding_source_range(reverse: bool) -> None:
    visual = [teaching(0, 40), teaching(30, 60), teaching(50, 75)]
    if reverse:
        visual.reverse()
    result = await fuse(visual)

    assert len(result.candidates) == 1
    item = result.candidates[0]
    assert (item.segment.start_seconds, item.segment.end_seconds) == (0, 40)
    assert sorted((e.start_seconds, e.end_seconds) for e in item.evidence) == [
        (0, 40),
        (30, 60),
        (50, 75),
    ]
    assert sorted((p.start_seconds, p.end_seconds) for p in item.playback_options) == [
        (0, 40),
        (30, 60),
        (50, 75),
    ]
    assert item.needs_confirmation is True  # Visual-only evidence stays unconfirmed.
    assert result.warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [SegmentRole.UNKNOWN, SegmentRole.FOLLOW_ALONG])
async def test_overlap_chain_cannot_collapse_unknown_or_follow_along_execution(
    role: SegmentRole,
) -> None:
    visual = [teaching(0, 40), teaching(30, 60), teaching(50, 75)]
    visual[1].segment_role = role
    result = await fuse(visual)

    assert len(result.candidates) == 3
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unrelated", "touching", "follow_along"])
async def test_teaching_chain_does_not_absorb_unrelated_or_follow_along_speech(case: str) -> None:
    speech = SpeechSignal(
        action_name="器械反向飞鸟",
        start_seconds=80,
        end_seconds=90,
        evidence_text="synthetic narration",
        reps=12,
    )
    if case == "touching":
        speech.start_seconds = 75
    elif case == "follow_along":
        speech.start_seconds, speech.end_seconds = 10, 20
        speech.segment_role = SegmentRole.FOLLOW_ALONG
    result = await fuse([teaching(0, 40), teaching(30, 60), teaching(50, 75)], [speech])

    assert len(result.candidates) == 4
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
async def test_continuous_chain_cannot_cross_another_visual_action_boundary() -> None:
    visual = [teaching(0, 40), teaching(30, 60), teaching(50, 75), teaching(42, 48, "2")]
    visual[-1].action_name = "坐姿推胸"
    result = await fuse(
        visual,
        groups=[
            {
                "member_ids": ["visual-1", "visual-2", "visual-3"],
                "name": "器械反向飞鸟",
                "relation": "same_demonstration",
            },
            {"member_ids": ["visual-4"], "name": "坐姿推胸", "relation": "same_demonstration"},
        ],
    )

    assert len(result.candidates) == 4
    assert sorted(
        (item.segment.start_seconds, item.segment.end_seconds) for item in result.candidates
    ) == [(0, 40), (30, 60), (42, 48), (50, 75)]
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("second_start", [40, 40.01, 50])
@pytest.mark.parametrize("narration", [False, True])
async def test_gap_or_touching_visuals_cannot_be_joined_even_by_broad_narration(
    second_start: float,
    narration: bool,
) -> None:
    speech = (
        [
            SpeechSignal(
                action_name="器械反向飞鸟",
                start_seconds=0,
                end_seconds=90,
                evidence_text="synthetic broad narration",
            )
        ]
        if narration
        else []
    )
    result = await fuse([teaching(0, 40), teaching(second_start, 75)], speech)

    assert len(result.candidates) == (3 if narration else 2)
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["2", "1左侧", "1第二轮"])
async def test_continuous_teaching_keeps_number_side_and_round_boundaries(label: str) -> None:
    result = await fuse([teaching(0, 40), teaching(30, 60), teaching(50, 75, label)])
    assert len(result.candidates) == 3
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
async def test_four_distinct_numbered_actions_are_not_collapsed_by_a_continuous_chain() -> None:
    result = await fuse(
        [
            teaching(0, 20, "1"),
            teaching(10, 30, "2"),
            teaching(20, 40, "3"),
            teaching(30, 50, "4"),
        ]
    )
    assert len(result.candidates) == 4
    assert [
        (item.segment.start_seconds, item.segment.end_seconds) for item in result.candidates
    ] == [(0, 20), (10, 30), (20, 40), (30, 50)]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["uncertain_relation", "uncertain_role", "distinct_variants"])
async def test_connectivity_does_not_override_model_uncertainty_or_distinct_variants(
    decision: str,
) -> None:
    visual = [teaching(0, 40), teaching(30, 60), teaching(50, 75)]
    groups: list[dict[str, object]] = [
        {
            "member_ids": ["visual-1", "visual-2", "visual-3"],
            "name": "器械反向飞鸟",
            "relation": "same_demonstration",
        }
    ]
    if decision == "uncertain_relation":
        groups[0]["relation"] = "uncertain"
    elif decision == "uncertain_role":
        groups[0]["content_role"] = "uncertain"
    else:
        groups = [
            {"member_ids": [f"visual-{i}"], "name": name, "relation": "same_demonstration"}
            for i, name in enumerate(["正握反向飞鸟", "中立握反向飞鸟", "单臂反向飞鸟"], 1)
        ]
    result = await fuse(visual, groups=groups)
    assert len(result.candidates) == 3
    if decision == "distinct_variants":
        assert [item.name for item in result.candidates] == [
            "正握反向飞鸟",
            "中立握反向飞鸟",
            "单臂反向飞鸟",
        ]
    else:
        assert all(item.needs_confirmation for item in result.candidates)
        assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
async def test_connected_teaching_handles_nested_ranges_and_preserves_parameter_sources() -> None:
    visual = [teaching(0, 40), teaching(5, 10), teaching(30, 60), teaching(50, 75, "动作一")]
    visual[0].text_parameters = CandidateParameters(mode="reps", reps=8)
    visual[-1].text_parameters = CandidateParameters(mode="reps", reps=12)
    speech = [
        SpeechSignal(
            action_name="器械反向飞鸟",
            start_seconds=55,
            end_seconds=65,
            evidence_text="synthetic reps",
            reps=12,
            segment_role=SegmentRole.TEACHING_DEMO,
        )
    ]
    result = await fuse(visual, speech)

    assert len(result.candidates) == 1
    item = result.candidates[0]
    assert item.needs_confirmation is True
    assert item.parameters.reps is None
    assert (item.segment.start_seconds, item.segment.end_seconds) == (0, 40)
    assert sorted((e.start_seconds, e.end_seconds) for e in item.evidence) == [
        (0, 40),
        (5, 10),
        (30, 60),
        (50, 75),
        (55, 65),
    ]
    conflict = item.parameter_conflicts[0]
    assert conflict.field == "reps"
    assert sorted(
        (alt.parameters.reps, sorted((e.start_seconds, e.end_seconds) for e in alt.evidence))
        for alt in conflict.alternatives
    ) == [(8, [(0, 40)]), (12, [(50, 75), (55, 65)])]


@pytest.mark.asyncio
@pytest.mark.parametrize("outside", ["before", "after", "unknown_inside", "speech_inside"])
async def test_only_overlapping_outside_visual_evidence_blocks_the_teaching_exception(
    outside: str,
) -> None:
    visual = [teaching(10, 40), teaching(30, 60), teaching(50, 75)]
    speech: list[SpeechSignal] = []
    if outside == "speech_inside":
        speech.append(
            SpeechSignal(
                action_name="坐姿推胸",
                start_seconds=42,
                end_seconds=48,
                evidence_text="synthetic separate narration",
            )
        )
        other_id = "speech-1"
    else:
        start, end = {"before": (0, 10), "after": (75, 90), "unknown_inside": (42, 48)}[outside]
        extra = teaching(start, end, "2")
        extra.segment_role = SegmentRole.UNKNOWN
        visual.append(extra)
        other_id = "visual-4"
    result = await fuse(
        visual,
        speech,
        groups=[
            {
                "member_ids": ["visual-1", "visual-2", "visual-3"],
                "name": "器械反向飞鸟",
                "relation": "same_demonstration",
            },
            {"member_ids": [other_id], "name": None, "relation": "uncertain"},
        ],
    )
    assert len(result.candidates) == (4 if outside == "unknown_inside" else 2)


@pytest.mark.asyncio
async def test_speech_only_chain_still_requires_a_common_time_intersection() -> None:
    speech = [
        SpeechSignal(
            action_name="器械反向飞鸟",
            start_seconds=start,
            end_seconds=end,
            evidence_text="synthetic narration",
            segment_role=SegmentRole.TEACHING_DEMO,
        )
        for start, end in [(0, 40), (30, 60), (50, 75)]
    ]
    result = await fuse([], speech)
    assert len(result.candidates) == 3
    assert all(item.needs_confirmation for item in result.candidates)


@pytest.mark.asyncio
async def test_single_visual_cannot_bridge_nonoverlapping_speech_observations() -> None:
    speech = [
        SpeechSignal(
            action_name="器械反向飞鸟",
            start_seconds=start,
            end_seconds=end,
            evidence_text="synthetic narration",
        )
        for start, end in [(0, 10), (60, 75)]
    ]
    result = await fuse([teaching(0, 75)], speech)
    assert len(result.candidates) == 3
