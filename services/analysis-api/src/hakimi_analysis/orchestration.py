import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from itertools import pairwise
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from hakimi_analysis.evidence_time import offset_tip
from hakimi_analysis.fusion import CandidateFusionSkill, normalize_action_name
from hakimi_analysis.media import (
    AnalysisWindow,
    LocalMediaProcessor,
    MediaProcessingError,
    PreparedMedia,
    PreparedVisualChunk,
)
from hakimi_analysis.models import (
    AnalysisCandidate,
    AnalysisWarning,
    CandidateParameters,
    CoverageGap,
    CoverageGapReason,
    CoverageStatus,
    RunStage,
    Segment,
    SegmentRole,
    SpeechSignal,
    SpeechUnderstandingResult,
    Transcript,
    TranscriptUtterance,
    VisualLocalizationResult,
    VisualSegment,
)
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.shared_source_context import SpeechSourceContext
from hakimi_analysis.source_tips import safe_tips
from hakimi_analysis.sources import VideoSource
from hakimi_analysis.visual_evidence import (
    CaptionReading,
    OrganizedVisualEvidence,
    PointCaptionReading,
    PointCaptionTimeline,
    VisualCaptionTimeline,
    VisualObservation,
    VisualReading,
    VisualReadings,
)
from hakimi_analysis.visual_source_context import VisualSourceContext


class MediaProcessor(Protocol):
    def prepare_source(
        self,
        source_path: Path,
        duration_seconds: float,
        *,
        start_seconds: float = 0,
        include_contact_sheet: bool = True,
    ) -> AbstractAsyncContextManager[PreparedMedia]: ...

    async def prepare_visual_chunk(
        self,
        source_path: Path,
        window: AnalysisWindow,
        directory: Path,
        *,
        source_offset_seconds: float = 0,
    ) -> PreparedVisualChunk: ...


class SpeechRecognizer(Protocol):
    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript: ...


class ArkAnalyzer(Protocol):
    async def understand_speech(
        self,
        *,
        transcript: dict[str, Any],
        window: Segment,
        instructions: str,
    ) -> SpeechUnderstandingResult: ...

    async def locate_visual(
        self,
        *,
        video_path: Path,
        window: Segment,
        instructions: str,
    ) -> VisualLocalizationResult: ...


class EvidenceFirstAnalyzer(Protocol):
    async def read_visual_evidence(
        self, *, video_path: Path, window: Segment
    ) -> VisualReadings | VisualCaptionTimeline: ...

    async def organize_visual_evidence(
        self,
        *,
        run_id: str,
        source_id: str,
        input_version: str,
        readings: VisualReadings | VisualCaptionTimeline,
        utterances: list[TranscriptUtterance],
    ) -> OrganizedVisualEvidence: ...


@dataclass(frozen=True, slots=True)
class SkillRepository:
    speech_instructions: str
    speech_version: str
    visual_instructions: str
    visual_version: str
    fusion_instructions: str
    fusion_version: str

    @classmethod
    def load(cls, skills_root: Path) -> "SkillRepository":
        speech_path = skills_root / "training-speech-understanding" / "SKILL.md"
        visual_path = skills_root / "visual-action-localization" / "SKILL.md"
        fusion_path = skills_root / "candidate-fusion" / "SKILL.md"
        speech_instructions = speech_path.read_text(encoding="utf-8")
        visual_instructions = visual_path.read_text(encoding="utf-8")
        fusion_instructions = fusion_path.read_text(encoding="utf-8")
        return cls(
            speech_instructions=speech_instructions,
            speech_version=_skill_version(speech_instructions),
            visual_instructions=visual_instructions,
            visual_version=_skill_version(visual_instructions),
            fusion_instructions=fusion_instructions,
            fusion_version=_skill_version(fusion_instructions),
        )


def _skill_version(instructions: str) -> str:
    for line in instructions.splitlines():
        if line.startswith("version:"):
            version = line.partition(":")[2].strip()
            if version:
                return version
    raise ValueError("Skill contract is missing a version")


@dataclass(slots=True)
class VisualChunkResults:
    segments: list[VisualSegment]
    successful_windows: list[AnalysisWindow]
    failures: list[tuple[AnalysisWindow, ProviderError]]
    readings: list[VisualObservation] = dataclass_field(default_factory=list)
    captions: list[CaptionReading] = dataclass_field(default_factory=list)
    independent_captions: bool = False
    point_captions: list[PointCaptionReading] = dataclass_field(default_factory=list)


def build_visual_chunks(
    duration_seconds: float,
    *,
    chunk_seconds: float,
    overlap_seconds: float,
) -> list[AnalysisWindow]:
    if duration_seconds <= 0 or chunk_seconds <= 0:
        raise ValueError("visual chunk durations must be positive")
    if overlap_seconds < 0 or overlap_seconds >= chunk_seconds:
        raise ValueError("visual chunk overlap must be shorter than the chunk")
    windows: list[AnalysisWindow] = []
    start_seconds = 0.0
    while start_seconds < duration_seconds:
        end_seconds = min(duration_seconds, start_seconds + chunk_seconds)
        windows.append(
            AnalysisWindow(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                expanded=False,
            )
        )
        if end_seconds >= duration_seconds:
            break
        start_seconds = end_seconds - overlap_seconds
    return windows


class OrchestratedAnalysisPipeline:
    def __init__(
        self,
        *,
        media: MediaProcessor,
        asr: SpeechRecognizer,
        ark: ArkAnalyzer,
        skills: SkillRepository,
        evidence_timeout_seconds: float = 170,
        visual_chunk_timeout_seconds: float = 20,
        visual_chunk_seconds: float = 60,
        visual_overlap_seconds: float = 10,
        run_timeout_seconds: float = 180,
        completion_margin_seconds: float = 10,
        semantic_fusion: SemanticCandidateFusion | None = None,
        evidence_first_ark: EvidenceFirstAnalyzer | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._media = media
        self._asr = asr
        self._ark = ark
        self._skills = skills
        self._semantic_fusion = semantic_fusion
        if evidence_first_ark is not None and semantic_fusion is None:
            raise ValueError("evidence-first pilot requires semantic fusion")
        self._evidence_first_ark = evidence_first_ark
        self._evidence_timeout_seconds = evidence_timeout_seconds
        self._visual_chunk_timeout_seconds = visual_chunk_timeout_seconds
        self._visual_chunk_seconds = visual_chunk_seconds
        self._visual_overlap_seconds = visual_overlap_seconds
        if (
            evidence_timeout_seconds <= 0
            or visual_chunk_timeout_seconds <= 0
            or run_timeout_seconds <= 0
            or completion_margin_seconds < 0
        ):
            raise ValueError("run budget and completion margin must be valid")
        if completion_margin_seconds >= run_timeout_seconds:
            raise ValueError("completion margin must be shorter than the run budget")
        self._run_timeout_seconds = run_timeout_seconds
        self._completion_margin_seconds = completion_margin_seconds
        self._clock = clock
        build_visual_chunks(
            1,
            chunk_seconds=self._visual_chunk_seconds,
            overlap_seconds=self._visual_overlap_seconds,
        )
        self._fusion = CandidateFusionSkill(
            instructions=skills.fusion_instructions,
            version=skills.fusion_version,
        )

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds
        if self._evidence_first_ark is not None and source.duration_seconds > 300:
            raise PipelineFailure("media_error", "视频超过300秒分析上限", retryable=False)
        utterances: list[TranscriptUtterance] = []
        evidence_deadline = (
            self._clock() + self._run_timeout_seconds - self._completion_margin_seconds
        )
        await emit(
            RunStage.PREPARING_MEDIA,
            "stage.changed",
            {"analysis_scope": "full_source"},
        )
        try:
            async with self._media.prepare_source(
                source.path,
                source.analysis_duration_seconds,
                start_seconds=source.analysis_start_seconds,
                include_contact_sheet=False,
            ) as prepared:
                speech_task = asyncio.create_task(
                    self._timed_speech_branch(
                        prepared,
                        emit,
                        deadline=evidence_deadline,
                        utterances=utterances if self._evidence_first_ark is not None else None,
                    )
                )
                try:
                    visual_results = await self._analyze_visual_chunks(
                        source,
                        prepared,
                        emit,
                        deadline=evidence_deadline,
                        speech_task=speech_task if self._evidence_first_ark is not None else None,
                    )
                    speech_result = await speech_task
                finally:
                    if not speech_task.done():
                        speech_task.cancel()
                    await asyncio.gather(speech_task, return_exceptions=True)
        except MediaProcessingError as error:
            raise PipelineFailure(
                "media_error",
                "视频准备失败，请重试",
                retryable=False,
            ) from error

        speech_signals = speech_result if isinstance(speech_result, list) else []
        visual_segments = (
            visual_results.segments
            if self._semantic_fusion is not None
            else _deduplicate_visual_segments(visual_results.segments)
        )
        speech_context = None
        visual_context = None
        visual_input_binding = None
        unassigned_uncertainty = False
        point_caption_uncertainty = False
        if self._evidence_first_ark is not None:
            if isinstance(speech_result, ProviderError) or visual_results.failures:
                raise PipelineFailure(
                    "provider_error", "证据读取不完整，未执行整理", retryable=True
                )
            remaining = evidence_deadline - self._clock()
            try:
                async with asyncio.timeout(max(0, remaining)):
                    organized = await self._evidence_first_ark.organize_visual_evidence(
                        run_id=str(uuid4()),
                        source_id=source.id,
                        input_version="visual-caption-timeline-v1"
                        if visual_results.independent_captions
                        else "visual-evidence-first-v1",
                        readings=(
                            PointCaptionTimeline(
                                observations=visual_results.readings,
                                captions=visual_results.captions,
                                point_captions=visual_results.point_captions,
                            )
                            if visual_results.point_captions
                            else VisualCaptionTimeline(
                                observations=visual_results.readings,
                                captions=visual_results.captions,
                            )
                            if visual_results.independent_captions
                            else VisualReadings(
                                observations=[
                                    VisualReading.model_validate(r.model_dump())
                                    for r in visual_results.readings
                                ]
                            )
                        ),
                        utterances=utterances,
                    )
                visual_segments = organized.to_visual_segments()
                if (
                    self._semantic_fusion is not None
                    and self._semantic_fusion.includes_visual_context
                ):
                    visual_context = VisualSourceContext.bind(
                        source_id=source.id, evidence=organized
                    )
                    visual_input_binding = organized.binding_id
                point_caption_uncertainty = bool(organized.point_captions)
                unassigned_uncertainty = any(
                    j.purpose == "uncertain" for j in organized.unassigned_judgments
                )
                speech_context = SpeechSourceContext.bind(
                    source_id=source.id,
                    speech_signals=speech_signals,
                    visual_segments=visual_segments,
                    utterances=utterances,
                )
            except TimeoutError:
                raise PipelineFailure("timeout", "证据整理超时", retryable=True) from None
            except (ProviderError, ValueError):
                raise PipelineFailure(
                    "schema_error", "证据整理失败，未生成候选", retryable=True
                ) from None
            finally:
                utterances.clear()
                visual_results.readings.clear()
                visual_results.captions.clear()
                visual_results.point_captions.clear()
        coverage_gaps = _coverage_gaps(
            source.analysis_duration_seconds,
            visual_results.successful_windows,
            visual_results.failures,
        )
        warnings: list[AnalysisWarning] = []
        if point_caption_uncertainty:
            warnings.append(
                AnalysisWarning(
                    code="semantic_fusion_conflict",
                    message="部分字幕时间尚未确认，未用于训练提醒或参数",
                )
            )
        if unassigned_uncertainty:
            warnings.append(
                AnalysisWarning(
                    code="semantic_fusion_conflict",
                    message="部分字幕归属尚未确认，未用于训练提醒或参数",
                )
            )
        if isinstance(speech_result, ProviderError):
            warnings.append(
                AnalysisWarning(
                    code="speech_unavailable",
                    message="语音依据暂不可用，请重点确认动作名称和参数",
                )
            )
        if visual_results.failures:
            warnings.append(
                AnalysisWarning(
                    code="visual_unavailable",
                    message="部分画面依据暂不可用，请重试覆盖缺口",
                )
            )
        candidates: list[AnalysisCandidate] = []
        if speech_signals or visual_segments:
            await emit(
                RunStage.FUSING_CANDIDATES,
                "stage.changed",
                {"skill_version": self._fusion.version},
            )
            if self._semantic_fusion is not None:
                fused = await self._semantic_fusion.run(
                    source_id=source.id,
                    speech_signals=speech_signals,
                    visual_segments=visual_segments,
                    remaining_seconds=evidence_deadline - self._clock(),
                    speech_context=speech_context,
                    visual_context=visual_context,
                    visual_input_binding=visual_input_binding,
                )
                candidates = fused.candidates
                warnings.extend(fused.warnings)
            else:
                candidates = self._fusion.run(
                    source_id=source.id,
                    speech_signals=speech_signals,
                    visual_segments=visual_segments,
                )
        # Non-training observations may all disappear during semantic filtering.
        # Decide empty/failure coverage from usable candidates, not raw observations.
        if isinstance(speech_result, ProviderError) and not candidates and not coverage_gaps:
            coverage_gaps = [
                CoverageGap(
                    start_seconds=0,
                    end_seconds=source.analysis_duration_seconds,
                    reason=_coverage_gap_reason(speech_result),
                    retryable=True,
                )
            ]
        processed_seconds = source.analysis_duration_seconds - sum(
            gap.end_seconds - gap.start_seconds for gap in coverage_gaps
        )
        if candidates:
            return PipelineOutput(
                candidates=candidates,
                warnings=warnings,
                coverage_status=(
                    CoverageStatus.PARTIAL if coverage_gaps else CoverageStatus.COMPLETE
                ),
                coverage_gaps=coverage_gaps,
                processed_seconds=processed_seconds if coverage_gaps else None,
            )

        if coverage_gaps:
            return PipelineOutput(
                candidates=[],
                warnings=warnings,
                empty_reason="insufficient_evidence",
                coverage_status=CoverageStatus.INSUFFICIENT,
                coverage_gaps=coverage_gaps,
                processed_seconds=processed_seconds,
            )
        return PipelineOutput(candidates=[], warnings=warnings, empty_reason="no_evidence")

    async def _timed_speech_branch(
        self,
        prepared: PreparedMedia,
        emit: EmitCallback,
        *,
        deadline: float,
        utterances: list[TranscriptUtterance] | None = None,
    ) -> list[SpeechSignal] | ProviderError:
        remaining_seconds = deadline - self._clock()
        if remaining_seconds <= 0:
            return ProviderError("timeout", "run budget exhausted before speech", retryable=True)
        try:
            async with asyncio.timeout(min(self._evidence_timeout_seconds, remaining_seconds)):
                return await self._speech_branch(prepared, emit, utterances=utterances)
        except TimeoutError:
            return ProviderError("timeout", "speech evidence budget exceeded", retryable=True)
        except ProviderError as error:
            return error
        except Exception:
            return ProviderError("provider_error", "speech branch failed", retryable=True)

    async def _analyze_visual_chunks(
        self,
        source: VideoSource,
        prepared: PreparedMedia,
        emit: EmitCallback,
        *,
        deadline: float,
        speech_task: asyncio.Task[list[SpeechSignal] | ProviderError] | None = None,
    ) -> VisualChunkResults:
        windows = build_visual_chunks(
            source.analysis_duration_seconds,
            chunk_seconds=self._visual_chunk_seconds,
            overlap_seconds=self._visual_overlap_seconds,
        )
        segments: list[VisualSegment] = []
        readings: list[VisualObservation] = []
        captions: list[CaptionReading] = []
        point_captions: list[PointCaptionReading] = []
        timeline_mode: bool | None = None
        successful_windows: list[AnalysisWindow] = []
        failures: list[tuple[AnalysisWindow, ProviderError]] = []
        for index, window in enumerate(windows, start=1):
            if (
                speech_task is not None
                and speech_task.done()
                and isinstance(speech_task.result(), ProviderError)
            ):
                break
            source_window_start = source.analysis_start_seconds + window.start_seconds
            source_window_end = source.analysis_start_seconds + window.end_seconds
            remaining_seconds = deadline - self._clock()
            if remaining_seconds < self._visual_chunk_timeout_seconds:
                budget_error = ProviderError(
                    "timeout", "run budget exhausted before visual chunk", retryable=True
                )
                failures.extend((remaining, budget_error) for remaining in windows[index - 1 :])
                break
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "visual_chunk.started",
                {
                    "chunk_index": index,
                    "chunk_count": len(windows),
                    "start_seconds": source_window_start,
                    "end_seconds": source_window_end,
                },
            )
            try:
                async with asyncio.timeout(
                    min(self._visual_chunk_timeout_seconds, remaining_seconds)
                ):
                    chunk = await self._media.prepare_visual_chunk(
                        source.path,
                        window,
                        prepared.directory,
                        source_offset_seconds=source.analysis_start_seconds,
                    )
                    if (
                        speech_task is not None
                        and speech_task.done()
                        and isinstance(speech_task.result(), ProviderError)
                    ):
                        break
                    bounds = Segment(start_seconds=0, end_seconds=window.duration_seconds)
                    if self._evidence_first_ark is None:
                        result = await self._ark.locate_visual(
                            video_path=chunk.video_path,
                            window=bounds,
                            instructions=self._skills.visual_instructions,
                        )
                    else:
                        raw = await self._evidence_first_ark.read_visual_evidence(
                            video_path=chunk.video_path, window=bounds
                        )
                        independent = isinstance(raw, VisualCaptionTimeline)
                        if timeline_mode is not None and timeline_mode != independent:
                            raise ProviderError(
                                "schema_error", "visual evidence version changed", retryable=False
                            )
                        timeline_mode = independent
                        if isinstance(raw, PointCaptionTimeline):
                            point_captions.extend(
                                c.model_copy(
                                    update={
                                        "start_seconds": c.start_seconds + window.start_seconds,
                                        "end_seconds": c.end_seconds + window.start_seconds,
                                    }
                                )
                                for c in raw.point_captions
                            )
                        if isinstance(raw, VisualCaptionTimeline):
                            captions.extend(
                                c.model_copy(
                                    update={
                                        "start_seconds": c.start_seconds + window.start_seconds,
                                        "end_seconds": c.end_seconds + window.start_seconds,
                                    }
                                )
                                for c in raw.captions
                            )
                        readings.extend(
                            item.model_copy(
                                deep=True,
                                update={
                                    "start_seconds": item.start_seconds + window.start_seconds,
                                    "end_seconds": item.end_seconds + window.start_seconds,
                                    **(
                                        {
                                            "captions": [
                                                caption.model_copy(
                                                    update={
                                                        "start_seconds": caption.start_seconds
                                                        + window.start_seconds,
                                                        "end_seconds": caption.end_seconds
                                                        + window.start_seconds,
                                                    }
                                                )
                                                for caption in item.captions
                                            ]
                                        }
                                        if isinstance(item, VisualReading)
                                        else {}
                                    ),
                                },
                            )
                            for item in raw.observations
                        )
                        result = VisualLocalizationResult(
                            segments=[
                                VisualSegment.model_validate(item.model_dump(exclude={"captions"}))
                                for item in raw.observations
                            ]
                        )
            except TimeoutError:
                timeout_error = ProviderError(
                    "timeout", "visual chunk evidence budget exceeded", retryable=True
                )
                failures.append((window, timeout_error))
            except MediaProcessingError:
                media_error = ProviderError("media_error", "visual chunk failed", retryable=True)
                failures.append((window, media_error))
            except ProviderError as provider_error:
                failures.append((window, provider_error))
            except Exception:
                unexpected_error = ProviderError(
                    "provider_error", "visual chunk failed", retryable=True
                )
                failures.append((window, unexpected_error))
            else:
                segments.extend(_offset_visual_segments(result.segments, window.start_seconds))
                successful_windows.append(window)
                processed_seconds = _covered_seconds(successful_windows)
                await emit(
                    RunStage.ANALYZING_EVIDENCE,
                    "visual_chunk.completed",
                    {
                        "chunk_index": index,
                        "chunk_count": len(windows),
                        "processed_seconds": processed_seconds,
                        "evidence_count": len(result.segments),
                    },
                )
            if self._evidence_first_ark is not None and failures:
                # The pilot requires complete readings; do not buy more unusable evidence.
                break
        return VisualChunkResults(
            segments=segments,
            successful_windows=successful_windows,
            failures=failures,
            readings=readings,
            captions=captions,
            independent_captions=bool(timeline_mode),
            point_captions=point_captions,
        )

    async def _speech_branch(
        self,
        prepared: PreparedMedia,
        emit: EmitCallback,
        *,
        utterances: list[TranscriptUtterance] | None = None,
    ) -> list[SpeechSignal]:
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "speech", "skill_version": self._skills.speech_version},
        )
        transcript = await self._asr.recognize(
            audio_path=prepared.audio_path,
            window_start_seconds=prepared.window.start_seconds,
            request_id=str(uuid4()),
        )
        if utterances is not None:
            utterances.extend(item.model_copy(deep=True) for item in transcript.utterances)
        if not transcript.text.strip():
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "branch.completed",
                {"branch": "speech", "evidence_count": 0},
            )
            return []
        window = Segment(
            start_seconds=prepared.window.start_seconds,
            end_seconds=prepared.window.end_seconds,
        )
        result = await self._ark.understand_speech(
            transcript={
                "text": transcript.text,
                "utterances": [
                    {
                        "text": utterance.text,
                        "start_seconds": utterance.start_seconds,
                        "end_seconds": utterance.end_seconds,
                    }
                    for utterance in transcript.utterances
                ],
            },
            window=window,
            instructions=self._skills.speech_instructions,
        )
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.completed",
            {"branch": "speech", "evidence_count": len(result.signals)},
        )
        return [signal for signal in result.signals if signal.is_training_content]


def _covered_seconds(windows: list[AnalysisWindow]) -> float:
    if not windows:
        return 0.0
    ordered = sorted(windows, key=lambda window: (window.start_seconds, window.end_seconds))
    covered = 0.0
    current_start = ordered[0].start_seconds
    current_end = ordered[0].end_seconds
    for window in ordered[1:]:
        if window.start_seconds <= current_end:
            current_end = max(current_end, window.end_seconds)
            continue
        covered += current_end - current_start
        current_start = window.start_seconds
        current_end = window.end_seconds
    return covered + current_end - current_start


def _coverage_gaps(
    duration_seconds: float,
    successful_windows: list[AnalysisWindow],
    failures: list[tuple[AnalysisWindow, ProviderError]],
) -> list[CoverageGap]:
    if not failures:
        return []
    boundaries = {0.0, duration_seconds}
    for window in successful_windows:
        boundaries.update((window.start_seconds, window.end_seconds))
    for window, _error in failures:
        boundaries.update((window.start_seconds, window.end_seconds))
    ordered = sorted(boundaries)
    gaps: list[CoverageGap] = []
    for start_seconds, end_seconds in pairwise(ordered):
        if end_seconds <= start_seconds:
            continue
        midpoint = (start_seconds + end_seconds) / 2
        if any(
            window.start_seconds <= midpoint < window.end_seconds for window in successful_windows
        ):
            continue
        error = next(
            (
                failed_error
                for window, failed_error in failures
                if window.start_seconds <= midpoint < window.end_seconds
            ),
            ProviderError("provider_error", "visual coverage unavailable", retryable=True),
        )
        reason = _coverage_gap_reason(error)
        if gaps and gaps[-1].end_seconds == start_seconds and gaps[-1].reason == reason:
            gaps[-1] = gaps[-1].model_copy(update={"end_seconds": end_seconds})
        else:
            gaps.append(
                CoverageGap(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    reason=reason,
                    retryable=True,
                )
            )
    return gaps


def _offset_visual_segments(
    segments: list[VisualSegment], analysis_window_offset_seconds: float
) -> list[VisualSegment]:
    if analysis_window_offset_seconds == 0:
        return segments
    return [
        segment.model_copy(
            update={
                "start_seconds": (segment.start_seconds + analysis_window_offset_seconds),
                "end_seconds": segment.end_seconds + analysis_window_offset_seconds,
                "tips": [offset_tip(tip, analysis_window_offset_seconds) for tip in segment.tips],
            }
        )
        for segment in segments
    ]


def _coverage_gap_reason(error: ProviderError) -> CoverageGapReason:
    if error.code == "timeout":
        return CoverageGapReason.TIMEOUT
    if error.code == "media_error":
        return CoverageGapReason.MEDIA_ERROR
    if error.code == "provider_error":
        return CoverageGapReason.PROVIDER_ERROR
    return CoverageGapReason.UNKNOWN


def _merge_visual_segments(previous: VisualSegment, current: VisualSegment) -> VisualSegment | None:
    parameters: dict[str, Any] = {}
    for segment in (previous, current):
        if segment.text_parameters is None:
            continue
        for field, value in segment.text_parameters.model_dump(exclude_none=True).items():
            if field in parameters and parameters[field] != value:
                return None
            parameters[field] = value
    try:
        merged_parameters = CandidateParameters.model_validate(parameters) if parameters else None
    except ValidationError:
        # Complementary fields can still be incompatible (e.g. reps and duration).
        return None
    return previous.model_copy(
        update={
            "start_seconds": min(previous.start_seconds, current.start_seconds),
            "end_seconds": max(previous.end_seconds, current.end_seconds),
            "segment_role": (
                previous.segment_role
                if previous.segment_role == current.segment_role
                else SegmentRole.UNKNOWN
            ),
            "text_parameters": merged_parameters,
            "tips": safe_tips([*previous.tips, *current.tips]),
        }
    )


def _deduplicate_visual_segments(segments: list[VisualSegment]) -> list[VisualSegment]:
    grouped: dict[tuple[str, str | None], list[VisualSegment]] = {}
    ungrouped: list[VisualSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start_seconds, item.end_seconds)):
        if not segment.is_training_content:
            continue
        action_key = normalize_action_name(segment.action_name)
        if not action_key:
            ungrouped.append(segment)
            continue
        action_segments = grouped.setdefault((action_key, segment.sequence_label), [])
        if action_segments and segment.start_seconds <= action_segments[-1].end_seconds:
            merged = _merge_visual_segments(action_segments[-1], segment)
            if merged is not None:
                action_segments[-1] = merged
                continue
        action_segments.append(segment)
    return sorted(
        [segment for action_segments in grouped.values() for segment in action_segments]
        + ungrouped,
        key=lambda item: (
            item.start_seconds,
            item.end_seconds,
            normalize_action_name(item.action_name),
            item.visual_cue,
        ),
    )


__all__ = [
    "LocalMediaProcessor",
    "OrchestratedAnalysisPipeline",
    "SkillRepository",
    "build_visual_chunks",
]
