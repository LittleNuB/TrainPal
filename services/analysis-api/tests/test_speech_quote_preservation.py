"""Synthetic transcription, tested through the existing Provider -> fusion seam."""

import json
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


async def analyze_quote(
    original: str,
    proposed: str,
    *,
    utterance_start: float = 3,
    utterance_end: float = 5,
    additional_utterances: list[dict[str, Any]] | None = None,
    signal_overrides: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["text"]["format"]["name"] == "training_speech_understanding":
            result: dict[str, Any] = {
                "signals": [
                    {
                        "action_name": "反向飞鸟",
                        "start_seconds": 1,
                        "end_seconds": 10,
                        "evidence_text": "反向飞鸟",
                        "reps": 12,
                        "sets": 4,
                        "tips": [
                            {
                                "text": proposed,
                                "category": "path",
                                "evidence": {
                                    "type": "speech",
                                    "start_seconds": 3,
                                    "end_seconds": 5,
                                },
                            }
                        ],
                    }
                ],
            }
            result["signals"][0].update(signal_overrides or {})
        else:
            captured.update(json.loads(payload["input"][0]["content"][0]["text"]))
            # Even unconditional model approval cannot resurrect a rejected quote.
            result = {
                "groups": [
                    {
                        "member_ids": ["speech-1"],
                        "name": "反向飞鸟",
                        "relation": "same_demonstration",
                        "accepted_tip_ids": ["speech-1-tip-1"],
                    }
                ]
            }
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test",
            http_client=client,
            retry_delays=(),
        )
        speech = await model.understand_speech(
            transcript={
                "utterances": [
                    {
                        "text": original,
                        "start_seconds": utterance_start,
                        "end_seconds": utterance_end,
                    },
                    *(additional_utterances or []),
                ]
            },
            window=Segment(start_seconds=0, end_seconds=30),
            instructions="synthetic",
        )
        result = await SemanticCandidateFusion(model=model, instructions="synthetic").run(
            source_id="synthetic",
            speech_signals=speech.signals,
            visual_segments=[],
        )
    assert len(result.candidates) == 1
    assert result.candidates[0].name == "反向飞鸟"
    assert result.candidates[0].parameters.reps == 12
    assert result.candidates[0].parameters.sets == 4
    return [tip.text for tip in result.candidates[0].tips], captured


@pytest.mark.asyncio
async def test_dropped_negation_cannot_reach_training_even_if_model_approves() -> None:
    tips, _ = await analyze_quote("不要耸肩", "耸肩")
    assert tips == []


@pytest.mark.asyncio
async def test_overlong_whole_quote_drops_only_the_tip_not_the_valid_action() -> None:
    original = "保持稳定，" * 17
    tips, captured = await analyze_quote(original, original)
    assert tips == []
    assert captured["observations"][0]["name"] == "反向飞鸟"


@pytest.mark.asyncio
async def test_maximum_length_quote_is_retained_without_truncation() -> None:
    original = "保持稳定" * 20
    tips, _ = await analyze_quote(original, original)
    assert tips == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"reps": 0}, {"action_name": ""}, {"tips": "invalid"}])
async def test_omitting_long_quote_does_not_repair_other_schema_errors(
    overrides: dict[str, Any],
) -> None:
    original = "保持稳定，" * 17
    with pytest.raises(ProviderSchemaError):
        await analyze_quote(original, original, signal_overrides=overrides)


@pytest.mark.asyncio
async def test_overlong_quote_does_not_hide_another_valid_quote() -> None:
    original = "保持稳定，" * 17
    tips, _ = await analyze_quote(
        original,
        original,
        additional_utterances=[{"text": "不要耸肩", "start_seconds": 7, "end_seconds": 8}],
        signal_overrides={
            "tips": [
                {
                    "text": original,
                    "category": "path",
                    "evidence": {
                        "type": "speech",
                        "start_seconds": 3,
                        "end_seconds": 5,
                    },
                },
                {
                    "text": "不要耸肩",
                    "category": "path",
                    "evidence": {
                        "type": "speech",
                        "start_seconds": 7,
                        "end_seconds": 8,
                    },
                },
            ]
        },
    )
    assert tips == ["不要耸肩"]


@pytest.mark.asyncio
async def test_original_quote_keeps_the_full_source_time_not_an_invented_shorter_span() -> None:
    tips, captured = await analyze_quote(
        "做反向飞鸟时，不要耸肩。",
        "做反向飞鸟时，不要耸肩。",
        utterance_start=2,
        utterance_end=6,
    )
    assert tips == ["做反向飞鸟时，不要耸肩。"]
    assert captured["observations"][0]["tip_evidence"][0]["evidence"] == {
        "type": "speech",
        "start_seconds": 2,
        "end_seconds": 6,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "original,proposed",
    [
        ("只有保持稳定时，才向外打开。", "向外打开"),
        ("左侧保持不动，右手向外打开。", "右手向外打开"),
        ("不要，向上抬肩。", "不要向上抬肩"),
        ("保持稳定。然后再慢慢打开。", "保持稳定"),
        ("不要耸肩" * 30, "不要耸肩"),
        ("不要耸肩", "肩膀保持放松"),
    ],
)
async def test_partial_or_rewritten_quotes_are_not_reconstructed(
    original: str,
    proposed: str,
) -> None:
    tips, captured = await analyze_quote(original, proposed)
    assert tips == []
    assert captured["observations"][0]["tip_evidence"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "original",
    [
        "不要耸肩",
        "只有保持稳定时，才向外打开。",
        "左侧保持不动，右手向外打开。",
    ],
)
async def test_complete_qualifiers_reach_review_and_output_unchanged(original: str) -> None:
    tips, captured = await analyze_quote(original, original)
    assert tips == [original]
    assert captured["observations"][0]["tip_evidence"][0]["text"] == original


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(0, 6), (2, 11), (4, 6), (2, 4), (2, 31)])
async def test_quote_cannot_escape_action_window_or_match_a_different_time(
    start: float,
    end: float,
) -> None:
    tips, _ = await analyze_quote("不要耸肩", "不要耸肩", utterance_start=start, utterance_end=end)
    assert tips == []


@pytest.mark.asyncio
async def test_overlapping_duplicate_quotes_do_not_choose_an_arbitrary_source() -> None:
    tips, _ = await analyze_quote(
        "不要耸肩",
        "不要耸肩",
        additional_utterances=[
            {
                "text": "不要耸肩",
                "start_seconds": 2,
                "end_seconds": 6,
            }
        ],
    )
    assert tips == []


@pytest.mark.asyncio
async def test_another_occurrence_or_malformed_record_does_not_replace_the_source() -> None:
    tips, captured = await analyze_quote(
        "不要耸肩",
        "不要耸肩",
        additional_utterances=[
            {"text": "不要耸肩", "start_seconds": 20, "end_seconds": 22},
            {"text": "不要耸肩", "start_seconds": "bad", "end_seconds": 5},
        ],
    )
    assert tips == ["不要耸肩"]
    assert captured["observations"][0]["tip_evidence"][0]["evidence"]["start_seconds"] == 3
