from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorResponse(StrictModel):
    detail: str


class ReadyResponse(StrictModel):
    status: Literal["ready"]


class NotReadyResponse(StrictModel):
    status: Literal["not_ready"]
    code: str = Field(min_length=1)


class CapabilitiesView(StrictModel):
    local_upload_enabled: bool
    local_analysis_max_seconds: float = Field(gt=0, le=300)
    local_upload_max_bytes: int = Field(ge=1)


class ActionMode(StrEnum):
    REPS = "reps"
    DURATION = "duration"


class EvidenceType(StrEnum):
    SPEECH = "speech"
    VISUAL = "visual"


class SegmentRole(StrEnum):
    FOLLOW_ALONG = "follow_along"
    TEACHING_DEMO = "teaching_demo"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class CoverageGapReason(StrEnum):
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    MEDIA_ERROR = "media_error"
    UNKNOWN = "unknown"


class AccessTier(StrEnum):
    PUBLIC = "public"
    JUDGE = "judge"


class RunStage(StrEnum):
    QUEUED = "queued"
    PREPARING_MEDIA = "preparing_media"
    ANALYZING_EVIDENCE = "analyzing_evidence"
    EXPANDING_WINDOW = "expanding_window"
    FUSING_CANDIDATES = "fusing_candidates"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    CONFIGURATION_ERROR = "configuration_error"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_ERROR = "schema_error"
    MEDIA_ERROR = "media_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SourceSummary(StrictModel):
    id: str
    title: str
    media_url: str
    duration_seconds: float
    origin_url: str | None = None


class Segment(StrictModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "Segment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("segment end must be after start")
        return self


class EvidenceSpan(Segment):
    type: EvidenceType


class CoverageGap(Segment):
    reason: CoverageGapReason
    retryable: Literal[True]


class CandidateParameters(StrictModel):
    mode: ActionMode | None = None
    sets: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    reps_max: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_mode(self) -> "CandidateParameters":
        if self.reps_max is not None and (
            self.reps is None or self.reps_max < self.reps or self.mode != ActionMode.REPS
        ):
            raise ValueError("repetition range requires ordered bounds and reps mode")
        if self.mode == ActionMode.REPS and self.duration_seconds is not None:
            raise ValueError("reps mode cannot include duration_seconds")
        if self.mode == ActionMode.DURATION and self.reps is not None:
            raise ValueError("duration mode cannot include reps")
        return self


class SourceTip(StrictModel):
    text: str = Field(min_length=2, max_length=80)
    category: Literal["setup", "path", "breathing", "rhythm", "caution"]
    evidence: EvidenceSpan


class SourceClip(Segment):
    label: str = Field(min_length=1, max_length=160)


class ParameterAlternative(StrictModel):
    parameters: CandidateParameters
    evidence: list[EvidenceSpan]


class ParameterConflict(StrictModel):
    field: Literal["mode", "sets", "reps", "duration_seconds", "rest_seconds"]
    alternatives: list[ParameterAlternative] = Field(min_length=2)


class AnalysisCandidate(StrictModel):
    id: str
    name: str = Field(min_length=1)
    source_id: str
    segment: Segment
    parameters: CandidateParameters
    evidence: list[EvidenceSpan]
    needs_confirmation: bool
    parameter_conflicts: list[ParameterConflict] = Field(default_factory=list)
    tips: list[SourceTip] = Field(default_factory=list, max_length=3)
    playback_options: list[SourceClip] = Field(default_factory=list, max_length=12)


class SpeechSignal(StrictModel):
    tips: list[SourceTip] = Field(default_factory=list, max_length=3)
    action_name: str = Field(min_length=1)
    sets: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    rest_seconds: int | None = Field(default=None, ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    evidence_text: str = Field(min_length=1)
    segment_role: SegmentRole = SegmentRole.UNKNOWN
    is_training_content: bool = True


class SpeechUnderstandingResult(StrictModel):
    signals: list[SpeechSignal]


class VisualSegment(StrictModel):
    tips: list[SourceTip] = Field(default_factory=list, max_length=3)
    action_name: str | None = Field(
        default=None,
        description=(
            "Natural Chinese exercise name from clearly visible movement, retaining "
            "grip/posture/equipment; null only if unidentifiable, not merely because "
            "the caption omits a name."
        ),
    )
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    visual_cue: str = Field(min_length=1)
    segment_role: SegmentRole = SegmentRole.UNKNOWN
    text_parameters: CandidateParameters | None = None
    sequence_label: str | None = Field(default=None, max_length=40)
    is_training_content: bool = Field(
        default=True,
        description=(
            "True only for a training instruction/demonstration; false for an introductory "
            "hook, unrelated posing, promotion or end card even if a person is moving."
        ),
    )


class VisualLocalizationResult(StrictModel):
    segments: list[VisualSegment]


class TranscriptWord(StrictModel):
    text: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class TranscriptUtterance(StrictModel):
    text: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    words: list[TranscriptWord] = Field(default_factory=list)


class Transcript(StrictModel):
    text: str
    utterances: list[TranscriptUtterance]
    provider_request_id: str | None = None


class CreateAnalysisRunRequest(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    trigger_seconds: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra={"deprecated": True},
        description="Deprecated compatibility metadata; full-source analysis ignores it.",
    )


class UpgradeAccessSessionRequest(StrictModel):
    access_code: str = Field(min_length=1, max_length=256)


class AccessSessionView(StrictModel):
    tier: AccessTier
    can_analyze: bool
    retry_after_seconds: int | None = Field(default=None, ge=1)


class GymtiAnswer(StrictModel):
    question_id: str = Field(pattern=r"^\S{1,128}$")
    option_id: str = Field(pattern=r"^\S{1,128}$")


class GymtiNextQuestionRequest(StrictModel):
    questionnaire_version: str = Field(min_length=1, max_length=128)
    scoring_version: str = Field(min_length=1, max_length=128)
    answered: list[GymtiAnswer] = Field(default_factory=list, max_length=8)
    candidate_question_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_distinct_ids(self) -> "GymtiNextQuestionRequest":
        question_ids = [answer.question_id for answer in self.answered]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("answered question ids must be unique")
        if len(set(self.candidate_question_ids)) != len(self.candidate_question_ids):
            raise ValueError("candidate question ids must be unique")
        return self


class GymtiNextQuestionView(StrictModel):
    question_id: str
    source: Literal["llm", "local_fallback"]
    model: str | None = None
    version: str


class GymtiResultNarrativeRequest(StrictModel):
    questionnaire_version: str = Field(min_length=1, max_length=128)
    scoring_version: str = Field(min_length=1, max_length=128)
    answered: list[GymtiAnswer] = Field(min_length=1, max_length=8)
    formal_result_id: str = Field(pattern=r"^\S{1,128}$")
    secondary_result_id: str | None = Field(default=None, pattern=r"^\S{1,128}$")
    coach_style_id: str = Field(pattern=r"^\S{1,128}$")
    reason_codes: list[str] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_distinct_ids(self) -> "GymtiResultNarrativeRequest":
        question_ids = [answer.question_id for answer in self.answered]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("answered question ids must be unique")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason codes must be unique")
        return self


class GymtiNarrativeSnapshotView(StrictModel):
    source: Literal["llm", "template"]
    model: str | None = None
    version: str
    generated_at: datetime
    text: str = Field(min_length=1, max_length=600)


class AnalysisWarning(StrictModel):
    code: Literal["speech_unavailable", "visual_unavailable", "semantic_fusion_unavailable",
                  "semantic_fusion_conflict"]
    message: str


class AnalysisError(StrictModel):
    code: ErrorCode
    message: str
    retryable: bool


class AnalysisRunView(StrictModel):
    id: str
    source_id: str
    trigger_seconds: float | None
    status: RunStatus
    stage: RunStage
    candidates: list[AnalysisCandidate] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    empty_reason: Literal["no_evidence", "insufficient_evidence"] | None = None
    error: AnalysisError | None = None
    source_duration_seconds: float = Field(gt=0)
    processed_seconds: float = Field(default=0, ge=0)
    discovered_candidate_count: int = Field(
        default=0,
        ge=0,
        description=(
            "While running, the conservative maximum evidence count from completed evidence; "
            "at terminal completion, the exact fused candidate count."
        ),
    )
    coverage_status: CoverageStatus | None = None
    coverage_gaps: list[CoverageGap] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalysisEvent(StrictModel):
    sequence: int
    type: str
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)
