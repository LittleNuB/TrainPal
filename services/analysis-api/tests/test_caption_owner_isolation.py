"""Local ownership rejection through the Provider HTTP boundary."""

import json

import httpx
import pytest
from test_purpose_specific_caption_decisions import decisions, readings

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.visual_evidence import EvidenceVisualSegment


@pytest.mark.asyncio
async def test_wrong_owner_preserves_original_caption_and_other_valid_judgments() -> None:
    source = readings()
    for name, start, end in [("合成深蹲", 22, 30), ("合成推举", 32, 40)]:
        source.observations.append(
            source.observations[0].model_copy(
                update={"action_name": name, "start_seconds": start, "end_seconds": end}
            )
        )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = decisions(body)
        output["decisions"][1]["action_id"] = body["observations"][1]["id"]
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
            isolate_invalid_caption_owners=True,
        )
        result = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
    rejected = result.unassigned_judgments[1]
    assert rejected.caption == source.captions[1]
    assert rejected.purpose == "uncertain" and rejected.ownership == "unknown"
    assert rejected.parameters is None and rejected.tip is None and rejected.support == []
    assert result.rejected_caption_owners == 1
    assert [o.evidence_incomplete for o in result.observations] == [True, True, False]
    assert [o.reading for o in result.observations] == source.observations
    segments = result.to_visual_segments()
    first = segments[0]
    assert isinstance(first, EvidenceVisualSegment)
    assert [t.text for t in first.tip_evidence] == ["合成划船回程吸气"]
    assert all(not s.tips for s in segments)
    assert all(isinstance(s, EvidenceVisualSegment) and not s.parameter_claims for s in segments)
    assert all(isinstance(s, EvidenceVisualSegment) and not s.tip_evidence for s in segments[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("caption_outside_all_actions", [False, True])
async def test_isolated_ownership_stays_pending_through_fusion(
    caption_outside_all_actions: bool,
) -> None:
    source = readings()
    source.observations.append(
        source.observations[0].model_copy(
            update={
                "start_seconds": 22,
                "end_seconds": 30,
                "action_name": "合成深蹲",
                "sequence_label": "2",
            }
        )
    )
    if caption_outside_all_actions:
        source.captions[1].start_seconds = 40
        source.captions[1].end_seconds = 44

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        if "captions" in body:
            output = decisions(body)
            output["decisions"][1]["action_id"] = body["observations"][1]["id"]
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "store": False,
                    "text": payload["text"],
                    "output_text": json.dumps(output),
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
                                "name": o["name"],
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
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            isolate_invalid_caption_owners=True,
        )
        organized = await provider.organize_visual_evidence(
            run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
        )
        result = await SemanticCandidateFusion(model=provider, instructions="synthetic").run(
            source_id="s", speech_signals=[], visual_segments=organized.to_visual_segments()
        )
    assert organized.unassigned_judgments[1].caption == source.captions[1]
    assert len(result.candidates) == 2
    assert result.candidates[1].needs_confirmation and not result.candidates[1].tips
    if not caption_outside_all_actions:
        assert result.candidates[0].needs_confirmation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "disabled",
        "foreign_support",
        "duplicate_support",
        "missing_caption",
        "duplicate_caption",
        "stale_binding",
        "unknown_owner",
        "non_training_owner",
        "empty_parameters",
        "unsafe_tip",
        "later_bad_support",
    ],
)
async def test_local_ownership_rejection_never_hides_global_failure(fault: str) -> None:
    source = readings()
    source.observations.append(
        source.observations[0].model_copy(
            update={
                "start_seconds": 22,
                "end_seconds": 30,
                "is_training_content": fault != "non_training_owner",
            }
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        output = decisions(body)
        items = output["decisions"]
        items[1]["action_id"] = body["observations"][1]["id"]
        if fault == "foreign_support":
            items[1]["support_ids"] = ["foreign"]
        elif fault == "duplicate_support":
            items[1]["support_ids"] *= 2
        elif fault == "missing_caption":
            items.pop()
        elif fault == "duplicate_caption":
            items.append(items[0])
        elif fault == "stale_binding":
            output["binding_id"] = "stale"
        elif fault == "unknown_owner":
            items[1]["action_id"] = "foreign"
        elif fault == "empty_parameters":
            items[1]["parameters"] = {k: None for k in items[1]["parameters"]}
        elif fault == "later_bad_support":
            items[2]["support_ids"] *= 2
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps(output),
            },
        )

    if fault == "unsafe_tip":
        source.captions[1].text = "疼痛时继续坚持训练"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            independent_visual_captions=True,
            purpose_specific_caption_decisions=True,
            isolate_invalid_caption_owners=fault != "disabled",
        )
        with pytest.raises(ProviderSchemaError):
            await provider.organize_visual_evidence(
                run_id="r", source_id="s", input_version="v", readings=source, utterances=[]
            )
