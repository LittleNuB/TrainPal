"""JSON syntax mode never substitutes for canonical visual evidence validation."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError
from hakimi_analysis.visual_evidence import VisualCaptionTimeline


@pytest.mark.asyncio
async def test_json_object_reading_states_fractional_evidence_bounds_in_the_http_instructions(
    tmp_path: Path,
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "0.0 <= start_seconds < end_seconds <= 17.83" in payload["instructions"]
        assert "不能用编码后容器时长或向上取整替代" in payload["instructions"]
        assert json.loads(payload["input"][0]["content"][-1]["text"]) == {
            "clip_timeline": {"start_seconds": 0.0, "end_seconds": 17.83}
        }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(
                    {
                        "observations": [
                            {
                                "action_name": "合成动作",
                                "visual_cue": "合成末尾示范",
                                "segment_role": "teaching_demo",
                                "sequence_label": None,
                                "is_training_content": True,
                                "start_seconds": 16.0,
                                "end_seconds": 17.83,
                            }
                        ],
                        "captions": [
                            {"text": "合成完整原句", "start_seconds": 16.0, "end_seconds": 17.83}
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
            visual_reading_format="json_object",
        )
        result = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=17.83)
        )
    assert isinstance(result, VisualCaptionTimeline)
    assert result.observations[0].end_seconds == 17.83
    assert result.captions[0].end_seconds == 17.83
    assert result.captions[0].text == "合成完整原句"


@pytest.mark.asyncio
async def test_json_object_reading_retains_complete_caption_and_point(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    caption = {"text": "合成原句：不要改变侧别，转场后再开始", "start_seconds": 1, "end_seconds": 5}
    point = {"text": "合成点字幕", "start_seconds": 6, "end_seconds": 6}

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["text"]["format"] == {"type": "json_object"}
        assert payload["store"] is False and payload["thinking"] == {"type": "disabled"}
        assert (
            '"observations"' in payload["instructions"] and '"captions"' in payload["instructions"]
        )
        assert "字幕可以提前出现、跨转场或覆盖多个动作" in payload["instructions"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(
                    {
                        "observations": [
                            {
                                "action_name": None,
                                "visual_cue": "合成教学",
                                "segment_role": "teaching_demo",
                                "sequence_label": "2-左侧",
                                "is_training_content": True,
                                "start_seconds": 2,
                                "end_seconds": 8,
                            }
                        ],
                        "captions": [caption, point],
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
            visual_reading_format="json_object",
        )
        result = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=10)
        )
    assert result.model_dump() == {
        "observations": [
            {
                "action_name": None,
                "visual_cue": "合成教学",
                "segment_role": "teaching_demo",
                "sequence_label": "2-左侧",
                "is_training_content": True,
                "start_seconds": 2,
                "end_seconds": 8,
            }
        ],
        "captions": [caption],
        "point_captions": [point],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "extra_top",
        "extra_observation",
        "extra_caption",
        "missing_identity",
        "wrong_role",
        "wrong_boolean",
        "outside_observation",
        "fractional_round_up_observation",
        "nonpositive_observation",
        "outside_caption",
        "fractional_round_up_caption",
        "reversed_caption",
        "blank_caption",
        "long_caption",
        "missing_caption_time",
        "bad_json",
        "code_fence",
        "nonfinite",
        "format_mismatch",
        "stored_response",
        "incomplete",
        "refusal",
    ],
)
async def test_json_object_reading_still_rejects_invalid_source_or_contract(
    tmp_path: Path,
    fault: str,
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        result: dict[str, Any] = {
            "observations": [
                {
                    "action_name": "合成推举",
                    "visual_cue": "合成教学",
                    "segment_role": "teaching_demo",
                    "sequence_label": "1",
                    "is_training_content": True,
                    "start_seconds": 0,
                    "end_seconds": 8,
                }
            ],
            "captions": [
                {"text": "PRIVATE_SYNTHETIC_CANARY", "start_seconds": 1, "end_seconds": 2}
            ],
        }
        observation, caption = result["observations"][0], result["captions"][0]
        if fault == "extra_top":
            result["tips"] = []
        elif fault == "extra_observation":
            observation["parameters"] = {"reps": 10}
        elif fault == "extra_caption":
            # Synthetic replay of the observed failure class, not the discarded model field.
            caption["synthetic_unexpected_field"] = "PRIVATE_SYNTHETIC_CANARY"
        elif fault == "missing_identity":
            del observation["action_name"]
        elif fault == "wrong_role":
            observation["segment_role"] = "unsupported"
        elif fault == "wrong_boolean":
            observation["is_training_content"] = "true"
        elif fault == "outside_observation":
            observation["end_seconds"] = 11
        elif fault == "fractional_round_up_observation":
            observation["end_seconds"] = 17.9
        elif fault == "nonpositive_observation":
            observation["end_seconds"] = 0
        elif fault == "outside_caption":
            caption["end_seconds"] = 11
        elif fault == "fractional_round_up_caption":
            caption["end_seconds"] = 17.9
        elif fault == "reversed_caption":
            caption["end_seconds"] = 0
        elif fault == "blank_caption":
            caption["text"] = "  "
        elif fault == "long_caption":
            caption["text"] = "a" * 501
        elif fault == "missing_caption_time":
            del caption["end_seconds"]
        elif fault == "nonfinite":
            caption["start_seconds"] = float("nan")
        text = json.dumps(result)
        if fault == "bad_json":
            text = '{"observations":undefined,"captions":[]}'
        elif fault == "code_fence":
            text = "```json\n" + text + "\n```"
        metadata: dict[str, Any] = {
            "status": "completed",
            "store": False,
            "text": deepcopy(payload["text"]),
            "output_text": text,
        }
        if fault == "format_mismatch":
            metadata["text"]["format"] = {"type": "text"}
        elif fault == "stored_response":
            metadata["store"] = True
        elif fault == "incomplete":
            metadata["status"] = "incomplete"
        elif fault == "refusal":
            metadata["output"] = [{"content": [{"type": "refusal"}]}]
        return httpx.Response(200, json=metadata)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            independent_visual_captions=True,
            isolate_point_captions=True,
            visual_reading_format="json_object",
        )
        with pytest.raises(ProviderSchemaError) as error:
            await provider.read_visual_evidence(
                video_path=video,
                window=Segment(
                    start_seconds=0,
                    end_seconds=17.83 if fault.startswith("fractional_round_up") else 10,
                ),
            )
    assert len(calls) == 1
    assert "PRIVATE_SYNTHETIC_CANARY" not in str(error.value)


@pytest.mark.asyncio
async def test_reading_default_remains_strict_schema(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        assert "精确边界" not in payload["instructions"]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": '{"observations":[],"captions":[]}',
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
            video_path=video, window=Segment(start_seconds=0, end_seconds=10)
        )
    assert isinstance(result, VisualCaptionTimeline)
    assert result.observations == [] and result.captions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"visual_reading_format": "unsupported"},
        {"visual_reading_format": "json_object"},
        {"visual_reading_format": "json_object", "independent_visual_captions": False},
    ],
)
async def test_invalid_reading_format_configuration_fails_closed(options: dict[str, Any]) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError) as error:
            ArkResponsesClient(
                api_key="synthetic",
                model_id="synthetic",
                base_url="https://synthetic.test",
                http_client=http,
                **options,
            )
    assert error.value.code == "configuration_error"


@pytest.mark.asyncio
async def test_json_object_option_does_not_change_speech_contract(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        format_ = payload["text"]["format"]
        result: dict[str, Any]
        if format_["type"] == "json_object":
            result = {"observations": [], "captions": []}
        else:
            assert format_["type"] == "json_schema" and format_["strict"] is True
            assert format_["name"] == "training_speech_understanding"
            result = {"signals": []}
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
            visual_reading_format="json_object",
        )
        await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=10)
        )
        speech = await provider.understand_speech(
            transcript={"utterances": []},
            window=Segment(start_seconds=0, end_seconds=10),
            instructions="synthetic",
        )
    assert speech.signals == []
