import asyncio
import json
import math
import re
import secrets
import shutil
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hakimi_analysis.access import (
    ACCESS_COOKIE_NAME,
    ACCESS_SESSION_SECONDS,
    ADMISSION_REASON_HEADER,
    AccessCodeRateLimited,
    AccessManager,
    AccessSession,
    AdmissionDenied,
    AnalysisLease,
)
from hakimi_analysis.gymti import GymtiContextError, GymtiService
from hakimi_analysis.media import probe_duration_sync
from hakimi_analysis.models import (
    AccessSessionView,
    AccessTier,
    AnalysisRunView,
    ApiErrorResponse,
    CapabilitiesView,
    CreateAnalysisRunRequest,
    GymtiNarrativeSnapshotView,
    GymtiNextQuestionRequest,
    GymtiNextQuestionView,
    GymtiResultNarrativeRequest,
    NotReadyResponse,
    ReadyResponse,
    SourceSummary,
    UpgradeAccessSessionRequest,
)
from hakimi_analysis.pipeline import AnalysisPipeline
from hakimi_analysis.playback import PlaybackChoice, PlaybackRequest, PlaybackService, VoiceCommand
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.readiness import ReadinessProbe, StaticReadiness
from hakimi_analysis.runs import AnalysisRunManager, as_pipeline
from hakimi_analysis.runtime_cleanup import RuntimeCleanupProbe, RuntimeCleanupProbeError
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog, VideoSource

LOCAL_SOURCE_ID_PATTERN = re.compile(
    r"^local:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
LOCAL_MEDIA_TYPES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}
LOCAL_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_LOCAL_UPLOAD_LEASE_STATE = "trainpal_local_upload_lease"
_LOCAL_UPLOAD_SESSION_STATE = "trainpal_local_upload_session"
_LOCAL_UPLOAD_LEASE_TRANSFERRED_STATE = "trainpal_local_upload_lease_transferred"


class _RequestBodyTooLarge(OSError):
    # Starlette closes partially spooled multipart files when parsing raises OSError.
    pass


class _NonBlockingConcurrencyGate:
    """Single-worker admission gate that never queues callers."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("GYMTI model concurrency must be positive")
        self._capacity = capacity
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._capacity:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active = max(0, self._active - 1)


class LocalUploadBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        app_env: str,
        development_origins: list[str],
        access_manager: AccessManager,
        readiness_probe: ReadinessProbe,
        trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes
        self._app_env = app_env
        self._development_origins = development_origins
        self._access_manager = access_manager
        self._readiness_probe = readiness_probe
        self._trusted_proxy_networks = trusted_proxy_networks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/api/v1/analysis-runs/local"
        ):
            await self._app(scope, receive, send)
            return
        request = Request(scope)
        try:
            _require_same_origin(
                request,
                app_env=self._app_env,
                development_origins=self._development_origins,
            )
        except HTTPException as error:
            response = JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        if self._app_env == "production":
            failure_code = await asyncio.to_thread(self._readiness_probe.check)
            if failure_code is not None:
                response = JSONResponse(
                    status_code=503,
                    content={"detail": "动作分析服务尚未就绪"},
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
        session = self._access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            client_ip = _client_ip(request, self._trusted_proxy_networks)
        except HTTPException as error:
            response = JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        try:
            lease = await self._access_manager.reserve_provisional(
                session,
                source_id="local:provisional",
                client_ip=client_ip,
            )
        except AdmissionDenied as error:
            response = JSONResponse(
                status_code=429,
                content={"detail": "真实动作分析暂时繁忙，请稍后重试"},
                headers={
                    "Cache-Control": "no-store",
                    "Retry-After": str(error.retry_after_seconds),
                    ADMISSION_REASON_HEADER: error.reason,
                },
            )
            await response(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state[_LOCAL_UPLOAD_LEASE_STATE] = lease
        state[_LOCAL_UPLOAD_SESSION_STATE] = session
        state[_LOCAL_UPLOAD_LEASE_TRANSFERRED_STATE] = False
        try:
            await self._handle_admitted_request(scope, receive, send)
        finally:
            if not state[_LOCAL_UPLOAD_LEASE_TRANSFERRED_STATE]:
                await _run_cleanup_to_completion(lease.release())

    async def _handle_admitted_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = 0
            if declared_bytes > self._max_body_bytes:
                await _local_upload_too_large_response(scope, receive, send)
                return
        received_bytes = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, too_large
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    too_large = True
                    raise _RequestBodyTooLarge
            return message

        async def limited_send(message: Message) -> None:
            if not too_large:
                await send(message)

        with suppress(_RequestBodyTooLarge):
            await self._app(scope, limited_receive, limited_send)
        if too_large:
            await _local_upload_too_large_response(scope, receive, send)


async def _local_upload_too_large_response(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        status_code=413,
        content={"detail": "视频文件超过上传大小限制"},
        headers={"Cache-Control": "no-store"},
    )
    await response(scope, receive, send)


async def stream_run_events(
    manager: AnalysisRunManager,
    run_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    owner_session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    async for event in manager.events(run_id, owner_session_id=owner_session_id):
        if await is_disconnected():
            return
        data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        yield f"event: {event.type}\ndata: {data}\n\n"


def create_app(
    *,
    catalog: SourceCatalog | None = None,
    pipeline: AnalysisPipeline | object | None = None,
    timeout_seconds: float = 180,
    ttl_seconds: float = 600,
    cors_origins: list[str] | None = None,
    close_callbacks: list[Callable[[], Awaitable[None]]] | None = None,
    access: AccessManager | None = None,
    app_env: str = "test",
    readiness: ReadinessProbe | None = None,
    web_static_root: Path | None = None,
    trusted_proxy_cidrs: list[str] | None = None,
    local_upload_enabled: bool = True,
    local_analysis_max_seconds: float = 300,
    local_upload_max_bytes: int = 256 * 1024 * 1024,
    local_upload_temp_root: Path | None = None,
    local_duration_probe: Callable[[Path], float] | None = None,
    gymti_service: GymtiService | None = None,
    gymti_llm_enabled: bool = False,
    gymti_llm_concurrency: int = 3,
    release_sha: str | None = None,
    runtime_cleanup: RuntimeCleanupProbe | None = None,
    playback_service: PlaybackService | None = None,
) -> FastAPI:
    if (
        not math.isfinite(local_analysis_max_seconds)
        or local_analysis_max_seconds <= 0
        or local_analysis_max_seconds > 300
    ):
        raise ValueError("local analysis limit must be between 0 and 300 seconds")
    if local_upload_max_bytes < 1:
        raise ValueError("local upload byte limit must be positive")
    if release_sha is not None and re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ValueError("release SHA must be a full lowercase Git commit SHA")
    duration_probe = local_duration_probe or probe_duration_sync
    source_catalog = catalog or EmptySourceCatalog()
    resolved_pipeline: AnalysisPipeline
    if pipeline is None:
        from hakimi_analysis.bootstrap import UnconfiguredPipeline

        resolved_pipeline = UnconfiguredPipeline()
    else:
        resolved_pipeline = as_pipeline(pipeline)
    manager = AnalysisRunManager(
        resolved_pipeline,
        timeout_seconds=timeout_seconds,
        ttl_seconds=ttl_seconds,
    )
    access_manager = access or AccessManager(
        cookie_secret=secrets.token_urlsafe(32),
        judge_access_code=secrets.token_urlsafe(16),
    )
    gymti_model_gate = _NonBlockingConcurrencyGate(gymti_llm_concurrency)
    playback_gate = _NonBlockingConcurrencyGate(2)
    readiness_probe = readiness or StaticReadiness(
        "provider_configuration_invalid" if app_env == "production" else None
    )
    if cors_origins is None:
        resolved_cors_origins = (
            [] if app_env == "production" else ["http://localhost:5173", "http://127.0.0.1:5173"]
        )
    else:
        resolved_cors_origins = cors_origins
    if app_env == "production":
        _validate_production_cors_origins(resolved_cors_origins)
    trusted_proxy_networks = tuple(
        ip_network(value, strict=False) for value in trusted_proxy_cidrs or []
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await manager.close()
        for callback in close_callbacks or []:
            await callback()

    app = FastAPI(
        title="Hachimi Action Analysis API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.run_manager = manager
    app.state.source_catalog = source_catalog
    app.state.access_manager = access_manager
    app.state.gymti_service = gymti_service
    app.state.gymti_llm_enabled = gymti_llm_enabled

    async def playback_body(request: Request, limit: int) -> bytes:
        _require_same_origin(request, app_env=app_env, development_origins=resolved_cors_origins)
        if playback_service is None:
            raise HTTPException(status_code=503, detail="片段调整暂不可用，请手动播放")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                raise HTTPException(status_code=413, detail="请求过长，请缩短后重试")
        return bytes(body)

    # Body is streamed above to enforce the byte limit before parsing. Derive
    # the published request contract from the same validator, inlining its one
    # option definition because nested JSON schemas cannot use root $defs refs.
    playback_json_schema = PlaybackRequest.model_json_schema()
    playback_json_schema["properties"]["options"]["items"] = playback_json_schema.pop("$defs")[
        "PlaybackOption"
    ]

    @app.post(
        "/api/v1/playback-adjustments",
        response_model=PlaybackChoice,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": playback_json_schema}},
            }
        },
    )
    async def select_playback(request: Request, response: Response) -> PlaybackChoice:
        body = await playback_body(request, 16_000)
        assert playback_service is not None
        if not playback_gate.try_acquire():
            raise HTTPException(status_code=429, detail="TrainPal正忙，请稍后再试")
        try:
            payload = PlaybackRequest.model_validate_json(body)
            choice = await playback_service.select(payload)
        except (ValueError, ValidationError) as error:
            raise HTTPException(
                status_code=422, detail="没有找到可靠片段，原片段保持不变"
            ) from error
        except (TimeoutError, ProviderError) as error:
            raise HTTPException(status_code=503, detail="片段查找未完成，训练保持暂停") from error
        finally:
            playback_gate.release()
        response.headers["Cache-Control"] = "no-store"
        return choice

    @app.post(
        "/api/v1/playback-voice",
        response_model=VoiceCommand,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
                "description": (
                    "Single-channel 16-bit 16000 Hz PCM WAV, "
                    "at most 15 seconds and 480044 bytes."
                ),
            }
        },
    )
    async def transcribe_playback(request: Request, response: Response) -> VoiceCommand:
        body = await playback_body(request, 480_044)
        assert playback_service is not None
        if not playback_gate.try_acquire():
            raise HTTPException(status_code=429, detail="TrainPal正忙，请稍后再试")
        try:
            command = await playback_service.transcribe(body)
        except (ValueError, ValidationError) as error:
            raise HTTPException(status_code=422, detail="没有听清，请改用文字") from error
        except (TimeoutError, ProviderError) as error:
            raise HTTPException(status_code=503, detail="语音暂不可用，请改用文字") from error
        finally:
            playback_gate.release()
        response.headers["Cache-Control"] = "no-store"
        return command

    if local_upload_enabled:
        app.add_middleware(
            LocalUploadBodyLimitMiddleware,
            max_body_bytes=local_upload_max_bytes + LOCAL_MULTIPART_OVERHEAD_BYTES,
            app_env=app_env,
            development_origins=resolved_cors_origins,
            access_manager=access_manager,
            readiness_probe=readiness_probe,
            trusted_proxy_networks=trusted_proxy_networks,
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        payload = {"status": "ok"}
        if release_sha is not None:
            payload["release_sha"] = release_sha
        return payload

    @app.get("/api/v1/runtime-cleanup", include_in_schema=False)
    async def runtime_cleanup_status() -> JSONResponse:
        if runtime_cleanup is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "code": "runtime_cleanup_probe_unavailable",
                },
                headers={"Cache-Control": "no-store"},
            )
        try:
            snapshot = await asyncio.to_thread(runtime_cleanup.snapshot)
        except RuntimeCleanupProbeError:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "code": "runtime_cleanup_probe_unavailable",
                },
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            status_code=200,
            content={
                "clean": snapshot.clean,
                "residue_count": snapshot.residue_count,
                "ffmpeg_process_count": snapshot.ffmpeg_process_count,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/capabilities", response_model=CapabilitiesView)
    async def capabilities(response: Response) -> CapabilitiesView:
        response.headers["Cache-Control"] = "no-store"
        return CapabilitiesView(
            local_upload_enabled=local_upload_enabled,
            local_analysis_max_seconds=local_analysis_max_seconds,
            local_upload_max_bytes=local_upload_max_bytes,
        )

    def reserve_gymti_model(
        request: Request,
    ) -> tuple[AccessSession, bool]:
        """Admit model work immediately or select the request's local fallback."""

        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        if not gymti_llm_enabled or gymti_service is None or not gymti_service.model_available:
            return session, False
        return session, gymti_model_gate.try_acquire()

    @app.post(
        "/api/v1/gymti/next-question",
        response_model=GymtiNextQuestionView,
        responses={
            403: {"model": ApiErrorResponse, "description": "请求未通过同源校验。"},
            422: {"model": ApiErrorResponse, "description": "问卷上下文与当前合同不匹配。"},
            503: {"model": ApiErrorResponse, "description": "GYMTI 问卷合同暂不可用。"},
        },
    )
    async def gymti_next_question(
        payload: GymtiNextQuestionRequest,
        request: Request,
        response: Response,
    ) -> GymtiNextQuestionView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        if gymti_service is None:
            raise HTTPException(status_code=503, detail="GYMTI 问卷暂不可用")
        answered_question_option_ids = tuple(
            (answer.question_id, answer.option_id) for answer in payload.answered
        )
        try:
            gymti_service.validate_next_question_context(
                questionnaire_version=payload.questionnaire_version,
                scoring_version=payload.scoring_version,
                answered_question_option_ids=answered_question_option_ids,
                candidate_question_ids=payload.candidate_question_ids,
            )
        except GymtiContextError as error:
            raise HTTPException(status_code=422, detail="GYMTI 问卷上下文无效") from error
        session, model_admitted = reserve_gymti_model(request)
        try:
            selected = await gymti_service.next_question(
                questionnaire_version=payload.questionnaire_version,
                scoring_version=payload.scoring_version,
                answered_question_option_ids=answered_question_option_ids,
                candidate_question_ids=payload.candidate_question_ids,
                allow_model=model_admitted,
            )
        except GymtiContextError as error:
            raise HTTPException(status_code=422, detail="GYMTI 问卷上下文无效") from error
        finally:
            if model_admitted:
                gymti_model_gate.release()
        _set_access_cookie(response, access_manager, session)
        response.headers["Cache-Control"] = "no-store"
        return GymtiNextQuestionView(
            question_id=selected.question_id,
            source=selected.source,
            model=selected.model,
            version=selected.version,
        )

    @app.post(
        "/api/v1/gymti/result-narrative",
        response_model=GymtiNarrativeSnapshotView,
        responses={
            403: {"model": ApiErrorResponse, "description": "请求未通过同源校验。"},
            422: {"model": ApiErrorResponse, "description": "正式结果与当前合同不匹配。"},
            503: {"model": ApiErrorResponse, "description": "GYMTI 问卷合同暂不可用。"},
        },
    )
    async def gymti_result_narrative(
        payload: GymtiResultNarrativeRequest,
        request: Request,
        response: Response,
    ) -> GymtiNarrativeSnapshotView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        if gymti_service is None:
            raise HTTPException(status_code=503, detail="GYMTI 问卷暂不可用")
        answered_question_option_ids = tuple(
            (answer.question_id, answer.option_id) for answer in payload.answered
        )
        try:
            gymti_service.validate_result_narrative_context(
                questionnaire_version=payload.questionnaire_version,
                scoring_version=payload.scoring_version,
                answered_question_option_ids=answered_question_option_ids,
                formal_result_id=payload.formal_result_id,
                secondary_result_id=payload.secondary_result_id,
                coach_style_id=payload.coach_style_id,
                reason_codes=payload.reason_codes,
            )
        except GymtiContextError as error:
            raise HTTPException(status_code=422, detail="GYMTI 正式结果无效") from error
        session, model_admitted = reserve_gymti_model(request)
        try:
            snapshot = await gymti_service.result_narrative(
                questionnaire_version=payload.questionnaire_version,
                scoring_version=payload.scoring_version,
                answered_question_option_ids=answered_question_option_ids,
                formal_result_id=payload.formal_result_id,
                secondary_result_id=payload.secondary_result_id,
                coach_style_id=payload.coach_style_id,
                reason_codes=payload.reason_codes,
                allow_model=model_admitted,
            )
        except GymtiContextError as error:
            raise HTTPException(status_code=422, detail="GYMTI 正式结果无效") from error
        finally:
            if model_admitted:
                gymti_model_gate.release()
        _set_access_cookie(response, access_manager, session)
        response.headers["Cache-Control"] = "no-store"
        return GymtiNarrativeSnapshotView(
            source=snapshot.source,
            model=snapshot.model,
            version=snapshot.version,
            generated_at=snapshot.generated_at,
            text=snapshot.text,
        )

    @app.get(
        "/api/v1/ready",
        response_model=ReadyResponse,
        responses={
            503: {
                "model": NotReadyResponse,
                "description": "生产就绪检查未通过，返回脱敏错误码。",
            }
        },
    )
    async def ready() -> JSONResponse:
        failure_code = await asyncio.to_thread(readiness_probe.check)
        if failure_code is not None:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "code": failure_code},
            )
        return JSONResponse(status_code=200, content={"status": "ready"})

    @app.get("/api/v1/access/session", response_model=AccessSessionView)
    async def get_access_session(request: Request, response: Response) -> AccessSessionView:
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        _set_access_cookie(response, access_manager, session)
        response.headers["Cache-Control"] = "no-store"
        return await access_view(
            session,
            client_ip=_client_ip(request, trusted_proxy_networks),
        )

    @app.post("/api/v1/access/session", response_model=AccessSessionView)
    async def upgrade_access_session(
        payload: UpgradeAccessSessionRequest,
        request: Request,
        response: Response,
    ) -> AccessSessionView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            upgraded = await access_manager.upgrade(
                session,
                payload.access_code,
                client_ip=_client_ip(request, trusted_proxy_networks),
            )
        except AccessCodeRateLimited as error:
            raise HTTPException(
                status_code=429,
                detail="体验码尝试过于频繁，请稍后重试",
                headers={
                    "Cache-Control": "no-store",
                    "Retry-After": str(error.retry_after_seconds),
                },
            ) from error
        if upgraded is None:
            raise HTTPException(
                status_code=401,
                detail="体验码无效",
                headers={"Cache-Control": "no-store"},
            )
        _set_access_cookie(response, access_manager, upgraded)
        response.headers["Cache-Control"] = "no-store"
        return await access_view(
            upgraded,
            client_ip=_client_ip(request, trusted_proxy_networks),
        )

    async def access_view(
        session: AccessSession,
        *,
        client_ip: str | None = None,
    ) -> AccessSessionView:
        view = access_manager.view(session, client_ip=client_ip)
        if app_env != "production":
            return view
        failure_code = await asyncio.to_thread(readiness_probe.check)
        if failure_code is None:
            return view
        return view.model_copy(update={"can_analyze": False, "retry_after_seconds": None})

    @app.get("/api/v1/sources", response_model=list[SourceSummary])
    async def list_sources() -> list[SourceSummary]:
        return source_catalog.list()

    @app.get("/api/v1/sources/{source_id}/media")
    async def source_media(source_id: str) -> Response:
        try:
            source = source_catalog.get(source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="受控视频源不存在") from error
        if source.public_media_url is not None:
            return RedirectResponse(source.public_media_url, status_code=307)
        return FileResponse(source.path, media_type="video/mp4")

    @app.post(
        "/api/v1/analysis-runs",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            400: {
                "model": ApiErrorResponse,
                "description": "可信代理提供的客户端地址无效。",
            },
            403: {
                "model": ApiErrorResponse,
                "description": "请求未通过同源校验。",
            },
            404: {
                "model": ApiErrorResponse,
                "description": "受控视频源不存在。",
            },
            429: {
                "model": ApiErrorResponse,
                "description": "分析容量或调用频率已达到限制。",
                "headers": {
                    "Retry-After": {
                        "description": "再次尝试前需要等待的秒数。",
                        "schema": {"type": "string"},
                    }
                },
            },
            503: {
                "model": ApiErrorResponse,
                "description": "生产分析服务尚未就绪。",
            },
        },
    )
    async def create_run(
        payload: CreateAnalysisRunRequest,
        request: Request,
        response: Response,
    ) -> AnalysisRunView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        if app_env == "production":
            failure_code = await asyncio.to_thread(readiness_probe.check)
            if failure_code is not None:
                raise HTTPException(status_code=503, detail="动作分析服务尚未就绪")
        try:
            source = source_catalog.get(payload.source_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="受控视频源不存在") from error
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        active = await access_manager.active_run(session.id)
        if (
            session.tier == AccessTier.JUDGE
            and active is not None
            and active.source_id != source.id
            and active.run_id is not None
        ):
            with suppress(KeyError):
                await manager.cancel(active.run_id, owner_session_id=session.id)
        try:
            lease = await access_manager.reserve(
                session,
                source_id=source.id,
                client_ip=_client_ip(request, trusted_proxy_networks),
            )
        except AdmissionDenied as error:
            raise HTTPException(
                status_code=429,
                detail="真实动作分析暂时繁忙，请稍后重试",
                headers={
                    "Retry-After": str(error.retry_after_seconds),
                    ADMISSION_REASON_HEADER: error.reason,
                },
            ) from error
        try:
            created = await manager.create(
                source,
                payload.trigger_seconds,
                owner_session_id=session.id,
                on_terminal=lease.release,
            )
        except ValueError as error:
            await lease.release()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception:
            await lease.release()
            raise
        lease.commit(created.id)
        _set_access_cookie(response, access_manager, session)
        return created

    local_upload_routes = app if local_upload_enabled else APIRouter()

    @local_upload_routes.post(
        "/api/v1/analysis-runs/local",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            400: {
                "model": ApiErrorResponse,
                "description": "可信代理提供的客户端地址无效。",
            },
            403: {
                "model": ApiErrorResponse,
                "description": "请求未通过同源校验。",
            },
            404: {"model": ApiErrorResponse, "description": "本地视频导入未启用。"},
            413: {"model": ApiErrorResponse, "description": "上传媒体超过大小限制。"},
            415: {"model": ApiErrorResponse, "description": "上传媒体类型不受支持。"},
            422: {"model": ApiErrorResponse, "description": "媒体或分析范围无效。"},
            429: {
                "model": ApiErrorResponse,
                "description": "分析容量或调用频率已达到限制。",
                "headers": {
                    "Retry-After": {
                        "description": "再次尝试前需要等待的秒数。",
                        "schema": {"type": "string"},
                    }
                },
            },
            503: {"model": ApiErrorResponse, "description": "生产分析服务尚未就绪。"},
        },
    )
    async def create_local_run(
        request: Request,
        response: Response,
        media: Annotated[UploadFile, File()],
        local_source_id: Annotated[str, Form()],
        range_start_seconds: Annotated[float | None, Form(ge=0)] = None,
        range_end_seconds: Annotated[float | None, Form(gt=0)] = None,
    ) -> AnalysisRunView:
        upload_directory: Path | None = None
        duration_task: asyncio.Task[float] | None = None
        ownership_transferred = False
        lease = getattr(request.state, _LOCAL_UPLOAD_LEASE_STATE, None)
        session = getattr(request.state, _LOCAL_UPLOAD_SESSION_STATE, None)
        if not isinstance(lease, AnalysisLease) or not isinstance(session, AccessSession):
            raise RuntimeError("local upload admission state is missing")

        async def cleanup_request_resources() -> None:
            try:
                await media.close()
            finally:
                if duration_task is not None:
                    await asyncio.gather(duration_task, return_exceptions=True)
                if not ownership_transferred and upload_directory is not None:
                    await _remove_upload_directory(upload_directory)

        try:
            _require_same_origin(
                request,
                app_env=app_env,
                development_origins=resolved_cors_origins,
            )
            if not local_upload_enabled:
                raise HTTPException(status_code=404, detail="本地视频导入未启用")
            if app_env == "production":
                failure_code = await asyncio.to_thread(readiness_probe.check)
                if failure_code is not None:
                    raise HTTPException(status_code=503, detail="动作分析服务尚未就绪")
            if LOCAL_SOURCE_ID_PATTERN.fullmatch(local_source_id) is None:
                raise HTTPException(status_code=422, detail="本地来源标识无效")
            if (range_start_seconds is None) != (range_end_seconds is None):
                raise HTTPException(
                    status_code=422,
                    detail="分析范围必须同时包含开始和结束时间",
                )
            suffix = LOCAL_MEDIA_TYPES.get(media.content_type or "")
            if suffix is None:
                raise HTTPException(status_code=415, detail="暂不支持这种视频格式")

            upload_directory = _make_upload_directory(local_upload_temp_root)
            source_path = upload_directory / f"source{suffix}"
            await _write_upload(media, source_path, max_bytes=local_upload_max_bytes)
            duration_task = asyncio.create_task(asyncio.to_thread(duration_probe, source_path))
            try:
                duration_seconds = await asyncio.shield(duration_task)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise HTTPException(status_code=422, detail="无法读取视频时长") from error
            if not math.isfinite(duration_seconds) or duration_seconds <= 0:
                raise HTTPException(status_code=422, detail="无法读取视频时长")
            if duration_seconds > local_analysis_max_seconds:
                raise HTTPException(status_code=422, detail="视频时长超过当前分析上限")

            analysis_start = 0.0 if range_start_seconds is None else range_start_seconds
            analysis_end = duration_seconds if range_end_seconds is None else range_end_seconds
            if (
                not math.isfinite(analysis_start)
                or not math.isfinite(analysis_end)
                or analysis_start >= analysis_end
                or analysis_end > duration_seconds
            ):
                raise HTTPException(status_code=422, detail="分析范围超出视频时长")
            source = VideoSource(
                id=local_source_id,
                title="本地导入视频",
                path=source_path,
                duration_seconds=duration_seconds,
                analysis_start_seconds=analysis_start,
                analysis_end_seconds=analysis_end,
            )

            await lease.activate(source.id)

            owned_directory = upload_directory
            owned_lease = lease

            async def finalize_local_run() -> None:
                try:
                    await _remove_upload_directory(owned_directory)
                finally:
                    await owned_lease.release()

            created = await manager.create(
                source,
                None,
                owner_session_id=session.id,
                on_terminal=finalize_local_run,
            )
            lease.commit(created.id)
            ownership_transferred = True
            request.scope["state"][_LOCAL_UPLOAD_LEASE_TRANSFERRED_STATE] = True
            _set_access_cookie(response, access_manager, session)
            return created
        finally:
            await _run_cleanup_to_completion(cleanup_request_resources())

    if not local_upload_enabled:

        @app.post("/api/v1/analysis-runs/local", include_in_schema=False)
        async def reject_disabled_local_upload() -> None:
            raise HTTPException(status_code=404, detail="本地视频导入未启用")

    @app.get(
        "/api/v1/analysis-runs/{run_id}",
        response_model=AnalysisRunView,
        responses={
            404: {
                "model": ApiErrorResponse,
                "description": "分析请求不存在、已过期或不属于当前会话。",
            }
        },
    )
    async def get_run(run_id: str, request: Request) -> AnalysisRunView:
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            return manager.get(run_id, owner_session_id=session.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

    @app.delete(
        "/api/v1/analysis-runs/{run_id}",
        response_model=AnalysisRunView,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            403: {
                "model": ApiErrorResponse,
                "description": "请求未通过同源校验。",
            },
            404: {
                "model": ApiErrorResponse,
                "description": "分析请求不存在、已过期或不属于当前会话。",
            },
        },
    )
    async def cancel_run(run_id: str, request: Request) -> AnalysisRunView:
        _require_same_origin(
            request,
            app_env=app_env,
            development_origins=resolved_cors_origins,
        )
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            return await manager.cancel(run_id, owner_session_id=session.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

    @app.get(
        "/api/v1/analysis-runs/{run_id}/events",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "分析阶段与终态事件流。",
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                    }
                },
            },
            404: {
                "model": ApiErrorResponse,
                "description": "分析请求不存在、已过期或不属于当前会话。",
            },
        },
    )
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        session = access_manager.resolve(request.cookies.get(ACCESS_COOKIE_NAME))
        try:
            manager.get(run_id, owner_session_id=session.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="分析请求不存在或已过期") from error

        return StreamingResponse(
            stream_run_events(
                manager,
                run_id,
                request.is_disconnected,
                owner_session_id=session.id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    resolved_web_root = _usable_web_root(web_static_root)
    if resolved_web_root is not None:
        index_path = resolved_web_root / "index.html"

        @app.get("/{spa_path:path}", include_in_schema=False)
        async def web_spa(spa_path: str) -> FileResponse:
            if spa_path == "api" or spa_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="页面不存在")
            candidate = (resolved_web_root / spa_path).resolve()
            try:
                candidate.relative_to(resolved_web_root)
            except ValueError as error:
                raise HTTPException(status_code=404, detail="页面不存在") from error
            if candidate.is_file():
                return FileResponse(candidate)
            if spa_path == "assets" or spa_path.startswith("assets/"):
                raise HTTPException(status_code=404, detail="页面不存在")
            return FileResponse(index_path)

    return app


def _usable_web_root(web_static_root: Path | None) -> Path | None:
    if web_static_root is None:
        return None
    resolved = web_static_root.expanduser().resolve()
    if not resolved.is_dir() or not (resolved / "index.html").is_file():
        return None
    return resolved


def _make_upload_directory(root: Path | None) -> Path:
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="hachimi-local-run-", dir=str(root) if root else None))


async def _write_upload(media: UploadFile, destination: Path, *, max_bytes: int) -> None:
    written = 0
    try:
        with destination.open("xb") as output:
            while chunk := await media.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="视频文件超过上传大小限制")
                output.write(chunk)
    except OSError as error:
        raise HTTPException(status_code=422, detail="无法接收视频文件") from error
    if written == 0:
        raise HTTPException(status_code=422, detail="视频文件为空")


async def _remove_upload_directory(directory: Path) -> None:
    await asyncio.to_thread(shutil.rmtree, directory)


async def _run_cleanup_to_completion(cleanup: Awaitable[None]) -> None:
    cleanup_task = asyncio.ensure_future(cleanup)
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise


def _validate_production_cors_origins(origins: list[str]) -> None:
    for origin in origins:
        parsed = urlparse(origin)
        try:
            _ = parsed.port
        except ValueError as error:
            raise ValueError("production CORS origin must be an exact HTTPS origin") from error
        if (
            origin != origin.strip()
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or "*" in parsed.netloc
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("production CORS origin must be an exact HTTPS origin")


def _set_access_cookie(
    response: Response,
    access_manager: AccessManager,
    session: AccessSession,
) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_manager.encode(session),
        max_age=ACCESS_SESSION_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _require_same_origin(
    request: Request,
    *,
    app_env: str,
    development_origins: list[str],
) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        if app_env == "production":
            raise HTTPException(status_code=403, detail="请求来源无效")
        return
    expected_host = request.headers.get("host", "").lower()
    parsed = urlparse(origin)
    is_same_host = parsed.scheme in {"http", "https"} and parsed.netloc.lower() == expected_host
    is_allowed_development_origin = app_env != "production" and origin in development_origins
    if not is_same_host and not is_allowed_development_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")


def _client_ip(
    request: Request,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str:
    peer_value = request.client.host if request.client is not None else ""
    try:
        peer_ip = ip_address(peer_value)
    except ValueError:
        return "unknown"
    if not any(peer_ip in network for network in trusted_proxy_networks):
        return str(peer_ip)

    forwarded_for = request.headers.get("x-forwarded-for")
    real_ip = request.headers.get("x-real-ip")
    forwarded_values = [value.strip() for value in (forwarded_for, real_ip) if value]
    if not forwarded_values or any("," in value for value in forwarded_values):
        raise HTTPException(status_code=400, detail="客户端地址无效")
    if len(set(forwarded_values)) != 1:
        raise HTTPException(status_code=400, detail="客户端地址无效")
    try:
        return str(ip_address(forwarded_values[0]))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="客户端地址无效") from error
