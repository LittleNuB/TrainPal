"""Point-only subtitles remain uncertain through the Provider HTTP boundary."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_orchestrated_pipeline import FakeAsr, FakeMediaProcessor, no_op_emit

from hakimi_analysis.models import Segment
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.sources import VideoSource
from hakimi_analysis.visual_evidence import EvidenceVisualSegment


@pytest.mark.asyncio
async def test_point_caption_is_preserved_without_blocking_valid_tip(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    point = {"text": "合成时间待确认字幕", "start_seconds": 4, "end_seconds": 4}

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["text"]["format"]["name"] == "visual_caption_timeline":
            result = {
                "observations": [
                    {
                        "action_name": "合成推举",
                        "visual_cue": "合成教学",
                        "segment_role": "teaching_demo",
                        "sequence_label": str(i),
                        "is_training_content": True,
                        "start_seconds": start,
                        "end_seconds": end,
                    }
                    for i, start, end in [(1, 0, 8), (2, 10, 20)]
                ],
                "captions": [
                    point,
                    {"text": "合成推举保持稳定", "start_seconds": 11, "end_seconds": 12},
                ],
            }
        else:
            body = json.loads(payload["input"][0]["content"][0]["text"])
            assert "合成时间待确认字幕" not in json.dumps(body, ensure_ascii=False)
            assert len(body["captions"]) == 1
            result = {
                "binding_id": body["binding_id"],
                "decisions": [
                    {
                        "caption_id": body["captions"][0]["id"],
                        "purpose": "tip",
                        "action_id": body["observations"][1]["id"],
                        "ownership": "explicit_name",
                        "support_ids": [body["captions"][0]["id"]],
                        "category": "path",
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(result),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            isolate_point_captions=True,
        )
        readings = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=20)
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=readings, utterances=[]
        )
    assert result.model_dump()["point_captions"] == [point]
    assert [o.evidence_incomplete for o in result.observations] == [True, False]
    segments = result.to_visual_segments()
    assert not segments[0].tips and not segments[0].model_dump()["parameter_claims"]
    assert isinstance(segments[0], EvidenceVisualSegment) and not segments[0].tip_evidence
    assert isinstance(segments[1], EvidenceVisualSegment)
    assert [tip.text for tip in segments[1].tip_evidence] == ["合成推举保持稳定"]
    assert all(not s.tips for s in segments)
    assert [(s.start_seconds, s.end_seconds) for s in segments] == [(0, 8), (10, 20)]


@pytest.mark.asyncio
async def test_pipeline_keeps_point_uncertainty_across_chunk_offset_and_fusion(
    tmp_path: Path,
) -> None:
    for i in (1, 2):
        (tmp_path / f"chunk-{i}.mp4").write_bytes(b"synthetic")
    visual_calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal visual_calls
        payload = json.loads(request.content)
        kind = payload["text"]["format"]["name"]
        result: dict[str, Any]
        if kind == "training_speech_understanding":
            result = {"signals": []}
        elif kind == "visual_caption_timeline":
            visual_calls += 1
            result = {
                "observations": [
                    {
                        "action_name": "合成推举",
                        "visual_cue": "合成教学",
                        "segment_role": "teaching_demo",
                        "sequence_label": str(visual_calls),
                        "is_training_content": True,
                        "start_seconds": 0,
                        "end_seconds": 10,
                    }
                ],
                "captions": []
                if visual_calls == 1
                else [{"text": "合成时间待确认字幕", "start_seconds": 4, "end_seconds": 4}],
            }
        else:
            assert kind == "semantic_action_grouping", (
                "point-only evidence must not buy an organizer call"
            )
            body = json.loads(payload["input"][0]["content"][0]["text"])
            assert [o["evidence_incomplete"] for o in body["observations"]] == [False, True]
            assert "合成时间待确认字幕" not in json.dumps(body, ensure_ascii=False)
            result = {
                "groups": [
                    {
                        "member_ids": [o["id"]],
                        "name": "合成推举",
                        "relation": "same_demonstration",
                        "content_role": "exercise",
                        "accepted_tip_ids": [],
                    }
                    for o in body["observations"]
                ]
            }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(result),
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
            isolate_point_captions=True,
        )
        pipeline = OrchestratedAnalysisPipeline(
            media=FakeMediaProcessor(tmp_path),
            asr=FakeAsr(),
            ark=provider,
            skills=SkillRepository.load(Path(__file__).parents[3] / "skills"),
            semantic_fusion=SemanticCandidateFusion(model=provider, instructions="synthetic"),
            evidence_first_ark=provider,
        )
        result = await pipeline.analyze(
            VideoSource(
                id="s", title="synthetic", path=tmp_path / "source.mp4", duration_seconds=70
            ),
            None,
            no_op_emit,
        )
    assert len(result.candidates) == 2
    assert [(c.segment.start_seconds, c.segment.end_seconds) for c in result.candidates] == [
        (0, 10),
        (50, 60),
    ]
    assert result.candidates[1].needs_confirmation
    assert all(not c.tips and c.parameters.reps is None for c in result.candidates)
    assert any(w.message == "部分字幕时间尚未确认，未用于训练提醒或参数" for w in result.warnings)
    assert all("合成时间待确认字幕" not in c.model_dump_json() for c in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["disabled", "reversed", "outside", "negative", "nonfinite", "blank", "extra"]
)
async def test_point_isolation_does_not_accept_other_bad_evidence(
    tmp_path: Path, fault: str
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    caption: dict[str, object] = {"text": "合成字幕", "start_seconds": 4, "end_seconds": 4}
    if fault == "reversed":
        caption["end_seconds"] = 3
    elif fault == "outside":
        caption.update(start_seconds=21, end_seconds=21)
    elif fault == "negative":
        caption.update(start_seconds=-1, end_seconds=-1)
    elif fault == "nonfinite":
        caption.update(start_seconds=float("inf"), end_seconds=float("inf"))
    elif fault == "blank":
        caption["text"] = " "
    elif fault == "extra":
        caption["parameters"] = {"reps": 12}

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps({"observations": [], "captions": [caption]}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
            isolate_point_captions=fault != "disabled",
        )
        with pytest.raises(ProviderSchemaError):
            await provider.read_visual_evidence(
                video_path=video, window=Segment(start_seconds=0, end_seconds=20)
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [0, 8, 20])
async def test_point_at_boundary_is_preserved_without_an_organizer_request(
    tmp_path: Path, timestamp: int
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["text"]["format"]["name"] == "visual_caption_timeline"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(
                    {
                        "observations": [],
                        "captions": [
                            {
                                "text": "合成无动作字幕",
                                "start_seconds": timestamp,
                                "end_seconds": timestamp,
                            }
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
            independent_visual_captions=True,
            isolate_point_captions=True,
        )
        readings = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=20)
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=readings, utterances=[]
        )
    assert result.model_dump()["point_captions"] == [
        {"text": "合成无动作字幕", "start_seconds": timestamp, "end_seconds": timestamp}
    ]
    assert not result.to_visual_segments()


@pytest.mark.asyncio
async def test_changed_point_text_invalidates_old_organizer_binding(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    reads = 0
    first_binding = ""

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal reads, first_binding
        payload = json.loads(request.content)
        result: dict[str, Any]
        if payload["text"]["format"]["name"] == "visual_caption_timeline":
            reads += 1
            result = {
                "observations": [],
                "captions": [
                    {"text": f"合成待确认{reads}", "start_seconds": 4, "end_seconds": 4},
                    {"text": "合成背景", "start_seconds": 5, "end_seconds": 6},
                ],
            }
        else:
            body = json.loads(payload["input"][0]["content"][0]["text"])
            if not first_binding:
                first_binding = body["binding_id"]
            else:
                assert first_binding != body["binding_id"]
            result = {
                "binding_id": first_binding,
                "decisions": [{"caption_id": body["captions"][0]["id"], "purpose": "context"}],
            }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(result),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
            isolate_point_captions=True,
            purpose_specific_caption_decisions=True,
            compact_visual_evidence_ids=True,
        )
        first = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=20)
        )
        await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=first, utterances=[]
        )
        second = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=20)
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r", source_id="s", input_version="v", readings=second, utterances=[]
            )
