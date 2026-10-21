"""Explicit local acceptance composition; the normal application stays unchanged."""

import os
import secrets
from collections.abc import MutableMapping
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import (
    build_adjustment_service,
    build_catalog,
    build_gymti_service,
    build_http_client,
    build_pipeline,
    build_playback_service,
    build_reminder_planner,
)
from hakimi_analysis.dev import configure_local_environment
from hakimi_analysis.runtime_cleanup import RuntimeCleanupMonitor
from hakimi_analysis.settings import PROJECT_ROOT, Settings


def configure_acceptance_environment(environment: MutableMapping[str, str]) -> None:
    if (
        environment.get("APP_ENV", "development") != "development"
        or environment.get("ANALYSIS_PROVIDER", "cloud") != "cloud"
    ):
        raise ValueError("acceptance requires APP_ENV=development and ANALYSIS_PROVIDER=cloud")
    configure_local_environment(environment)


def build_acceptance_app(
    *,
    settings: Settings | None = None,
    provider_transport: httpx.AsyncBaseTransport | None = None,
    temp_root: Path | None = None,
) -> FastAPI:
    settings = settings or Settings(web_static_root=PROJECT_ROOT / "apps" / "web" / "dist")
    if settings.app_env != "development" or settings.analysis_provider != "cloud":
        raise ValueError("acceptance requires APP_ENV=development and ANALYSIS_PROVIDER=cloud")
    absent_credentials = {
        field: None
        for field, value in {
            "ark_api_key": settings.ark_api_key,
            "volc_asr_api_key": settings.volc_asr_api_key,
        }.items()
        if value is not None and not value.get_secret_value().strip()
    }
    settings = settings.model_copy(update=absent_credentials)
    web_root = settings.web_static_root or PROJECT_ROOT / "apps" / "web" / "dist"
    if not (web_root / "index.html").is_file() or not (web_root / "assets").is_dir():
        raise ValueError("acceptance requires a built frontend; run pnpm build first")
    http_client = build_http_client(settings, transport=provider_transport)
    temp_root = temp_root or PROJECT_ROOT / "tmp" / "reminder-acceptance"
    runtime_cleanup = RuntimeCleanupMonitor(temp_root)
    access = AccessManager(
        cookie_secret=secrets.token_urlsafe(32),
        judge_access_code=secrets.token_urlsafe(16),
        judge_concurrency=0,
        public_concurrency=settings.public_analysis_concurrency,
        public_attempt_limit=None,
    )
    return create_app(
        playback_service=build_playback_service(settings, http_client),
        adjustment_service=build_adjustment_service(settings, http_client),
        gymti_service=build_gymti_service(
            settings.model_copy(update={"gymti_llm_max_attempts": 1}), http_client
        ),
        gymti_llm_enabled=settings.gymti_llm_enabled,
        gymti_llm_concurrency=settings.gymti_llm_concurrency,
        catalog=build_catalog(settings),
        pipeline=build_pipeline(
            settings,
            http_client,
            temp_root=temp_root,
            runtime_cleanup=runtime_cleanup,
            provider_retry_delays=(),
        ),
        access=access,
        app_env=settings.app_env,
        timeout_seconds=settings.run_timeout_seconds,
        ttl_seconds=settings.run_ttl_seconds,
        local_upload_enabled=settings.local_upload_enabled,
        local_analysis_max_seconds=settings.local_analysis_max_seconds,
        local_upload_max_bytes=settings.local_upload_max_bytes,
        local_upload_temp_root=temp_root,
        runtime_cleanup=runtime_cleanup,
        web_static_root=web_root,
        cors_origins=[],
        release_sha=settings.app_release_sha,
        reminder_planner=build_reminder_planner(settings, http_client),
        close_callbacks=[http_client.aclose],
    )


if __name__ == "__main__":
    configure_acceptance_environment(os.environ)
    uvicorn.run(
        "hakimi_analysis.acceptance:build_acceptance_app",
        factory=True,
        host="127.0.0.1",
        port=15177,
    )
