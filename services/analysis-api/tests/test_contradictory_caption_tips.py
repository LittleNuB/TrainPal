"""Contradictory tips are quarantined, not repaired, at the Provider HTTP seam."""

import json
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_purpose_specific_caption_decisions import decisions, readings

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.visual_evidence import EvidenceVisualSegment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "training,ownership", [(False, "unknown"), (False, "explicit_name"), (True, "unknown")]
)
@pytest.mark.parametrize("compact_ids", [False, True])
async def test_known_nontraining_unknown_tip_preserves_source_and_valid_claims(
    training: bool,
    ownership: str,
    compact_ids: bool,
) -> None:
    source = readings()
    source.observations.append(
        source.observations[0].model_copy(
            update={"start_seconds": 22, "end_seconds": 30, "is_training_content": training}
        )
    )
    source.observations.append(
        source.observations[0].model_copy(update={"start_seconds": 32, "end_seconds": 40})
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = decisions(body)
        output["decisions"][2].update(action_id=body["observations"][1]["id"], ownership=ownership)
        # The model is still asked for strict, valid ownership, not encouraged to err.
        assert not Draft202012Validator(payload["text"]["format"]["schema"]).is_valid(output)
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
            compact_visual_evidence_ids=compact_ids,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
    rejected = result.unassigned_judgments[1]
    assert rejected.caption == source.captions[2]
    assert rejected.purpose == "uncertain" and rejected.ownership == "unknown"
    assert rejected.parameters is None and rejected.tip is None and rejected.support == []
    assert result.rejected_caption_owners == 1
    assert [o.evidence_incomplete for o in result.observations] == [True, True, False]
    assert [o.reading for o in result.observations] == source.observations
    segments = result.to_visual_segments()
    assert isinstance(segments[0], EvidenceVisualSegment)
    assert [t.text for t in segments[0].tip_evidence] == [source.captions[1].text]
    assert all(not s.tips for s in segments)
    claim = segments[0].parameter_claims[0]
    assert claim.parameters.reps == 12 and claim.parameters.sets == 3
    assert (claim.evidence[0].start_seconds, claim.evidence[0].end_seconds) == (4, 8)
    assert all(isinstance(s, EvidenceVisualSegment) and not s.tip_evidence for s in segments[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "disabled",
        "null_owner",
        "unknown_owner",
        "foreign_support",
        "empty_support",
        "duplicate_support",
        "missing_caption",
        "duplicate_caption",
        "stale_binding",
        "missing_category",
        "invalid_category",
        "extra_parameters",
        "invalid_ownership",
        "training_target",
        "load_selection_condition",
        "unsafe_tip",
        "later_bad_support",
    ],
)
async def test_contradictory_tip_cannot_hide_integrity_or_field_errors(fault: str) -> None:
    source = readings()
    if fault == "unsafe_tip":
        source.captions[2].text = "疼痛时继续坚持训练"
    if fault == "later_bad_support":
        original = source.captions
        source.captions = [original[2], original[0], original[1]]

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if fault == "later_bad_support":
            # Restore the fixture's semantic order while retaining the source IDs.
            body["captions"] = [body["captions"][1], body["captions"][2], body["captions"][0]]
        output = decisions(body)
        tip = output["decisions"][2]
        tip["ownership"] = "unknown"
        if fault == "null_owner":
            tip["action_id"] = None
        elif fault == "unknown_owner":
            tip["action_id"] = "foreign"
        elif fault == "foreign_support":
            tip["support_ids"] = ["foreign"]
        elif fault == "empty_support":
            tip["support_ids"] = []
        elif fault == "duplicate_support":
            tip["support_ids"] *= 2
        elif fault == "missing_caption":
            output["decisions"].pop(0)
        elif fault == "duplicate_caption":
            output["decisions"].append(output["decisions"][0])
        elif fault == "stale_binding":
            output["binding_id"] = "stale"
        elif fault == "missing_category":
            tip.pop("category")
        elif fault == "invalid_category":
            tip["category"] = "invalid"
        elif fault == "extra_parameters":
            tip["parameters"] = None
        elif fault == "invalid_ownership":
            tip["ownership"] = "invalid"
        elif fault == "training_target":
            tip["purpose"] = "training_target"
            tip["parameters"] = output["decisions"][1]["parameters"]
        elif fault == "load_selection_condition":
            tip["purpose"] = "load_selection_condition"
            tip.pop("category")
        elif fault == "later_bad_support":
            output["decisions"][1]["support_ids"] *= 2
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
            isolate_contradictory_caption_tips=fault != "disabled",
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
            )


@pytest.mark.asyncio
async def test_rejected_tip_stays_pending_and_legal_tip_survives_fusion() -> None:
    source = readings()

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output: dict[str, Any]
        if "captions" in body:
            output = decisions(body)
            output["decisions"][2]["ownership"] = "unknown"
        else:
            # Only the legitimate joint parameter/reminder reaches fusion.
            assert "合成划船回程吸气" not in json.dumps(body, ensure_ascii=False)
            output = {
                "groups": [
                    {
                        "member_ids": [o["id"]],
                        "name": o["name"],
                        "relation": "same_demonstration",
                        "content_role": "exercise",
                        "accepted_tip_ids": [t["id"] for t in o["tip_evidence"]],
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
        organized = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
        result = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s", speech_signals=[], visual_segments=organized.to_visual_segments()
        )
    assert len(result.candidates) == 1 and result.candidates[0].needs_confirmation
    assert [t.text for t in result.candidates[0].tips] == [source.captions[1].text]
    assert (
        result.candidates[0].tips[0].evidence.start_seconds,
        result.candidates[0].tips[0].evidence.end_seconds,
    ) == (4, 8)


@pytest.mark.asyncio
@pytest.mark.parametrize("outside", [False, True])
async def test_all_nontraining_targets_keep_uncertain_caption_without_inventing_training(
    outside: bool,
) -> None:
    source = readings()
    source.observations[0].is_training_content = False
    source.captions = [source.captions[2]]
    if outside:
        source.captions[0].start_seconds = 40
        source.captions[0].end_seconds = 44

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = {
            "binding_id": body["binding_id"],
            "decisions": [
                {
                    "caption_id": body["captions"][0]["id"],
                    "purpose": "tip",
                    "action_id": body["observations"][0]["id"],
                    "ownership": "unknown",
                    "support_ids": [body["captions"][0]["id"]],
                    "category": "breathing",
                }
            ],
        }
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
    assert result.unassigned_judgments[0].caption == source.captions[0]
    assert result.rejected_caption_owners == 1
    assert result.observations[0].evidence_incomplete
    assert not result.observations[0].reading.is_training_content
    assert result.to_visual_segments()[0].tips == []


@pytest.mark.asyncio
async def test_contradictory_tip_isolation_requires_purpose_specific_timeline() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError):
            ArkResponsesClient(
                api_key="test",
                model_id="test",
                base_url="https://test.invalid",
                http_client=http,
                isolate_contradictory_caption_tips=True,
            )
