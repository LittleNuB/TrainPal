"""Visible full headings versus bare ordinals at the Provider HTTP fusion seam."""

import json

import httpx
import pytest

from hakimi_analysis.models import CandidateParameters, SourceTip, SpeechSignal, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


@pytest.mark.asyncio
async def test_matching_visible_heading_does_not_split_action_or_drop_reminder() -> None:
    name = "大剪刀下拉"
    visuals = [
        VisualSegment(
            action_name=name,
            start_seconds=start,
            end_seconds=end,
            visual_cue="合成教学证据",
            segment_role="teaching_demo",
            sequence_label=label,
            text_parameters=CandidateParameters(mode="reps", sets=4, reps=8, reps_max=12),
        )
        for start, end, label in ((47.2, 57.4, "动作四"), (50, 58, "动作四：大剪刀下拉"))
    ]
    tip = SourceTip(
        text="双手保持稳定",
        category="setup",
        evidence={"type": "speech", "start_seconds": 51, "end_seconds": 52},
    )
    speech = [
        SpeechSignal(
            action_name=name,
            start_seconds=47.49,
            end_seconds=55.77,
            evidence_text="合成语音",
            segment_role="teaching_demo",
            tips=[tip],
        )
    ]
    original = [v.model_dump_json() for v in visuals]

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        assert [o["sequence_label"] for o in body["observations"]] == [
            None,
            "动作四",
            "动作四：大剪刀下拉",
        ]
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": ["speech-1", "visual-1", "visual-2"],
                                "name": name,
                                "relation": "same_demonstration",
                                "accepted_tip_ids": ["speech-1-tip-1"],
                            }
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        ark = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            retry_delays=(),
        )
        result = await SemanticCandidateFusion(
            model=ark, instructions="synthetic", allow_titled_sequence=True
        ).run(source_id="synthetic", speech_signals=speech, visual_segments=visuals)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.tips == [tip]
    assert candidate.parameters == CandidateParameters(mode="reps", sets=4, reps=8, reps_max=12)
    assert not candidate.needs_confirmation
    assert [(e.start_seconds, e.end_seconds) for e in candidate.evidence] == [
        (47.49, 55.77),
        (47.2, 57.4),
        (50, 58),
    ]
    assert (candidate.segment.start_seconds, candidate.segment.end_seconds) == (47.2, 57.4)
    assert [v.model_dump_json() for v in visuals] == original


async def fuse_headings(
    *,
    left: str = "动作四",
    right: str = "动作四：大剪刀下拉",
    names: tuple[str | None, str | None] = ("大剪刀下拉", "大剪刀下拉"),
    enabled: bool = True,
    uncertain: bool = False,
    later: bool = False,
    conflict: bool = False,
    preview: bool = False,
) -> SemanticFusionResult:
    visuals = [
        VisualSegment(
            action_name=name,
            start_seconds=start,
            end_seconds=end,
            visual_cue="合成教学",
            sequence_label=label,
            segment_role="teaching_demo",
            text_parameters=CandidateParameters(sets=4 if index == 0 or not conflict else 5),
        )
        for index, (name, label, start, end) in enumerate(
            [
                (names[0], left, 0, 5),
                (names[1], right, 10 if later or preview else 1, 15 if later or preview else 4),
            ]
        )
    ]
    original = [v.model_dump_json() for v in visuals]

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        assert [o["sequence_label"] for o in body["observations"]] == [left, right]
        groups = [
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "大剪刀下拉",
                "relation": "uncertain" if uncertain else "same_demonstration",
            }
        ]
        if preview:
            groups = [
                {
                    "member_ids": ["visual-1"],
                    "name": "大剪刀下拉",
                    "content_role": "preview",
                    "related_member_id": "visual-2",
                    "relation": "same_demonstration",
                },
                {
                    "member_ids": ["visual-2"],
                    "name": "大剪刀下拉",
                    "relation": "same_demonstration",
                },
            ]
        return httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        ark = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            retry_delays=(),
        )
        result = await SemanticCandidateFusion(
            model=ark, instructions="synthetic", allow_titled_sequence=enabled
        ).run(source_id="synthetic", speech_signals=[], visual_segments=visuals)
    assert [v.model_dump_json() for v in visuals] == original
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("right", ["动作4:大剪刀下拉", " 第四个动作 ： 大剪刀下拉 "])
async def test_complete_ordinal_heading_syntax_is_compatible(right: str) -> None:
    result = await fuse_headings(right=right)
    assert len(result.candidates) == 1
    assert result.candidates[0].needs_confirmation  # Visual-only never becomes confirmed.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "right",
    [
        "动作三：大剪刀下拉",
        "动作四 左侧：大剪刀下拉",
        "动作四 第2轮：大剪刀下拉",
        "4组：大剪刀下拉",
        "动作四：大剪刀下拉 左侧",
        "动作四：大剪刀下拉 第2轮",
        "动作四：窄握大剪刀下拉",
        "动作四大剪刀下拉",
        "动作四：",
    ],
)
async def test_heading_does_not_erase_number_side_round_or_unknown_modifier(right: str) -> None:
    result = await fuse_headings(right=right)
    assert len(result.candidates) == 2
    assert result.diagnostics["sequence_label_conflict"] == 1
    assert all(c.needs_confirmation for c in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names",
    [
        ("大剪刀下拉", "窄握大剪刀下拉"),
        ("左侧大剪刀下拉", "右侧大剪刀下拉"),
        (None, "大剪刀下拉"),
        ("大剪刀下拉", "大剪刀下拉 第2轮"),
    ],
)
async def test_heading_requires_all_visual_names_to_agree(
    names: tuple[str | None, str | None],
) -> None:
    result = await fuse_headings(names=names)
    assert len(result.candidates) == 2
    assert result.diagnostics["sequence_label_conflict"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize(
    ("names", "right"),
    [
        ((None, None), "动作四：待确认动作"),
        (("squat", "squat"), "动作四：深蹲"),
        (("pushup", "俯卧撑"), "动作四：俯卧撑"),
    ],
)
async def test_heading_cannot_use_placeholder_or_translated_display_name(
    names: tuple[str | None, str | None], right: str, preview: bool
) -> None:
    result = await fuse_headings(names=names, right=right, preview=preview)
    assert len(result.candidates) == 2
    assert (
        result.diagnostics["reference_label_conflict" if preview else "sequence_label_conflict"]
        == 1
    )


@pytest.mark.asyncio
async def test_heading_compatibility_is_off_by_default() -> None:
    result = await fuse_headings(enabled=False)
    assert len(result.candidates) == 2
    assert result.diagnostics["sequence_label_conflict"] == 1


@pytest.mark.asyncio
async def test_matching_heading_does_not_override_model_uncertainty_or_later_execution() -> None:
    uncertain = await fuse_headings(uncertain=True)
    later = await fuse_headings(later=True)
    assert len(uncertain.candidates) == len(later.candidates) == 2
    assert uncertain.diagnostics["model_uncertain"] == 1
    assert later.diagnostics["time_alignment_rejected"] == 1


@pytest.mark.asyncio
async def test_heading_keeps_parameter_conflict_and_all_values() -> None:
    result = await fuse_headings(conflict=True)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.needs_confirmation
    assert [a.parameters.sets for a in candidate.parameter_conflicts[0].alternatives] == [4, 5]


@pytest.mark.asyncio
async def test_reference_attachment_uses_same_heading_guard_without_widening_playback() -> None:
    result = await fuse_headings(preview=True)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.needs_confirmation
    assert (candidate.segment.start_seconds, candidate.segment.end_seconds) == (10, 15)
    assert len(candidate.evidence) == 2
    rejected = await fuse_headings(preview=True, names=("左侧大剪刀下拉", "大剪刀下拉"))
    assert len(rejected.candidates) == 2
    assert rejected.diagnostics["reference_label_conflict"] == 1
