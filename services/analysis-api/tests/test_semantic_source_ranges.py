import json

import httpx
import pytest

from hakimi_analysis.models import CandidateParameters, SegmentRole, SpeechSignal, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


async def fuse_one_demonstration(
    visual: list[VisualSegment], speech: list[SpeechSignal] | None = None
) -> SemanticFusionResult:
    speech = speech or []
    member_ids = [f"speech-{index}" for index in range(1, len(speech) + 1)] + [
        f"visual-{index}" for index in range(1, len(visual) + 1)
    ]
    response = {
        "groups": [
            {"member_ids": member_ids, "name": "器械反向飞鸟", "relation": "same_demonstration"}
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps(response)})
        )
    ) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group same demonstrations; preserve later executions.",
        )
        return await fusion.run(
            source_id="synthetic-source", speech_signals=speech, visual_segments=visual
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_complete_reference_uses_existing_covering_visual_span_not_first_short_chunk(
    reverse: bool,
) -> None:
    visual = [
        VisualSegment(
            action_name="器械反向飞鸟",
            start_seconds=40,
            end_seconds=48,
            visual_cue="同次教学开头",
            segment_role=SegmentRole.TEACHING_DEMO,
        ),
        VisualSegment(
            action_name="器械反向飞鸟",
            start_seconds=39,
            end_seconds=82,
            visual_cue="同次教学完整画面",
            segment_role=SegmentRole.TEACHING_DEMO,
        ),
    ]
    if reverse:
        visual.reverse()
    result = await fuse_one_demonstration(visual)

    assert len(result.candidates) == 1
    item = result.candidates[0]
    assert (item.segment.start_seconds, item.segment.end_seconds) == (39, 82)
    assert sorted((span.start_seconds, span.end_seconds) for span in item.evidence) == [
        (39, 82),
        (40, 48),
    ]
    assert sorted((span.start_seconds, span.end_seconds) for span in item.playback_options) == [
        (39, 82),
        (40, 48),
    ]


@pytest.mark.asyncio
async def test_reference_selection_preserves_conflicting_values_and_each_original_source() -> None:
    result = await fuse_one_demonstration(
        [
            VisualSegment(
                action_name="器械反向飞鸟",
                start_seconds=40,
                end_seconds=48,
                visual_cue="较短教学",
                text_parameters=CandidateParameters(mode="reps", reps=8),
            ),
            VisualSegment(
                action_name="器械反向飞鸟",
                start_seconds=39,
                end_seconds=82,
                visual_cue="完整教学",
                text_parameters=CandidateParameters(mode="reps", reps=10),
            ),
        ]
    )
    assert len(result.candidates) == 1
    item = result.candidates[0]
    assert (item.segment.start_seconds, item.segment.end_seconds) == (39, 82)
    assert item.needs_confirmation is True
    assert item.parameters.reps is None
    assert [
        (alt.parameters.reps, alt.evidence[0].start_seconds, alt.evidence[0].end_seconds)
        for alt in item.parameter_conflicts[0].alternatives
    ] == [(8, 40, 48), (10, 39, 82)]


@pytest.mark.asyncio
async def test_reference_stays_an_existing_visual_range_not_a_hull_or_broad_narration() -> None:
    result = await fuse_one_demonstration(
        [
            VisualSegment(
                action_name="器械反向飞鸟",
                start_seconds=30,
                end_seconds=60,
                visual_cue="后半段同次教学",
            ),
            VisualSegment(
                action_name="器械反向飞鸟",
                start_seconds=0,
                end_seconds=40,
                visual_cue="前半段同次教学",
            ),
        ],
        [
            SpeechSignal(
                action_name="器械反向飞鸟",
                start_seconds=0,
                end_seconds=90,
                evidence_text="synthetic narration",
            )
        ],
    )
    assert len(result.candidates) == 1
    assert (
        result.candidates[0].segment.start_seconds,
        result.candidates[0].segment.end_seconds,
    ) == (0, 40)


@pytest.mark.asyncio
async def test_equal_duration_reference_choice_does_not_depend_on_model_member_order() -> None:
    result = await fuse_one_demonstration(
        [
            VisualSegment(
                action_name="器械反向飞鸟",
                start_seconds=30,
                end_seconds=60,
                visual_cue="后半段同次教学",
            ),
            VisualSegment(
                action_name="器械反向飞鸟",
                start_seconds=10,
                end_seconds=40,
                visual_cue="前半段同次教学",
            ),
        ]
    )
    assert (
        result.candidates[0].segment.start_seconds,
        result.candidates[0].segment.end_seconds,
    ) == (10, 40)
