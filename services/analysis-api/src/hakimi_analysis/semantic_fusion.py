"""Text-only semantic grouping; source values remain owned by deterministic code."""

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Literal, Protocol

from pydantic import Field, ValidationError

from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.fusion_diagnostics import FusionDiagnosticCode
from hakimi_analysis.models import (
    AnalysisCandidate,
    AnalysisWarning,
    CandidateParameters,
    ParameterAlternative,
    ParameterConflict,
    SegmentRole,
    SourceTip,
    SpeechSignal,
    StrictModel,
    VisualSegment,
)
from hakimi_analysis.observability import log_safe_fields
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.source_tips import safe_tips


class SemanticGroup(StrictModel):
    member_ids: list[str] = Field(min_length=1)
    name: str | None = Field(max_length=120)
    relation: Literal["same_demonstration", "uncertain"]
    content_role: Literal[
        "exercise", "preview", "recap", "equipment_setup", "non_training_gesture", "uncertain"
    ] = "exercise"
    related_member_id: str | None = None
    accepted_tip_ids: list[str] = Field(default_factory=list, max_length=3)


class SemanticGrouping(StrictModel):
    groups: list[SemanticGroup]


class SemanticGroupingModel(Protocol):
    async def group_action_evidence(
        self,
        *,
        observations: list[dict[str, object]],
        instructions: str,
    ) -> SemanticGrouping: ...


@dataclass
class SemanticFusionResult:
    candidates: list[AnalysisCandidate]
    warnings: list[AnalysisWarning]
    diagnostics: dict[FusionDiagnosticCode, int] = dataclass_field(default_factory=dict)


@dataclass
class _Observation:
    id: str
    candidate: AnalysisCandidate
    role: SegmentRole
    sequence_label: str | None
    description: str

    def tip_evidence(self) -> dict[str, SourceTip]:
        return {f"{self.id}-tip-{index}": tip for index, tip in enumerate(self.candidate.tips, 1)}

    def model_input(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.candidate.name,
            "source_type": self.candidate.evidence[0].type.value,
            "segment": self.candidate.segment.model_dump(),
            "parameters": self.candidate.parameters.model_dump(exclude_none=True),
            "role": self.role.value,
            "sequence_label": self.sequence_label,
            "description": self.description,
            "tip_evidence": [
                {"id": tip_id, **tip.model_dump(mode="json")}
                for tip_id, tip in self.tip_evidence().items()
            ],
        }


def _observations(
    source_id: str,
    speech: list[SpeechSignal],
    visual: list[VisualSegment],
) -> list[_Observation]:
    result = []
    for index, signal in enumerate(speech, 1):
        if signal.is_training_content:
            candidate = fuse_candidates(
                source_id=source_id,
                speech_signals=[signal],
                visual_segments=[],
            )[0]
            result.append(_Observation(f"speech-{index}", candidate, signal.segment_role, None, ""))
    for index, segment in enumerate(visual, 1):
        if segment.is_training_content:
            candidate = fuse_candidates(
                source_id=source_id,
                speech_signals=[],
                visual_segments=[segment],
            )[0]
            result.append(
                _Observation(
                    f"visual-{index}",
                    candidate,
                    segment.segment_role,
                    segment.sequence_label,
                    segment.visual_cue[:600],
                )
            )
    return result


class SemanticCandidateFusion:
    def __init__(
        self, *, model: SemanticGroupingModel, instructions: str, timeout_seconds: float = 20
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("semantic fusion timeout must be positive")
        self._model = model
        self._instructions = instructions
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        *,
        source_id: str,
        speech_signals: list[SpeechSignal],
        visual_segments: list[VisualSegment],
        remaining_seconds: float | None = None,
    ) -> SemanticFusionResult:
        def finish(result: SemanticFusionResult) -> SemanticFusionResult:
            log_safe_fields(
                logging.getLogger(__name__),
                source_id=source_id,
                stage="fusing_candidates",
                fusion_diagnostics=result.diagnostics,
            )
            return result

        observations = _observations(source_id, speech_signals, visual_segments)
        if not observations:
            return finish(SemanticFusionResult([], [], {"no_observations": 1}))
        model_input = [item.model_input() for item in observations]
        if (
            len(observations) > 200
            or len(json.dumps(model_input, ensure_ascii=False).encode("utf-8")) > 64_000
        ):
            return finish(_unavailable(observations, "unavailable_input_budget"))
        budget = (
            min(self._timeout_seconds, remaining_seconds)
            if remaining_seconds is not None
            else self._timeout_seconds
        )
        if budget <= 0:
            return finish(_unavailable(observations, "unavailable_time_budget"))
        try:
            async with asyncio.timeout(budget):
                grouping = await self._model.group_action_evidence(
                    observations=model_input,
                    instructions=self._instructions,
                )
        except TimeoutError:
            return finish(_unavailable(observations, "unavailable_timeout"))
        except ProviderError:
            return finish(_unavailable(observations, "unavailable_provider"))
        by_id = {item.id: item for item in observations}
        if Counter(item_id for group in grouping.groups for item_id in group.member_ids) != Counter(
            by_id.keys()
        ):
            return finish(_unavailable(observations, "unavailable_membership"))
        owners = {item_id: group for group in grouping.groups for item_id in group.member_ids}
        for group in grouping.groups:
            if group.content_role in {"preview", "recap"}:
                target_group = owners.get(group.related_member_id or "")
                if target_group is None or target_group.content_role != "exercise":
                    return finish(_unavailable(observations, "unavailable_reference_target"))
            elif group.related_member_id is not None:
                return finish(_unavailable(observations, "unavailable_reference_target"))
        candidates: list[AnalysisCandidate] = []
        warnings: list[AnalysisWarning] = []
        resolved: dict[str, AnalysisCandidate] = {}
        diagnostics: Counter[FusionDiagnosticCode] = Counter()

        def retain_pending(members: list[_Observation]) -> None:
            candidates.extend(
                item.candidate.model_copy(update={"needs_confirmation": True, "tips": []})
                for item in members
            )
            if not warnings:
                warnings.append(
                    AnalysisWarning(
                        code="semantic_fusion_conflict",
                        message="部分候选存在动作、时间或参数冲突，已分别保留，请核对后确认",
                    )
                )

        for group in grouping.groups:
            if group.content_role in {"preview", "recap"}:
                continue
            members = [by_id[item_id] for item_id in group.member_ids]
            if (
                group.content_role in {"equipment_setup", "non_training_gesture"}
                and group.relation == "same_demonstration"
                and _can_reclassify(members)
            ):
                diagnostics["non_training_excluded"] += 1
                continue
            parameters, conflicts = _resolve_parameters(members)
            simultaneous = max(item.candidate.segment.start_seconds for item in members) < min(
                item.candidate.segment.end_seconds for item in members
            )
            labels = {
                _sequence_identity(item.sequence_label)
                for item in members
                if item.sequence_label is not None
            }
            reasons: set[FusionDiagnosticCode] = set()
            if group.relation == "uncertain":
                reasons.add("model_uncertain")
            if group.content_role != "exercise":
                reasons.add("content_role_rejected")
            if not simultaneous:
                time_reasons = _continuous_teaching_rejections(members, observations)
                if time_reasons:
                    reasons.update(time_reasons)
                    reasons.add("time_alignment_rejected")
            if len(labels) > 1:
                reasons.add("sequence_label_conflict")
            if reasons:
                diagnostics["group_rejected"] += 1
                diagnostics.update(reasons)
                retain_pending(members)
                continue
            diagnostics[
                "common_intersection_accepted" if simultaneous else "continuous_teaching_accepted"
            ] += 1
            if conflicts:
                diagnostics["parameter_conflict_groups"] += 1
            reference = max(
                (item for item in members if item.id.startswith("visual-")),
                key=lambda item: (
                    item.candidate.segment.end_seconds - item.candidate.segment.start_seconds,
                    -item.candidate.segment.start_seconds,
                ),
                default=members[0],
            )
            evidence = [span for member in members for span in member.candidate.evidence]
            roles = {item.role for item in members} - {SegmentRole.UNKNOWN}
            name = _chinese_name(group.name)
            source_name = next(
                (
                    value
                    for item in [reference, *members]
                    if (value := _chinese_name(item.candidate.name))
                ),
                reference.candidate.name,
            )
            candidates.append(
                reference.candidate.model_copy(
                    update={
                        "name": name or source_name,
                        "parameters": parameters,
                        "parameter_conflicts": conflicts,
                        "playback_options": [
                            option
                            for member in members
                            for option in member.candidate.playback_options
                        ][:12],
                        "evidence": evidence,
                        "tips": _accepted_tips(group, members),
                        "needs_confirmation": (
                            bool(conflicts)
                            or not name
                            or len({span.type for span in evidence}) < 2
                            or len(roles) != 1
                        ),
                    }
                )
            )
            if conflicts and not warnings:
                warnings.append(
                    AnalysisWarning(
                        code="semantic_fusion_conflict",
                        message="同一动作的部分参数存在不同说法，已保留来源，请核对后确认",
                    )
                )
            for item_id in group.member_ids:
                resolved[item_id] = candidates[-1]
        for group in grouping.groups:
            if group.content_role not in {"preview", "recap"}:
                continue
            members = [by_id[item_id] for item_id in group.member_ids]
            target_id = group.related_member_id or ""
            target = resolved.get(target_id)
            primary = [by_id[item_id] for item_id in owners[target_id].member_ids]
            reference_reasons = _reference_rejections(group, members, primary)
            if target is None:
                reference_reasons.add("reference_target_unresolved")
            if target is not None and not reference_reasons:
                target.evidence.extend(span for item in members for span in item.candidate.evidence)
                diagnostics["reference_attached"] += 1
            else:
                diagnostics["reference_rejected"] += 1
                diagnostics.update(reference_reasons)
                retain_pending(members)
        candidates.sort(key=lambda item: item.segment.start_seconds)
        return finish(
            SemanticFusionResult(
                [
                    item.model_copy(update={"id": f"candidate-{index}"})
                    for index, item in enumerate(candidates, 1)
                ],
                warnings,
                dict(diagnostics),
            )
        )


def _accepted_tips(group: SemanticGroup, members: list[_Observation]) -> list[SourceTip]:
    available = {
        tip_id: tip
        for member in members
        for tip_id, tip in member.tip_evidence().items()
        if tip.evidence.type == member.candidate.evidence[0].type
        and member.candidate.segment.start_seconds
        <= tip.evidence.start_seconds
        < tip.evidence.end_seconds
        <= member.candidate.segment.end_seconds
    }
    if len(set(group.accepted_tip_ids)) != len(group.accepted_tip_ids) or any(
        tip_id not in available for tip_id in group.accepted_tip_ids
    ):
        return []
    return safe_tips(available[tip_id] for tip_id in group.accepted_tip_ids)


def _continuous_teaching_rejections(
    members: list[_Observation], observations: list[_Observation]
) -> set[FusionDiagnosticCode]:
    # ADR-0055: only explicit teaching visuals can bridge a missing common intersection.
    visual = sorted(
        (item for item in members if item.id.startswith("visual-")),
        key=lambda item: item.candidate.segment.start_seconds,
    )
    reasons: set[FusionDiagnosticCode] = set()
    if len(visual) < 2:
        reasons.add("visual_count_insufficient")
    if any(item.role != SegmentRole.TEACHING_DEMO for item in visual):
        reasons.add("visual_role_not_teaching")
    if any(item.role == SegmentRole.FOLLOW_ALONG for item in members):
        reasons.add("follow_along_present")
    if not visual:
        return reasons
    covered_end = visual[0].candidate.segment.end_seconds
    for item in visual[1:]:
        segment = item.candidate.segment
        if segment.start_seconds >= covered_end:
            reasons.add("visual_chain_disconnected")
        covered_end = max(covered_end, segment.end_seconds)
    member_ids = {item.id for item in members}
    start = visual[0].candidate.segment.start_seconds
    # A separate visual observation is a boundary even if its identity is uncertain.
    if any(
        item.id.startswith("visual-")
        and item.id not in member_ids
        and item.candidate.segment.start_seconds < covered_end
        and start < item.candidate.segment.end_seconds
        for item in observations
    ):
        reasons.add("external_visual_boundary")
    if not all(
        any(
            item.candidate.segment.start_seconds < segment.candidate.segment.end_seconds
            and segment.candidate.segment.start_seconds < item.candidate.segment.end_seconds
            for segment in visual
        )
        for item in members
        if item.id.startswith("speech-")
    ):
        reasons.add("speech_outside_visual")
    return reasons


def _reference_rejections(
    group: SemanticGroup, members: list[_Observation], primary: list[_Observation]
) -> set[FusionDiagnosticCode]:
    reasons: set[FusionDiagnosticCode] = set()
    if group.relation != "same_demonstration":
        reasons.add("reference_uncertain")
    if not _can_reclassify(members):
        reasons.add("reference_role_protected")
    labels = {
        _sequence_identity(item.sequence_label)
        for item in [*members, *primary]
        if item.sequence_label is not None
    }
    if len(labels) > 1:
        reasons.add("reference_label_conflict")
    if _compatible_parameters([*primary, *members]) is None:
        reasons.add("reference_parameter_conflict")
    if group.content_role == "preview":
        direction_valid = max(item.candidate.segment.end_seconds for item in members) <= min(
            item.candidate.segment.start_seconds for item in primary
        )
    else:
        direction_valid = min(item.candidate.segment.start_seconds for item in members) >= max(
            item.candidate.segment.end_seconds for item in primary
        )
    if not direction_valid:
        reasons.add("reference_direction_conflict")
    return reasons


def _can_reclassify(members: list[_Observation]) -> bool:
    return all(
        item.id.startswith("visual-")
        and item.description.strip()
        and item.role != SegmentRole.FOLLOW_ALONG
        for item in members
    )


def _resolve_parameters(
    members: list[_Observation],
) -> tuple[CandidateParameters, list[ParameterConflict]]:
    compatible = _compatible_parameters(members)

    # A missing mode is not a vote against its explicit count/duration evidence.
    # Detect cross-field disagreement before attempting to construct a merged mode.
    def mode_family(params: CandidateParameters) -> str | None:
        return params.mode or (
            "reps"
            if params.reps is not None
            else "duration"
            if params.duration_seconds is not None
            else None
        )

    families = {mode_family(member.candidate.parameters) for member in members} - {None}
    if compatible is not None and len(families) <= 1:
        return compatible, []
    merged: dict[str, object] = {}
    conflicts: list[ParameterConflict] = []
    for field in ("mode", "sets", "reps", "duration_seconds", "rest_seconds"):
        variants: dict[str, ParameterAlternative] = {}
        for member in members:
            params = member.candidate.parameters
            value = mode_family(params) if field == "mode" else getattr(params, field)
            if value is None:
                continue
            key = str((value, params.reps_max or value)) if field == "reps" else str(value)
            if key in variants:
                variants[key].evidence.extend(member.candidate.evidence)
            else:
                variants[key] = ParameterAlternative(
                    parameters=params, evidence=list(member.candidate.evidence)
                )
        if len(variants) > 1:
            conflicts.append(
                ParameterConflict.model_validate(
                    {"field": field, "alternatives": list(variants.values())}
                )
            )
        elif variants:
            params = next(iter(variants.values())).parameters
            merged[field] = getattr(params, field)
            if field == "reps":
                merged["reps_max"] = params.reps_max
    if any(conflict.field == "mode" for conflict in conflicts):
        for field in ("mode", "reps", "reps_max", "duration_seconds"):
            merged.pop(field, None)
    return CandidateParameters.model_validate(merged), conflicts


def _compatible_parameters(members: list[_Observation]) -> CandidateParameters | None:
    repetition_ranges = {
        (
            item.candidate.parameters.reps,
            item.candidate.parameters.reps_max or item.candidate.parameters.reps,
        )
        for item in members
        if item.candidate.parameters.reps is not None
    }
    if len(repetition_ranges) > 1:
        return None
    parameters: dict[str, object] = {}
    for member in members:
        for field, value in member.candidate.parameters.model_dump(exclude_none=True).items():
            if field in parameters and parameters[field] != value:
                return None
            parameters[field] = value
    try:
        return CandidateParameters.model_validate(parameters)
    except ValidationError:
        return None


def _sequence_identity(label: str) -> tuple[str, str]:
    # Normalize only complete, unambiguous ordinal labels; retain side/round modifiers.
    numeral = r"([0-9]+|[零一二三四五六七八九十])"
    match = re.fullmatch(
        rf"(?:动作|#)?\s*{numeral}|第\s*{numeral}\s*(?:个)?\s*动作", label.strip()
    )
    if match is None:
        return ("literal", label)
    number = match.group(1) or match.group(2)
    value = int(number) if number.isascii() else "零一二三四五六七八九十".index(number)
    return ("number", str(value))


def _chinese_name(name: str | None) -> str | None:
    if name and any("\u4e00" <= character <= "\u9fff" for character in name):
        return name.strip()
    return None


def _unavailable(
    observations: list[_Observation], reason: FusionDiagnosticCode
) -> SemanticFusionResult:
    return SemanticFusionResult(
        [
            item.candidate.model_copy(
                update={"id": f"candidate-{index}", "needs_confirmation": True, "tips": []}
            )
            for index, item in enumerate(
                sorted(observations, key=lambda item: item.candidate.segment.start_seconds), 1
            )
        ],
        [
            AnalysisWarning(
                code="semantic_fusion_unavailable",
                message="语义去重未完成，已保留原始候选，请核对重复动作",
            )
        ],
        {reason: 1},
    )
