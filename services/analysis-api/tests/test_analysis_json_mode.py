import json
from pathlib import Path

import httpx
import pytest

from hakimi_analysis.models import MAX_VIDEO_SOURCE_TIPS, Segment, SpeechSignal, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError


@pytest.mark.parametrize("bad", [None, "missing_field", "string_number", "incomplete", "refusal"])
async def test_explicit_analysis_json_mode_validates_the_original_schema(bad: str | None) -> None:
    signal = SpeechSignal(
        action_name="抬臂", evidence_text="抬臂六次", reps=6, start_seconds=1, end_seconds=3
    ).model_dump(mode="json")
    if bad == "missing_field":
        signal.pop("content_scope")
    elif bad == "string_number":
        signal["reps"] = "6"

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["text"]["format"] == {"type": "json_object"}
        assert '"content_scope"' in body["instructions"]
        assert body["store"] is False
        return httpx.Response(
            200,
            json={
                "status": "incomplete" if bad == "incomplete" else "completed",
                "store": False,
                "text": {"format": {"type": "json_object"}},
                "output": [{"content": [{"type": "refusal"}]}] if bad == "refusal" else [],
                "output_text": json.dumps({"signals": [signal]}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://ark.test",
            http_client=client,
            analysis_response_format="json_object",
        )
        call = model.understand_speech(
            transcript={"utterances": []},
            window=Segment(start_seconds=0, end_seconds=5),
            instructions="Read source",
        )
        if bad:
            with pytest.raises(ProviderSchemaError):
                await call
        else:
            result = await call
            assert result.signals[0].reps == 6


async def test_analysis_json_mode_does_not_change_personalization_format() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["name"] == "plan_adjustment"
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "changes": [],
                        "summary": "保持这次安排。",
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://ark.test",
            http_client=client,
            analysis_response_format="json_object",
        )
        result = await model.propose_plan_adjustment({})
    assert result.changes == []


async def test_visual_and_fusion_json_mode_keep_timing_and_members(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic transport fixture, not real media")
    responses = [
        {
            "segments": [
                VisualSegment(
                    action_name="抬臂", visual_cue="教学示范", start_seconds=1, end_seconds=3
                ).model_dump(mode="json")
            ]
        },
        {
            "groups": [
                {
                    "member_ids": ["visual-1"],
                    "name": "抬臂",
                    "relation": "same_demonstration",
                    "content_role": "exercise",
                    "related_member_id": None,
                    "accepted_tip_ids": [],
                }
            ]
        },
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["text"]["format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": body["text"],
                "output_text": json.dumps(responses.pop(0)),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://ark.test",
            http_client=client,
            analysis_response_format="json_object",
        )
        located = await model.locate_visual(
            video_path=video,
            window=Segment(start_seconds=0, end_seconds=5),
            instructions="Read source",
        )
        grouped = await model.group_action_evidence(observations=[], instructions="Group source")
    assert located.segments[0].start_seconds == 1
    assert located.segments[0].end_seconds == 3
    assert grouped.groups[0].member_ids == ["visual-1"]


@pytest.mark.parametrize("tip_ids", [None, [123], ["tip"] * (MAX_VIDEO_SOURCE_TIPS + 1), ["tip-1"]])
async def test_json_mode_isolates_invalid_optional_tip_selection(tip_ids: object) -> None:
    group: dict[str, object] = {
        "member_ids": ["visual-1"],
        "name": "抬臂",
        "relation": "same_demonstration",
        "content_role": "exercise",
        "related_member_id": None,
    }
    if tip_ids is not None:
        group["accepted_tip_ids"] = tip_ids

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": {"format": {"type": "json_object"}},
                "output_text": json.dumps({"groups": [group]}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://ark.test",
            http_client=client,
            analysis_response_format="json_object",
        )
        grouped = await model.group_action_evidence(observations=[], instructions="Group source")
    assert grouped.groups[0].member_ids == ["visual-1"]
    assert grouped.groups[0].accepted_tip_ids == (["tip-1"] if tip_ids == ["tip-1"] else [])


async def test_json_mode_omits_an_overlong_quote_but_keeps_action_parameters() -> None:
    signal = SpeechSignal(
        action_name="抬臂", evidence_text="抬臂六次", reps=6, start_seconds=1, end_seconds=3
    ).model_dump(mode="json")
    signal["tips"] = [
        {
            "text": "长" * 81,
            "category": "path",
            "evidence": {"type": "speech", "start_seconds": 1, "end_seconds": 3},
        }
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": {"format": {"type": "json_object"}},
                "output_text": json.dumps({"signals": [signal]}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://ark.test",
            http_client=client,
            analysis_response_format="json_object",
        )
        result = await model.understand_speech(
            transcript={"utterances": []},
            window=Segment(start_seconds=0, end_seconds=5),
            instructions="Read source",
        )
    assert len(result.signals) == 1
    assert result.signals[0].reps == 6
    assert result.signals[0].tips == []
