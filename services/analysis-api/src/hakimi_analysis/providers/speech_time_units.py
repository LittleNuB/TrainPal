"""Conservative repair of a dropped minute unit, using timestamped ASR only."""

import re
from typing import Any

from hakimi_analysis.models import SpeechUnderstandingResult

# Match a complete, affirmative short statement rather than mining a time out
# of arbitrary prose. Unsupported wording is intentionally not repaired.
_AFFIRMATIVE_PREFIX = (
    r"(?:(?:训练|练)后)?(?:可|可以)?(?:再)?(?:加|做|进行)?\s*"
    r"|晚可以加\s*"
)
_REST_CONTEXT = re.compile(r"休息|间歇|组间|(?<![a-z])rest(?![a-z])", re.IGNORECASE)


def reconcile_speech_time_units(
    result: SpeechUnderstandingResult, transcript: dict[str, Any]
) -> SpeechUnderstandingResult:
    utterances = transcript.get("utterances", [])
    if not isinstance(utterances, list):
        return result
    signals = []
    for signal in result.signals:
        texts = []
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            start, end = utterance.get("start_seconds"), utterance.get("end_seconds")
            text = utterance.get("text")
            if (
                isinstance(start, (int, float))
                and isinstance(end, (int, float))
                and start < end
                and start >= signal.start_seconds
                and end <= signal.end_seconds
                and isinstance(text, str)
            ):
                texts.append(text)
        text = " ".join(texts)
        if (
            signal.duration_seconds is not None
            and signal.reps is None
            and signal.rest_seconds is None
            and not _REST_CONTEXT.search(text)
        ):
            action = re.escape(signal.action_name)
            minute_statement = re.fullmatch(
                rf"\s*(?:{_AFFIRMATIVE_PREFIX})(?P<value>[0-9]+)\s*分钟\s*"
                rf"(?:的)?{action}[。.!！]?\s*",
                text,
            )
            if (
                minute_statement is not None
                and signal.duration_seconds == int(minute_statement.group("value"))
            ):
                signal = signal.model_copy(
                    update={"duration_seconds": signal.duration_seconds * 60}
                )
        signals.append(signal)
    return result.model_copy(update={"signals": signals})
