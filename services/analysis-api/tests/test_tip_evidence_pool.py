"""Complete reminder evidence survives organizer and grouping HTTP boundaries."""

import json

import httpx
import pytest

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.visual_evidence import VisualCaptionTimeline
from hakimi_analysis.visual_source_context import VisualSourceContext


@pytest.mark.asyncio
@pytest.mark.parametrize("two_stage", [False, True])
@pytest.mark.parametrize(
    ("count", "selected_ids", "expected"),
    [
        (
            3,
            ["visual-1-tip-1", "visual-1-tip-2", "visual-1-tip-3"],
            [
                ("合成深蹲先站稳", "visual", 0, 1),
                ("合成深蹲缓慢下蹲", "visual", 1, 2),
                ("合成深蹲保持呼吸", "visual", 2, 3),
            ],
        ),
        (
            4,
            ["visual-1-tip-1", "visual-1-tip-2", "visual-1-tip-3", "visual-1-tip-4"],
            [
                ("合成深蹲先站稳", "visual", 0, 1),
                ("合成深蹲缓慢下蹲", "visual", 1, 2),
                ("合成深蹲保持呼吸", "visual", 2, 3),
                ("合成深蹲不要突然加速", "visual", 3, 4),
            ],
        ),
        (
            5,
            [
                "visual-1-tip-1",
                "visual-1-tip-2",
                "visual-1-tip-3",
                "visual-1-tip-4",
                "visual-1-tip-5",
            ],
            [
                ("合成深蹲先站稳", "visual", 0, 1),
                ("合成深蹲缓慢下蹲", "visual", 1, 2),
                ("合成深蹲保持呼吸", "visual", 2, 3),
                ("合成深蹲不要突然加速", "visual", 3, 4),
                ("合成深蹲双脚保持稳定", "visual", 4, 5),
            ],
        ),
        (
            6,
            [
                "visual-1-tip-2",
                "visual-1-tip-3",
                "visual-1-tip-4",
                "visual-1-tip-5",
                "visual-1-tip-6",
            ],
            [
                ("合成深蹲缓慢下蹲", "visual", 1, 2),
                ("合成深蹲保持呼吸", "visual", 2, 3),
                ("合成深蹲不要突然加速", "visual", 3, 4),
                ("合成深蹲双脚保持稳定", "visual", 4, 5),
                ("合成深蹲起身保持节奏", "visual", 5, 6),
            ],
        ),
        (
            6,
            [
                "visual-1-tip-1",
                "visual-1-tip-2",
                "visual-1-tip-3",
                "visual-1-tip-4",
                "visual-1-tip-5",
                "visual-1-tip-6",
            ],
            [],
        ),
    ],
)
async def test_complete_tip_pool_is_reviewed_before_bounded_selection(
    count: int,
    selected_ids: list[str],
    expected: list[tuple[str, str, int, int]],
    two_stage: bool,
) -> None:
    readings = VisualCaptionTimeline.model_validate(
        {
            "observations": [
                {
                    "action_name": "合成深蹲",
                    "visual_cue": "合成动作教学",
                    "start_seconds": 0,
                    "end_seconds": 10,
                    "segment_role": "teaching_demo",
                    "sequence_label": "动作一",
                    "is_training_content": True,
                }
            ],
            "captions": [
                {"text": "合成深蹲先站稳", "start_seconds": 0, "end_seconds": 1},
                {"text": "合成深蹲缓慢下蹲", "start_seconds": 1, "end_seconds": 2},
                {"text": "合成深蹲保持呼吸", "start_seconds": 2, "end_seconds": 3},
                {"text": "合成深蹲不要突然加速", "start_seconds": 3, "end_seconds": 4},
                {"text": "合成深蹲双脚保持稳定", "start_seconds": 4, "end_seconds": 5},
                {"text": "合成深蹲起身保持节奏", "start_seconds": 5, "end_seconds": 6},
            ],
        }
    )
    readings.captions = readings.captions[:count]
    reviewed: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] == "visual_evidence_organization":
            output = {
                "binding_id": body["binding_id"],
                "decisions": [
                    {
                        "caption_id": c["id"],
                        "purpose": "tip",
                        "action_id": body["observations"][0]["id"],
                        "ownership": "explicit_name",
                        "support_ids": [c["id"]],
                        "category": "rhythm",
                    }
                    for c in body["captions"]
                ],
            }
        elif payload["text"]["format"]["name"] == "locked_action_tip_selection":
            reviewed.extend(body["observations"][0]["tip_evidence"])
            target = body["locked_groups"][0]
            output = {
                "selections": [
                    {
                        "group_id": target["group_id"],
                        "accepted_tip_ids": [
                            tip_id
                            for tip_id in target["allowed_tip_ids"]
                            if tip_id.rsplit(":", 1)[-1] in selected_ids
                        ],
                    }
                ]
            }
        else:
            assert payload["text"]["format"]["name"] == "semantic_action_grouping"
            if two_stage:
                assert body["observations"][0]["tip_evidence"] == []
            else:
                reviewed.extend(body["observations"][0]["tip_evidence"])
            output = {
                "groups": [
                    {
                        "member_ids": ["visual-1"],
                        "name": "合成深蹲",
                        "relation": "same_demonstration",
                        "content_role": "exercise",
                        "related_member_id": None,
                        "accepted_tip_ids": [] if two_stage else selected_ids,
                    }
                ]
            }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(output),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
        )
        organized = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v1", readings=readings, utterances=[]
        )
        result = await SemanticCandidateFusion(
            model=provider,
            instructions="synthetic",
            include_visual_context=True,
            tip_selection_model=provider if two_stage else None,
        ).run(
            source_id="s",
            speech_signals=[],
            visual_segments=organized.to_visual_segments(),
            visual_context=VisualSourceContext.bind(source_id="s", evidence=organized),
            visual_input_binding=organized.binding_id,
        )

    assert [t["text"] for t in reviewed] == [
        "合成深蹲先站稳",
        "合成深蹲缓慢下蹲",
        "合成深蹲保持呼吸",
        "合成深蹲不要突然加速",
        "合成深蹲双脚保持稳定",
        "合成深蹲起身保持节奏",
    ][:count]
    if expected:
        assert not result.warnings
    assert len(result.candidates) == 1
    assert "tip_evidence" not in result.candidates[0].model_dump()
    assert "caption_context" not in result.candidates[0].model_dump()
    assert [
        (t.text, t.evidence.type.value, t.evidence.start_seconds, t.evidence.end_seconds)
        for t in result.candidates[0].tips
    ] == expected
