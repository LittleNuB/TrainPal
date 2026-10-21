import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.access import AccessManager
from hakimi_analysis.app import create_app
from hakimi_analysis.readiness import PortfolioReadiness, ProductionReadiness
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import EmptySourceCatalog, SourceCatalog, VideoSource


@pytest.mark.asyncio
async def test_production_ready_fails_closed_without_real_provider_configuration(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        source_manifest_path=tmp_path / "missing-sources.json",
        source_media_root=tmp_path / "media",
        public_media_base_url="https://media.example.com/hachimi/",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=EmptySourceCatalog(),
        temp_root=tmp_path / "analysis-runs",
        skills_root=tmp_path / "skills",
    )
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        health = await client.get("/api/v1/health")
        ready = await client.get("/api/v1/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "code": "provider_configuration_invalid",
    }
    serialized = ready.text
    assert "missing-sources.json" not in serialized
    assert "cookie-signing-secret" not in serialized


@pytest.mark.asyncio
async def test_production_does_not_start_analysis_while_not_ready(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        source_manifest_path=tmp_path / "missing-sources.json",
        source_media_root=tmp_path / "media",
        public_media_base_url="https://media.example.com/hachimi/",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=EmptySourceCatalog(),
        temp_root=tmp_path / "analysis-runs",
        skills_root=tmp_path / "skills",
    )
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"not-read")
    catalog = SourceCatalog(
        [VideoSource(id="arm-01", title="手臂训练", path=source_path, duration_seconds=60)]
    )
    access = AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-code",
        public_concurrency=1,
    )
    app = create_app(
        catalog=catalog,
        access=access,
        app_env="production",
        readiness=readiness,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/analysis-runs",
            json={"source_id": "arm-01", "trigger_seconds": 10},
            headers={"Origin": "https://test"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "动作分析服务尚未就绪"}


@pytest.mark.asyncio
async def test_access_session_does_not_claim_analysis_is_available_while_not_ready(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        analysis_provider="cloud",
        judge_access_code="judge-code",
        access_cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
    )
    readiness = ProductionReadiness(
        settings=settings,
        catalog=EmptySourceCatalog(),
        temp_root=tmp_path / "analysis-runs",
        skills_root=tmp_path / "skills",
    )
    access = AccessManager(
        cookie_secret="cookie-signing-secret-with-at-least-32-bytes",
        judge_access_code="judge-code",
    )
    app = create_app(
        access=access,
        app_env="production",
        readiness=readiness,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        await client.get("/api/v1/access/session")
        upgraded = await client.post(
            "/api/v1/access/session",
            json={"access_code": "judge-code"},
            headers={"Origin": "https://test"},
        )

    assert upgraded.status_code == 200
    assert upgraded.json() == {
        "tier": "judge",
        "can_analyze": False,
        "retry_after_seconds": None,
    }


def configured_readiness(
    tmp_path: Path,
    *,
    readiness_type: type[ProductionReadiness] = ProductionReadiness,
    controlled_sources: bool = True,
    controlled_source_count: int = 5,
    include_web_root: bool = True,
    trusted_proxy_cidrs: str = "",
    judge_access_code: str = "judge-access-code-at-least-16-bytes",
    include_ffmpeg: bool = True,
    include_ffmpeg_receipt: bool = True,
    ffmpeg_audit_passes: bool = True,
    settings_overrides: dict[str, Any] | None = None,
) -> tuple[ProductionReadiness, Path]:
    media_root = tmp_path / "media"
    media_path = media_root / "controlled-01.mp4"
    manifest_path = tmp_path / "sources.json"
    source_durations: dict[Path, float] = {}
    if controlled_sources:
        media_root.mkdir()
        sources: list[dict[str, object]] = []
        for index in range(1, controlled_source_count + 1):
            fixture_path = media_root / f"controlled-{index:02d}.mp4"
            fixture_content = f"authorized-video-{index:02d}".encode()
            fixture_duration = float(59 + index)
            fixture_path.write_bytes(fixture_content)
            source_durations[fixture_path] = fixture_duration
            sources.append(
                {
                    "id": f"controlled-{index:02d}",
                    "title": "Controlled training video",
                    "media_path": fixture_path.name,
                    "duration_seconds": fixture_duration,
                    "sha256": hashlib.sha256(fixture_content).hexdigest(),
                    "origin_url": None,
                }
            )
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": sources,
                }
            ),
            encoding="utf-8",
        )
    skills_root = tmp_path / "skills"
    for skill_name in (
        "training-speech-understanding",
        "visual-action-localization",
        "candidate-fusion",
    ):
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("version: 1.0.0\n", encoding="utf-8")
    web_static_root = tmp_path / "web-dist"
    if include_web_root:
        web_static_root.mkdir()
        (web_static_root / "index.html").write_text("<main>ready</main>", encoding="utf-8")
    ffmpeg_root = tmp_path / "opt" / "trainpal" / "ffmpeg"
    ffmpeg_path = ffmpeg_root / "bin" / "ffmpeg"
    ffmpeg_receipt_path = ffmpeg_root / "receipt.json"
    if include_ffmpeg:
        ffmpeg_path.parent.mkdir(parents=True)
        ffmpeg_path.write_bytes(b"server-managed-ffmpeg")
        if include_ffmpeg_receipt:
            ffmpeg_receipt_path.write_text("{}", encoding="utf-8")
    ffmpeg_sha256 = hashlib.sha256(b"server-managed-ffmpeg").hexdigest()
    settings_kwargs: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "analysis_provider": "cloud",
        "ark_api_key": "ark-key",
        "volc_asr_api_key": "asr-key",
        "source_manifest_path": manifest_path if controlled_sources else None,
        "source_media_root": media_root if controlled_sources else None,
        "public_media_base_url": (
            "https://media.example.com/hachimi/" if controlled_sources else None
        ),
        "judge_access_code": judge_access_code,
        "access_cookie_secret": "cookie-signing-secret-with-at-least-32-bytes",
        "judge_analysis_concurrency": 0,
        "public_analysis_concurrency": 1,
        "gymti_llm_api_key": "ark-key",
        "gymti_llm_enabled": True,
        "gymti_llm_retention_confirmed": True,
        "gymti_llm_concurrency": 3,
        "web_static_root": web_static_root,
        "trusted_proxy_cidrs": trusted_proxy_cidrs,
        "imageio_ffmpeg_exe": ffmpeg_path if include_ffmpeg else None,
        "ffmpeg_build_receipt_path": (
            ffmpeg_receipt_path if include_ffmpeg and include_ffmpeg_receipt else None
        ),
        "ffmpeg_expected_sha256": ffmpeg_sha256 if include_ffmpeg else None,
        "ffmpeg_expected_configuration_sha256": "c" * 64 if include_ffmpeg else None,
    }
    settings_kwargs.update(settings_overrides or {})
    settings = Settings(**settings_kwargs)
    catalog = (
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=media_root,
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: source_durations[path],
        )
        if controlled_sources
        else EmptySourceCatalog()
    )
    return (
        readiness_type(
            settings=settings,
            catalog=catalog,
            temp_root=tmp_path / "analysis-runs",
            skills_root=skills_root,
            duration_probe=lambda path: source_durations[path],
            ffmpeg_receipt_probe=lambda *_: ffmpeg_audit_passes,
        ),
        media_path,
    )


@pytest.mark.asyncio
async def test_production_ready_requires_a_high_entropy_judge_code(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, judge_access_code="short")
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "access_configuration_invalid",
    }


@pytest.mark.asyncio
async def test_production_ready_requires_a_server_managed_ffmpeg(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, include_ffmpeg=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "media_processor_unavailable",
    }


@pytest.mark.asyncio
async def test_production_ready_rejects_ffmpeg_that_fails_runtime_audit(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, ffmpeg_audit_passes=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "media_processor_unavailable",
    }


@pytest.mark.asyncio
async def test_production_ready_rejects_legacy_hashes_without_signed_build_receipt(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path, include_ffmpeg_receipt=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "code": "media_processor_unavailable",
    }


@pytest.mark.parametrize(
    ("setting_name", "unsafe_value"),
    [
        ("local_upload_enabled", False),
        ("local_analysis_max_seconds", 299),
        ("local_upload_max_bytes", 128 * 1024 * 1024),
        ("analysis_evidence_timeout_seconds", 169),
        ("analysis_chunk_timeout_seconds", 19),
        ("analysis_visual_chunk_seconds", 50),
        ("analysis_visual_overlap_seconds", 5),
        ("run_timeout_seconds", 179),
        ("judge_analysis_concurrency", 1),
        ("public_analysis_concurrency", 0),
        ("trusted_proxy_cidrs", "10.0.0.0/8"),
    ],
)
def test_production_ready_rejects_competition_profile_drift(
    tmp_path: Path,
    setting_name: str,
    unsafe_value: object,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        settings_overrides={setting_name: unsafe_value},
    )

    assert readiness.check() == "competition_configuration_invalid"


@pytest.mark.parametrize(
    ("setting_name", "unsafe_value"),
    [
        ("gymti_llm_api_key", None),
        ("gymti_llm_enabled", False),
        ("gymti_llm_retention_confirmed", False),
    ],
)
def test_production_ready_requires_the_approved_gymti_provider_gate(
    tmp_path: Path,
    setting_name: str,
    unsafe_value: object,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        settings_overrides={setting_name: unsafe_value},
    )

    assert readiness.check() == "provider_configuration_invalid"


@pytest.mark.parametrize(
    "setting_name",
    ["ark_api_key", "volc_asr_api_key", "gymti_llm_api_key"],
)
def test_production_ready_rejects_whitespace_only_provider_keys(
    tmp_path: Path,
    setting_name: str,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        settings_overrides={setting_name: "   "},
    )

    assert readiness.check() == "provider_configuration_invalid"


def test_production_ready_rejects_gymti_model_drift_from_doubao_seed_mini(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path)
    readiness._settings.gymti_llm_model = "doubao-seed-2-0-pro-260428"

    assert readiness.check() == "provider_configuration_invalid"


@pytest.mark.parametrize("contract_payload", [None, "{}"])
def test_production_ready_rejects_a_missing_or_invalid_gymti_contract(
    tmp_path: Path,
    contract_payload: str | None,
) -> None:
    contract_path = tmp_path / "invalid-gymti-contract.json"
    if contract_payload is not None:
        contract_path.write_text(contract_payload, encoding="utf-8")
    readiness, _ = configured_readiness(
        tmp_path,
        settings_overrides={"gymti_contract_path": contract_path},
    )

    assert readiness.check() == "gymti_contract_invalid"


def test_production_ready_requires_the_independent_three_slot_gymti_gate(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        settings_overrides={"gymti_llm_concurrency": 2},
    )

    assert readiness.check() == "competition_configuration_invalid"


@pytest.mark.asyncio
async def test_production_ready_requires_built_web_entrypoint(tmp_path: Path) -> None:
    readiness, _ = configured_readiness(tmp_path, include_web_root=False)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "code": "web_static_unavailable"}


@pytest.mark.asyncio
async def test_production_ready_uses_direct_peer_when_no_trusted_proxy_is_configured(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path, trusted_proxy_cidrs="")
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_production_ready_rejects_local_upload_without_controlled_catalog(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(
        tmp_path,
        controlled_sources=False,
        trusted_proxy_cidrs="",
    )
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "code": "source_manifest_invalid"}


@pytest.mark.asyncio
async def test_production_ready_requires_exactly_five_controlled_sources(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path, controlled_source_count=4)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "code": "source_manifest_invalid"}


@pytest.mark.asyncio
async def test_production_ready_checks_manifest_media_skills_access_and_temp_storage(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path)
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert list((tmp_path / "analysis-runs").iterdir()) == []


@pytest.mark.asyncio
async def test_production_ready_detects_media_changed_after_startup(tmp_path: Path) -> None:
    readiness, media_path = configured_readiness(tmp_path)
    media_path.write_bytes(b"tampered-video")
    app = create_app(app_env="production", readiness=readiness)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "code": "media_cache_invalid"}


def test_production_ready_rejects_an_unlisted_file_added_to_the_media_cache(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path)
    unexpected = tmp_path / "media" / "unlisted-private-video.mp4"
    unexpected.write_bytes(b"synthetic-unlisted-media")

    assert readiness.check() == "media_cache_invalid"


def test_production_ready_rejects_a_symbolic_link_added_to_the_media_cache(
    tmp_path: Path,
) -> None:
    readiness, _ = configured_readiness(tmp_path)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    link = tmp_path / "media" / "unlisted-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("symbolic links are unavailable for this test user")
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory links are unavailable for this test user")

    assert readiness.check() == "media_cache_invalid"


@pytest.mark.parametrize(
    "incorrect", [{}, {"ark_model_id": "doubao-seed-2-1-pro-260628"}, {"ai_budget_db_path": None}]
)
def test_portfolio_readiness_has_separate_strict_policy(
    tmp_path: Path, incorrect: dict[str, Any]
) -> None:
    overrides = {
        "deployment_profile": "portfolio",
        "ark_model_id": "doubao-seed-2-1-pro-260915",
        "ark_visual_model_id": "doubao-seed-2-1-pro-260628",
        "ark_analysis_response_format": "json_object",
        "training_reminders_enabled": True,
        "ai_budget_db_path": tmp_path / "budget" / "ai.sqlite",
        "public_base_path": "/trainpal",
        "local_upload_max_bytes": 64 * 1024 * 1024,
        "run_timeout_seconds": 600,
        "analysis_evidence_timeout_seconds": 590,
        "analysis_chunk_timeout_seconds": 90,
        "analysis_visual_short_context_seconds": 90,
    }
    overrides.update(incorrect)
    readiness, _ = configured_readiness(
        tmp_path,
        readiness_type=PortfolioReadiness,
        trusted_proxy_cidrs="127.0.0.1/32",
        settings_overrides=overrides,
    )
    assert readiness.check() == ("portfolio_configuration_invalid" if incorrect else None)
