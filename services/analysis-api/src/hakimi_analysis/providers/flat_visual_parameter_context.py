"""Explicit flat wire contract; not a repair path for invalid nested responses."""

import copy
import json
from typing import Any, NoReturn

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.providers.visual_parameter_context import PARAMETER_CONTEXT_INSTRUCTIONS

_FIELDS = (
    "repetition_purpose",
    "repetition_caption",
    "repetition_start_seconds",
    "repetition_end_seconds",
)

_BASE_TIP_RULES = """\
- Extract at most three short action `tips` ONLY from clearly readable captions,
  not inferred posture or world knowledge. Each has `text` (2–80 characters,
  faithful complete caption excerpt), `category` (setup/path/breathing/rhythm/caution),
  and `evidence` {type:"visual",start_seconds,end_seconds} in clip-local time
  inside this action segment where the caption is visible. Keep negation and
  conditions; no medical, pain, rehabilitation, load or effectiveness advice.
  Unknown or unreadable captions mean []; do not fabricate tips.
"""


def flat_visual_contract(
    schema: dict[str, Any], instructions: str, *, bind_visual_source: bool = False
) -> tuple[dict[str, Any], str]:
    """Change only the purpose representation, preserving the caller's suffix."""
    schema = copy.deepcopy(schema)
    definitions = schema["$defs"]
    item = definitions["ContextualVisualSegment"]
    context = definitions.pop("VisualParameterContext")["properties"]
    segment = definitions["Segment"]["properties"]
    item["properties"].pop("repetition_context")
    for name, value in zip(
        _FIELDS,
        (context["purpose"], context["caption"], segment["start_seconds"], segment["end_seconds"]),
        strict=True,
    ):
        item["properties"][name] = {"anyOf": [copy.deepcopy(value), {"type": "null"}]}
    item["required"] = list(item["properties"])
    # Keep the unused Segment definition for parity with the measured wire contract.
    before, found, after = instructions.partition(PARAMETER_CONTEXT_INSTRUCTIONS)
    if not found or PARAMETER_CONTEXT_INSTRUCTIONS in after:
        raise ProviderError("configuration_error", "视觉平铺用途合同版本不兼容", retryable=False)
    before = before.replace(
        '"repetition_context": null',
        ",\n      ".join(f'"{name}": null' for name in _FIELDS),
        1,
    )
    semantic = PARAMETER_CONTEXT_INSTRUCTIONS.split(
        "- repetition_context 只能是单个对象或 null", 1
    )[0]
    replacements = {
        "选重量条件只记录在 repetition_context": "选重量条件只记录在下面四个用途字段",
        (
            "每个观察包含 repetition_context。读取到次数时给出 purpose、caption、segment："
            "purpose 为"
        ): "每个观察包含四个平铺用途字段。读取到次数时 repetition_purpose 为",
        (
            "caption 是保留条件和否定的完整必要字幕短句（2–120字符），"
            "segment 是字幕在本观察内的正长度时间。"
        ): (
            "repetition_caption 是保留条件和否定的完整必要字幕短句（2–120字符）；\n"
            "  repetition_start_seconds、repetition_end_seconds 是字幕在本观察内的正长度时间。"
        ),
        "没有次数相关字幕时 repetition_context=null": "没有次数相关字幕时四个用途字段全部为 null",
        "context 引用实际安排": "repetition_caption 引用实际安排",
    }
    for old, new in replacements.items():
        if semantic.count(old) != 1:
            raise ProviderError(
                "configuration_error", "视觉平铺用途合同版本不兼容", retryable=False
            )
        semantic = semantic.replace(old, new, 1)
    instructions = (
        before
        + semantic
        + """
- 四个用途字段直接放在每个观察对象内；有上下文时四个都必须非 null，无上下文时四个全部为 null。
  不能省略、返回数组、嵌套 repetition_context 对象或只填其中一部分。
  时间与当前观察及请求 time_rule 使用同一时间坐标，不能超过本观察范围。
  以下只是观察内四个字段的结构示例，不是视频证据；不要复制示例字幕或时间：
```json
{
  "repetition_purpose": "training_target",
  "repetition_caption": "每组做六次",
  "repetition_start_seconds": 2,
  "repetition_end_seconds": 4
}
```
"""
        + after
    )
    if bind_visual_source:
        old = 'and `evidence` {type:"visual",start_seconds,end_seconds} in clip-local time'
        if (
            instructions.count(_BASE_TIP_RULES) != 1
            or instructions.count("- Extract at most three short action `tips`") != 1
            or instructions.count(old) != 1
        ):
            raise ProviderError(
                "configuration_error", "视觉来源绑定合同版本不兼容", retryable=False
            )
        bound_tip_rules = _BASE_TIP_RULES.replace(
            old,
            "and `evidence` {start_seconds,end_seconds} in clip-local time. Do not return "
            "evidence.type: source type is assigned by this video-only Provider branch, "
            "including frame sheets. This does not verify caption accuracy or ownership. "
            "The complete caption and its actual start/end times must still be provided",
            1,
        )
        instructions = instructions.replace(_BASE_TIP_RULES, bound_tip_rules, 1)
        evidence = definitions["EvidenceSpan"]
        evidence["properties"].pop("type")
        evidence["required"] = list(evidence["properties"])
    return schema, instructions


def restore_flat_context(decoded: dict[str, Any]) -> dict[str, Any]:
    """Translate the declared representation; original business validation still follows."""
    result = copy.deepcopy(decoded)
    for item in result["segments"]:
        values = [item.pop(name) for name in _FIELDS]
        if all(value is None for value in values):
            context = None
        elif any(value is None for value in values):
            raise ValueError("flat_context_partial")
        else:
            context = {
                "purpose": values[0],
                "caption": values[1],
                "segment": {"start_seconds": values[2], "end_seconds": values[3]},
            }
        item["repetition_context"] = context
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("flat_visual_non_json_number")


def decode_flat_context(
    text: str, schema: dict[str, Any], *, bind_visual_source: bool = False
) -> dict[str, Any]:
    """Validate the actual wire before mapping; never use coercion as model correctness."""
    decoded = json.loads(text, parse_constant=_reject_constant)
    try:
        Draft202012Validator(schema).validate(decoded)
    except JsonSchemaValidationError:
        # Do not attach a validator exception containing model content.
        raise ValueError("flat_visual_schema_invalid") from None
    result = restore_flat_context(decoded)
    if bind_visual_source:
        for item in result["segments"]:
            for tip in item["tips"]:
                tip["evidence"]["type"] = "visual"
    return result


def validate_flat_response_metadata(
    payload: dict[str, Any], response_format: dict[str, Any]
) -> None:
    refused = any(
        part.get("type") == "refusal"
        for item in payload.get("output", [])
        if isinstance(item, dict)
        for part in item.get("content", [])
        if isinstance(part, dict)
    )
    if (
        payload.get("status") != "completed"
        or refused
        or payload.get("error")
        or payload.get("incomplete_details")
        or payload.get("store") is not False
        or payload.get("text") != {"format": response_format}
    ):
        raise ValueError("flat_visual_response_unassessable")
