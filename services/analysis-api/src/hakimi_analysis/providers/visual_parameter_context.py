"""Default-off repetition-purpose contract; caption readings stay request-local."""

from typing import Literal

from pydantic import Field, field_validator

from hakimi_analysis.models import Segment, StrictModel, VisualLocalizationResult, VisualSegment
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError

_BASE_PARAMETER_RULES = """\
- Read explicit training instructions displayed on screen. `text_parameters`
  may contain ONLY readable instructions attached to this exercise: `mode`,
  `sets`, `reps`, `reps_max`, `duration_seconds`, `rest_seconds`. Missing values
  stay null. For a repetitions range, reps is its lower bound and reps_max its
  upper bound (e.g. 8-12 repetitions is 8 and 12); never silently choose one value.
  An exact repetition count has reps_max null. Mode is reps or duration when
  supported by the instruction. If no readable instruction exists, use null.
  Do not infer these values from repetitions performed, clip length, a countdown
  without training context, model knowledge, promotional claims or creator load.
  Keep only a short observable cue, not full subtitles or account information.
"""

PARAMETER_CONTEXT_INSTRUCTIONS = """
- 本次启用次数用途合同：text_parameters 只填当前动作字幕明确的实际训练安排。
  选重量条件只记录在 repetition_context，不把条件里的次数填入 text_parameters。
  reps/reps_max 为实际安排的次数下界/上界，精确次数的 reps_max=null；有次数安排时 mode="reps"，
  duration_seconds=null；有时长安排时 mode="duration"，reps/reps_max=null。未知字段用 null。
  不能因选重量次数删掉真实训练时长、组数或休息。例如选能做8–10次的重量且每组持续30秒，
  参数保留 mode="duration"、duration_seconds=30，次数为空，用途上下文记录选重量原句。
  不从实际演示数量、片段长度、无训练含义倒数、角度、技术步骤、举例或常识推算参数。
- 每个观察包含 repetition_context。读取到次数时给出 purpose、caption、segment：purpose 为
  training_target（实际安排）、load_selection_condition（选重量能力条件）或 uncertain（用途不明）；
  caption 是保留条件和否定的完整必要字幕短句（2–120字符），segment 是字幕在本观察内的正长度时间。
  没有次数相关字幕时 repetition_context=null，其他有依据的参数保留。
  用途不明如实填 uncertain，不强行归类；字幕和用途均待核验，不提供自报置信度。
  ‘选择每组能完成8到10次的重量’是 load_selection_condition，次数只保留在原句中；
  ‘每组做8到12次，选择适合自己的重量’是 training_target，不能按重量关键词过滤。
  同一动作同时有选重量条件和明确次数安排时，context 引用实际安排，参数不得混入选重量次数。
  多个明确训练要求保留独立来源观察，不以后文覆盖前文或用范围包含关系消除冲突。
  不改变动作、编号、侧别、分段、角色和最多三条提醒的规则。visual_cue 保持简短，不含账号信息。
- repetition_context 只能是单个对象或 null，不能返回数组（包括空数组或单元素数组），
  也不能只返回用途字符串。对象必须同时有 purpose、caption 和 segment；segment 是包含
  start_seconds、end_seconds 的对象，不是时间数组，与当前观察及请求 time_rule 使用同一时间坐标。
  以下只是单个观察内该字段的结构示例，不是视频证据；不要复制示例字幕或时间：
```json
{
  "repetition_context": {
    "purpose": "training_target",
    "caption": "每组做六次",
    "segment": {"start_seconds": 2, "end_seconds": 4}
  }
}
```
"""


def repetition_context_instructions(base: str) -> str:
    """Replace the bundled parameter meaning instead of appending a second one."""
    if _BASE_PARAMETER_RULES in base:
        before, _, after = base.partition(_BASE_PARAMETER_RULES)
        before = before.replace(
            '"text_parameters": null\n',
            '"text_parameters": null,\n      "repetition_context": null\n',
            1,
        )
        return before + PARAMETER_CONTEXT_INSTRUCTIONS + after
    if "- Read explicit training instructions displayed on screen." in base:
        raise ProviderError(
            "configuration_error", "视觉次数用途试点与基础参数规则版本不兼容", retryable=False
        )
    return base + PARAMETER_CONTEXT_INSTRUCTIONS


class VisualParameterContext(StrictModel):
    purpose: Literal["training_target", "load_selection_condition", "uncertain"]
    caption: str = Field(min_length=2, max_length=120)
    segment: Segment

    @field_validator("caption")
    @classmethod
    def require_readable_caption(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("parameter caption must contain readable text")
        return value


class ContextualVisualSegment(VisualSegment):
    repetition_context: VisualParameterContext | None


class ContextualVisualLocalizationResult(StrictModel):
    segments: list[ContextualVisualSegment]


def project_visual_parameters(
    result: ContextualVisualLocalizationResult,
) -> VisualLocalizationResult:
    segments = []
    for item in result.segments:
        context = item.repetition_context
        has_values = item.text_parameters is not None and (
            item.text_parameters.reps is not None or item.text_parameters.reps_max is not None
        )
        if has_values and context is None:
            raise ProviderSchemaError("视觉参数缺少用途与字幕出处")
        if context is not None:
            if not (
                item.start_seconds
                <= context.segment.start_seconds
                < context.segment.end_seconds
                <= item.end_seconds
            ):
                raise ProviderSchemaError("视觉参数字幕超出动作范围")
            if has_values and context.purpose == "uncertain":
                raise ProviderSchemaError("视觉参数用途尚未确定")
        payload = item.model_dump(exclude={"repetition_context"})
        if has_values and context is not None and context.purpose == "load_selection_condition":
            parameters = payload["text_parameters"]
            parameters["reps"] = parameters["reps_max"] = None
            if parameters["mode"] == "reps":
                parameters["mode"] = None
        segments.append(VisualSegment.model_validate(payload))
    return VisualLocalizationResult(segments=segments)
