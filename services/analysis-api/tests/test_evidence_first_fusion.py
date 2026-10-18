"""Evidence-first organizer into existing fusion, mocked only at Provider HTTP."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_orchestrated_pipeline import FakeAsr, FakeMediaProcessor, no_op_emit

from hakimi_analysis.models import SpeechSignal
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.pipeline import PipelineFailure
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.sources import VideoSource
from hakimi_analysis.visual_evidence import VisualReadings


def readings() -> VisualReadings:
    return VisualReadings.model_validate(
        {
            "observations": [
                {
                    "action_name": "合成推举",
                    "start_seconds": 0,
                    "end_seconds": 20,
                    "visual_cue": "合成教学",
                    "segment_role": "teaching_demo",
                    "sequence_label": "动作一",
                    "is_training_content": True,
                    "captions": [
                        {"text": "每组八次", "start_seconds": 2, "end_seconds": 3},
                        {"text": "每组十二次", "start_seconds": 12, "end_seconds": 13},
                    ],
                }
            ]
        }
    )


def organization(payload: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
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
                        {
                            "caption_id": q["id"],
                            "action_id": q.get(
                                "observed_with",
                                body["observations"][min(i, len(body["observations"]) - 1)]["id"],
                            ),
                            "purpose": "training_target",
                            "parameters": {
                                "mode": "reps",
                                "sets": None,
                                "reps": 8 if i == 0 else 12,
                                "reps_max": None,
                                "duration_seconds": None,
                                "rest_seconds": None,
                            },
                            "category": None,
                            "ownership": "bounded_context",
                            "support_ids": [q["id"]],
                        }
                        for i, q in enumerate(body["captions"])
                    ],
                }
            ),
        },
    )


@pytest.mark.asyncio
async def test_one_visual_action_retains_two_parameter_claims_with_exact_caption_sources() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] == "visual_evidence_organization":
            return organization(payload, body)
        observations = body["observations"]
        assert len(observations) == 1, "parameter claims must not create extra actions"
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [o["id"] for o in observations],
                                "name": "合成推举",
                                "relation": "same_demonstration",
                                "content_role": "exercise",
                                "related_member_id": None,
                                "accepted_tip_ids": [],
                            }
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
        )
        organized = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v1", readings=readings(), utterances=[]
        )
        result = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s", speech_signals=[], visual_segments=organized.to_visual_segments()
        )
    assert len(result.candidates) == 1
    action = result.candidates[0]
    assert action.needs_confirmation and action.parameters.reps is None
    assert len(action.parameter_conflicts) == 1
    assert [
        (a.parameters.reps, [(e.start_seconds, e.end_seconds) for e in a.evidence])
        for a in action.parameter_conflicts[0].alternatives
    ] == [(8, [(2, 3)]), (12, [(12, 13)])]
    assert (action.playback_options[0].start_seconds, action.playback_options[0].end_seconds) == (
        0,
        20,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_start", [0, 30])
@pytest.mark.parametrize("independent", [False, True])
async def test_full_pipeline_organizes_once_after_chunks_and_offsets_caption_times_once(
    tmp_path: Path,
    source_start: int,
    independent: bool,
) -> None:
    for i in (1, 2):
        (tmp_path / f"chunk-{i}.mp4").write_bytes(b"synthetic")
    media = FakeMediaProcessor(tmp_path)
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        kind = payload["text"]["format"]["name"]
        calls.append(kind)
        if kind == "training_speech_understanding":
            return httpx.Response(200, json={"output_text": '{"signals":[]}'})
        if kind in {"visual_evidence_reading", "visual_caption_timeline"}:
            first = calls.count(kind) == 1
            item = readings().observations[0].model_dump(mode="json")
            item.update(
                start_seconds=45 if first else 0,
                end_seconds=60 if first else 15,
                captions=[
                    {
                        "text": "每组八次" if first else "每组十二次",
                        "start_seconds": 46 if first else 2,
                        "end_seconds": 47 if first else 3,
                    }
                ],
            )
            output = {"observations": [item]}
            if independent:
                output["captions"] = item.pop("captions")
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": payload["text"],
                    "output_text": json.dumps(output),
                },
            )
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if kind == "visual_evidence_organization":
            assert media.exited.is_set()
            assert [(c["start_seconds"], c["end_seconds"]) for c in body["captions"]] == [
                (46, 47),
                (52, 53),
            ]
            return organization(payload, body)
        assert kind == "semantic_action_grouping"
        assert len(body["observations"]) == 2
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
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
            independent_visual_captions=independent,
        )
        pipeline = OrchestratedAnalysisPipeline(
            media=media,
            asr=FakeAsr(),
            ark=provider,
            skills=SkillRepository.load(Path(__file__).parents[3] / "skills"),
            semantic_fusion=SemanticCandidateFusion(model=provider, instructions="synthetic"),
            evidence_first_ark=provider,
        )
        result = await pipeline.analyze(
            VideoSource(
                id="s",
                title="synthetic",
                path=tmp_path / "source.mp4",
                duration_seconds=70 + source_start,
                analysis_start_seconds=source_start,
            ),
            None,
            no_op_emit,
        )
    assert len(result.candidates) == 1
    conflict = result.candidates[0].parameter_conflicts[0]
    assert [
        (p.parameters.reps, [(e.start_seconds, e.end_seconds) for e in p.evidence])
        for p in conflict.alternatives
    ] == [(8, [(46, 47)]), (12, [(52, 53)])]
    assert calls.count("visual_caption_timeline" if independent else "visual_evidence_reading") == 2
    assert calls.count("visual_evidence_organization") == 1
    assert calls.count("semantic_action_grouping") == 1
    assert "visual_action_localization" not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["visual", "organization"])
async def test_failed_evidence_stage_stops_before_fusion(
    tmp_path: Path,
    failure: str,
) -> None:
    for i in (1, 2):
        (tmp_path / f"chunk-{i}.mp4").write_bytes(b"synthetic")
    media = FakeMediaProcessor(tmp_path)
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        kind = payload["text"]["format"]["name"]
        calls.append(kind)
        if kind == "training_speech_understanding":
            return httpx.Response(200, json={"output_text": '{"signals":[]}'})
        assert kind in {"visual_evidence_reading", "visual_evidence_organization"}
        output = "{"
        if kind == "visual_evidence_reading" and failure != "visual":
            output = readings().model_dump_json()
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": output,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
        )
        pipeline = OrchestratedAnalysisPipeline(
            media=media,
            asr=FakeAsr(),
            ark=provider,
            skills=SkillRepository.load(Path(__file__).parents[3] / "skills"),
            semantic_fusion=SemanticCandidateFusion(model=provider, instructions="synthetic"),
            evidence_first_ark=provider,
        )
        with pytest.raises(PipelineFailure):
            await pipeline.analyze(
                VideoSource(
                    id="s", title="synthetic", path=tmp_path / "source.mp4", duration_seconds=70
                ),
                None,
                no_op_emit,
            )
    assert media.exited.is_set()
    assert calls.count("visual_evidence_reading") == (1 if failure == "visual" else 2)
    assert calls.count("visual_evidence_organization") == (0 if failure == "visual" else 1)
    assert "semantic_action_grouping" not in calls


@pytest.mark.asyncio
async def test_failed_parallel_speech_stops_the_next_visual_request(tmp_path: Path) -> None:
    for i in (1, 2):
        (tmp_path / f"chunk-{i}.mp4").write_bytes(b"synthetic")
    speech_failed = asyncio.Event()
    calls: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        kind = payload["text"]["format"]["name"]
        calls.append(kind)
        if kind == "training_speech_understanding":
            speech_failed.set()
            return httpx.Response(503)
        assert kind == "visual_evidence_reading"
        await speech_failed.wait()
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": readings().model_dump_json(),
            },
        )

    media = FakeMediaProcessor(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
        )
        pipeline = OrchestratedAnalysisPipeline(
            media=media,
            asr=FakeAsr(),
            ark=provider,
            skills=SkillRepository.load(Path(__file__).parents[3] / "skills"),
            semantic_fusion=SemanticCandidateFusion(model=provider, instructions="synthetic"),
            evidence_first_ark=provider,
        )
        with pytest.raises(PipelineFailure):
            await pipeline.analyze(
                VideoSource(
                    id="s",
                    title="synthetic",
                    path=tmp_path / "source.mp4",
                    duration_seconds=70,
                ),
                None,
                no_op_emit,
            )
    assert media.exited.is_set()
    assert calls.count("visual_evidence_reading") <= 1
    assert calls.count("training_speech_understanding") == 1
    assert len(calls) <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["uncertain", "tip"])
async def test_caption_judgment_survives_fusion_with_speech(purpose: str) -> None:
    source = readings()
    source.observations[0].captions = [source.observations[0].captions[0]]
    source.observations[0].captions[0].text = "推起时呼气"

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] == "visual_evidence_organization":
            q = body["captions"][0]
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
                                {
                                    "caption_id": q["id"],
                                    "action_id": q["observed_with"] if purpose == "tip" else None,
                                    "purpose": purpose,
                                    "parameters": None,
                                    "category": "breathing" if purpose == "tip" else None,
                                    "ownership": "bounded_context"
                                    if purpose == "tip"
                                    else "unknown",
                                    "support_ids": [q["id"]] if purpose == "tip" else [],
                                }
                            ],
                        }
                    ),
                },
            )
        observations = body["observations"]
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [o["id"] for o in observations],
                                "name": "合成推举",
                                "relation": "same_demonstration",
                                "content_role": "exercise",
                                "related_member_id": None,
                                "accepted_tip_ids": [
                                    t["id"] for o in observations for t in o["tip_evidence"]
                                ],
                            }
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
        )
        organized = await provider.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v1",
            readings=source,
            utterances=[],
        )
        result = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s",
            visual_segments=organized.to_visual_segments(),
            speech_signals=[
                SpeechSignal(
                    action_name="合成推举",
                    evidence_text="合成推举教学",
                    start_seconds=0,
                    end_seconds=20,
                    segment_role="teaching_demo",
                )
            ],
        )
    assert len(result.candidates) == 1
    action = result.candidates[0]
    if purpose == "uncertain":
        assert action.needs_confirmation
        assert not action.tips
        assert any(w.code == "semantic_fusion_conflict" for w in result.warnings)
    else:
        assert [
            (t.text, t.evidence.type, t.evidence.start_seconds, t.evidence.end_seconds)
            for t in action.tips
        ] == [("推起时呼气", "visual", 2, 3)]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["duplicate", "ordinal", "side", "variant", "repeat"])
async def test_enriched_visual_evidence_keeps_identity_guards_and_never_adds_votes(
    case: str,
) -> None:
    source = readings()
    first = source.observations[0]
    first.action_name = "左侧哑铃推举" if case == "side" else "哑铃推举"
    second = first.model_copy(deep=True)
    if case == "ordinal":
        second.sequence_label = "动作二"
    elif case == "side":
        first.sequence_label = "动作一 左侧"
        second.sequence_label = "动作一 右侧"
        second.action_name = "右侧哑铃推举"
    elif case == "variant":
        first.sequence_label = "动作一 哑铃"
        second.sequence_label = "动作一 杠铃"
        second.action_name = "杠铃推举"
    elif case == "repeat":
        second.start_seconds = 30
        second.end_seconds = 50
        for caption in second.captions:
            caption.start_seconds += 30
            caption.end_seconds += 30
    source.observations.append(second)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] == "visual_evidence_organization":
            return organization(payload, body)
        assert len(body["observations"]) == 2
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [o["id"] for o in body["observations"]],
                                "name": first.action_name,
                                "relation": "same_demonstration",
                                "content_role": "exercise",
                                "related_member_id": None,
                                "accepted_tip_ids": [],
                            }
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
        )
        organized = await provider.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v1",
            readings=source,
            utterances=[],
        )
        result = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s",
            visual_segments=organized.to_visual_segments(),
            speech_signals=[],
        )
    assert len(result.candidates) == (1 if case == "duplicate" else 2)
    assert all(c.needs_confirmation for c in result.candidates)
    assert all(c.parameter_conflicts for c in result.candidates[:1])
