"""Default-off clarification of ordinal transcription; never rewrites evidence."""

from hakimi_analysis.providers.base import ProviderError

# The known 1.6.1 paragraph is replaced atomically so later safeguards cannot disappear.
_ORIGINAL_RULE = """- `sequence_label` is a transcription of an explicitly visible exercise heading,
  NOT a position in your output array. This clip can begin halfway through an
  exercise: never number its segments 1, 2, 3 by appearance, restart numbering
  at the clip boundary, or infer a missing number from neighbouring exercises.
  Copy a readable exercise ordinal exactly, including any side/round/variation
  qualifier. If no exercise ordinal is visible in this segment, use null.
  Repetition counts, set counts, countdowns and technique-step numbers are not
  exercise ordinals. A null ordinal does not mean the exercise should be omitted.
"""

ORDINAL_RULE = """- `sequence_label` records the exercise ordinal, not the full action title.
  Copy only the explicitly visible exercise ordinal together with all attached
  side, round, or variation qualifiers. Put the complete readable heading in visual_cue
  and its action name in action_name; do not append an unqualified action name to
  the ordinal. Never strip a qualifier to obtain a pure number. If a qualifier is
  attached to the title (left/right, second round, grip/variant), keep it in both
  sequence_label and action_name, even when the label is no longer a pure ordinal.
  If the ordinal and qualifying text cannot be separated reliably, keep the full
  visible label; do not guess or silently drop a qualifier.
  This is a transcription, NOT a position in the output array. Never restart
  numbering at a clip boundary or infer a missing number from neighbouring actions.
  If no exercise ordinal is visible, use null. Repetition counts, set counts,
  countdowns and technique-step numbers are not exercise ordinals.
  For example, visible “动作六：深蹲” gives sequence_label “动作六”,
  action_name “深蹲”, with the full heading retained in visual_cue.
  Visible “动作六：左侧弓步” must retain “左侧” in sequence_label;
  visible “动作九 第2轮” must retain “第2轮”. Null never means omit the action.
"""


def clarify_ordinal_instructions(instructions: str) -> str:
    before, marker, rest = instructions.partition("- `sequence_label` is a transcription")
    old, end, after = rest.partition("- Merge only")
    if not end or marker + old != _ORIGINAL_RULE or instructions.count(_ORIGINAL_RULE) != 1:
        raise ProviderError("configuration_error", "视觉编号说明格式不兼容", retryable=False)
    return before + ORDINAL_RULE + end + after
