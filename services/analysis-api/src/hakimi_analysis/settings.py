import re
from ipaddress import ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
GYMTI_DOUBAO_SEED_MINI_MODEL = "doubao-seed-2-0-mini-260428"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_release_sha: str | None = None
    analysis_provider: Literal["cloud", "test"] = "cloud"
    ark_api_key: SecretStr | None = None
    ark_model_id: str = "doubao-seed-2-0-mini-260428"
    ark_visual_model_id: str = "doubao-seed-2-0-mini-260428"
    ark_fusion_model_id: str | None = Field(default=None, min_length=1)
    ark_analysis_response_format: Literal["json_schema", "json_object"] = "json_schema"
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volc_asr_api_key: SecretStr | None = None
    volc_asr_resource_id: str = "volc.seedasr.sauc.duration"
    volc_asr_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    hakimi_demo_video_path: Path | None = None
    source_manifest_path: Path | None = None
    source_media_root: Path | None = None
    public_media_base_url: str | None = None
    smoke_annotations_path: Path | None = None
    web_static_root: Path | None = None
    imageio_ffmpeg_exe: Path | None = None
    ffmpeg_build_receipt_path: Path | None = None
    ffmpeg_expected_sha256: str | None = None
    ffmpeg_expected_configuration_sha256: str | None = None
    judge_access_code: SecretStr | None = None
    access_cookie_secret: SecretStr | None = None
    judge_analysis_concurrency: int = Field(default=0, ge=0)
    public_analysis_concurrency: int = Field(default=1, ge=0)
    trusted_proxy_cidrs: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    run_ttl_seconds: int = Field(default=600, ge=1)
    run_timeout_seconds: int = Field(default=180, ge=1)
    local_upload_enabled: bool = True
    local_analysis_max_seconds: float = Field(default=300, gt=0, le=300)
    local_upload_max_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    analysis_evidence_timeout_seconds: float = Field(default=170.0, gt=0)
    analysis_chunk_timeout_seconds: float = Field(default=20.0, gt=0)
    analysis_visual_chunk_seconds: float = Field(default=60.0, gt=0, le=300)
    analysis_visual_overlap_seconds: float = Field(default=10.0, ge=0)
    analysis_visual_short_context_seconds: float = Field(default=0.0, ge=0, le=90)
    analysis_latency_target_max_seconds_per_video_minute: float = Field(default=15.0, gt=0)
    gymti_contract_path: Path = PROJECT_ROOT / "contracts" / "gymti-questionnaire.v1.json"
    gymti_llm_api_key: SecretStr | None = None
    gymti_llm_model: str = GYMTI_DOUBAO_SEED_MINI_MODEL
    gymti_llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    gymti_llm_enabled: bool = False
    gymti_llm_retention_confirmed: bool = False
    gymti_llm_timeout_seconds: float = Field(default=4.0, gt=0, le=20)
    gymti_llm_temperature: float = Field(default=0.7, ge=0, le=2)
    gymti_llm_max_attempts: int = Field(default=2, ge=1, le=3)
    gymti_llm_concurrency: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def reject_runtime_test_provider(self) -> "Settings":
        if self.analysis_provider == "test" and self.app_env != "test":
            raise ValueError("test analysis provider is allowed only when APP_ENV=test")
        if self.analysis_visual_overlap_seconds >= self.analysis_visual_chunk_seconds:
            raise ValueError("visual chunk overlap must be shorter than the chunk")
        if self.app_env == "production" and self.gymti_llm_enabled:
            gymti_url = urlparse(self.gymti_llm_base_url)
            if (
                gymti_url.scheme != "https"
                or gymti_url.hostname != "ark.cn-beijing.volces.com"
                or gymti_url.path.rstrip("/") != "/api/v3"
                or gymti_url.username is not None
                or gymti_url.password is not None
                or gymti_url.params
                or gymti_url.query
                or gymti_url.fragment
                or self.gymti_llm_model.strip() != GYMTI_DOUBAO_SEED_MINI_MODEL
            ):
                raise ValueError(
                    "production GYMTI must use the approved Ark endpoint and "
                    "Doubao Seed Mini model"
                )
        return self

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        for entry in (item.strip() for item in value.split(",")):
            if entry:
                network = ip_network(entry, strict=False)
                if network.prefixlen == 0:
                    raise ValueError("trusted proxy CIDR must not trust every address")
        return value

    @field_validator(
        "ffmpeg_expected_sha256",
        "ffmpeg_expected_configuration_sha256",
        mode="before",
    )
    @classmethod
    def validate_optional_sha256(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("expected a 64-character SHA-256 digest")
        return normalized

    @field_validator("app_release_sha")
    @classmethod
    def validate_optional_release_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError("release SHA must be a full lowercase Git commit SHA")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_proxy_cidr_list(self) -> list[str]:
        return [entry.strip() for entry in self.trusted_proxy_cidrs.split(",") if entry.strip()]
