"""Content-free, bounded diagnostic vocabulary; never model-supplied strings."""

from typing import Literal, get_args

FusionDiagnosticCode = Literal[
    "group_rejected",
    "model_uncertain",
    "content_role_rejected",
    "sequence_label_conflict",
    "time_alignment_rejected",
    "visual_count_insufficient",
    "visual_role_not_teaching",
    "follow_along_present",
    "visual_chain_disconnected",
    "external_visual_boundary",
    "speech_outside_visual",
    "common_intersection_accepted",
    "continuous_teaching_accepted",
    "parameter_conflict_groups",
    "non_training_excluded",
    "reference_attached",
    "reference_rejected",
    "reference_target_unresolved",
    "reference_uncertain",
    "reference_role_protected",
    "reference_label_conflict",
    "reference_parameter_conflict",
    "reference_direction_conflict",
    "unavailable_input_budget",
    "unavailable_time_budget",
    "unavailable_timeout",
    "unavailable_provider",
    "unavailable_membership",
    "unavailable_reference_target",
    "no_observations",
]

FUSION_DIAGNOSTIC_CODES = frozenset(get_args(FusionDiagnosticCode))
