import asyncio
import json
import logging
import subprocess
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import imageio_ffmpeg
import pytest
from fastapi.routing import APIRoute

from hakimi_analysis.access import ACCESS_COOKIE_NAME, AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import DeterministicTestPipeline
from hakimi_analysis.media import LocalMediaProcessor
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    Segment,
    SourceClip,
    SourceTip,
)
from hakimi_analysis.observability import ALLOWED_LOG_FIELDS
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.sources import SourceCatalog, VideoSource

LOCAL_SOURCE_ID = "local:62f31c4b-bd1c-4b6a-8ad8-c6121b56135a"
SECOND_LOCAL_SOURCE_ID = "local:a55df85a-e80d-4c85-807f-cd4a9c26f7e5"


def make_test_access(
    *,
    public_attempt_limit: int = 1,
    public_concurrency: int = 1,
) -> AccessManager:
    return AccessManager(
        cookie_secret="test-cookie-secret-with-at-least-32-bytes",
        judge_access_code="test-judge-code",
        public_concurrency=public_concurrency,
        public_attempt_limit=public_attempt_limit,
    )


async def wait_for_status(client: httpx.AsyncClient, run_id: str, status: str) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f"/api/v1/analysis-runs/{run_id}")
        payload = cast(dict[str, object], response.json())
        if payload["status"] == status:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"run did not reach {status}")


class RelativeCandidatePipeline:
    def __init__(self) -> None:
        self.source: VideoSource | None = None

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.source = source
        assert source.path.is_file()
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-local",
                    name="深蹲",
                    source_id=source.id,
                    segment=Segment(start_seconds=1, end_seconds=3),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=1.25,
                            end_seconds=2.75,
                        )
                    ],
                    needs_confirmation=False,
                    tips=[
                        SourceTip(
                            text="保持呼吸",
                            category="breathing",
                            evidence=EvidenceSpan(
                                type=EvidenceType.VISUAL, start_seconds=1.5, end_seconds=2.5
                            ),
                        )
                    ],
                    playback_options=[SourceClip(label="动作示范", start_seconds=1, end_seconds=3)],
                )
            ]
        )


class BlockingCapturePipeline:
    def __init__(self) -> None:
        self.sources: list[VideoSource] = []
        self.cancelled_source_ids: set[str] = set()

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.sources.append(source)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled_source_ids.add(source.id)
            raise
        return PipelineOutput()


class SecretFailureCapturePipeline:
    def __init__(self) -> None:
        self.source: VideoSource | None = None

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.source = source
        raise PipelineFailure(
            "provider_error",
            f"private-filename.mp4 {source.path} secret transcript text",
            retryable=True,
        )


class RealMediaEmptyPipeline:
    def __init__(self, media: LocalMediaProcessor) -> None:
        self._media = media
        self.source_path: Path | None = None

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        self.source_path = source.path
        async with self._media.prepare_source(
            source.path,
            source.analysis_duration_seconds,
            start_seconds=source.analysis_start_seconds,
        ) as prepared:
            assert prepared.audio_path.is_file()
            assert prepared.contact_sheet_ready is not None
            await prepared.contact_sheet_ready
            assert prepared.contact_sheet_path is not None
            assert prepared.contact_sheet_path.is_file()
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


class CandidateBoundaryPipeline:
    def __init__(
        self,
        *,
        segment_end_seconds: float = 3,
        evidence_end_seconds: float = 2.75,
        candidate_source_id: str | None = None,
    ) -> None:
        self._segment_end_seconds = segment_end_seconds
        self._evidence_end_seconds = evidence_end_seconds
        self._candidate_source_id = candidate_source_id

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        del trigger_seconds, emit
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-local",
                    name="深蹲",
                    source_id=self._candidate_source_id or source.id,
                    segment=Segment(start_seconds=1, end_seconds=self._segment_end_seconds),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=1.25,
                            end_seconds=self._evidence_end_seconds,
                        )
                    ],
                    needs_confirmation=True,
                )
            ]
        )


class FailsIfConsumedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.consumed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.consumed = True
        raise AssertionError("request body must not be consumed")
        yield b""  # pragma: no cover


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, payload: bytes, *, chunk_size: int = 4096) -> None:
        self._payload = payload
        self._chunk_size = chunk_size

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self._payload), self._chunk_size):
            yield self._payload[offset : offset + self._chunk_size]


class PausingStream(httpx.AsyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.paused = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._payload[:4096]
        self.paused.set()
        await asyncio.Event().wait()
        yield self._payload[4096:]  # pragma: no cover


def test_local_upload_completes_without_async_subprocess_support(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    generated = subprocess.run(
        (
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101820:s=320x568:r=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "2",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(source),
        ),
        capture_output=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr.decode("utf-8", errors="replace")

    upload_root = tmp_path / "uploads"
    media_root = tmp_path / "media"
    pipeline = RealMediaEmptyPipeline(LocalMediaProcessor(temp_root=media_root))
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_upload_temp_root=upload_root,
    )
    loop = asyncio.new_event_loop()

    async def unsupported_subprocess_exec(*_args: object, **_kwargs: object) -> None:
        raise NotImplementedError

    loop.subprocess_exec = unsupported_subprocess_exec  # type: ignore[assignment]

    async def exercise() -> dict[str, object]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            created = await client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("source.mp4", source.read_bytes(), "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )
            assert created.status_code == 202
            return await wait_for_status(client, created.json()["id"], "completed")

    try:
        completed = loop.run_until_complete(exercise())
    finally:
        loop.close()

    assert completed["candidates"] == []
    assert completed["empty_reason"] == "no_evidence"
    assert pipeline.source_path is not None
    assert not pipeline.source_path.exists()
    assert list(upload_root.iterdir()) == []
    assert list(media_root.iterdir()) == []


@pytest.mark.asyncio
async def test_declared_oversize_local_upload_is_rejected_before_reading_body(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    probe_called = False

    def duration_probe(_: Path) -> float:
        nonlocal probe_called
        probe_called = True
        return 10.0

    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_max_bytes=4,
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    body = FailsIfConsumedStream()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            content=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=not-consumed",
                "Content-Length": "10000000",
            },
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "视频文件超过上传大小限制"}
    assert response.headers["cache-control"] == "no-store"
    assert body.consumed is False
    assert probe_called is False
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_declared_oversize_upload_keeps_allowed_development_cors_headers(
    tmp_path: Path,
) -> None:
    body = FailsIfConsumedStream()
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_max_bytes=4,
        local_upload_temp_root=tmp_path / "uploads",
        local_duration_probe=lambda _: 10.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            content=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=not-consumed",
                "Content-Length": "10000000",
                "Origin": "http://localhost:5173",
            },
        )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert body.consumed is False


@pytest.mark.asyncio
async def test_streamed_oversize_local_upload_is_rejected_before_endpoint(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    probe_called = False

    def duration_probe(_: Path) -> float:
        nonlocal probe_called
        probe_called = True
        return 10.0

    encoded = httpx.Request(
        "POST",
        "https://test/api/v1/analysis-runs/local",
        files={"media": ("private.mp4", b"x" * 70_000, "video/mp4")},
        data={"local_source_id": LOCAL_SOURCE_ID},
    )
    payload = encoded.read()
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_max_bytes=4,
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            content=ChunkedStream(payload),
            headers={"Content-Type": encoded.headers["Content-Type"]},
        )
        assert response.status_code == 413
        assert probe_called is False
        retried = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"1234", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        await wait_for_status(client, retried.json()["id"], "completed")

    assert response.json() == {"detail": "视频文件超过上传大小限制"}
    assert response.headers["cache-control"] == "no-store"
    assert retried.status_code == 202
    assert probe_called is True
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_cross_origin_local_upload_is_rejected_before_reading_body(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    body = FailsIfConsumedStream()
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 10.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            content=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=not-consumed",
                "Content-Length": "1",
                "Origin": "https://evil.example",
            },
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "请求来源无效"}
    assert body.consumed is False
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_unavailable_session_capacity_is_rejected_before_reading_body(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    body = FailsIfConsumedStream()
    probe_called = False

    def duration_probe(_: Path) -> float:
        nonlocal probe_called
        probe_called = True
        return 10.0

    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(public_concurrency=0),
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            content=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=not-consumed",
                "Content-Length": "1",
            },
        )

    assert response.status_code == 429
    assert response.json() == {"detail": "真实动作分析暂时繁忙，请稍后重试"}
    assert response.headers["retry-after"] == "15"
    assert response.headers["x-trainpal-admission-reason"] == "capacity"
    assert response.headers["cache-control"] == "no-store"
    assert body.consumed is False
    assert probe_called is False
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_same_ip_cooldown_is_rejected_before_reading_a_fresh_cookie_upload(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    body = FailsIfConsumedStream()
    access = make_test_access()
    previous_session = access.resolve(None)
    lease = await access.reserve(
        previous_session,
        source_id="local:previous",
        client_ip="198.51.100.10",
    )
    await lease.release()
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=access,
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 10.0,
    )
    transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1002))
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            content=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=not-consumed",
                "Content-Length": "1",
            },
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    assert response.headers["x-trainpal-admission-reason"] == "rate_limit"
    assert response.headers["cache-control"] == "no-store"
    assert body.consumed is False
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_concurrent_fresh_session_upload_is_rejected_before_reading_second_body(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    second_body = FailsIfConsumedStream()
    probe_started = threading.Event()
    release_probe = threading.Event()
    probe_calls = 0

    def duration_probe(_: Path) -> float:
        nonlocal probe_calls
        probe_calls += 1
        probe_started.set()
        assert release_probe.wait(3)
        return 20.0

    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(public_attempt_limit=10),
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    first_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1001))
    second_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1002))
    async with (
        httpx.AsyncClient(transport=first_transport, base_url="https://test") as first_client,
        httpx.AsyncClient(transport=second_transport, base_url="https://test") as second_client,
    ):
        first_task = asyncio.create_task(
            first_client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("first.mp4", b"first-video", "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )
        )
        assert await asyncio.to_thread(probe_started.wait, 1)
        try:
            rejected = await second_client.post(
                "/api/v1/analysis-runs/local",
                content=second_body,
                headers={
                    "Content-Type": "multipart/form-data; boundary=not-consumed",
                    "Content-Length": "1",
                },
            )

            assert rejected.status_code == 429
            assert rejected.headers["retry-after"] == "15"
            assert rejected.headers["x-trainpal-admission-reason"] == "capacity"
            assert rejected.headers["cache-control"] == "no-store"
            assert second_body.consumed is False
            assert probe_calls == 1
            assert len(list(upload_root.iterdir())) == 1
        finally:
            release_probe.set()

        first = await first_task
        assert first.status_code == 202
        await wait_for_status(first_client, first.json()["id"], "completed")

        retried = await first_client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("retry.mp4", b"retry-video", "video/mp4")},
            data={"local_source_id": SECOND_LOCAL_SOURCE_ID},
        )
        assert retried.status_code == 202
        await wait_for_status(first_client, retried.json()["id"], "completed")

    assert probe_calls == 2
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_slow_local_upload_reserves_the_shared_public_slot_before_reading_its_body(
    tmp_path: Path,
) -> None:
    controlled_path = tmp_path / "controlled.mp4"
    controlled_path.write_bytes(b"controlled-video")
    catalog = SourceCatalog(
        [
            VideoSource(
                id="controlled-01",
                title="controlled",
                path=controlled_path,
                duration_seconds=20,
            )
        ]
    )
    encoded = httpx.Request(
        "POST",
        "https://test/api/v1/analysis-runs/local",
        files={"media": ("private.mp4", b"x" * 8192, "video/mp4")},
        data={"local_source_id": LOCAL_SOURCE_ID},
    )
    slow_body = PausingStream(encoded.read())
    app = create_app(
        catalog=catalog,
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "uploads",
        local_duration_probe=lambda _: 20.0,
    )
    local_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1001))
    controlled_transport = httpx.ASGITransport(app=app, client=("198.51.100.20", 1002))
    status_transport = httpx.ASGITransport(app=app, client=("198.51.100.30", 1003))
    async with (
        httpx.AsyncClient(transport=local_transport, base_url="https://test") as local_client,
        httpx.AsyncClient(
            transport=controlled_transport,
            base_url="https://test",
        ) as controlled_client,
        httpx.AsyncClient(transport=status_transport, base_url="https://test") as status_client,
    ):
        local_task = asyncio.create_task(
            local_client.post(
                "/api/v1/analysis-runs/local",
                content=slow_body,
                headers={"Content-Type": encoded.headers["Content-Type"]},
            )
        )
        await asyncio.wait_for(slow_body.paused.wait(), timeout=1)
        try:
            controlled = await controlled_client.post(
                "/api/v1/analysis-runs",
                json={"source_id": "controlled-01"},
            )
            access_view = await status_client.get("/api/v1/access/session")

            assert controlled.status_code == 429
            assert controlled.headers["retry-after"] == "15"
            assert controlled.headers["x-trainpal-admission-reason"] == "capacity"
            assert access_view.status_code == 200
            assert access_view.json()["can_analyze"] is False
        finally:
            local_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await local_task

        retried = await controlled_client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "controlled-01"},
        )
        assert retried.status_code == 202
        await wait_for_status(controlled_client, retried.json()["id"], "completed")


@pytest.mark.asyncio
async def test_active_session_upload_is_rejected_before_reading_a_second_body(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    body = FailsIfConsumedStream()
    access = make_test_access()
    session = access.resolve(None)
    lease = await access.reserve(
        session,
        source_id="local:active",
        client_ip="198.51.100.10",
    )
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=access,
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 10.0,
    )
    transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 1002))
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://test",
            cookies={ACCESS_COOKIE_NAME: access.encode(session)},
        ) as client:
            response = await client.post(
                "/api/v1/analysis-runs/local",
                content=body,
                headers={
                    "Content-Type": "multipart/form-data; boundary=not-consumed",
                    "Content-Length": "1",
                },
            )
    finally:
        await lease.release()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "15"
    assert response.headers["x-trainpal-admission-reason"] == "capacity"
    assert response.headers["cache-control"] == "no-store"
    assert body.consumed is False
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_capabilities_publish_the_configured_local_upload_limits(tmp_path: Path) -> None:
    app = create_app(
        access=make_test_access(),
        local_upload_enabled=True,
        local_analysis_max_seconds=45,
        local_upload_max_bytes=12_345,
        local_upload_temp_root=tmp_path,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "local_upload_enabled": True,
        "local_analysis_max_seconds": 45.0,
        "local_upload_max_bytes": 12_345,
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_default_local_upload_accepts_300_seconds_and_rejects_longer_before_provider(
    tmp_path: Path,
) -> None:
    accepted_pipeline = RelativeCandidatePipeline()
    accepted_app = create_app(
        pipeline=accepted_pipeline,
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "accepted",
        local_duration_probe=lambda _: 300.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=accepted_app), base_url="https://test"
    ) as client:
        capabilities = await client.get("/api/v1/capabilities")
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        completed = await wait_for_status(client, created.json()["id"], "completed")

    assert capabilities.json()["local_analysis_max_seconds"] == 300.0
    assert completed["source_duration_seconds"] == 300.0
    assert accepted_pipeline.source is not None

    rejected_pipeline = BlockingCapturePipeline()
    rejected_app = create_app(
        pipeline=rejected_pipeline,
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "rejected",
        local_duration_probe=lambda _: 300.001,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rejected_app), base_url="https://test"
    ) as client:
        rejected = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": SECOND_LOCAL_SOURCE_ID},
        )

    assert rejected.status_code == 422
    assert rejected.json() == {"detail": "视频时长超过当前分析上限"}
    assert rejected_pipeline.sources == []


@pytest.mark.asyncio
async def test_disabled_local_upload_is_fail_closed_without_writing_media(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_enabled=False,
        local_upload_temp_root=upload_root,
    )
    assert "/api/v1/analysis-runs/local" not in app.openapi()["paths"]
    disabled_route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/analysis-runs/local"
        and "POST" in getattr(route, "methods", set())
    )
    assert isinstance(disabled_route, APIRoute)
    assert disabled_route.body_field is None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "本地视频导入未启用"}
    assert not upload_root.exists()


@pytest.mark.asyncio
async def test_local_range_upload_returns_absolute_candidate_times_and_cleans_media(
    tmp_path: Path,
) -> None:
    pipeline = RelativeCandidatePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_analysis_max_seconds=60,
        local_upload_max_bytes=1024,
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 30.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
            data={
                "local_source_id": LOCAL_SOURCE_ID,
                "range_start_seconds": "10",
                "range_end_seconds": "20",
            },
        )
        assert created.status_code == 202
        completed = await wait_for_status(client, created.json()["id"], "completed")

    assert completed["source_duration_seconds"] == 30.0
    assert completed["processed_seconds"] == 10.0
    assert completed["coverage_status"] == "complete"
    assert completed["coverage_gaps"] == []
    candidate = completed["candidates"][0]  # type: ignore[index]
    assert candidate["source_id"] == LOCAL_SOURCE_ID
    assert candidate["segment"] == {"start_seconds": 11.0, "end_seconds": 13.0}
    assert candidate["tips"][0]["evidence"] == {
        "type": "visual",
        "start_seconds": 11.5,
        "end_seconds": 12.5,
    }
    assert candidate["playback_options"][0] == {
        "label": "动作示范",
        "start_seconds": 11.0,
        "end_seconds": 13.0,
    }
    assert candidate["evidence"] == [
        {"type": "visual", "start_seconds": 11.25, "end_seconds": 12.75}
    ]
    assert pipeline.source is not None
    assert pipeline.source.analysis_start_seconds == 10.0
    assert pipeline.source.analysis_end_seconds == 20.0
    assert pipeline.source.analysis_duration_seconds == 10.0
    for _ in range(100):
        if not pipeline.source.path.exists():
            break
        await asyncio.sleep(0.01)
    assert not pipeline.source.path.exists()
    assert not upload_root.exists() or list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_deterministic_provider_respects_short_range_and_unknown_role_confirmation(
    tmp_path: Path,
) -> None:
    app = create_app(
        pipeline=DeterministicTestPipeline(),
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "uploads",
        local_duration_probe=lambda _: 30.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={
                "local_source_id": LOCAL_SOURCE_ID,
                "range_start_seconds": "10",
                "range_end_seconds": "14",
            },
        )
        completed = await wait_for_status(client, created.json()["id"], "completed")

    candidates = cast(list[dict[str, object]], completed["candidates"])
    assert candidates[0]["segment"] == {
        "start_seconds": 10.0,
        "end_seconds": 14.0,
    }
    assert "segment_role" not in candidates[0]
    assert candidates[0]["needs_confirmation"] is True


@pytest.mark.asyncio
async def test_range_run_fails_closed_when_candidate_segment_exceeds_requested_range(
    tmp_path: Path,
) -> None:
    app = create_app(
        pipeline=CandidateBoundaryPipeline(segment_end_seconds=10.5),
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "uploads",
        local_duration_probe=lambda _: 30.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={
                "local_source_id": LOCAL_SOURCE_ID,
                "range_start_seconds": "10",
                "range_end_seconds": "20",
            },
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "schema_error",
        "message": "这次没有分析成功，请重试",
        "retryable": True,
    }
    assert failed["candidates"] == []


@pytest.mark.asyncio
async def test_range_run_fails_closed_when_evidence_exceeds_requested_range(
    tmp_path: Path,
) -> None:
    app = create_app(
        pipeline=CandidateBoundaryPipeline(
            segment_end_seconds=10,
            evidence_end_seconds=10.5,
        ),
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "uploads",
        local_duration_probe=lambda _: 30.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={
                "local_source_id": LOCAL_SOURCE_ID,
                "range_start_seconds": "10",
                "range_end_seconds": "20",
            },
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "schema_error",
        "message": "这次没有分析成功，请重试",
        "retryable": True,
    }
    assert failed["candidates"] == []


@pytest.mark.asyncio
async def test_local_run_fails_closed_when_candidate_source_does_not_match(
    tmp_path: Path,
) -> None:
    app = create_app(
        pipeline=CandidateBoundaryPipeline(candidate_source_id=SECOND_LOCAL_SOURCE_ID),
        access=make_test_access(),
        local_upload_temp_root=tmp_path / "uploads",
        local_duration_probe=lambda _: 30.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "schema_error",
        "message": "这次没有分析成功，请重试",
        "retryable": True,
    }
    assert failed["candidates"] == []


@pytest.mark.asyncio
async def test_local_upload_rejects_media_outside_the_video_allowlist(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 10.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("payload.bin", b"not-video", "application/octet-stream")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        retried = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": SECOND_LOCAL_SOURCE_ID},
        )
        await wait_for_status(client, retried.json()["id"], "completed")

    assert response.status_code == 415
    assert response.json() == {"detail": "暂不支持这种视频格式"}
    assert retried.status_code == 202
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_local_upload_enforces_the_streamed_byte_limit_and_cleans_partial_file(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    probe_called = False

    def duration_probe(_: Path) -> float:
        nonlocal probe_called
        probe_called = True
        return 10.0

    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_max_bytes=4,
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("too-large.mp4", b"12345", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "视频文件超过上传大小限制"}
    assert probe_called is False
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form_overrides", "expected_detail"),
    [
        ({"local_source_id": "local:not-a-uuid"}, "本地来源标识无效"),
        ({"range_start_seconds": "5"}, "分析范围必须同时包含开始和结束时间"),
        ({"range_end_seconds": "15"}, "分析范围必须同时包含开始和结束时间"),
        ({"range_start_seconds": "10", "range_end_seconds": "10"}, "分析范围超出视频时长"),
        ({"range_start_seconds": "0", "range_end_seconds": "20.1"}, "分析范围超出视频时长"),
    ],
)
async def test_local_upload_rejects_invalid_ids_and_out_of_bounds_ranges(
    tmp_path: Path,
    form_overrides: dict[str, str],
    expected_detail: str,
) -> None:
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    form = {"local_source_id": LOCAL_SOURCE_ID, **form_overrides}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
            data=form,
        )

    assert response.status_code == 422
    assert response.json() == {"detail": expected_detail}
    assert not upload_root.exists() or list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_local_upload_rejects_unreadable_and_over_limit_durations(tmp_path: Path) -> None:
    for duration_probe, expected_detail in (
        (lambda _: (_ for _ in ()).throw(RuntimeError("decoder detail")), "无法读取视频时长"),
        (lambda _: float("nan"), "无法读取视频时长"),
        (lambda _: float("inf"), "无法读取视频时长"),
        (lambda _: 60.1, "视频时长超过当前分析上限"),
    ):
        upload_root = tmp_path / expected_detail
        app = create_app(
            pipeline=RelativeCandidatePipeline(),
            access=make_test_access(),
            local_analysis_max_seconds=60,
            local_upload_temp_root=upload_root,
            local_duration_probe=duration_probe,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("source.mp4", b"video-bytes", "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )

        assert response.status_code == 422
        assert response.json() == {"detail": expected_detail}
        assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_cancelling_during_duration_probe_cleans_upload_and_allows_retry(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    probe_started = threading.Event()
    release_probe = threading.Event()
    probe_calls = 0

    def duration_probe(_: Path) -> float:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 1:
            probe_started.set()
            assert release_probe.wait(timeout=5)
        return 10.0

    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/api/v1/access/session")
        request = asyncio.create_task(
            client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )
        )
        assert await asyncio.to_thread(probe_started.wait, 1)
        request.cancel()
        await asyncio.sleep(0)
        release_probe.set()
        with pytest.raises(asyncio.CancelledError):
            await request

        retried = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        completed = await wait_for_status(client, retried.json()["id"], "completed")

    assert retried.status_code == 202
    assert completed["status"] == "completed"
    assert probe_calls == 2
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_cancelling_during_request_upload_never_acquires_run_resources(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    probe_calls = 0

    def duration_probe(_: Path) -> float:
        nonlocal probe_calls
        probe_calls += 1
        return 10.0

    encoded = httpx.Request(
        "POST",
        "https://test/api/v1/analysis-runs/local",
        files={"media": ("private.mp4", b"x" * 8192, "video/mp4")},
        data={"local_source_id": LOCAL_SOURCE_ID},
    )
    body = PausingStream(encoded.read())
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=duration_probe,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/api/v1/access/session")
        request = asyncio.create_task(
            client.post(
                "/api/v1/analysis-runs/local",
                content=body,
                headers={"Content-Type": encoded.headers["Content-Type"]},
            )
        )
        await asyncio.wait_for(body.paused.wait(), timeout=1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

        assert not upload_root.exists()
        retried = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        completed = await wait_for_status(client, retried.json()["id"], "completed")

    assert retried.status_code == 202
    assert completed["status"] == "completed"
    assert probe_calls == 1
    assert list(upload_root.iterdir()) == []


def test_direct_app_configuration_rejects_invalid_local_analysis_limit() -> None:
    with pytest.raises(ValueError):
        create_app(local_analysis_max_seconds=float("nan"))
    with pytest.raises(ValueError):
        create_app(local_analysis_max_seconds=300.001)


@pytest.mark.asyncio
async def test_explicit_delete_cancels_local_run_and_cleans_the_uploaded_media(
    tmp_path: Path,
) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if pipeline.sources:
                break
            await asyncio.sleep(0.01)
        assert pipeline.sources
        source_path = pipeline.sources[0].path
        assert source_path.is_file()

        cancelled = await client.delete(f"/api/v1/analysis-runs/{created.json()['id']}")

    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert LOCAL_SOURCE_ID in pipeline.cancelled_source_ids
    assert not source_path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_timeout_cleans_local_media_and_keeps_a_retryable_safe_error(tmp_path: Path) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        timeout_seconds=0.01,
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "timeout",
        "message": "动作分析超时，请重试",
        "retryable": True,
    }
    assert pipeline.sources
    assert not pipeline.sources[0].path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_failure_cleans_local_media_and_never_logs_names_paths_or_transcripts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = SecretFailureCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.runs"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            created = await client.post(
                "/api/v1/analysis-runs/local",
                files={"media": ("private-filename.mp4", b"video-bytes", "video/mp4")},
                data={"local_source_id": LOCAL_SOURCE_ID},
            )
            failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"] == {
        "code": "provider_error",
        "message": "动作分析暂时不可用，请稍后重试",
        "retryable": True,
    }
    assert pipeline.source is not None
    sensitive_values = (
        "private-filename.mp4",
        str(pipeline.source.path),
        "secret transcript text",
    )
    messages = [record.getMessage() for record in caplog.records]
    assert messages
    assert all(not any(value in message for value in sensitive_values) for message in messages)
    payloads = [json.loads(message) for message in messages]
    assert all(set(payload) <= ALLOWED_LOG_FIELDS for payload in payloads)
    assert not pipeline.source.path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_terminal_cleanup_failure_is_not_silently_reported_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_root = tmp_path / "uploads"

    def fail_unless_errors_are_ignored(
        _directory: Path,
        ignore_errors: bool = False,
    ) -> None:
        if ignore_errors:
            return
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(
        "hakimi_analysis.app.shutil.rmtree",
        fail_unless_errors_are_ignored,
    )
    app = create_app(
        pipeline=RelativeCandidatePipeline(),
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        created = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("private.mp4", b"video-bytes", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        failed = await wait_for_status(client, created.json()["id"], "failed")

    assert failed["status"] == "failed"
    assert list(upload_root.iterdir())


@pytest.mark.asyncio
async def test_a_second_local_source_cannot_replace_the_active_public_run(
    tmp_path: Path,
) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        first = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("first.mp4", b"first-video", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if len(pipeline.sources) == 1:
                break
            await asyncio.sleep(0.01)
        first_path = pipeline.sources[0].path

        second = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("second.mp4", b"second-video", "video/mp4")},
            data={"local_source_id": SECOND_LOCAL_SOURCE_ID},
        )
        first_view = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}")

        assert first.status_code == 202
        assert second.status_code == 429
        assert second.headers["retry-after"] == "15"
        assert second.headers["x-trainpal-admission-reason"] == "capacity"
        assert first_view.json()["status"] == "running"
        assert pipeline.cancelled_source_ids == set()
        assert first_path.is_file()
        assert len(pipeline.sources) == 1
        assert len(list(upload_root.iterdir())) == 1

        await client.delete(f"/api/v1/analysis-runs/{first.json()['id']}")

    assert not first_path.exists()
    assert list(upload_root.iterdir()) == []


@pytest.mark.asyncio
async def test_second_request_for_the_same_source_does_not_cancel_the_active_run(
    tmp_path: Path,
) -> None:
    pipeline = BlockingCapturePipeline()
    upload_root = tmp_path / "uploads"
    app = create_app(
        pipeline=pipeline,
        access=make_test_access(public_attempt_limit=10),
        local_upload_temp_root=upload_root,
        local_duration_probe=lambda _: 20.0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        first = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("first.mp4", b"first-video", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        for _ in range(100):
            if pipeline.sources:
                break
            await asyncio.sleep(0.01)
        first_path = pipeline.sources[0].path

        rejected = await client.post(
            "/api/v1/analysis-runs/local",
            files={"media": ("retry.mp4", b"retry-video", "video/mp4")},
            data={"local_source_id": LOCAL_SOURCE_ID},
        )
        first_view = await client.get(f"/api/v1/analysis-runs/{first.json()['id']}")

        assert rejected.status_code == 429
        assert first_view.json()["status"] == "running"
        assert pipeline.cancelled_source_ids == set()
        assert first_path.is_file()
        assert len(list(upload_root.iterdir())) == 1

        await client.delete(f"/api/v1/analysis-runs/{first.json()['id']}")

    assert not first_path.exists()
    assert list(upload_root.iterdir()) == []
