import hashlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

from hakimi_analysis.ai_budget import AiBudgetLedger
from hakimi_analysis.gymti import GymtiContractError, load_gymti_contract
from hakimi_analysis.media import probe_duration_sync
from hakimi_analysis.orchestration import SkillRepository
from hakimi_analysis.settings import GYMTI_DOUBAO_SEED_MINI_MODEL, Settings
from hakimi_analysis.sources import COMPETITION_CONTROLLED_SOURCE_COUNT, SourceCatalog

FFMPEG_VERSION_TIMEOUT_SECONDS = 5
FFMPEG_CONFIGURATION_PREFIX = "configuration:"
FORBIDDEN_FFMPEG_CONFIGURATION_FLAGS = frozenset({"--enable-gpl", "--enable-nonfree"})
FFMPEG_RELEASE_VERSION = "8.1.2"
FFMPEG_RELEASE_FINGERPRINT = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
FFMPEG_RELEASE_SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_RELEASE_VERSION}.tar.xz"
FFMPEG_RECEIPT_KEYS = {
    "schema_version",
    "version",
    "source_url",
    "signing_key_fingerprint",
    "binary_sha256",
    "configuration_line",
    "configuration_sha256",
}


def validate_ffmpeg_runtime(
    executable: Path,
    expected_sha256: str,
    expected_configuration_sha256: str,
    *,
    expected_version: str | None = None,
) -> bool:
    try:
        resolved = executable.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return False
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            return False
        completed = subprocess.run(
            [str(resolved), "-version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=FFMPEG_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("ffmpeg version "):
        return False
    version_tokens = lines[0].split()
    if expected_version is not None and (
        len(version_tokens) < 3 or version_tokens[2] != expected_version
    ):
        return False
    configuration_line = next(
        (line for line in lines if line.startswith(FFMPEG_CONFIGURATION_PREFIX)),
        None,
    )
    if configuration_line is None:
        return False
    configuration_flags = set(configuration_line.removeprefix(FFMPEG_CONFIGURATION_PREFIX).split())
    if configuration_flags & FORBIDDEN_FFMPEG_CONFIGURATION_FLAGS:
        return False
    return (
        hashlib.sha256(configuration_line.encode("utf-8")).hexdigest()
        == expected_configuration_sha256
    )


def validate_ffmpeg_build_receipt(executable: Path, receipt_path: Path) -> bool:
    try:
        resolved_executable = executable.resolve(strict=True)
        resolved_receipt = receipt_path.resolve(strict=True)
        if resolved_executable != resolved_receipt.parent / "bin" / "ffmpeg":
            return False
        payload = json.loads(resolved_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or set(payload) != FFMPEG_RECEIPT_KEYS:
        return False
    if (
        payload.get("schema_version") != 1
        or payload.get("version") != FFMPEG_RELEASE_VERSION
        or payload.get("source_url") != FFMPEG_RELEASE_SOURCE_URL
        or payload.get("signing_key_fingerprint") != FFMPEG_RELEASE_FINGERPRINT
    ):
        return False
    binary_sha256 = payload.get("binary_sha256")
    configuration_line = payload.get("configuration_line")
    configuration_sha256 = payload.get("configuration_sha256")
    if not all(
        isinstance(value, str)
        for value in (binary_sha256, configuration_line, configuration_sha256)
    ):
        return False
    if not str(configuration_line).startswith(FFMPEG_CONFIGURATION_PREFIX):
        return False
    if hashlib.sha256(str(configuration_line).encode("utf-8")).hexdigest() != str(
        configuration_sha256
    ):
        return False
    return validate_ffmpeg_runtime(
        resolved_executable,
        str(binary_sha256),
        str(configuration_sha256),
        expected_version=FFMPEG_RELEASE_VERSION,
    )


class ReadinessProbe(Protocol):
    def check(self) -> str | None: ...


class ProductionReadiness:
    def __init__(
        self,
        *,
        settings: Settings,
        catalog: SourceCatalog,
        temp_root: Path,
        skills_root: Path,
        duration_probe: Callable[[Path], float] | None = None,
        ffmpeg_receipt_probe: Callable[[Path, Path], bool] | None = None,
        cache_seconds: float = 5,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._temp_root = temp_root
        self._skills_root = skills_root
        self._duration_probe = duration_probe or probe_duration_sync
        self._ffmpeg_receipt_probe = ffmpeg_receipt_probe or validate_ffmpeg_build_receipt
        self._cache_seconds = cache_seconds
        self._cache_lock = Lock()
        self._cached_at: float | None = None
        self._cached_result: str | None = None

    def check(self) -> str | None:
        with self._cache_lock:
            now = monotonic()
            if self._cached_at is not None and now - self._cached_at < self._cache_seconds:
                return self._cached_result
            result = self._check_uncached()
            self._cached_at = now
            self._cached_result = result
            return result

    def _check_uncached(self) -> str | None:
        if (
            self._settings.app_env != "production"
            or self._settings.analysis_provider != "cloud"
            or self._settings.ark_api_key is None
            or self._settings.volc_asr_api_key is None
            or self._settings.gymti_llm_api_key is None
            or not self._settings.ark_api_key.get_secret_value().strip()
            or not self._settings.volc_asr_api_key.get_secret_value().strip()
            or not self._settings.gymti_llm_api_key.get_secret_value().strip()
            or not self._settings.ark_model_id.strip()
            or not self._settings.ark_base_url.strip()
            or not self._settings.volc_asr_resource_id.strip()
            or not self._settings.volc_asr_url.strip()
            or not self._settings.gymti_llm_enabled
            or not self._settings.gymti_llm_retention_confirmed
            or self._settings.gymti_llm_model.strip() != GYMTI_DOUBAO_SEED_MINI_MODEL
            or not self._settings.gymti_llm_base_url.strip()
        ):
            return "provider_configuration_invalid"
        ark_url = urlparse(self._settings.ark_base_url)
        asr_url = urlparse(self._settings.volc_asr_url)
        gymti_url = urlparse(self._settings.gymti_llm_base_url)
        if ark_url.scheme != "https" or not ark_url.netloc:
            return "provider_configuration_invalid"
        if asr_url.scheme != "wss" or not asr_url.netloc:
            return "provider_configuration_invalid"
        if (
            gymti_url.scheme != "https"
            or gymti_url.hostname != "ark.cn-beijing.volces.com"
            or gymti_url.path.rstrip("/") != "/api/v3"
            or gymti_url.username is not None
            or gymti_url.password is not None
            or gymti_url.params
            or gymti_url.query
            or gymti_url.fragment
        ):
            return "provider_configuration_invalid"
        try:
            load_gymti_contract(self._settings.gymti_contract_path)
        except GymtiContractError:
            return "gymti_contract_invalid"
        controlled_source_values = (
            self._settings.source_manifest_path,
            self._settings.source_media_root,
            self._settings.public_media_base_url,
        )
        if (
            any(value is None for value in controlled_source_values)
            or self._settings.source_manifest_path is None
            or not self._settings.source_manifest_path.is_file()
            or not self._catalog.manifest_backed
            or self._catalog.source_count != COMPETITION_CONTROLLED_SOURCE_COUNT
        ):
            return "source_manifest_invalid"
        parsed_media_url = urlparse(self._settings.public_media_base_url)
        if parsed_media_url.scheme != "https" or not parsed_media_url.netloc:
            return "source_manifest_invalid"
        ffmpeg_executable = self._settings.imageio_ffmpeg_exe
        ffmpeg_receipt = self._settings.ffmpeg_build_receipt_path
        receipt_valid = (
            ffmpeg_executable is not None
            and ffmpeg_receipt is not None
            and self._ffmpeg_receipt_probe(ffmpeg_executable, ffmpeg_receipt)
        )
        if not receipt_valid:
            return "media_processor_unavailable"
        if not self._catalog.validate_media(self._duration_probe):
            return "media_cache_invalid"
        try:
            SkillRepository.load(self._skills_root)
        except (OSError, ValueError):
            return "skill_configuration_invalid"
        if (
            self._settings.judge_access_code is None
            or self._settings.access_cookie_secret is None
            or len(self._settings.judge_access_code.get_secret_value().encode("utf-8")) < 16
            or len(self._settings.access_cookie_secret.get_secret_value().encode("utf-8")) < 32
        ):
            return "access_configuration_invalid"
        web_static_root = self._settings.web_static_root
        if (
            web_static_root is None
            or not web_static_root.is_dir()
            or not (web_static_root / "index.html").is_file()
        ):
            return "web_static_unavailable"
        if not self._temp_storage_available():
            return "temp_storage_unavailable"
        return self._deployment_configuration_failure()

    def _deployment_configuration_failure(self) -> str | None:
        return None if self._competition_profile_valid() else "competition_configuration_invalid"

    def _competition_profile_valid(self) -> bool:
        return (
            self._settings.local_upload_enabled
            and self._settings.local_analysis_max_seconds == 300
            and self._settings.local_upload_max_bytes == 256 * 1024 * 1024
            and self._settings.analysis_evidence_timeout_seconds == 170
            and self._settings.analysis_chunk_timeout_seconds == 20
            and self._settings.analysis_visual_chunk_seconds == 60
            and self._settings.analysis_visual_overlap_seconds == 10
            and self._settings.run_timeout_seconds == 180
            and self._settings.judge_analysis_concurrency == 0
            and self._settings.public_analysis_concurrency == 1
            and self._settings.gymti_llm_concurrency == 3
            and not self._settings.trusted_proxy_cidr_list
        )

    def _temp_storage_available(self) -> bool:
        probe_path = self._temp_root / f".ready-{uuid4().hex}"
        try:
            self._temp_root.mkdir(parents=True, exist_ok=True)
            probe_path.write_bytes(b"ready")
            if probe_path.read_bytes() != b"ready":
                return False
            probe_path.unlink()
            return True
        except OSError:
            return False
        finally:
            with suppress(OSError):
                probe_path.unlink(missing_ok=True)


class StaticReadiness:
    def __init__(self, failure_code: str | None) -> None:
        self._failure_code = failure_code

    def check(self) -> str | None:
        return self._failure_code


class PortfolioReadiness(ProductionReadiness):
    """Separate personal-site deployment; competition checks remain unchanged."""

    def _deployment_configuration_failure(self) -> str | None:
        settings = self._settings
        if (
            settings.app_env != "production"
            or settings.analysis_provider != "cloud"
            or settings.ark_model_id != "doubao-seed-2-1-pro-260915"
            or settings.ark_visual_model_id != "doubao-seed-2-1-pro-260628"
            or settings.ark_fusion_model_id not in (None, "doubao-seed-2-1-pro-260915")
            or settings.ark_analysis_response_format != "json_object"
            or settings.ark_base_url.rstrip("/") != "https://ark.cn-beijing.volces.com/api/v3"
            or settings.volc_asr_resource_id != "volc.seedasr.sauc.duration"
            or settings.volc_asr_url
            != "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
            or not settings.training_reminders_enabled
            or settings.ai_budget_db_path is None
            or settings.public_base_path != "/trainpal"
            or settings.local_upload_max_bytes != 64 * 1024 * 1024
            or settings.local_analysis_max_seconds != 300
            or settings.public_analysis_concurrency != 1
            or settings.judge_analysis_concurrency != 0
            or settings.run_timeout_seconds != 600
            or settings.analysis_evidence_timeout_seconds != 590
            or settings.analysis_chunk_timeout_seconds != 90
            or settings.analysis_visual_short_context_seconds != 90
            or settings.trusted_proxy_cidr_list != ["127.0.0.1/32"]
        ):
            return "portfolio_configuration_invalid"
        budget_path = settings.ai_budget_db_path.resolve()
        if budget_path.is_relative_to(self._temp_root.resolve()) or (
            settings.web_static_root is not None
            and budget_path.is_relative_to(settings.web_static_root.resolve())
        ):
            return "portfolio_configuration_invalid"
        try:
            AiBudgetLedger(settings.ai_budget_db_path).snapshot()
        except (OSError, ValueError, sqlite3.Error):
            return "ai_budget_unavailable"
        return None
