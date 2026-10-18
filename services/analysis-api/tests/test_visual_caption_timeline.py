"""Independent captions through the public Provider HTTP boundary."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.visual_evidence import VisualCaptionTimeline


def timeline() -> dict[str, object]:
    return {
        "observations": [
            {
                "action_name": "合成推举",
                "start_seconds": 10,
                "end_seconds": 20,
                "visual_cue": "合成教学",
                "segment_role": "teaching_demo",
                "sequence_label": "动作一",
                "is_training_content": True,
            }
        ],
        "captions": [{"text": "下一个动作准备", "start_seconds": 8, "end_seconds": 14}],
    }


@pytest.mark.asyncio
async def test_cross_boundary_caption_keeps_its_own_time_and_does_not_change_action(
    tmp_path: Path,
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
                "output_text": json.dumps(timeline()),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
        )
        result = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=30)
        )
    assert result.observations[0].start_seconds == 10
    assert result.observations[0].end_seconds == 20
    assert result.model_dump()["captions"] == [
        {
            "text": "下一个动作准备",
            "start_seconds": 8,
            "end_seconds": 14,
        }
    ]


@pytest.mark.asyncio
async def test_organizer_uses_independent_chapter_support_without_rewriting_source() -> None:
    source = VisualCaptionTimeline.model_validate(timeline())
    source.captions[0].text = "不要耸肩"
    source.captions.append(
        type(source.captions[0])(text="动作一 合成推举", start_seconds=5, end_seconds=6)
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if payload["text"]["format"]["name"] == "semantic_action_grouping":
            observations = body["observations"]
            assert observations[0]["caption_context"][0]["caption"]["text"] == "不要耸肩"
            assert observations[0]["caption_context"][0]["support"][0] == {
                "text": "动作一 合成推举",
                "start_seconds": 5,
                "end_seconds": 6,
            }
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
                                    "accepted_tip_ids": [
                                        t["id"] for o in observations for t in o["tip_evidence"]
                                    ],
                                }
                            ]
                        }
                    )
                },
            )
        assert all("observed_with" not in c for c in body["captions"])
        cue, chapter = body["captions"]
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
                                "caption_id": cue["id"],
                                "action_id": body["observations"][0]["id"],
                                "purpose": "tip",
                                "parameters": None,
                                "category": "caution",
                                "ownership": "bounded_context",
                                "support_ids": [chapter["id"]],
                            },
                            {
                                "caption_id": chapter["id"],
                                "action_id": None,
                                "purpose": "context",
                                "parameters": None,
                                "category": None,
                                "ownership": "unknown",
                                "support_ids": [],
                            },
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
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="timeline-v1", readings=source, utterances=[]
        )
        fused = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s", speech_signals=[], visual_segments=result.to_visual_segments()
        )
    action = result.observations[0]
    assert (action.reading.start_seconds, action.reading.end_seconds) == (10, 20)
    assert len(action.judgments) == 1
    tip = action.judgments[0].tip
    assert tip is not None
    assert (tip.text, tip.evidence.start_seconds, tip.evidence.end_seconds) == ("不要耸肩", 8, 14)
    assert action.judgments[0].support[0].text == "动作一 合成推举"
    assert result.unassigned_judgments[0].caption.text == "动作一 合成推举"
    assert fused.candidates[0].tips == [tip]
    assert (fused.candidates[0].segment.start_seconds, fused.candidates[0].segment.end_seconds) == (
        10,
        20,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["caption_before_source", "caption_after_source", "blank", "nonfinite", "nested_caption"],
)
async def test_timeline_still_rejects_invalid_source_evidence(tmp_path: Path, fault: str) -> None:
    source = VisualCaptionTimeline.model_validate(timeline()).model_dump(mode="json")
    if fault == "caption_before_source":
        source["captions"][0]["start_seconds"] = -1
    elif fault == "caption_after_source":
        source["captions"][0]["end_seconds"] = 31
    elif fault == "blank":
        source["captions"][0]["text"] = " "
    elif fault == "nonfinite":
        source["captions"][0]["start_seconds"] = float("nan")
    else:
        source["observations"][0]["captions"] = source.pop("captions")
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": json.loads(r.content)["text"],
                    "output_text": json.dumps(source),
                },
            )
        )
    ) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.read_visual_evidence(
                video_path=video, window=Segment(start_seconds=0, end_seconds=30)
            )


def decision(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "caption_id": body["captions"][0]["id"],
        "action_id": body["observations"][0]["id"],
        "purpose": "tip",
        "parameters": None,
        "category": "path",
        "ownership": "explicit_name",
        "support_ids": [body["captions"][0]["id"]],
    }


@pytest.mark.asyncio
async def test_organizer_wire_contract_limits_references_and_purpose_combinations() -> None:
    source = VisualCaptionTimeline.model_validate(timeline())
    source.captions[0].text = "合成推举不要耸肩"
    source.observations.append(
        source.observations[0].model_copy(
            update={"is_training_content": False, "action_name": None}
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        validator = Draft202012Validator(payload["text"]["format"]["schema"])
        output = {"binding_id": body["binding_id"], "decisions": [decision(body)]}
        assert validator.is_valid(output)
        malformed = json.loads(json.dumps(output))
        malformed["decisions"][0]["action_id"] = body["observations"][-1]["id"]
        assert not validator.is_valid(malformed), (
            "Non-training observations cannot own active claims"
        )
        malformed = json.loads(json.dumps(output))
        malformed["decisions"][0]["support_ids"] = [body["observations"][0]["id"]]
        assert not validator.is_valid(malformed), "Action IDs are not caption support IDs"
        malformed["decisions"][0]["support_ids"] = ["invented-caption"]
        assert not validator.is_valid(malformed)
        malformed = json.loads(json.dumps(output))
        malformed["decisions"][0].update(purpose="context", category=None)
        assert not validator.is_valid(malformed), "Context cannot claim an action or supports"
        malformed["decisions"][0].update(action_id=None, ownership="unknown", support_ids=[])
        assert validator.is_valid(malformed)
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
            independent_visual_captions=True,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="timeline-v1", readings=source, utterances=[]
        )
    assert result.observations[0].judgments[0].tip is not None


@pytest.mark.asyncio
async def test_no_training_observations_only_allow_unassigned_caption_decisions() -> None:
    source = VisualCaptionTimeline.model_validate(timeline())
    source.observations[0].is_training_content = False

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        validator = Draft202012Validator(payload["text"]["format"]["schema"])
        output = {"binding_id": body["binding_id"], "decisions": [decision(body)]}
        assert not validator.is_valid(output)
        output["decisions"][0].update(
            purpose="uncertain", action_id=None, ownership="unknown", support_ids=[], category=None
        )
        assert validator.is_valid(output)
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
            independent_visual_captions=True,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="timeline-v1", readings=source, utterances=[]
        )
    assert result.observations[0].judgments == []
    assert result.unassigned_judgments[0].purpose == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "stale_binding",
        "unknown_support",
        "duplicate_support",
        "unknown_action",
        "later_execution",
        "non_training",
        "unknown_ownership",
        "missing_caption",
        "duplicate_caption",
    ],
)
async def test_independent_captions_do_not_bypass_binding_or_target_guards(fault: str) -> None:
    source = VisualCaptionTimeline.model_validate(timeline())
    source.captions[0].text = "合成推举不要耸肩"
    source.observations.append(
        source.observations[0].model_copy(update={"start_seconds": 20, "end_seconds": 30})
    )
    if fault == "non_training":
        source.observations[0].is_training_content = False

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        d = decision(body)
        output = {"binding_id": body["binding_id"], "decisions": [d]}
        if fault == "stale_binding":
            output["binding_id"] = "old-run"
        elif fault == "unknown_support":
            d["support_ids"] = ["other-source:caption:0"]
        elif fault == "duplicate_support":
            d["support_ids"] *= 2
        elif fault == "unknown_action":
            d["action_id"] = "other-source:action:0"
        elif fault == "later_execution":
            d["action_id"] = body["observations"][1]["id"]
        elif fault == "unknown_ownership":
            d["ownership"] = "unknown"
        elif fault == "missing_caption":
            output["decisions"] = []
        elif fault == "duplicate_caption":
            output["decisions"] = [d, d]
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
            independent_visual_captions=True,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r",
                source_id="s",
                input_version="timeline-v1",
                readings=source,
                utterances=[],
            )


@pytest.mark.asyncio
async def test_uncertain_transition_is_preserved_but_not_used_as_a_tip() -> None:
    source = VisualCaptionTimeline.model_validate(timeline())
    source.observations.append(
        source.observations[0].model_copy(
            update={"start_seconds": 0, "end_seconds": 10, "sequence_label": "动作零"}
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if "captions" in body:
            d = decision(body)
            d.update(
                action_id=None,
                purpose="uncertain",
                category=None,
                ownership="unknown",
                support_ids=[],
            )
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": payload["text"],
                    "output_text": json.dumps({"binding_id": body["binding_id"], "decisions": [d]}),
                },
            )
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
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
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
        )
        organized = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="timeline-v1", readings=source, utterances=[]
        )
        result = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s", speech_signals=[], visual_segments=organized.to_visual_segments()
        )
    assert len(result.candidates) == 2
    assert all(c.needs_confirmation and not c.tips for c in result.candidates)
    assert all(o.evidence_incomplete for o in organized.observations)
    assert organized.unassigned_judgments[0].caption == source.captions[0]
