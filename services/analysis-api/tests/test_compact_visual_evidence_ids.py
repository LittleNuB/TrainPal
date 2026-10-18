"""Scoped short IDs preserve source binding at the Provider HTTP boundary."""

import json

import httpx
import pytest
from test_purpose_specific_caption_decisions import decisions, readings

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError


@pytest.mark.asyncio
async def test_compact_ids_keep_source_and_complete_binding_with_smaller_request() -> None:
    source = readings()
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        sent.append((len(request.content), body))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(decisions(body)),
            },
        )

    results = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        for compact in (False, True):
            client = ArkResponsesClient(
                api_key="test",
                model_id="test",
                base_url="https://test.invalid",
                http_client=http,
                independent_visual_captions=True,
                purpose_specific_caption_decisions=True,
                compact_visual_evidence_ids=compact,
            )
            results.append(
                await client.organize_visual_evidence(
                    run_id="r",
                    source_id="s",
                    input_version="v",
                    readings=source,
                    utterances=[],
                )
            )
    assert results[0] == results[1]
    assert sent[0][1]["binding_id"] == sent[1][1]["binding_id"]
    assert len(sent[1][1]["binding_id"]) == 64
    assert sent[1][0] < sent[0][0]
    assert sent[1][1]["observations"][0]["id"] == "a0"
    assert [c["id"] for c in sent[1][1]["captions"]] == ["c0", "c1", "c2"]
    assert results[1].observations[0].judgments[0].caption == source.captions[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "binding",
        "action",
        "caption",
        "support",
        "duplicate",
        "missing",
        "old_encoding",
    ],
)
async def test_compact_ids_preserve_binding_and_reference_validation(fault: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = decisions(body)
        items = output["decisions"]
        if fault == "binding":
            output["binding_id"] = "other-input"
        elif fault == "action":
            items[1]["action_id"] = "a999"
        elif fault == "caption":
            items[1]["caption_id"] = "c999"
        elif fault == "support":
            items[1]["support_ids"] = ["c999"]
        elif fault == "duplicate":
            items.append(items[0])
        elif fault == "missing":
            items.pop()
        elif fault == "old_encoding":
            items[1]["caption_id"] = f"{body['binding_id']}:caption:1"
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
        client = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            compact_visual_evidence_ids=True,
        )
        with pytest.raises(ProviderSchemaError):
            await client.organize_visual_evidence(
                run_id="r",
                source_id="s",
                input_version="v",
                readings=readings(),
                utterances=[],
            )


@pytest.mark.asyncio
async def test_equal_short_ids_cannot_replay_another_sources_decisions() -> None:
    responses = []
    bindings = []
    ids = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        bindings.append(body["binding_id"])
        ids.append([c["id"] for c in body["captions"]])
        responses.append(decisions(body))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(responses[0]),
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
            compact_visual_evidence_ids=True,
        )
        await client.organize_visual_evidence(
            run_id="r",
            source_id="first",
            input_version="v",
            readings=readings(),
            utterances=[],
        )
        with pytest.raises(ProviderSchemaError):
            await client.organize_visual_evidence(
                run_id="r",
                source_id="second",
                input_version="v",
                readings=readings(),
                utterances=[],
            )
    assert ids[0] == ids[1] and bindings[0] != bindings[1]


@pytest.mark.asyncio
async def test_compact_ids_require_independent_caption_timeline() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError) as error:
            ArkResponsesClient(
                api_key="test",
                model_id="test",
                base_url="https://test.invalid",
                http_client=http,
                compact_visual_evidence_ids=True,
            )
    assert error.value.code == "configuration_error"
