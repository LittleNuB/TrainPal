"""Bounded, source-labelled tips; not a knowledge generation or medical feature."""

import math
import re
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from hakimi_analysis.models import (
    MAX_SOURCE_TIP_LENGTH,
    EvidenceSpan,
    Segment,
    SourceTip,
    SpeechSignal,
    TranscriptUtterance,
)


def omit_overlong_speech_quotes(payload: Any) -> Any:
    """Omit oversized optional quotes before strict parsing; do not repair other fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get("signals"), list):
        return payload
    for signal in payload["signals"]:
        if not isinstance(signal, dict) or not isinstance(signal.get("tips"), list):
            continue
        signal["tips"] = [
            tip
            for tip in signal["tips"]
            if not (
                isinstance(tip, dict)
                and isinstance(tip.get("text"), str)
                and len(tip["text"]) > MAX_SOURCE_TIP_LENGTH
            )
        ]
    return payload


def speech_quote_tips(
    signal: SpeechSignal, transcript: dict[str, Any], window: Segment
) -> list[SourceTip]:
    """Keep whole ASR utterances only; never reconstruct words from model excerpts.

    This proves fidelity to ASR text, not to the audio or the action attribution.
    Utterance segmentation can still be incomplete; semantic review remains required.
    """
    raw_utterances = transcript.get("utterances", [])
    if not isinstance(raw_utterances, list):
        return []
    utterances: list[TranscriptUtterance] = []
    for raw in raw_utterances:
        try:
            utterance = TranscriptUtterance.model_validate(raw)
        except ValidationError:
            continue
        if (
            math.isfinite(utterance.start_seconds)
            and math.isfinite(utterance.end_seconds)
            and max(window.start_seconds, signal.start_seconds)
            <= utterance.start_seconds
            < utterance.end_seconds
            <= min(window.end_seconds, signal.end_seconds)
        ):
            utterances.append(utterance)
    retained: list[SourceTip] = []
    for tip in signal.tips:
        if tip.evidence.type != "speech":
            continue
        matches = [
            utterance
            for utterance in utterances
            if tip.text == utterance.text.strip()
            and utterance.start_seconds
            <= tip.evidence.start_seconds
            < tip.evidence.end_seconds
            <= utterance.end_seconds
        ]
        # An ambiguous source is not permission to pick the first occurrence.
        if len(matches) != 1:
            continue
        quote = matches[0]
        retained.append(
            tip.model_copy(
                update={
                    "text": quote.text.strip(),
                    "evidence": EvidenceSpan(
                        type="speech",
                        start_seconds=quote.start_seconds,
                        end_seconds=quote.end_seconds,
                    ),
                }
            )
        )
    return safe_tips(retained)


def compact_text(text: str) -> str:
    return "".join(character for character in text if character.isalnum())


def safe_tips(tips: Iterable[SourceTip], *, limit: int = 3) -> list[SourceTip]:
    result: list[SourceTip] = []
    seen: set[str] = set()
    for tip in tips:
        key = compact_text(tip.text)
        if (
            not key
            or key in seen
            or re.search(
                r"疼|痛|眩晕|康复|治疗|治愈|损伤|伤病|禁忌|保证|必瘦|公斤|千克|kg|负重\d",
                tip.text,
                re.I,
            )
        ):
            continue
        seen.add(key)
        result.append(tip)
        if len(result) == limit:
            break
    return result
