"""Purpose-specific wire expressions through the Provider HTTP boundary."""

import json
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError
from hakimi_analysis.visual_evidence import VisualCaptionTimeline


def readings() -> VisualCaptionTimeline:
    return VisualCaptionTimeline.model_validate(
        {
            "observations": [
                {
                    "action_name": "合成划船",
                    "start_seconds": 0,
                    "end_seconds": 20,
                    "visual_cue": "合成教学",
                    "segment_role": "teaching_demo",
                    "sequence_label": "1",
                    "is_training_content": True,
                }
            ],
            "captions": [
                {"text": "动作一 合成划船", "start_seconds": 0, "end_seconds": 3},
                {
                    "text": "合成划船每组12次，共3组，回拉时不要耸肩",
                    "start_seconds": 4,
                    "end_seconds": 8,
                },
                {"text": "合成划船回程吸气", "start_seconds": 9, "end_seconds": 12},
            ],
        }
    )


def decisions(body: dict[str, Any]) -> dict[str, Any]:
    target = body["observations"][0]["id"]
    captions = body["captions"]
    return {
        "binding_id": body["binding_id"],
        "decisions": [
            {"caption_id": captions[0]["id"], "purpose": "context"},
            {
                "caption_id": captions[1]["id"],
                "purpose": "training_target",
                "action_id": target,
                "ownership": "explicit_name",
                "support_ids": [captions[1]["id"]],
                "parameters": {
                    "mode": "reps",
                    "sets": 3,
                    "reps": 12,
                    "reps_max": None,
                    "duration_seconds": None,
                    "rest_seconds": None,
                },
                "category": "caution",
            },
            {
                "caption_id": captions[2]["id"],
                "purpose": "tip",
                "action_id": target,
                "ownership": "explicit_name",
                "support_ids": [captions[2]["id"]],
                "category": "breathing",
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("isolate_owners", [False, True])
async def test_purpose_specific_decisions_preserve_complete_source_and_joint_claim(
    isolate_owners: bool,
) -> None:
    source = readings()

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = decisions(body)
        assert Draft202012Validator(payload["text"]["format"]["schema"]).is_valid(output)
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
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            isolate_invalid_caption_owners=isolate_owners,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
    assert result.unassigned_judgments[0].caption == source.captions[0]
    assert result.unassigned_judgments[0].ownership == "unknown"
    assert result.unassigned_judgments[0].support == []
    claims = result.observations[0].judgments
    assert claims[0].parameters is not None and claims[0].parameters.reps == 12
    assert claims[0].parameters.sets == 3
    assert claims[0].tip is not None and claims[0].tip.text == source.captions[1].text
    assert (claims[0].tip.evidence.start_seconds, claims[0].tip.evidence.end_seconds) == (4, 8)
    assert claims[1].parameters is None
    assert claims[1].tip is not None and claims[1].tip.text == source.captions[2].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "context_owner",
        "empty_target",
        "tip_parameters",
        "foreign_support",
        "stale_binding",
        "duplicate_caption",
        "non_training_owner",
        "nonoverlapping_owner",
        "invalid_mode",
    ],
)
async def test_compact_expression_does_not_repair_invalid_claims(fault: str) -> None:
    source = readings()
    if fault == "non_training_owner":
        source.observations[0].is_training_content = False
    elif fault == "nonoverlapping_owner":
        source.observations[0].end_seconds = 3

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = decisions(body)
        items = output["decisions"]
        if fault == "context_owner":
            items[0]["action_id"] = body["observations"][0]["id"]
        elif fault == "empty_target":
            items[1]["parameters"] = {key: None for key in items[1]["parameters"]}
        elif fault == "tip_parameters":
            items[2]["parameters"] = items[1]["parameters"]
        elif fault == "foreign_support":
            items[2]["support_ids"] = ["another-request:caption:0"]
        elif fault == "stale_binding":
            output["binding_id"] = "another-request"
        elif fault == "duplicate_caption":
            items.append(items[0])
        elif fault == "invalid_mode":
            items[1]["parameters"]["mode"] = "duration"
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
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
            )


@pytest.mark.asyncio
async def test_compact_non_training_source_can_remain_unassigned_without_inventing_owner() -> None:
    source = readings()
    source.observations[0].is_training_content = False

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = {
            "binding_id": body["binding_id"],
            "decisions": [
                {"caption_id": c["id"], "purpose": "uncertain"} for c in body["captions"]
            ],
        }
        assert Draft202012Validator(payload["text"]["format"]["schema"]).is_valid(output)
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
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
    assert result.observations[0].judgments == []
    assert result.observations[0].evidence_incomplete
    assert [j.caption for j in result.unassigned_judgments] == source.captions
    assert all(j.parameters is None and j.tip is None for j in result.unassigned_judgments)


@pytest.mark.asyncio
async def test_http_prompt_expresses_unreliable_ownership_as_an_uncertain_non_claim() -> None:
    source = readings()

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        instructions = payload["instructions"]
        assert "则unknown" not in instructions
        assert "uncertain/unknown" not in instructions
        assert '{"caption_id":"当前字幕ID","purpose":"uncertain"}' in instructions
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = {
            "binding_id": body["binding_id"],
            "decisions": [
                {"caption_id": caption["id"], "purpose": "uncertain"}
                for caption in body["captions"]
            ],
        }
        assert Draft202012Validator(payload["text"]["format"]["schema"]).is_valid(output)
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
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            isolate_contradictory_caption_tips=True,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
    assert [judgment.caption for judgment in result.unassigned_judgments] == source.captions
    assert result.observations[0].judgments == []
    assert result.observations[0].evidence_incomplete
