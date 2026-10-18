"""Evidence-first pilot through real Provider HTTP methods, never private helpers."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment, TranscriptUtterance
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError
from hakimi_analysis.visual_evidence import VisualReadings


def reading() -> dict[str, Any]:
    return {
        "action_name": "合成推举",
        "start_seconds": 0,
        "end_seconds": 10,
        "visual_cue": "合成向上推举示范",
        "segment_role": "teaching_demo",
        "sequence_label": "动作一",
        "is_training_content": True,
        "captions": [{"text": "不要耸肩，向上推举", "start_seconds": 2, "end_seconds": 4}],
    }


def response(payload: dict[str, Any], output: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "store": False,
            "text": payload["text"],
            "output_text": json.dumps(output, ensure_ascii=False),
        },
    )


@pytest.mark.asyncio
async def test_visual_reading_preserves_complete_caption_without_training_fields(
    tmp_path: Path,
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        schema = json.dumps(payload["text"]["format"]["schema"])
        assert "text_parameters" not in schema and "repetition_purpose" not in schema
        assert "tips" not in schema
        return response(payload, {"observations": [reading()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        result = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=10)
        )
    assert isinstance(result, VisualReadings)
    assert result.observations[0].captions[0].model_dump() == {
        "text": "不要耸肩，向上推举",
        "start_seconds": 2,
        "end_seconds": 4,
    }
    assert result.observations[0].sequence_label == "动作一"


@pytest.mark.asyncio
async def test_text_organizer_preserves_identity_conflicts_and_whole_caption(
    tmp_path: Path,
) -> None:
    item = reading()
    item["captions"] += [
        {"text": "每组八次", "start_seconds": 4, "end_seconds": 5},
        {"text": "每组十二次", "start_seconds": 6, "end_seconds": 7},
        {"text": "选择能完成十次的重量", "start_seconds": 8, "end_seconds": 9},
    ]
    original = json.dumps(item)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        action = body["observations"][0]
        decisions = []
        for i, quote in enumerate(body["captions"]):
            decisions.append(
                {
                    "caption_id": quote["id"],
                    "action_id": action["id"],
                    "purpose": "tip"
                    if i == 0
                    else "load_selection_condition"
                    if i == 3
                    else "training_target",
                    "parameters": {
                        "mode": "reps",
                        "sets": None,
                        "reps": 8 if i == 1 else 12,
                        "reps_max": None,
                        "duration_seconds": None,
                        "rest_seconds": None,
                    }
                    if i in (1, 2)
                    else None,
                    "category": "path" if i == 0 else None,
                    "ownership": "bounded_context",
                    "support_ids": [quote["id"]],
                }
            )
        return response(payload, {"binding_id": body["binding_id"], "decisions": decisions})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        result = await provider.organize_visual_evidence(
            run_id="run-one",
            source_id="source-one",
            input_version="v1",
            readings=VisualReadings.model_validate({"observations": [item]}),
            utterances=[],
        )
    assert json.dumps(item) == original
    organized = result.observations[0]
    assert organized.reading.model_dump(mode="json") == item
    assert [j.parameters.reps for j in organized.judgments if j.parameters] == [8, 12]
    assert organized.judgments[3].parameters is None
    tip = organized.judgments[0].tip
    assert tip is not None
    assert tip.text == "不要耸肩，向上推举"
    assert tip.evidence.model_dump(mode="json") == {
        "type": "visual",
        "start_seconds": 2,
        "end_seconds": 4,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_caption_end", "extra_parameters", "outside", "blank"])
async def test_visual_reading_rejects_incomplete_or_invented_contract(
    tmp_path: Path, fault: str
) -> None:
    item = reading()
    if fault == "missing_caption_end":
        del item["captions"][0]["end_seconds"]
    elif fault == "extra_parameters":
        item["text_parameters"] = {"reps": 10}
    elif fault == "outside":
        item["captions"][0]["end_seconds"] = 11
    else:
        item["captions"][0]["text"] = "  "
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: response(json.loads(request.content), {"observations": [item]})
        )
    ) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.read_visual_evidence(
                video_path=video, window=Segment(start_seconds=0, end_seconds=10)
            )


def tip_decision(body: dict[str, Any]) -> dict[str, Any]:
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
@pytest.mark.parametrize(
    "fault",
    [
        "binding",
        "caption",
        "other_action",
        "other_support",
        "unknown_support",
        "no_support",
        "unknown_owner",
        "duplicate",
        "omitted",
        "load_with_parameters",
        "unknown_with_parameters",
        "new_text",
        "new_time",
        "missing_category",
        "numeric_string",
        "non_training",
    ],
)
async def test_organizer_rejects_invalid_reference_or_content_claims(fault: str) -> None:
    first, second = reading(), reading()
    second.update(action_name="合成划船", sequence_label="动作二")
    if fault == "non_training":
        first["is_training_content"] = False

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        decision = tip_decision(body)
        other = {
            "caption_id": body["captions"][1]["id"],
            "action_id": None,
            "purpose": "uncertain",
            "parameters": None,
            "category": None,
            "ownership": "unknown",
            "support_ids": [],
        }
        output = {"binding_id": body["binding_id"], "decisions": [decision, other]}
        if fault == "binding":
            output["binding_id"] = "stale"
        elif fault == "caption":
            decision["caption_id"] = "other-source"
        elif fault == "other_action":
            decision["action_id"] = body["observations"][1]["id"]
        elif fault == "other_support":
            decision["support_ids"] = [body["captions"][1]["id"]]
        elif fault == "unknown_support":
            decision["support_ids"] = ["speech-or-stale-id"]
        elif fault == "no_support":
            decision["support_ids"] = []
        elif fault == "unknown_owner":
            decision["ownership"] = "unknown"
        elif fault == "duplicate":
            output["decisions"] = [decision, decision]
        elif fault == "omitted":
            output["decisions"] = [decision]
        elif fault in {"load_with_parameters", "unknown_with_parameters", "numeric_string"}:
            decision.update(
                purpose="load_selection_condition"
                if fault == "load_with_parameters"
                else "uncertain"
                if fault == "unknown_with_parameters"
                else "training_target",
                category=None,
                parameters={
                    "mode": "reps",
                    "sets": None,
                    "reps": "8" if fault == "numeric_string" else 8,
                    "reps_max": None,
                    "duration_seconds": None,
                    "rest_seconds": None,
                },
            )
        elif fault == "new_text":
            decision["text"] = "耸肩"
        elif fault == "new_time":
            decision["start_seconds"] = 1
        elif fault == "missing_category":
            del decision["category"]
        return response(payload, output)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r",
                source_id="s",
                input_version="v1",
                readings=VisualReadings.model_validate({"observations": [first, second]}),
                utterances=[],
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["run", "source", "version", "content"])
async def test_quote_ids_from_previous_snapshot_cannot_be_reused(changed: str) -> None:
    previous: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if not previous:
            previous.update(tip_decision(body))
        return response(payload, {"binding_id": body["binding_id"], "decisions": [previous]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        data = VisualReadings.model_validate({"observations": [reading()]})
        first = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v1", readings=data, utterances=[]
        )
        assert first.observations[0].judgments[0].tip is not None
        if changed == "content":
            data.observations[0].captions[0].text = "完整的新原句"
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="new" if changed == "run" else "r",
                source_id="new" if changed == "source" else "s",
                input_version="v2" if changed == "version" else "v1",
                readings=data,
                utterances=[],
            )


@pytest.mark.asyncio
async def test_caller_mutation_during_text_request_does_not_rebind_original_caption() -> None:
    data = VisualReadings.model_validate({"observations": [reading()]})
    speech = TranscriptUtterance(text="这只是语音上下文", start_seconds=1, end_seconds=2)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        assert body["speech_context_only"][0]["text"] == "这只是语音上下文"
        data.observations[0].captions[0].text = "被调用者之外的改写"
        return response(
            payload, {"binding_id": body["binding_id"], "decisions": [tip_decision(body)]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v1", readings=data, utterances=[speech]
        )
    assert result.observations[0].judgments[0].caption.text == "不要耸肩，向上推举"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cancel", "http", "json", "refusal"])
async def test_text_failure_never_returns_success_or_retries(failure: str) -> None:
    calls = []
    data = VisualReadings.model_validate({"observations": [reading()]})
    frozen = data.model_dump_json()

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(True)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "http":
            return httpx.Response(503)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": "{" if failure == "json" else "{}",
                "output": [{"content": [{"type": "refusal"}]}] if failure == "refusal" else [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        from hakimi_analysis.providers.base import ProviderError

        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ProviderError):
            await provider.organize_visual_evidence(
                run_id="r", source_id="s", input_version="v1", readings=data, utterances=[]
            )
    assert len(calls) == 1
    assert data.model_dump_json() == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("end", [float("inf"), 301])
async def test_invalid_reading_window_stops_before_upload(tmp_path: Path, end: float) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid window must stop before media is sent")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.read_visual_evidence(
                video_path=video, window=Segment(start_seconds=0, end_seconds=end)
            )


@pytest.mark.asyncio
async def test_schema_error_does_not_expose_provider_payload_in_exception_chain(
    tmp_path: Path,
) -> None:
    item = reading()
    item["action_name"] = {"private": "synthetic-only-private-marker"}
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: response(json.loads(request.content), {"observations": [item]})
        )
    ) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        with pytest.raises(ProviderSchemaError) as raised:
            await provider.read_visual_evidence(
                video_path=video, window=Segment(start_seconds=0, end_seconds=10)
            )
    assert raised.value.__cause__ is None
    assert "private-marker" not in str(raised.value)


@pytest.mark.asyncio
async def test_four_numbered_readings_with_no_captions_need_no_text_call() -> None:
    observations = []
    for i in range(4):
        item = reading()
        item.update(
            action_name=None,
            sequence_label=f"动作{i + 1}",
            start_seconds=i * 10,
            end_seconds=(i + 1) * 10,
            captions=[],
        )
        observations.append(item)

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("no readable captions must not generate reminders")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        result = await provider.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v1",
            readings=VisualReadings.model_validate({"observations": observations}),
            utterances=[],
        )
    assert len(result.observations) == 4
    assert [o.reading.sequence_label for o in result.observations] == [
        "动作1",
        "动作2",
        "动作3",
        "动作4",
    ]
    assert all(o.reading.action_name is None and not o.judgments for o in result.observations)


@pytest.mark.asyncio
async def test_over_capacity_context_fails_before_request_without_truncation() -> None:
    data = VisualReadings.model_validate({"observations": [reading()]})

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("oversized context must not be sent or truncated")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r",
                source_id="s",
                input_version="v1",
                readings=data,
                utterances=[
                    TranscriptUtterance(text="不要" * 40000, start_seconds=0, end_seconds=10)
                ],
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_non_json_numbers_are_rejected_before_reading_is_usable(
    tmp_path: Path, literal: str
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        raw = json.dumps({"observations": [reading()]}).replace(
            '"start_seconds": 0', '"start_seconds": ' + literal, 1
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": raw,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.read_visual_evidence(
                video_path=video, window=Segment(start_seconds=0, end_seconds=10)
            )


@pytest.mark.asyncio
async def test_reading_then_text_organization_keeps_visual_source_distinct_from_speech(
    tmp_path: Path,
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        kind = payload["text"]["format"]["name"]
        requests.append(kind)
        if kind == "visual_evidence_reading":
            return response(payload, {"observations": [reading()]})
        body = json.loads(payload["input"][0]["content"][0]["text"])
        assert body["speech_context_only"][0]["text"] == "不要耸肩，向上推举"
        assert len(body["captions"]) == 1
        return response(
            payload, {"binding_id": body["binding_id"], "decisions": [tip_decision(body)]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        raw = await provider.read_visual_evidence(
            video_path=video, window=Segment(start_seconds=0, end_seconds=10)
        )
        result = await provider.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v1",
            readings=raw,
            utterances=[
                TranscriptUtterance(text="不要耸肩，向上推举", start_seconds=1, end_seconds=5)
            ],
        )
    tip = result.observations[0].judgments[0].tip
    assert tip is not None and tip.evidence.type == "visual"
    assert (tip.evidence.start_seconds, tip.evidence.end_seconds) == (2, 4)
    assert requests == ["visual_evidence_reading", "visual_evidence_organization"]


@pytest.mark.asyncio
async def test_one_caption_can_keep_training_target_and_technique_reminder() -> None:
    item = reading()
    item["captions"][0]["text"] = "合成推举每组八次，不要耸肩"

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        decision = tip_decision(body)
        decision.update(
            purpose="training_target",
            parameters={
                "mode": "reps",
                "sets": None,
                "reps": 8,
                "reps_max": None,
                "duration_seconds": None,
                "rest_seconds": None,
            },
        )
        return response(payload, {"binding_id": body["binding_id"], "decisions": [decision]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
        )
        result = await provider.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v1",
            readings=VisualReadings.model_validate({"observations": [item]}),
            utterances=[],
        )
    judgment = result.observations[0].judgments[0]
    assert judgment.parameters is not None and judgment.parameters.reps == 8
    assert judgment.tip is not None and judgment.tip.text == "合成推举每组八次，不要耸肩"
