"""Source context through organizer/fusion HTTP; it never becomes membership."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_orchestrated_pipeline import FakeAsr, FakeMediaProcessor, no_op_emit

from hakimi_analysis.models import TranscriptUtterance
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.shared_source_context import SpeechSourceContext
from hakimi_analysis.sources import VideoSource
from hakimi_analysis.visual_evidence import VisualCaptionTimeline


def timeline() -> VisualCaptionTimeline:
    return VisualCaptionTimeline.model_validate(
        {
            "observations": [
                {
                    "action_name": "合成推举",
                    "visual_cue": "合成示范",
                    "start_seconds": 0,
                    "end_seconds": 2,
                    "segment_role": "teaching_demo",
                    "sequence_label": "动作一",
                    "is_training_content": True,
                },
                {
                    "action_name": None,
                    "visual_cue": "合成器械准备，不是训练动作",
                    "start_seconds": 2,
                    "end_seconds": 4,
                    "segment_role": "unknown",
                    "sequence_label": "动作一",
                    "is_training_content": False,
                },
                {
                    "action_name": "合成推举",
                    "visual_cue": "合成示范",
                    "start_seconds": 4,
                    "end_seconds": 6,
                    "segment_role": "teaching_demo",
                    "sequence_label": "动作一",
                    "is_training_content": True,
                },
            ],
            "captions": [{"text": "如果握把松动，不要继续", "start_seconds": 2, "end_seconds": 4}],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_visual_setup_and_unassigned_caption_reach_grouping_without_bridging_gap(
    contextual: bool,
    enabled: bool,
) -> None:
    # Bind exists only inside the isolated caller; importing it here keeps the original
    # public-seam diagnostic runnable on the old implementation.
    from hakimi_analysis.visual_source_context import VisualSourceContext

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] == "visual_evidence_organization":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": payload["text"],
                    "output_text": json.dumps(
                        {
                            "binding_id": body["binding_id"],
                            "decisions": [
                                {"caption_id": body["captions"][0]["id"], "purpose": "context"}
                            ],
                        }
                    ),
                },
            )
        assert len(body["observations"]) == 2
        if enabled:
            visual_context = body["visual_source_context"]
            assert visual_context["context_only"] is True
            assert (
                visual_context["non_training_observations"][0]["visual_cue"]
                == "合成器械准备，不是训练动作"
            )
            assert visual_context["unassigned_captions"][0]["caption"] == {
                "text": "如果握把松动，不要继续",
                "start_seconds": 2.0,
                "end_seconds": 4.0,
            }
            assert visual_context["unassigned_captions"][0]["purpose"] == "context"
        else:
            assert "visual_source_context" not in body
        assert "如果握把松动" not in json.dumps(body["observations"], ensure_ascii=False)
        result: dict[str, Any] = {
            "groups": [
                {
                    "member_ids": [o["id"] for o in body["observations"]],
                    "name": "合成推举",
                    "relation": "same_demonstration",
                    "content_role": "exercise",
                    "related_member_id": None,
                    "accepted_tip_ids": [],
                }
            ]
        }
        if contextual:
            assert body["source_context"] == {"utterances": []}
            result["shared_sources"] = []
        return httpx.Response(200, json={"output_text": json.dumps(result)})

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
            run_id="r", source_id="s", input_version="v1", readings=timeline(), utterances=[]
        )
        visual = organized.to_visual_segments()
        context = VisualSourceContext.bind(source_id="s", evidence=organized)
        speech = (
            SpeechSourceContext.bind(
                source_id="s", speech_signals=[], visual_segments=visual, utterances=[]
            )
            if contextual
            else None
        )
        result = await SemanticCandidateFusion(
            model=provider,
            instructions="synthetic",
            source_context_model=provider if contextual else None,
            include_visual_context=enabled,
            allow_touching_teaching=True,
        ).run(
            source_id="s",
            speech_signals=[],
            visual_segments=visual,
            speech_context=speech,
            visual_context=context,
            visual_input_binding=organized.binding_id,
        )
    assert [(c.segment.start_seconds, c.segment.end_seconds) for c in result.candidates] == [
        (0, 2),
        (4, 6),
    ]
    assert result.diagnostics["visual_chain_disconnected"] == 1
    assert all(c.needs_confirmation and not c.tips for c in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["source", "binding", "visual", "missing"])
async def test_unavailable_or_stale_visual_context_never_reaches_grouping(invalid: str) -> None:
    from hakimi_analysis.visual_source_context import VisualSourceContext

    grouping_payloads: list[object] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] != "visual_evidence_organization":
            grouping_payloads.append(body)
            raise AssertionError("stale context must fail before grouping HTTP")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(
                    {
                        "binding_id": body["binding_id"],
                        "decisions": [
                            {"caption_id": c["id"], "purpose": "context"} for c in body["captions"]
                        ],
                    }
                ),
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
            run_id="r", source_id="s", input_version="v1", readings=timeline(), utterances=[]
        )
        context = VisualSourceContext.bind(source_id="s", evidence=organized)
        visual = organized.to_visual_segments()
        if invalid == "visual":
            visual[0].start_seconds = 1
        result = await SemanticCandidateFusion(
            model=provider, instructions="synthetic", include_visual_context=True
        ).run(
            source_id="other" if invalid == "source" else "s",
            speech_signals=[],
            visual_segments=visual,
            visual_context=None if invalid == "missing" else context,
            visual_input_binding="stale" if invalid == "binding" else organized.binding_id,
        )
    assert not grouping_payloads
    assert result.diagnostics == {"unavailable_source_context": 1}
    assert len(result.candidates) == 2
    assert all(c.needs_confirmation and not c.tips for c in result.candidates)


@pytest.mark.asyncio
async def test_visual_and_speech_context_share_original_combined_input_budget() -> None:
    from hakimi_analysis.visual_source_context import VisualSourceContext

    grouping_payloads: list[object] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] != "visual_evidence_organization":
            grouping_payloads.append(body)
            raise AssertionError("over-budget context must fail before grouping HTTP")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(
                    {
                        "binding_id": body["binding_id"],
                        "decisions": [
                            {"caption_id": c["id"], "purpose": "context"} for c in body["captions"]
                        ],
                    }
                ),
            },
        )

    readings = timeline()
    readings.captions = [
        readings.captions[0].model_copy(update={"text": "x" * 500}) for _ in range(50)
    ]
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
        visual = organized.to_visual_segments()
        speech = SpeechSourceContext.bind(
            source_id="s",
            speech_signals=[],
            visual_segments=visual,
            utterances=[
                TranscriptUtterance(text="y" * 1000, start_seconds=2, end_seconds=4)
                for _ in range(40)
            ],
        )
        result = await SemanticCandidateFusion(
            model=provider,
            instructions="synthetic",
            include_visual_context=True,
            source_context_model=provider,
        ).run(
            source_id="s",
            speech_signals=[],
            visual_segments=visual,
            speech_context=speech,
            visual_context=VisualSourceContext.bind(source_id="s", evidence=organized),
            visual_input_binding=organized.binding_id,
        )
    assert not grouping_payloads
    assert result.diagnostics == {"unavailable_input_budget": 1}
    assert all(c.needs_confirmation and not c.tips for c in result.candidates)


@pytest.mark.asyncio
async def test_full_evidence_pipeline_passes_visual_context_with_absolute_times_once(
    tmp_path: Path,
) -> None:
    for index in (1, 2):
        (tmp_path / f"chunk-{index}.mp4").write_bytes(b"synthetic")
    grouping_payloads: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        kind = payload["text"]["format"]["name"]
        if kind == "training_speech_understanding":
            return httpx.Response(200, json={"output_text": '{"signals":[]}'})
        if kind == "visual_caption_timeline":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": payload["text"],
                    "output_text": timeline().model_dump_json(),
                },
            )
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if kind == "visual_evidence_organization":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": payload["text"],
                    "output_text": json.dumps(
                        {
                            "binding_id": body["binding_id"],
                            "decisions": [
                                {"caption_id": c["id"], "purpose": "context"}
                                for c in body["captions"]
                            ],
                        }
                    ),
                },
            )
        assert kind == "semantic_action_grouping"
        grouping_payloads.append(body)
        context = body["visual_source_context"]
        assert [
            (o["start_seconds"], o["end_seconds"]) for o in context["non_training_observations"]
        ] == [(2, 4), (52, 54)]
        assert [c["caption"] for c in context["unassigned_captions"]] == [
            {"text": "如果握把松动，不要继续", "start_seconds": 2, "end_seconds": 4},
            {"text": "如果握把松动，不要继续", "start_seconds": 52, "end_seconds": 54},
        ]
        assert len(body["observations"]) == 4
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [o["id"]],
                                "name": "合成推举",
                                "relation": "uncertain",
                                "content_role": "exercise",
                                "related_member_id": None,
                                "accepted_tip_ids": [],
                            }
                            for o in body["observations"]
                        ]
                    }
                )
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
        pipeline = OrchestratedAnalysisPipeline(
            media=FakeMediaProcessor(tmp_path),
            asr=FakeAsr(),
            ark=provider,
            evidence_first_ark=provider,
            skills=SkillRepository.load(Path(__file__).parents[3] / "skills"),
            semantic_fusion=SemanticCandidateFusion(
                model=provider, instructions="synthetic", include_visual_context=True
            ),
        )
        result = await pipeline.analyze(
            VideoSource(
                id="s", title="synthetic", path=tmp_path / "source.mp4", duration_seconds=70
            ),
            None,
            no_op_emit,
        )
    assert len(grouping_payloads) == 1, "context must reach grouping, not merely fall back"
    assert [(c.segment.start_seconds, c.segment.end_seconds) for c in result.candidates] == [
        (0, 2),
        (4, 6),
        (50, 52),
        (54, 56),
    ]
    assert all(
        not c.tips and not c.parameters.model_dump(exclude_none=True) for c in result.candidates
    )
