import asyncio
import contextlib
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from hakimi_analysis.evidence_time import offset_span, offset_tip
from hakimi_analysis.models import (
    AnalysisCandidate,
    AnalysisError,
    AnalysisEvent,
    AnalysisRunView,
    CoverageGap,
    CoverageStatus,
    ErrorCode,
    RunStage,
    RunStatus,
    Segment,
)
from hakimi_analysis.observability import log_safe_fields
from hakimi_analysis.pipeline import AnalysisPipeline, PipelineFailure, PipelineOutput
from hakimi_analysis.sources import VideoSource

TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
LOGGER = logging.getLogger("hakimi_analysis.runs")


@dataclass(slots=True)
class RunRecord:
    view: AnalysisRunView
    owner_session_id: str | None = None
    events: list[AnalysisEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    terminal_at: datetime | None = None
    started_monotonic: float = field(default_factory=time.perf_counter)
    on_terminal: Callable[[], Awaitable[None]] | None = None
    terminal_notified: bool = False


class AnalysisRunManager:
    def __init__(
        self,
        pipeline: AnalysisPipeline,
        *,
        timeout_seconds: float = 180,
        ttl_seconds: float = 600,
    ) -> None:
        self._pipeline = pipeline
        self._timeout_seconds = timeout_seconds
        self._ttl = timedelta(seconds=ttl_seconds)
        self._records: dict[str, RunRecord] = {}

    async def create(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        *,
        owner_session_id: str | None = None,
        on_terminal: Callable[[], Awaitable[None]] | None = None,
    ) -> AnalysisRunView:
        self._prune()
        run_id = str(uuid4())
        record = RunRecord(
            view=AnalysisRunView(
                id=run_id,
                source_id=source.id,
                trigger_seconds=trigger_seconds,
                status=RunStatus.QUEUED,
                stage=RunStage.QUEUED,
                source_duration_seconds=source.duration_seconds,
            ),
            owner_session_id=owner_session_id,
            on_terminal=on_terminal,
        )
        self._records[run_id] = record
        record.task = asyncio.create_task(self._execute(record, source))
        return record.view.model_copy(deep=True)

    def get(
        self,
        run_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> AnalysisRunView:
        return self._record(run_id, owner_session_id=owner_session_id).view.model_copy(deep=True)

    async def cancel(
        self,
        run_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> AnalysisRunView:
        record = self._record(run_id, owner_session_id=owner_session_id)
        if record.view.status in TERMINAL_STATUSES:
            return record.view.model_copy(deep=True)
        if record.task is not None:
            record.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await record.task
        if record.view.status not in TERMINAL_STATUSES:
            await self._notify_terminal(record)
            record.view.status = RunStatus.CANCELLED
            record.view.stage = RunStage.CANCELLED
            record.terminal_at = datetime.now(UTC)
            record.view.updated_at = record.terminal_at
            await self._event(record, "run.cancelled", {"stage": RunStage.CANCELLED.value})
        await self._notify_terminal(record)
        return record.view.model_copy(deep=True)

    async def events(
        self,
        run_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> AsyncIterator[AnalysisEvent]:
        record = self._record(run_id, owner_session_id=owner_session_id)
        index = 0
        while True:
            while index < len(record.events):
                event = record.events[index]
                index += 1
                yield event
            if record.view.status in TERMINAL_STATUSES:
                return
            async with record.condition:
                try:
                    await asyncio.wait_for(record.condition.wait(), timeout=15)
                except TimeoutError:
                    yield AnalysisEvent(
                        sequence=index,
                        type="heartbeat",
                        run_id=run_id,
                        data=_progress_data(record.view),
                    )

    async def _execute(self, record: RunRecord, source: VideoSource) -> None:
        record.view.status = RunStatus.RUNNING
        record.view.stage = RunStage.PREPARING_MEDIA
        await self._event(record, "run.started", {"stage": RunStage.PREPARING_MEDIA.value})

        async def emit(
            stage: RunStage,
            event_type: str,
            data: dict[str, object],
        ) -> None:
            record.view.stage = stage
            if event_type in {"branch.completed", "visual_chunk.completed"}:
                evidence_count = data.get("evidence_count")
                if (
                    isinstance(evidence_count, int)
                    and not isinstance(evidence_count, bool)
                    and evidence_count >= 0
                ):
                    record.view.discovered_candidate_count = max(
                        record.view.discovered_candidate_count,
                        evidence_count,
                    )
            processed_seconds = data.get("processed_seconds")
            if (
                isinstance(processed_seconds, int | float)
                and not isinstance(processed_seconds, bool)
                and math.isfinite(processed_seconds)
                and 0 <= processed_seconds <= source.analysis_duration_seconds
            ):
                record.view.processed_seconds = max(
                    record.view.processed_seconds,
                    float(processed_seconds),
                )
            record.view.updated_at = datetime.now(UTC)
            await self._event(record, event_type, {"stage": stage.value, **data})

        try:
            async with asyncio.timeout(self._timeout_seconds):
                output = await self._pipeline.analyze(
                    source,
                    None,
                    emit,
                )
            _validate_pipeline_candidates(output.candidates, source)
            candidates = _offset_candidates(output.candidates, source.analysis_start_seconds)
            processed_seconds, coverage_gaps = _coverage_for_source(output, source)
            await self._notify_terminal(record)
            record.view.status = RunStatus.COMPLETED
            record.view.stage = RunStage.COMPLETED
            record.view.candidates = candidates
            record.view.warnings = output.warnings
            record.view.empty_reason = output.empty_reason
            record.view.processed_seconds = processed_seconds
            record.view.discovered_candidate_count = len(candidates)
            record.view.coverage_status = output.coverage_status
            record.view.coverage_gaps = coverage_gaps
            await self._event(
                record,
                "run.completed",
                {
                    "stage": RunStage.COMPLETED.value,
                    "candidate_count": len(candidates),
                },
            )
        except asyncio.CancelledError:
            await self._notify_terminal(record)
            record.view.status = RunStatus.CANCELLED
            record.view.stage = RunStage.CANCELLED
            await self._event(record, "run.cancelled", {"stage": RunStage.CANCELLED.value})
            raise
        except TimeoutError:
            await self._fail(
                record,
                ErrorCode.TIMEOUT,
                "动作分析超时，请重试",
                retryable=True,
            )
        except PipelineFailure as error:
            try:
                code = ErrorCode(error.code)
            except ValueError:
                code = ErrorCode.PROVIDER_ERROR
            await self._fail(
                record,
                code,
                _public_failure_message(code),
                retryable=error.retryable,
            )
        except Exception:
            await self._fail(
                record,
                ErrorCode.PROVIDER_ERROR,
                "动作分析暂时不可用，请稍后重试",
                retryable=True,
            )
        finally:
            if record.view.status in TERMINAL_STATUSES:
                record.terminal_at = datetime.now(UTC)
                record.view.updated_at = record.terminal_at
                async with record.condition:
                    record.condition.notify_all()
                await self._notify_terminal(record)

    async def _fail(
        self,
        record: RunRecord,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        await self._notify_terminal(record)
        record.view.status = RunStatus.FAILED
        record.view.stage = RunStage.FAILED
        record.view.error = AnalysisError(code=code, message=message, retryable=retryable)
        await self._event(
            record,
            "run.failed",
            {"stage": RunStage.FAILED.value, "error_code": code.value},
        )

    async def _event(
        self,
        record: RunRecord,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        event = AnalysisEvent(
            sequence=len(record.events) + 1,
            type=event_type,
            run_id=record.view.id,
            data={**data, **_progress_data(record.view)},
        )
        record.events.append(event)
        log_safe_fields(
            LOGGER,
            run_id=record.view.id,
            source_id=record.view.source_id,
            stage=record.view.stage.value,
            elapsed_ms=round((time.perf_counter() - record.started_monotonic) * 1000),
            model_version=data.get("model_version"),
            skill_version=data.get("skill_version"),
            provider_request_id=data.get("provider_request_id"),
            error_code=data.get("error_code"),
        )
        async with record.condition:
            record.condition.notify_all()

    def _record(
        self,
        run_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> RunRecord:
        self._prune()
        try:
            record = self._records[run_id]
        except KeyError as error:
            raise KeyError(f"unknown run_id: {run_id}") from error
        if owner_session_id is not None and record.owner_session_id != owner_session_id:
            raise KeyError(f"unknown run_id: {run_id}")
        return record

    def _prune(self) -> None:
        cutoff = datetime.now(UTC) - self._ttl
        expired = [
            run_id
            for run_id, record in self._records.items()
            if record.terminal_at is not None and record.terminal_at < cutoff
        ]
        for run_id in expired:
            record = self._records.pop(run_id)
            if record.task is not None and not record.task.done():
                record.task.cancel()

    async def close(self) -> None:
        tasks = [record.task for record in self._records.values() if record.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for record in self._records.values():
            await self._notify_terminal(record)
        self._records.clear()

    async def _notify_terminal(self, record: RunRecord) -> None:
        if record.terminal_notified:
            return
        record.terminal_notified = True
        if record.on_terminal is not None:
            await record.on_terminal()


def as_pipeline(value: object) -> AnalysisPipeline:
    return cast(AnalysisPipeline, value)


def _public_failure_message(code: ErrorCode) -> str:
    return {
        ErrorCode.CONFIGURATION_ERROR: "动作分析服务尚未就绪，请联系现场工作人员",
        ErrorCode.PROVIDER_ERROR: "动作分析暂时不可用，请稍后重试",
        ErrorCode.SCHEMA_ERROR: "这次没有分析成功，请重试",
        ErrorCode.MEDIA_ERROR: "视频片段准备失败，请重试",
        ErrorCode.TIMEOUT: "动作分析超时，请重试",
        ErrorCode.CANCELLED: "动作分析已取消",
    }[code]


def _progress_data(view: AnalysisRunView) -> dict[str, object]:
    return {
        "source_duration_seconds": view.source_duration_seconds,
        "processed_seconds": view.processed_seconds,
        "discovered_candidate_count": view.discovered_candidate_count,
        "coverage_status": view.coverage_status.value if view.coverage_status is not None else None,
        "coverage_gaps": [gap.model_dump(mode="json") for gap in view.coverage_gaps],
    }


def _offset_candidates(
    candidates: list[AnalysisCandidate],
    offset_seconds: float,
) -> list[AnalysisCandidate]:
    if offset_seconds == 0:
        return candidates
    return [
        candidate.model_copy(
            update={
                "segment": offset_span(candidate.segment, offset_seconds),
                "tips": [offset_tip(tip, offset_seconds) for tip in candidate.tips],
                "playback_options": [
                    offset_span(option, offset_seconds) for option in candidate.playback_options
                ],
                "parameter_conflicts": [
                    conflict.model_copy(
                        update={
                            "alternatives": [
                                alternative.model_copy(
                                    update={
                                        "evidence": [
                                            offset_span(span, offset_seconds)
                                            for span in alternative.evidence
                                        ]
                                    }
                                )
                                for alternative in conflict.alternatives
                            ]
                        }
                    )
                    for conflict in candidate.parameter_conflicts
                ],
                "evidence": [offset_span(span, offset_seconds) for span in candidate.evidence],
            }
        )
        for candidate in candidates
    ]


def _validate_pipeline_candidates(
    candidates: list[AnalysisCandidate],
    source: VideoSource,
) -> None:
    duration_seconds = source.analysis_duration_seconds
    for candidate in candidates:
        if candidate.source_id != source.id:
            raise PipelineFailure(
                "schema_error",
                "candidate source does not match the requested source",
                retryable=True,
            )
        if not _span_is_within_duration(candidate.segment, duration_seconds):
            raise PipelineFailure(
                "schema_error",
                "candidate segment is outside the requested range",
                retryable=True,
            )
        spans: list[Segment] = [
            *candidate.evidence,
            *candidate.playback_options,
            *(tip.evidence for tip in candidate.tips),
            *(
                span
                for conflict in candidate.parameter_conflicts
                for alternative in conflict.alternatives
                for span in alternative.evidence
            ),
        ]
        for evidence in spans:
            if not _span_is_within_duration(evidence, duration_seconds):
                raise PipelineFailure(
                    "schema_error",
                    "candidate evidence is outside the requested range",
                    retryable=True,
                )


def _span_is_within_duration(span: Segment, duration_seconds: float) -> bool:
    return (
        math.isfinite(span.start_seconds)
        and math.isfinite(span.end_seconds)
        and span.end_seconds <= duration_seconds
    )


def _coverage_for_source(
    output: PipelineOutput,
    source: VideoSource,
) -> tuple[float, list[CoverageGap]]:
    duration_seconds = source.analysis_duration_seconds
    if output.coverage_status == CoverageStatus.COMPLETE:
        return duration_seconds, []
    if output.processed_seconds is None or output.processed_seconds > duration_seconds:
        raise PipelineFailure(
            "schema_error",
            "partial analysis coverage is outside the requested range",
            retryable=True,
        )
    for gap in output.coverage_gaps:
        if gap.end_seconds > duration_seconds:
            raise PipelineFailure(
                "schema_error",
                "partial analysis gap is outside the requested range",
                retryable=True,
            )
    gap_seconds = sum(gap.end_seconds - gap.start_seconds for gap in output.coverage_gaps)
    if not math.isclose(
        output.processed_seconds + gap_seconds,
        duration_seconds,
        rel_tol=0,
        abs_tol=0.01,
    ):
        raise PipelineFailure(
            "schema_error",
            "partial analysis coverage does not match the requested range",
            retryable=True,
        )
    offset_seconds = source.analysis_start_seconds
    if offset_seconds == 0:
        return output.processed_seconds, output.coverage_gaps
    return (
        output.processed_seconds,
        [
            gap.model_copy(
                update={
                    "start_seconds": gap.start_seconds + offset_seconds,
                    "end_seconds": gap.end_seconds + offset_seconds,
                }
            )
            for gap in output.coverage_gaps
        ],
    )
