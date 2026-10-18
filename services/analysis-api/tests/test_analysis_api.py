import asyncio
import json
import logging
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app, stream_run_events
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    CoverageGap,
    CoverageStatus,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.observability import ALLOWED_LOG_FIELDS
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.sources import SourceCatalog, VideoSource


class SuccessfulPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        await emit(RunStage.FUSING_CANDIDATES, "stage.changed", {})
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-1",
                    name="拖拽弯举",
                    source_id=source.id,
                    segment=Segment(start_seconds=41, end_seconds=51),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=41,
                            end_seconds=51,
                        )
                    ],
                    needs_confirmation=True,
                )
            ]
        )


class TriggerCapturePipeline:
    def __init__(self) -> None:
        self.received_trigger: float | None | object = object()

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del source, emit
        self.received_trigger = trigger_seconds
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


class SlowPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        await asyncio.sleep(30)
        return PipelineOutput()


class SecretFailurePipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: EmitCallback,
    ) -> PipelineOutput:
        raise PipelineFailure(
            "provider_error",
            "provider-secret-response-must-not-be-logged",
            retryable=False,
        )


class PartialCoveragePipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del source, trigger_seconds, emit
        return PipelineOutput(
            coverage_status=CoverageStatus.PARTIAL,
            processed_seconds=44,
            coverage_gaps=[
                CoverageGap(
                    start_seconds=20,
                    end_seconds=30,
                    reason="provider_error",
                    retryable=True,
                )
            ],
        )


class InsufficientCoveragePipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del source, trigger_seconds, emit
        return PipelineOutput(
            empty_reason="insufficient_evidence",
            coverage_status=CoverageStatus.INSUFFICIENT,
            processed_seconds=24,
            coverage_gaps=[
                CoverageGap(
                    start_seconds=24,
                    end_seconds=54,
                    reason="timeout",
                    retryable=True,
                )
            ],
        )


class ProvisionalProgressPipeline:
    def __init__(self) -> None:
        self.emitted = asyncio.Event()
        self.release = asyncio.Event()

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del source, trigger_seconds
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "visual_chunk.completed",
            {"branch": "visual", "evidence_count": 2, "processed_seconds": 54.0},
        )
        self.emitted.set()
        await self.release.wait()
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


class InconsistentPartialCoveragePipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del source, trigger_seconds, emit
        return PipelineOutput(
            coverage_status=CoverageStatus.PARTIAL,
            processed_seconds=45,
            coverage_gaps=[
                CoverageGap(
                    start_seconds=20,
                    end_seconds=30,
                    reason="unknown",
                    retryable=True,
                )
            ],
        )


def source_catalog(tmp_path: Path, *, duration_seconds: float = 54) -> SourceCatalog:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"not-used-by-the-test-pipeline")
    return SourceCatalog(
        [
            VideoSource(
                id="legacy-arm-workout",
                title="本地联调视频",
                path=path,
                duration_seconds=duration_seconds,
            )
        ]
    )


def make_test_access() -> AccessManager:
    return AccessManager(
        cookie_secret="test-cookie-secret-with-at-least-32-bytes",
        judge_access_code="test-judge-code",
        public_concurrency=1,
    )


async def wait_for_status(client: httpx.AsyncClient, run_id: str, status: str) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f"/api/v1/analysis-runs/{run_id}")
        payload = cast(dict[str, object], response.json())
        if payload["status"] == status:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"run did not reach {status}")


@pytest.mark.asyncio
async def test_sources_and_completed_run_are_observable_through_http(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        sources = await client.get("/api/v1/sources")
        assert sources.status_code == 200
        assert sources.json() == [
            {
                "id": "legacy-arm-workout",
                "title": "本地联调视频",
                "media_url": "/api/v1/sources/legacy-arm-workout/media",
                "duration_seconds": 54.0,
                "origin_url": None,
            }
        ]

        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout"},
        )
        assert created.status_code == 202
        assert created.json()["trigger_seconds"] is None
        run_id = created.json()["id"]

        completed = await wait_for_status(client, run_id, "completed")
        assert completed["candidates"][0]["name"] == "拖拽弯举"  # type: ignore[index]

        events = await client.get(f"/api/v1/analysis-runs/{run_id}/events")
        assert events.status_code == 200
        assert "event: run.completed" in events.text


@pytest.mark.asyncio
async def test_completed_run_and_sse_report_real_non_chunked_coverage(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout"},
        )
        assert created.json()["source_duration_seconds"] == 54.0
        assert created.json()["processed_seconds"] == 0.0
        assert created.json()["discovered_candidate_count"] == 0
        assert created.json()["coverage_status"] is None
        assert created.json()["coverage_gaps"] == []

        completed = await wait_for_status(client, created.json()["id"], "completed")
        assert completed["source_duration_seconds"] == 54.0
        assert completed["processed_seconds"] == 54.0
        assert completed["discovered_candidate_count"] == 1
        assert completed["coverage_status"] == "complete"
        assert completed["coverage_gaps"] == []

        events = await client.get(f"/api/v1/analysis-runs/{created.json()['id']}/events")

    completed_payload = next(
        json.loads(line.removeprefix("data: "))
        for line in events.text.splitlines()
        if line.startswith("data: ") and '"type": "run.completed"' in line
    )
    assert completed_payload["data"] | {
        "source_duration_seconds": 54.0,
        "processed_seconds": 54.0,
        "discovered_candidate_count": 1,
        "coverage_status": "complete",
        "coverage_gaps": [],
    } == completed_payload["data"]


@pytest.mark.asyncio
async def test_injected_reliable_partial_output_is_returned_by_get_and_sse(
    tmp_path: Path,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=PartialCoveragePipeline(),
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout"},
        )
        completed = await wait_for_status(client, created.json()["id"], "completed")
        events = await client.get(f"/api/v1/analysis-runs/{created.json()['id']}/events")

    expected_gap = {
        "start_seconds": 20.0,
        "end_seconds": 30.0,
        "reason": "provider_error",
        "retryable": True,
    }
    assert completed["processed_seconds"] == 44.0
    assert completed["coverage_status"] == "partial"
    assert completed["coverage_gaps"] == [expected_gap]
    assert '"coverage_status": "partial"' in events.text
    assert json.dumps(expected_gap, ensure_ascii=False) in events.text


@pytest.mark.asyncio
async def test_insufficient_evidence_is_a_completed_retryable_coverage_outcome(
    tmp_path: Path,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=InsufficientCoveragePipeline(),
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout"},
        )
        completed = await wait_for_status(client, created.json()["id"], "completed")

    assert completed["coverage_status"] == "insufficient"
    assert completed["empty_reason"] == "insufficient_evidence"
    assert completed["candidates"] == []
    assert completed["processed_seconds"] == 24.0
    assert completed["coverage_gaps"] == [
        {
            "start_seconds": 24.0,
            "end_seconds": 54.0,
            "reason": "timeout",
            "retryable": True,
        }
    ]


def test_coverage_gap_reason_rejects_non_allowlisted_provider_text() -> None:
    with pytest.raises(ValidationError):
        CoverageGap(
            start_seconds=20,
            end_seconds=30,
            reason="provider transcript and path detail",
            retryable=True,
        )


def test_coverage_gap_must_be_retryable() -> None:
    with pytest.raises(ValidationError):
        CoverageGap(
            start_seconds=20,
            end_seconds=30,
            reason="provider_error",
            retryable=False,
        )


@pytest.mark.parametrize(
    "gaps",
    [
        [
            CoverageGap(
                start_seconds=20,
                end_seconds=30,
                reason="timeout",
                retryable=True,
            ),
            CoverageGap(
                start_seconds=10,
                end_seconds=15,
                reason="provider_error",
                retryable=True,
            ),
        ],
        [
            CoverageGap(
                start_seconds=10,
                end_seconds=20,
                reason="timeout",
                retryable=True,
            ),
            CoverageGap(
                start_seconds=19,
                end_seconds=25,
                reason="provider_error",
                retryable=True,
            ),
        ],
    ],
)
def test_partial_pipeline_rejects_unsorted_or_overlapping_coverage_gaps(
    gaps: list[CoverageGap],
) -> None:
    with pytest.raises(ValueError):
        PipelineOutput(
            coverage_status=CoverageStatus.PARTIAL,
            processed_seconds=30,
            coverage_gaps=gaps,
        )


@pytest.mark.asyncio
async def test_inconsistent_partial_coverage_fails_closed_as_a_schema_error(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=InconsistentPartialCoveragePipeline(),
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout"},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "schema_error",
        "message": "这次没有分析成功，请重试",
        "retryable": True,
    }
    assert failed["coverage_status"] is None
    assert failed["coverage_gaps"] == []


@pytest.mark.asyncio
async def test_completed_visual_chunk_updates_real_provisional_get_and_sse_progress(
    tmp_path: Path,
) -> None:
    pipeline = ProvisionalProgressPipeline()
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=pipeline,
        access=make_test_access(),
    )
    manager = app.state.run_manager
    source = app.state.source_catalog.get("legacy-arm-workout")
    created = await manager.create(source, None)
    await pipeline.emitted.wait()

    running = manager.get(created.id)
    assert running.status == "running"
    assert running.processed_seconds == 54.0
    assert running.discovered_candidate_count == 2

    async def connected() -> bool:
        return False

    stream = stream_run_events(manager, created.id, connected)
    for _ in range(10):
        event = await anext(stream)
        if "event: visual_chunk.completed" in event:
            payload = json.loads(event.split("data: ", maxsplit=1)[1])
            break
    else:
        raise AssertionError("visual_chunk.completed event not replayed")
    assert payload["data"]["processed_seconds"] == 54.0
    assert payload["data"]["discovered_candidate_count"] == 2
    await stream.aclose()

    pipeline.release.set()
    for _ in range(100):
        if manager.get(created.id).status == "completed":
            break
        await asyncio.sleep(0.01)
    assert manager.get(created.id).discovered_candidate_count == 0
    await manager.close()


@pytest.mark.asyncio
async def test_legacy_trigger_is_recorded_but_never_forwarded_to_analysis(
    tmp_path: Path,
) -> None:
    pipeline = TriggerCapturePipeline()
    app = create_app(catalog=source_catalog(tmp_path), pipeline=pipeline, access=make_test_access())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 999},
        )
        completed = await wait_for_status(client, created.json()["id"], "completed")

    assert created.status_code == 202
    assert completed["trigger_seconds"] == 999
    assert pipeline.received_trigger is None


@pytest.mark.asyncio
async def test_unknown_source_is_rejected_without_starting_a_run(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SuccessfulPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "../../private.mp4", "trigger_seconds": 10},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_controlled_media_supports_http_range(tmp_path: Path) -> None:
    catalog = source_catalog(tmp_path)
    source_bytes = catalog.get("legacy-arm-workout").path.read_bytes()
    app = create_app(catalog=catalog, pipeline=SuccessfulPipeline(), access=make_test_access())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get(
            "/api/v1/sources/legacy-arm-workout/media",
            headers={"Range": "bytes=0-3"},
        )

    assert response.status_code == 206
    assert response.content == source_bytes[:4]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == f"bytes 0-3/{len(source_bytes)}"


@pytest.mark.asyncio
async def test_delete_cancels_an_in_flight_run(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SlowPipeline(), access=make_test_access()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        run_id = created.json()["id"]
        cancelled = await client.delete(f"/api/v1/analysis-runs/{run_id}")
        assert cancelled.status_code == 202
        view = await wait_for_status(client, run_id, "cancelled")
        assert view["stage"] == "cancelled"


@pytest.mark.asyncio
async def test_run_timeout_is_an_explicit_failure(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SlowPipeline(),
        timeout_seconds=0.01,
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["stage"] == "failed"
    assert failed["error"] == {
        "code": "timeout",
        "message": "动作分析超时，请重试",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_closing_and_reconnecting_sse_does_not_cancel_an_in_flight_run(
    tmp_path: Path,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path), pipeline=SlowPipeline(), access=make_test_access()
    )
    manager = app.state.run_manager
    source = app.state.source_catalog.get("legacy-arm-workout")
    created = await manager.create(source, 45)

    async def still_connected() -> bool:
        return False

    stream = stream_run_events(manager, created.id, still_connected)
    first_event = await anext(stream)
    assert "event: run.started" in first_event
    await stream.aclose()

    assert manager.get(created.id).status == "running"

    reconnected = stream_run_events(manager, created.id, still_connected)
    replayed_event = await anext(reconnected)
    assert "event: run.started" in replayed_event
    await reconnected.aclose()

    assert manager.get(created.id).status == "running"
    await manager.cancel(created.id)
    await manager.close()


@pytest.mark.asyncio
async def test_runtime_logs_keep_only_allowlisted_diagnostic_fields(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SecretFailurePipeline(),
        access=make_test_access(),
    )
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.runs"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            created = await client.post(
                "/api/v1/analysis-runs",
                json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
            )
            await wait_for_status(client, created.json()["id"], "failed")

    messages = [
        record.getMessage() for record in caplog.records if record.name == "hakimi_analysis.runs"
    ]
    payloads = [json.loads(message) for message in messages]
    assert payloads
    assert all(set(payload) <= ALLOWED_LOG_FIELDS for payload in payloads)
    assert any(payload.get("error_code") == "provider_error" for payload in payloads)
    assert all("provider-secret-response-must-not-be-logged" not in message for message in messages)


@pytest.mark.asyncio
async def test_provider_failure_returns_only_a_safe_public_message(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SecretFailurePipeline(),
        access=make_test_access(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "provider_error",
        "message": "动作分析暂时不可用，请稍后重试",
        "retryable": False,
    }
    assert "provider-secret-response-must-not-be-logged" not in str(failed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("GET", ""),
        ("DELETE", ""),
        ("GET", "/events"),
    ],
)
async def test_terminal_run_is_unavailable_from_every_http_path_after_ttl(
    tmp_path: Path,
    method: str,
    path_suffix: str,
) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SuccessfulPipeline(),
        access=make_test_access(),
        ttl_seconds=1,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "legacy-arm-workout", "trigger_seconds": 45},
        )
        run_id = created.json()["id"]
        await wait_for_status(client, run_id, "completed")
        await asyncio.sleep(1.05)

        response = await client.request(
            method,
            f"/api/v1/analysis-runs/{run_id}{path_suffix}",
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_manager_event_stream_prunes_a_terminal_run_after_ttl(tmp_path: Path) -> None:
    app = create_app(
        catalog=source_catalog(tmp_path),
        pipeline=SuccessfulPipeline(),
        access=make_test_access(),
        ttl_seconds=1,
    )
    manager = app.state.run_manager
    source = app.state.source_catalog.get("legacy-arm-workout")
    created = await manager.create(source, 45)
    for _ in range(100):
        if manager.get(created.id).status == "completed":
            break
        await asyncio.sleep(0.001)
    else:
        raise AssertionError("run did not complete")
    await asyncio.sleep(1.05)

    events = manager.events(created.id)
    with pytest.raises(KeyError):
        await anext(events)
    await events.aclose()
    await manager.close()
