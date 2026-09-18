from dataclasses import dataclass

from hakimi_analysis.models import (
    ActionMode,
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    Segment,
    SegmentRole,
    SourceClip,
    SpeechSignal,
    VisualSegment,
)
from hakimi_analysis.source_tips import safe_tips

_ACTION_ALIASES = {
    "dragcurl": "drag-curl",
    "拖拽弯举": "drag-curl",
    "拖拽式弯举": "drag-curl",
}

# Exact, reviewed labels only: never drop an unknown modifier to force a match.
_CHINESE_ACTION_NAMES = {
    "dragcurl": "拖拽弯举",
    "wristcurl": "腕弯举",
    "dumbbellwristcurl": "哑铃腕弯举",
    "reversewristcurl": "反向腕弯举",
    "dumbbellreversewristcurl": "哑铃反向腕弯举",
    "barbellwristcurl": "杠铃腕弯举",
    "barbellreversewristcurl": "杠铃反向腕弯举",
    "hammercurl": "锤式弯举",
    "seatedhammercurl": "坐姿锤式弯举",
    "benchpress": "卧推",
    "pushup": "俯卧撑",
    "squat": "深蹲",
    "plank": "平板支撑",
}


def _display_action_name(name: str | None) -> str:
    if not name:
        return "待确认动作"
    key = "".join(c for c in name.casefold() if c.isalnum())
    return _CHINESE_ACTION_NAMES.get(key, name)


def normalize_action_name(name: str | None) -> str:
    """Match only reviewed aliases, preserving unknown action modifiers."""
    if not name:
        return ""
    normalized = "".join(
        character for character in _display_action_name(name).casefold() if character.isalnum()
    )
    return _ACTION_ALIASES.get(normalized, normalized)


def _same_action(speech_name: str, visual_name: str | None) -> bool:
    if visual_name is None:
        return True
    speech = normalize_action_name(speech_name)
    visual = normalize_action_name(visual_name)
    return bool(speech and visual) and speech == visual


@dataclass(frozen=True, slots=True)
class CandidateFusionSkill:
    instructions: str
    version: str

    def run(
        self,
        *,
        source_id: str,
        speech_signals: list[SpeechSignal],
        visual_segments: list[VisualSegment],
    ) -> list[AnalysisCandidate]:
        if not self.instructions.strip() or not self.version.strip():
            raise ValueError("candidate fusion Skill must be versioned")
        return fuse_candidates(
            source_id=source_id,
            speech_signals=speech_signals,
            visual_segments=visual_segments,
        )


def _overlap_seconds(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _gap_seconds(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> float:
    return max(0.0, max(left_start, right_start) - min(left_end, right_end))


def _parameters(
    signal: SpeechSignal | None, visual: VisualSegment | None = None
) -> CandidateParameters:
    visible = visual.text_parameters if visual is not None else None
    if signal is None:
        return visible or CandidateParameters()
    mode: ActionMode | None = None
    reps = signal.reps
    duration_seconds = signal.duration_seconds
    if reps is not None:
        mode = ActionMode.REPS
        duration_seconds = None
    elif duration_seconds is not None:
        mode = ActionMode.DURATION
        reps = None
    spoken = CandidateParameters(
        mode=mode,
        sets=signal.sets,
        reps=reps,
        duration_seconds=duration_seconds,
        rest_seconds=signal.rest_seconds,
    )
    if visible is None:
        return spoken
    return CandidateParameters(
        mode=spoken.mode or visible.mode,
        sets=spoken.sets if spoken.sets is not None else visible.sets,
        reps=spoken.reps if spoken.mode is not None else visible.reps,
        reps_max=None if spoken.mode is not None else visible.reps_max,
        duration_seconds=(
            spoken.duration_seconds if spoken.mode is not None else visible.duration_seconds
        ),
        rest_seconds=spoken.rest_seconds
        if spoken.rest_seconds is not None
        else visible.rest_seconds,
    )


def _segment_role(
    speech: SpeechSignal | None,
    visual: VisualSegment | None,
) -> SegmentRole:
    roles = {
        item.segment_role
        for item in (speech, visual)
        if item is not None and item.segment_role != SegmentRole.UNKNOWN
    }
    if len(roles) == 1:
        return next(iter(roles))
    return SegmentRole.UNKNOWN


def _conflicting_parameters(speech: SpeechSignal, visual: VisualSegment | None) -> bool:
    if visual is None or visual.text_parameters is None:
        return False
    visible = visual.text_parameters
    return (
        any(
            getattr(speech, field) is not None
            and getattr(visible, field) is not None
            and getattr(speech, field) != getattr(visible, field)
            for field in ("sets", "reps", "duration_seconds", "rest_seconds")
        )
        or (speech.reps is not None and visible.mode == ActionMode.DURATION)
        or (speech.duration_seconds is not None and visible.mode == ActionMode.REPS)
    )


def _speech_signal_quality(
    signal: SpeechSignal,
) -> tuple[int, int, tuple[int, int, int, int], str, str]:
    def sortable(value: int | None) -> int:
        return -1 if value is None else value

    parameter_values = (
        signal.sets,
        signal.reps,
        signal.duration_seconds,
        signal.rest_seconds,
    )
    return (
        sum(value is not None for value in parameter_values),
        int(signal.segment_role != SegmentRole.UNKNOWN),
        (
            sortable(signal.sets),
            sortable(signal.reps),
            sortable(signal.duration_seconds),
            sortable(signal.rest_seconds),
        ),
        signal.action_name.casefold(),
        signal.evidence_text,
    )


def _deduplicate_speech_signals(signals: list[SpeechSignal]) -> list[SpeechSignal]:
    grouped: dict[str, list[SpeechSignal]] = {}
    for signal in sorted(
        signals,
        key=lambda item: (
            item.start_seconds,
            item.end_seconds,
            normalize_action_name(item.action_name),
            item.evidence_text,
        ),
    ):
        action_key = normalize_action_name(signal.action_name)
        action_signals = grouped.setdefault(action_key, [])
        if action_signals and signal.start_seconds <= action_signals[-1].end_seconds:
            previous = action_signals[-1]
            preferred = max((previous, signal), key=_speech_signal_quality)
            role = (
                previous.segment_role
                if previous.segment_role == signal.segment_role
                else SegmentRole.UNKNOWN
            )
            action_signals[-1] = preferred.model_copy(
                update={
                    "start_seconds": min(previous.start_seconds, signal.start_seconds),
                    "end_seconds": max(previous.end_seconds, signal.end_seconds),
                    "segment_role": role,
                }
            )
            continue
        action_signals.append(signal)
    return sorted(
        [signal for action_signals in grouped.values() for signal in action_signals],
        key=lambda item: (
            item.start_seconds,
            item.end_seconds,
            normalize_action_name(item.action_name),
            item.evidence_text,
        ),
    )


def fuse_candidates(
    *,
    source_id: str,
    speech_signals: list[SpeechSignal],
    visual_segments: list[VisualSegment],
) -> list[AnalysisCandidate]:
    candidates: list[AnalysisCandidate] = []
    matched_visual_indexes: set[int] = set()
    speech_signals = _deduplicate_speech_signals(speech_signals)

    for speech in speech_signals:
        matches = [
            (
                index,
                _overlap_seconds(
                    speech.start_seconds,
                    speech.end_seconds,
                    visual.start_seconds,
                    visual.end_seconds,
                ),
                _gap_seconds(
                    speech.start_seconds,
                    speech.end_seconds,
                    visual.start_seconds,
                    visual.end_seconds,
                ),
            )
            for index, visual in enumerate(visual_segments)
            if index not in matched_visual_indexes
            and _same_action(speech.action_name, visual.action_name)
        ]
        matched_index, overlap, gap = min(
            matches,
            key=lambda item: (item[2], -item[1]),
            default=(-1, 0.0, float("inf")),
        )
        visual = visual_segments[matched_index] if overlap > 0 or gap <= 6 else None
        if visual is not None:
            matched_visual_indexes.add(matched_index)
        # Overlapping alternatives survived deduplication for a reason (such as
        # conflicting captions). Matching speech to one must not silently settle them.
        ambiguous_visual = visual is not None and any(
            alternative is not visual
            and _same_action(speech.action_name, alternative.action_name)
            and _overlap_seconds(
                visual.start_seconds,
                visual.end_seconds,
                alternative.start_seconds,
                alternative.end_seconds,
            )
            > 0
            for alternative in visual_segments
        )

        start_seconds = min(
            speech.start_seconds,
            visual.start_seconds if visual is not None else speech.start_seconds,
        )
        end_seconds = max(
            speech.end_seconds,
            visual.end_seconds if visual is not None else speech.end_seconds,
        )
        evidence = [
            EvidenceSpan(
                type=EvidenceType.SPEECH,
                start_seconds=speech.start_seconds,
                end_seconds=speech.end_seconds,
            )
        ]
        if visual is not None:
            evidence.append(
                EvidenceSpan(
                    type=EvidenceType.VISUAL,
                    start_seconds=visual.start_seconds,
                    end_seconds=visual.end_seconds,
                )
            )
        segment_role = _segment_role(speech, visual)
        candidates.append(
            AnalysisCandidate(
                id="pending",
                name=_display_action_name(speech.action_name),
                source_id=source_id,
                segment=Segment(start_seconds=start_seconds, end_seconds=end_seconds),
                parameters=_parameters(speech, visual),
                evidence=evidence,
                tips=safe_tips([*speech.tips, *(visual.tips if visual else [])]),
                playback_options=[
                    SourceClip(
                        start_seconds=visual.start_seconds,
                        end_seconds=visual.end_seconds,
                        label=visual.visual_cue[:160],
                    )
                ]
                if visual
                else [],
                needs_confirmation=(
                    visual is None
                    or visual.action_name is None
                    or segment_role == SegmentRole.UNKNOWN
                    or _conflicting_parameters(speech, visual)
                    or ambiguous_visual
                ),
            )
        )

    for index, visual in enumerate(visual_segments):
        if index in matched_visual_indexes:
            continue
        candidates.append(
            AnalysisCandidate(
                id="pending",
                name=_display_action_name(visual.action_name),
                source_id=source_id,
                segment=Segment(
                    start_seconds=visual.start_seconds,
                    end_seconds=visual.end_seconds,
                ),
                parameters=visual.text_parameters or CandidateParameters(),
                tips=safe_tips(visual.tips),
                playback_options=[
                    SourceClip(
                        start_seconds=visual.start_seconds,
                        end_seconds=visual.end_seconds,
                        label=visual.visual_cue[:160],
                    )
                ],
                evidence=[
                    EvidenceSpan(
                        type=EvidenceType.VISUAL,
                        start_seconds=visual.start_seconds,
                        end_seconds=visual.end_seconds,
                    )
                ],
                needs_confirmation=True,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.segment.start_seconds if candidate.segment is not None else float("inf")
        )
    )
    return [
        candidate.model_copy(update={"id": f"candidate-{index}"})
        for index, candidate in enumerate(candidates, start=1)
    ]
