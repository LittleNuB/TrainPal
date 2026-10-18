from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from hakimi_analysis.settings import Settings


def test_test_provider_is_rejected_outside_test_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="development", analysis_provider="test")


def test_test_provider_is_allowed_only_in_test_environment() -> None:
    settings = Settings(_env_file=None, app_env="test", analysis_provider="test")
    assert settings.analysis_provider == "test"


def test_analysis_capacity_has_safe_production_defaults() -> None:
    settings = Settings(_env_file=None, app_env="production")
    assert settings.judge_analysis_concurrency == 0
    assert settings.public_analysis_concurrency == 1


def test_default_development_cors_origins_match_vite_loopback_hosts() -> None:
    settings = Settings(_env_file=None)

    assert settings.cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_analysis_evidence_budget_has_a_bounded_positive_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.analysis_evidence_timeout_seconds == 170.0
    assert settings.analysis_chunk_timeout_seconds == 20.0
    assert settings.analysis_visual_chunk_seconds == 60.0
    assert settings.analysis_visual_short_context_seconds == 0.0
    assert settings.analysis_visual_overlap_seconds == 10.0
    assert settings.analysis_latency_target_max_seconds_per_video_minute == 15.0

    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_evidence_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_latency_target_max_seconds_per_video_minute=0)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            analysis_visual_chunk_seconds=60,
            analysis_visual_overlap_seconds=60,
        )


def test_local_analysis_defaults_to_300_seconds_and_has_a_300_second_hard_cap() -> None:
    settings = Settings(_env_file=None)

    assert settings.local_upload_enabled is True
    assert settings.local_analysis_max_seconds == 300
    assert settings.local_upload_max_bytes == 256 * 1024 * 1024

    configured = Settings(_env_file=None, local_analysis_max_seconds=300)
    assert configured.local_analysis_max_seconds == 300
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_analysis_max_seconds=300.001)


def test_web_static_root_is_an_optional_backend_path(tmp_path: Path) -> None:
    without_static = Settings(_env_file=None)
    with_static = Settings(_env_file=None, web_static_root=tmp_path / "dist")

    assert without_static.web_static_root is None
    assert with_static.web_static_root == tmp_path / "dist"


def test_release_sha_is_optional_but_must_be_an_immutable_git_identity() -> None:
    assert Settings(_env_file=None).app_release_sha is None
    assert Settings(_env_file=None, app_release_sha="a" * 40).app_release_sha == "a" * 40

    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_release_sha="A" * 40)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_release_sha="a" * 39)


def test_production_ffmpeg_is_an_explicit_optional_path(tmp_path: Path) -> None:
    without_ffmpeg = Settings(_env_file=None)
    with_ffmpeg = Settings(_env_file=None, imageio_ffmpeg_exe=tmp_path / "ffmpeg")

    assert without_ffmpeg.imageio_ffmpeg_exe is None
    assert with_ffmpeg.imageio_ffmpeg_exe == tmp_path / "ffmpeg"


def test_ffmpeg_audit_hashes_are_normalized_and_validated() -> None:
    settings = Settings(
        _env_file=None,
        ffmpeg_expected_sha256=f" {'A' * 64} ",
        ffmpeg_expected_configuration_sha256="B" * 64,
    )

    assert settings.ffmpeg_expected_sha256 == "a" * 64
    assert settings.ffmpeg_expected_configuration_sha256 == "b" * 64

    with pytest.raises(ValidationError):
        Settings(_env_file=None, ffmpeg_expected_sha256="not-a-sha256")


def test_trusted_proxy_cidrs_are_split_without_wildcard_defaults() -> None:
    default = Settings(_env_file=None)
    configured = Settings(
        _env_file=None,
        trusted_proxy_cidrs="172.30.248.2/32, 2001:db8::1/128",
    )

    assert default.trusted_proxy_cidr_list == []
    assert configured.trusted_proxy_cidr_list == ["172.30.248.2/32", "2001:db8::1/128"]

    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_proxy_cidrs="0.0.0.0/0")


def test_secret_values_are_redacted_from_settings_repr() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        analysis_provider="cloud",
        ark_api_key=SecretStr("ark-secret-value"),
        volc_asr_api_key=SecretStr("asr-secret-value"),
        judge_access_code=SecretStr("judge-secret-value"),
        access_cookie_secret=SecretStr("cookie-secret-value"),
    )
    representation = repr(settings)
    assert "ark-secret-value" not in representation
    assert "asr-secret-value" not in representation
    assert "judge-secret-value" not in representation
    assert "cookie-secret-value" not in representation


def test_gymti_ark_settings_have_safe_local_fallback_defaults() -> None:
    settings = Settings(_env_file=None)
    configured = Settings(
        _env_file=None,
        gymti_llm_api_key=SecretStr("ark-gymti-secret-value"),
        gymti_llm_model="doubao-seed-2-0-mini-260428",
        gymti_llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
        gymti_llm_enabled=True,
        gymti_llm_retention_confirmed=True,
    )

    assert settings.gymti_llm_api_key is None
    assert settings.gymti_llm_enabled is False
    assert settings.gymti_llm_retention_confirmed is False
    assert settings.gymti_llm_temperature > 0
    assert settings.gymti_llm_model == "doubao-seed-2-0-mini-260428"
    assert settings.gymti_llm_base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert settings.gymti_llm_concurrency == 3
    assert configured.gymti_llm_enabled is True
    assert configured.gymti_llm_retention_confirmed is True
    assert configured.gymti_llm_model == "doubao-seed-2-0-mini-260428"
    assert "ark-gymti-secret-value" not in repr(configured)


def test_legacy_deepseek_environment_variables_cannot_configure_gymti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-deepseek-secret")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    settings = Settings(_env_file=None)

    assert settings.gymti_llm_api_key is None
    assert settings.gymti_llm_model == "doubao-seed-2-0-mini-260428"
    assert settings.gymti_llm_base_url == "https://ark.cn-beijing.volces.com/api/v3"


def test_production_rejects_a_non_ark_gymti_endpoint() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            gymti_llm_enabled=True,
            gymti_llm_base_url="https://api.deepseek.com/v1",
        )


def test_production_gymti_requires_the_exact_doubao_seed_mini_model() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            gymti_llm_enabled=True,
            gymti_llm_model="doubao-seed-2-0-pro-260428",
        )

    settings = Settings(
        _env_file=None,
        app_env="production",
        ark_model_id="operator-selected-video-model",
        gymti_llm_enabled=True,
        gymti_llm_model="doubao-seed-2-0-mini-260428",
    )

    assert settings.ark_model_id == "operator-selected-video-model"
    assert settings.gymti_llm_model == "doubao-seed-2-0-mini-260428"


def test_gymti_model_gate_concurrency_is_configurable_and_positive() -> None:
    configured = Settings(_env_file=None, gymti_llm_concurrency=4)

    assert configured.gymti_llm_concurrency == 4
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gymti_llm_concurrency=0)
