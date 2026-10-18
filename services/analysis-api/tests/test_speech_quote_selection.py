"""Opt-in quote selection through the public Provider -> fusion boundary."""

import json
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import AnalysisCandidate, Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


async def analyze_selection(
    selections: list[dict[str, Any]],
    *,
    text: str = "不要耸肩，保持身体稳定。",
    start: float = 3,
    end: float = 5,
) -> tuple[AnalysisCandidate, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["text"]["format"]["name"] == "training_speech_understanding":
            utterances = json.loads(payload["input"][0]["content"][0]["text"])["transcript"][
                "utterances"
            ]
            choices = [
                {**item, "utterance_id": utterances[0]["id"]}
                if item.get("utterance_id") == "utterance-1"
                else item
                for item in selections
            ]
            content: dict[str, Any] = {
                "signals": [
                    {
                        "action_name": "反向飞鸟",
                        "start_seconds": 1,
                        "end_seconds": 10,
                        "evidence_text": "反向飞鸟",
                        "reps": 12,
                        "sets": 4,
                        "tips": choices,
                    }
                ]
            }
        else:
            observations = json.loads(payload["input"][0]["content"][0]["text"])["observations"]
            content = {
                "groups": [
                    {
                        "member_ids": ["speech-1"],
                        "name": "反向飞鸟",
                        "relation": "same_demonstration",
                        "accepted_tip_ids": [t["id"] for t in observations[0]["tip_evidence"]],
                    }
                ]
            }
        return httpx.Response(200, json={"output_text": json.dumps(content)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ark = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test",
            http_client=client,
            retry_delays=(),
            select_speech_quotes=True,
        )
        speech = await ark.understand_speech(
            transcript={"utterances": [{"text": text, "start_seconds": start, "end_seconds": end}]},
            window=Segment(start_seconds=0, end_seconds=30),
            instructions="synthetic",
        )
        result = await SemanticCandidateFusion(model=ark, instructions="synthetic").run(
            source_id="synthetic", speech_signals=speech.signals, visual_segments=[]
        )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert (candidate.parameters.sets, candidate.parameters.reps) == (4, 12)
    return candidate, requests


@pytest.mark.asyncio
async def test_selected_utterance_restores_whole_quote_and_original_time() -> None:
    candidate, requests = await analyze_selection(
        [{"utterance_id": "utterance-1", "category": "path"}]
    )
    assert [t.text for t in candidate.tips] == ["不要耸肩，保持身体稳定。"]
    assert candidate.tips[0].evidence.model_dump(mode="json") == {
        "type": "speech",
        "start_seconds": 3,
        "end_seconds": 5,
    }
    payload = json.loads(requests[0]["input"][0]["content"][0]["text"])
    assert isinstance(payload["transcript"]["utterances"][0]["id"], str)
    properties = requests[0]["text"]["format"]["schema"]["$defs"]["SourceTip"]["properties"]
    assert set(properties) == {"utterance_id", "category"}


@pytest.mark.asyncio
async def test_a_previous_request_quote_id_cannot_select_a_new_transcript() -> None:
    _, previous = await analyze_selection([])
    old_id = json.loads(previous[0]["input"][0]["content"][0]["text"])["transcript"]["utterances"][
        0
    ]["id"]
    current, requests = await analyze_selection(
        [{"utterance_id": old_id, "category": "path"}], text="缓慢向两侧展开。"
    )
    new_id = json.loads(requests[0]["input"][0]["content"][0]["text"])["transcript"]["utterances"][
        0
    ]["id"]
    assert new_id != old_id
    assert current.tips == []


@pytest.mark.parametrize(
    "selections",
    [
        [{"utterance_id": "not-current", "category": "path"}],
        [{"utterance_id": "utterance-1", "category": "path", "text": "耸肩"}],
        [{"utterance_id": "utterance-1", "category": "path", "evidence": {}}],
        [{"utterance_id": "utterance-1", "category": "prescription"}],
        [{"utterance_id": "utterance-1", "category": "path"}] * 2,
        [{"utterance_id": "utterance-1", "category": "path"}] * 4,
    ],
)
@pytest.mark.asyncio
async def test_invalid_optional_selection_cannot_supply_a_reminder_or_remove_action(
    selections: list[dict[str, Any]],
) -> None:
    candidate, _ = await analyze_selection(selections)
    assert candidate.tips == []


@pytest.mark.parametrize(
    "start,end,text",
    [
        (0, 5, "不要耸肩。"),
        (3, 11, "不要耸肩。"),
        (3, 5, "保持稳定，" * 17),
        (3, 5, "出现疼痛时继续坚持。"),
    ],
)
@pytest.mark.asyncio
async def test_selection_does_not_bypass_existing_time_length_or_safety_guards(
    start: float,
    end: float,
    text: str,
) -> None:
    candidate, _ = await analyze_selection(
        [{"utterance_id": "utterance-1", "category": "path"}], start=start, end=end, text=text
    )
    assert candidate.tips == []


@pytest.mark.parametrize(
    "text", ["不要耸肩。", "如果身体晃动，先停下再调整。", "左手保持不动，右手向外展开。"]
)
@pytest.mark.asyncio
async def test_selection_preserves_negation_condition_and_side_verbatim(text: str) -> None:
    candidate, _ = await analyze_selection(
        [{"utterance_id": "utterance-1", "category": "path"}], text=text
    )
    assert [tip.text for tip in candidate.tips] == [text]
