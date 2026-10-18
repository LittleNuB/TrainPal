"""An opt-in wire expression; existing source and ownership rules still apply."""

from copy import deepcopy
from typing import Any

from hakimi_analysis.visual_evidence import CaptionDecision


def purpose_specific_contract(
    schema: dict[str, Any],
    instructions: str,
) -> tuple[dict[str, Any], str]:
    schema = deepcopy(schema)
    original = schema["$defs"]["CaptionDecision"]["anyOf"]
    inactive = original[0]
    active = original[1] if len(original) == 2 else None
    variants = []
    for purpose in ("context", "uncertain", "training_target", "load_selection_condition", "tip"):
        is_inactive = purpose in {"context", "uncertain"}
        base = inactive if is_inactive else active
        if base is None:
            continue
        fields = ["caption_id", "purpose"]
        if not is_inactive:
            fields += ["action_id", "ownership", "support_ids"]
        if purpose == "training_target":
            fields += ["parameters", "category"]
        elif purpose == "tip":
            fields += ["category"]
        props = {key: deepcopy(base["properties"][key]) for key in fields}
        props["purpose"] = {"type": "string", "enum": [purpose]}
        if purpose == "training_target":
            props["parameters"] = {
                "allOf": [
                    {"$ref": "#/$defs/CandidateParameters"},
                    {
                        "anyOf": [
                            {"properties": {key: {"type": "integer"}}, "required": [key]}
                            for key in (
                                "sets",
                                "reps",
                                "reps_max",
                                "duration_seconds",
                                "rest_seconds",
                            )
                        ]
                    },
                ]
            }
        elif purpose == "tip":
            props["category"] = {
                "type": "string",
                "enum": ["setup", "path", "breathing", "rhythm", "caution"],
            }
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": props,
                "required": fields,
            }
        )
    schema["$defs"]["CaptionDecision"] = {"anyOf": variants}
    replacements = {
        "或矛盾则unknown。": "或矛盾时判为purpose=uncertain，不输出有效归属判断。",
        "填uncertain/unknown": "输出purpose=uncertain",
        "context/uncertain须action_id=null、ownership=unknown、support_ids=[]。": (
            "context/uncertain只返回caption_id和purpose，不填写动作归属或其它字段。"
        ),
        "其它purpose的category=null。": "其它purpose不返回category字段。",
    }
    for old, new in replacements.items():
        if old not in instructions:
            raise ValueError("caption_wire_instructions_mismatch")
        instructions = instructions.replace(old, new)
    marker = "字段约束：context/uncertain不生成参数或提醒，即使看得出字幕在说哪个动作，"
    if marker not in instructions:
        raise ValueError("caption_wire_instructions_mismatch")
    instructions = (
        instructions[: instructions.index(marker)]
        + """输出采用按用途区分的结构：
context/uncertain只含caption_id、purpose，即使能看出它在说哪个动作也不要增加其它字段。
load_selection_condition含caption_id、purpose、action_id、ownership、support_ids，没有parameters/category。
tip在有效归属字段之外带非空category，不带parameters。
training_target带有效归属字段、parameters（至少一个有根据的数值）、category（无技术提醒时null）。
无法可靠定位本次动作、归属矛盾或缺少支持引用时，该字幕输出purpose=uncertain。
格式示例：{"caption_id":"当前字幕ID","purpose":"uncertain"}；示例中的caption_id须替换为当前输入的真实字幕ID。
不要输出有效用途搭配ownership=unknown、空support_ids或空目标，也不能为满足格式猜支持或换目标。
这只改变输出表达，不改变归属、参数、提醒安全规则。上下文字幕仍可被其它判断引用作支持。
"""
    )
    return schema, instructions


def restore_purpose_specific_decisions(decoded: dict[str, Any]) -> dict[str, Any]:
    """Called only after wire validation. Never discard unexpected model fields."""
    return {
        **decoded,
        "decisions": [
            {
                "action_id": None,
                "ownership": "unknown",
                "support_ids": [],
                "parameters": None,
                "category": None,
                **decision,
            }
            for decision in decoded["decisions"]
        ],
    }


def contradictory_tip_receiving_schema(
    schema: dict[str, Any], *, action_ids: list[str]
) -> dict[str, Any]:
    """Receive locatable tip contradictions for quarantine, never as valid ownership.

    This schema is NOT sent to the model. All non-ownership fields remain strict;
    restore() must reject the contradictory row locally after integrity checks.
    """
    schema = deepcopy(schema)
    variants = schema["$defs"]["CaptionDecision"]["anyOf"]
    fields = ["caption_id", "purpose", "action_id", "ownership", "support_ids", "category"]
    base = CaptionDecision.model_json_schema()["properties"]
    props = {key: deepcopy(base[key]) for key in fields}
    props["caption_id"] = deepcopy(variants[0]["properties"]["caption_id"])
    props["purpose"] = {"type": "string", "enum": ["tip"]}
    props["action_id"] = {"type": "string", "enum": action_ids}
    props["support_ids"]["minItems"] = 1
    props["support_ids"]["items"]["enum"] = props["caption_id"]["enum"]
    props["category"] = next(p for p in base["category"]["anyOf"] if p.get("type") == "string")
    variants.append(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": fields,
        }
    )
    return schema
