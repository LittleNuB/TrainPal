"""The organizer receives complete utterances, without redundant word timing arrays."""

import json

import httpx
import pytest
from test_purpose_specific_caption_decisions import decisions, readings

from hakimi_analysis.models import TranscriptUtterance, TranscriptWord
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError


def speech() -> list[TranscriptUtterance]:
    text = "合成语音测试内容保留完整原句"
    return [
        TranscriptUtterance(
            text=text,
            start_seconds=i * 2.5,
            end_seconds=i * 2.5 + 2.5,
            words=[
                TranscriptWord(
                    text=c,
                    start_seconds=i * 2.5 + j * 0.1,
                    end_seconds=i * 2.5 + (j + 1) * 0.1,
                )
                for j, c in enumerate(text)
            ],
        )
        for i in range(80)
    ]


@pytest.mark.asyncio
async def test_sentence_context_keeps_all_utterances_and_times_without_word_duplication() -> None:
    utterances = speech()
    original = [u.model_copy(deep=True) for u in utterances]
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        requests.append(body)
        assert body["speech_context_only"] == [u.model_dump(exclude={"words"}) for u in original]
        assert len(payload["input"][0]["content"][0]["text"].encode()) < 64000
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(decisions(body)),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            sentence_only_visual_context=True,
        )
        result = await client.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v",
            readings=readings(),
            utterances=utterances,
        )
    assert len(requests) == 1 and len(requests[0]["speech_context_only"]) == 80
    assert utterances == original
    assert len(result.observations[0].judgments) == 2
    assert (
        result.observations[0].judgments[0].caption.text
        == "合成划船每组12次，共3组，回拉时不要耸肩"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["word", "sentence", "time", "source", "version"])
@pytest.mark.parametrize("compact_ids", [False, True])
async def test_sentence_projection_does_not_accept_response_bound_to_changed_input(
    change: str,
    compact_ids: bool,
) -> None:
    utterances = speech()[:1]
    previous = None
    bindings = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal previous
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        bindings.append(body["binding_id"])
        if previous is None:
            previous = decisions(body)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(previous),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            sentence_only_visual_context=True,
            compact_visual_evidence_ids=compact_ids,
        )
        await client.organize_visual_evidence(
            run_id="r",
            source_id="s",
            input_version="v",
            readings=readings(),
            utterances=utterances,
        )
        if change == "word":
            utterances[0].words[0].end_seconds = 0.09
        elif change == "sentence":
            utterances[0].text += "，保留否定条件"
        elif change == "time":
            utterances[0].end_seconds = 2.4
        with pytest.raises(ProviderSchemaError):
            await client.organize_visual_evidence(
                run_id="r",
                source_id="other" if change == "source" else "s",
                input_version="v2" if change == "version" else "v",
                readings=readings(),
                utterances=utterances,
            )
    assert len(bindings) == 2 and bindings[0] != bindings[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["disabled", "oversized_sentence", "invalid_time", "nan_word"])
@pytest.mark.parametrize("compact_ids", [False, True])
async def test_sentence_projection_preserves_capacity_and_input_guards(
    scenario: str,
    compact_ids: bool,
) -> None:
    utterances = speech()
    if scenario == "oversized_sentence":
        utterances[0].text = "合成" * 20000
    elif scenario == "invalid_time":
        utterances[0].end_seconds = 301
    elif scenario == "nan_word":
        utterances[0].words[0].end_seconds = float("nan")

    def refuse(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid or over-capacity input must fail before HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        client = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            sentence_only_visual_context=scenario != "disabled",
            compact_visual_evidence_ids=compact_ids,
        )
        with pytest.raises(ProviderSchemaError):
            await client.organize_visual_evidence(
                run_id="r",
                source_id="s",
                input_version="v",
                readings=readings(),
                utterances=utterances,
            )


@pytest.mark.asyncio
async def test_sentence_projection_requires_independent_caption_mode() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError) as error:
            ArkResponsesClient(
                api_key="test",
                model_id="test",
                base_url="https://test.invalid",
                http_client=http,
                sentence_only_visual_context=True,
            )
    assert error.value.code == "configuration_error"
