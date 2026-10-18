"""Request-local utterance selection; the model never supplies quote text or time."""

import math
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from hakimi_analysis.models import SourceTip, TranscriptUtterance

SELECTION_INSTRUCTIONS = """
实验合同：utterance-selection.v1。
本次语音提醒采用原句编号选择合同，取代 tips 的 text/evidence 输出格式。
tips 每项只能返回 {utterance_id, category}，选择本次 transcript.utterances 中已有 id。
不要输出提醒文字或时间，程序将原样回填完整原句及来源时间；不要编造编号。
仍只选择完整、2至80字、安全且属于当前动作区间的原句。不改写、不摘子串、
不拼接原句，不删除条件、否定、侧别或指代所需信息。相邻动作介绍可用于核对归属，
不必每句重复动作名称；但不能把缺失上下文、转场、预告或他动作提醒当成当前提醒。
动作提取与提醒选取分开：保留所有明确训练动作和要求，包括热身、放松、可选有氧；
无可靠提醒只令 tips=[]，不要删除动作或扩大其范围。所有其他动作及参数规则不变。
"""


def indexed_transcript(
    transcript: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, TranscriptUtterance]]:
    quotes: dict[str, TranscriptUtterance] = {}
    request_scope = uuid4().hex
    raw = transcript.get("utterances", [])
    if isinstance(raw, list):
        for index, item in enumerate(raw, 1):
            try:
                utterance = TranscriptUtterance.model_validate(item)
            except ValidationError:
                continue
            if (
                math.isfinite(utterance.start_seconds)
                and math.isfinite(utterance.end_seconds)
                and utterance.start_seconds < utterance.end_seconds
            ):
                quotes[f"{request_scope}-utterance-{index}"] = utterance
    return {
        **transcript,
        "utterances": [
            {"id": quote_id, **utterance.model_dump(exclude={"words"})}
            for quote_id, utterance in quotes.items()
        ],
    }, quotes


def restore_selected_quotes(payload: Any, quotes: dict[str, TranscriptUtterance]) -> Any:
    if not isinstance(payload, dict) or not isinstance(payload.get("signals"), list):
        return payload
    for signal in payload["signals"]:
        if not isinstance(signal, dict):
            continue
        selections = signal.get("tips", [])
        restored: list[dict[str, Any]] = []
        if isinstance(selections, list) and len(selections) <= 3:
            seen = set()
            for selection in selections:
                if not isinstance(selection, dict) or set(selection) != {
                    "utterance_id",
                    "category",
                }:
                    restored = []
                    break
                quote_id = selection["utterance_id"]
                if not isinstance(quote_id, str) or quote_id not in quotes or quote_id in seen:
                    restored = []
                    break
                seen.add(quote_id)
                quote = quotes[quote_id]
                try:
                    tip = SourceTip.model_validate(
                        {
                            "text": quote.text.strip(),
                            "category": selection["category"],
                            "evidence": {
                                "type": "speech",
                                "start_seconds": quote.start_seconds,
                                "end_seconds": quote.end_seconds,
                            },
                        }
                    )
                except ValidationError:
                    continue
                restored.append(tip.model_dump(mode="json"))
        signal["tips"] = restored
    return payload
