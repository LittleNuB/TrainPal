import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI

from hakimi_analysis.access import AccessManager
from hakimi_analysis.gymti import (
    GymtiContractError,
    GymtiService,
    OpenAiStyleGymtiModel,
    load_gymti_contract,
)
from hakimi_analysis.media import LocalMediaProcessor, probe_duration_sync
from hakimi_analysis.models import (
    AnalysisCandidate,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.pipeline import AnalysisPipeline, EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.playback import PlaybackService
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.asr import VolcAsrClient
from hakimi_analysis.readiness import ProductionReadiness
from hakimi_analysis.runtime_cleanup import RuntimeCleanupMonitor
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.settings import PROJECT_ROOT, Settings
from hakimi_analysis.sources import (
    MAX_ANALYZABLE_SOURCE_DURATION_SECONDS,
    EmptySourceCatalog,
    SourceCatalog,
    SourceManifestError,
    VideoSource,
)


class UnconfiguredPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        raise PipelineFailure(
            "configuration_error",
            "动作分析服务尚未配置",
            retryable=False,
        )


class DeterministicTestPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        await emit(RunStage.ANALYZING_EVIDENCE, "stage.changed", {})
        start = 0
        end = min(source.analysis_duration_seconds, 10)
        await emit(RunStage.FUSING_CANDIDATES, "stage.changed", {})
        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id="candidate-1",
                    name="拖拽弯举",
                    source_id=source.id,
                    segment=Segment(start_seconds=start, end_seconds=end),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=EvidenceType.SPEECH,
                            start_seconds=start,
                            end_seconds=end,
                        ),
                        EvidenceSpan(
                            type=EvidenceType.VISUAL,
                            start_seconds=start,
                            end_seconds=end,
                        ),
                    ],
                    needs_confirmation=True,
                )
            ]
        )


def build_catalog(settings: Settings) -> SourceCatalog:
    if (
        settings.source_manifest_path is not None
        and settings.source_media_root is not None
        and settings.public_media_base_url is not None
    ):
        try:
            return SourceCatalog.from_manifest(
                manifest_path=settings.source_manifest_path,
                media_root=settings.source_media_root,
                public_media_base_url=settings.public_media_base_url,
                duration_probe=probe_duration_sync,
            )
        except SourceManifestError:
            return EmptySourceCatalog()
    if settings.app_env == "production":
        # Production media is allowlisted only by the versioned manifest. A legacy
        # developer path must never become public when deployment configuration is
        # missing or incomplete.
        return EmptySourceCatalog()
    path = settings.hakimi_demo_video_path
    if path is None:
        return EmptySourceCatalog()
    resolved_path = path.expanduser().resolve()
    duration = probe_duration_sync(resolved_path)
    if duration > MAX_ANALYZABLE_SOURCE_DURATION_SECONDS:
        return EmptySourceCatalog()
    sources = [
        VideoSource(
            id="legacy-arm-workout",
            title="哈基米手臂训练｜本地来源视频",
            path=resolved_path,
            duration_seconds=duration,
        )
    ]
    if settings.app_env == "test":
        sources.append(
            VideoSource(
                id="legacy-arm-workout-alt",
                title="哈基米手臂训练 B｜合成测试来源",
                path=resolved_path,
                duration_seconds=duration,
            )
        )
    return SourceCatalog(sources)


def build_pipeline(
    settings: Settings,
    http_client: httpx.AsyncClient,
    *,
    temp_root: Path | None = None,
    runtime_cleanup: RuntimeCleanupMonitor | None = None,
) -> AnalysisPipeline:
    if settings.analysis_provider == "test":
        return DeterministicTestPipeline()
    if settings.ark_api_key is None or settings.volc_asr_api_key is None:
        return UnconfiguredPipeline()

    media = LocalMediaProcessor(
        temp_root=temp_root or PROJECT_ROOT / "tmp" / "analysis-runs",
        max_source_duration_seconds=settings.local_analysis_max_seconds,
        runtime_cleanup=runtime_cleanup,
    )
    asr = VolcAsrClient(
        api_key=settings.volc_asr_api_key.get_secret_value(),
        resource_id=settings.volc_asr_resource_id,
        url=settings.volc_asr_url,
        pace_audio=False,
    )
    ark = ArkResponsesClient(
        api_key=settings.ark_api_key.get_secret_value(),
        model_id=settings.ark_model_id,
        visual_model_id=settings.ark_visual_model_id,
        base_url=settings.ark_base_url,
        http_client=http_client,
    )
    try:
        skills = SkillRepository.load(PROJECT_ROOT / "skills")
    except (OSError, ValueError):
        return UnconfiguredPipeline()
    return OrchestratedAnalysisPipeline(
        media=media,
        asr=asr,
        ark=ark,
        skills=skills,
        semantic_fusion=SemanticCandidateFusion(model=ark, instructions=skills.fusion_instructions),
        evidence_timeout_seconds=settings.analysis_evidence_timeout_seconds,
        visual_chunk_timeout_seconds=settings.analysis_chunk_timeout_seconds,
        visual_chunk_seconds=settings.analysis_visual_chunk_seconds,
        visual_overlap_seconds=settings.analysis_visual_overlap_seconds,
        run_timeout_seconds=settings.run_timeout_seconds,
    )


def build_gymti_service(
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> GymtiService | None:
    """Build a stateless GYMTI seam; disabled or unavailable LLMs use local fallback."""

    try:
        contract = load_gymti_contract(settings.gymti_contract_path)
    except GymtiContractError:
        return None
    if not settings.gymti_llm_enabled or not settings.gymti_llm_retention_confirmed:
        return GymtiService(
            contract=contract,
            model=None,
            model_name=None,
            model_attempts=settings.gymti_llm_max_attempts,
        )
    api_key = (
        settings.gymti_llm_api_key.get_secret_value().strip()
        if settings.gymti_llm_api_key is not None
        else ""
    )
    model_name = settings.gymti_llm_model.strip()
    base_url = settings.gymti_llm_base_url.strip()
    if not api_key or not model_name or not base_url:
        return GymtiService(
            contract=contract,
            model=None,
            model_name=None,
            model_attempts=settings.gymti_llm_max_attempts,
        )
    return GymtiService(
        contract=contract,
        model=OpenAiStyleGymtiModel(
            api_key=api_key,
            model=model_name,
            base_url=base_url,
            http_client=http_client,
            timeout_seconds=settings.gymti_llm_timeout_seconds,
            temperature=settings.gymti_llm_temperature,
        ),
        model_name=model_name,
        model_attempts=settings.gymti_llm_max_attempts,
    )


def build_default_app() -> FastAPI:
    from hakimi_analysis.app import create_app

    settings = Settings()
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.run_timeout_seconds, connect=15),
        follow_redirects=False,
    )
    catalog = build_catalog(settings)
    temp_root = PROJECT_ROOT / "tmp" / "analysis-runs"
    runtime_cleanup = RuntimeCleanupMonitor(temp_root)
    configured_cookie_secret = (
        settings.access_cookie_secret.get_secret_value()
        if settings.access_cookie_secret is not None
        else ""
    )
    cookie_secret = (
        configured_cookie_secret
        if len(configured_cookie_secret.encode("utf-8")) >= 32
        else secrets.token_urlsafe(32)
    )
    configured_judge_access_code = (
        settings.judge_access_code.get_secret_value()
        if settings.judge_access_code is not None
        else ""
    )
    judge_access_code = (
        configured_judge_access_code if configured_judge_access_code else secrets.token_urlsafe(16)
    )
    public_concurrency = settings.public_analysis_concurrency
    if settings.app_env == "test":
        public_concurrency = max(1, public_concurrency)
    access = AccessManager(
        cookie_secret=cookie_secret,
        judge_access_code=judge_access_code,
        judge_concurrency=settings.judge_analysis_concurrency,
        public_concurrency=public_concurrency,
        public_attempt_limit=None,
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=catalog,
        temp_root=temp_root,
        skills_root=PROJECT_ROOT / "skills",
    )
    return create_app(
        playback_service=build_playback_service(settings, http_client),
        catalog=catalog,
        pipeline=build_pipeline(
            settings,
            http_client,
            temp_root=temp_root,
            runtime_cleanup=runtime_cleanup,
        ),
        timeout_seconds=settings.run_timeout_seconds,
        ttl_seconds=settings.run_ttl_seconds,
        cors_origins=settings.cors_origin_list,
        close_callbacks=[http_client.aclose],
        access=access,
        app_env=settings.app_env,
        readiness=readiness,
        web_static_root=settings.web_static_root,
        trusted_proxy_cidrs=settings.trusted_proxy_cidr_list,
        local_upload_enabled=settings.local_upload_enabled,
        local_analysis_max_seconds=settings.local_analysis_max_seconds,
        local_upload_max_bytes=settings.local_upload_max_bytes,
        local_upload_temp_root=temp_root,
        gymti_service=build_gymti_service(settings, http_client),
        gymti_llm_enabled=settings.gymti_llm_enabled,
        gymti_llm_concurrency=settings.gymti_llm_concurrency,
        release_sha=settings.app_release_sha,
        runtime_cleanup=runtime_cleanup,
    )


def build_playback_service(
    settings: Settings, http_client: httpx.AsyncClient
) -> PlaybackService | None:
    if settings.app_env == "test" or settings.ark_api_key is None:
        return None
    model = ArkResponsesClient(
        api_key=settings.ark_api_key.get_secret_value(),
        model_id=settings.ark_model_id,
        base_url=settings.ark_base_url,
        http_client=http_client,
        retry_delays=(),
    )
    asr = (
        VolcAsrClient(
            api_key=settings.volc_asr_api_key.get_secret_value(),
            resource_id=settings.volc_asr_resource_id,
            url=settings.volc_asr_url,
            pace_audio=False,
            retry_delays=(),
        )
        if settings.volc_asr_api_key
        else None
    )
    return PlaybackService(model=model, asr=asr)
