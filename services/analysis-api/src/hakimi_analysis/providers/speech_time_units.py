"""Conservative repair of a dropped minute unit, using timestamped ASR only."""

import re
from typing import Any

from hakimi_analysis.models import SpeechUnderstandingResult

_TIME = re.compile(r"(?P<value>\d+)\s*(?P<unit>分钟|秒钟|秒)")
_AMBIGUOUS_TIME_CONTEXT = re.compile(
    r"休息|间歇|组间|不|别|以内|以上|以下|超过|左右|大约|约|到|至|"
    r"小时|视频|片段|录像|讲解|分钟\s*[前后]|[.~～—-]"
)


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
        mentions = list(_TIME.finditer(text))
        if (
            signal.duration_seconds is not None
            and signal.reps is None
            and signal.rest_seconds is None
            and len(mentions) == 1
            and not _AMBIGUOUS_TIME_CONTEXT.search(text)
        ):
            mention = mentions[0]
            value = int(mention.group("value"))
            # Require a direct lexical link, not merely a time somewhere in the
            # same utterance. Unknown aliases are deliberately left unchanged.
            before = text[: mention.start()].rstrip()
            after = text[mention.end() :].lstrip()
            action = re.escape(signal.action_name)
            linked = bool(
                re.match(rf"(?:的)?{action}(?![\w])", after)
                or re.search(rf"{action}(?:做|持续|保持|进行)?\s*$", before)
            )
            if (
                linked
                and mention.group("unit") == "分钟"
                and signal.duration_seconds == value
            ):
                signal = signal.model_copy(update={"duration_seconds": value * 60})
        signals.append(signal)
    return result.model_copy(update={"signals": signals})
