"""Bounded, source-labelled tips; not a knowledge generation or medical feature."""

import re
from collections.abc import Iterable

from hakimi_analysis.models import SourceTip


def compact_text(text: str) -> str:
    return "".join(character for character in text if character.isalnum())


def safe_tips(tips: Iterable[SourceTip]) -> list[SourceTip]:
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
        if len(result) == 3:
            break
    return result
