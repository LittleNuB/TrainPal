import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Protocol
from urllib.parse import unquote

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from hakimi_analysis.bootstrap import build_catalog, build_http_client, build_pipeline
from hakimi_analysis.models import AnalysisCandidate, EvidenceType, RunStage
from hakimi_analysis.pipeline import AnalysisPipeline, PipelineFailure, PipelineOutput
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.settings import PROJECT_ROOT, Settings
from hakimi_analysis.sources import SourceCatalog, VideoSource


class SmokeManifestError(ValueError):
    """A cloud-smoke annotation manifest failed its safe contract."""


class SmokeFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        source_id: str | None = None,
        checkpoint_index: int | None = None,
        provider_calls: list[dict[str, object]] | None = None,
        observations: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.source_id = source_id
        self.checkpoint_index = checkpoint_index
        self.provider_calls = provider_calls or []
        self.observations = observations or {}


_SAFE_DIAGNOSTIC = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_OBSERVATION_KEYS = {
    "timeout_seconds",
    "controlled_source_count",
    "annotated_source_count",
    "ark_upload_count",
    "ark_delete_count",
    "candidate_count",
    "semantic_match_count",
    "temporal_overlap_count",
    "semantic_temporal_match_count",
    "dual_evidence_count",
    "curl_family_count",
    "dumbbell_curl_count",
    "drag_curl_count",
    "seconds_per_video_minute",
    "target_max_seconds_per_video_minute",
}


def _safe_diagnostic(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if _SAFE_DIAGNOSTIC.fullmatch(text) else "redacted"


def failure_payload(error: SmokeFailure) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "FAIL",
        "error_code": _safe_diagnostic(error.code) or "unexpected_error",
    }
    if error.source_id is not None:
        payload["source_id"] = _safe_diagnostic(error.source_id) or "redacted"
    if error.checkpoint_index is not None:
        payload["checkpoint"] = error.checkpoint_index
    if error.provider_calls:
        payload["provider_diagnostics"] = [
            {
                key: _safe_diagnostic(call.get(key))
                for key in (
                    "provider",
                    "provider_status",
                    "provider_request_id",
                    "provider_error_code",
                )
            }
            for call in error.provider_calls
        ]
    safe_observations = {
        key: value
        for key, value in error.observations.items()
        if key in _SAFE_OBSERVATION_KEYS and isinstance(value, (int, float))
    }
    if safe_observations:
        payload["safe_observations"] = safe_observations
    return payload


class SmokeSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "SmokeSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("expected segment end must be after start")
        return self


class SmokeCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_seconds: float = Field(ge=0)
    accepted_action_names: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    ] = Field(min_length=1)
    expected_segment: SmokeSegment


class SmokeSourceAnnotations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    checkpoints: list[SmokeCheckpoint] = Field(min_length=1)


class SmokeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    sources: list[SmokeSourceAnnotations] = Field(min_length=1)


def load_smoke_manifest(path: Path) -> SmokeManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = SmokeManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise SmokeManifestError("smoke annotation manifest is invalid") from error
    if manifest.version != 1:
        raise SmokeManifestError("smoke annotation manifest version is unsupported")
    source_ids = [source.source_id for source in manifest.sources]
    if len(set(source_ids)) != len(source_ids):
        raise SmokeManifestError("smoke annotation source ids must be unique")
    return manifest


class PipelineBuilder(Protocol):
    def __call__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        *,
        temp_root: Path | None = None,
    ) -> AnalysisPipeline: ...


class EventRecorder:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.events: list[tuple[RunStage, str, dict[str, object], float]] = []

    async def emit(
        self,
        stage: RunStage,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        self.events.append((stage, event_type, data, time.perf_counter()))

    def stage_elapsed_seconds(self, ended: float) -> dict[str, float]:
        transitions: list[tuple[RunStage, float]] = []
        for stage, _event_type, _data, at in self.events:
            if not transitions or transitions[-1][0] != stage:
                transitions.append((stage, at))
        elapsed: dict[str, float] = {}
        for index, (stage, at) in enumerate(transitions):
            next_at = transitions[index + 1][1] if index + 1 < len(transitions) else ended
            elapsed[stage.value] = round(elapsed.get(stage.value, 0) + next_at - at, 3)
        return elapsed

    def completed_branches(self) -> set[str]:
        return {
            str(data.get("branch"))
            for _stage, event_type, data, _at in self.events
            if event_type == "branch.completed"
        }

    def branch_elapsed_seconds(self) -> dict[str, float]:
        started: dict[str, float] = {}
        elapsed: dict[str, float] = {}
        for _stage, event_type, data, at in self.events:
            branch = data.get("branch")
            if not isinstance(branch, str):
                continue
            if event_type == "branch.started":
                started[branch] = at
            elif event_type == "branch.completed" and branch in started:
                elapsed[branch] = round(at - started[branch], 3)
        return elapsed

    def versions(self, settings: Settings) -> dict[str, str]:
        versions = {
            "ark_model": _safe_diagnostic(settings.ark_model_id) or "redacted",
            "ark_visual_model": _safe_diagnostic(settings.ark_visual_model_id) or "redacted",
            "asr_resource": _safe_diagnostic(settings.volc_asr_resource_id) or "redacted",
        }
        for stage, event_type, data, _at in self.events:
            version = data.get("skill_version")
            if not isinstance(version, str):
                continue
            branch = data.get("branch")
            if event_type == "branch.started" and isinstance(branch, str):
                safe_branch = _safe_diagnostic(branch) or "redacted"
                versions[f"{safe_branch}_skill"] = _safe_diagnostic(version) or "redacted"
            elif stage == RunStage.FUSING_CANDIDATES:
                versions["fusion_skill"] = _safe_diagnostic(version) or "redacted"
        return versions


class ProviderAudit:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.ark_uploaded_file_ids: list[str] = []
        self.ark_deleted_file_ids: list[str] = []

    @property
    def ark_uploads(self) -> int:
        return len(self.ark_uploaded_file_ids)

    @property
    def ark_deletes(self) -> int:
        return len(self.ark_deleted_file_ids)

    @property
    def ark_cleanup_matches(self) -> bool:
        return Counter(self.ark_uploaded_file_ids) == Counter(self.ark_deleted_file_ids)

    async def observe(self, response: httpx.Response) -> None:
        await response.aread()
        call = _provider_call(response)
        self.calls.append(call)
        if call["provider"] != "ark" or not response.is_success:
            return
        path = response.request.url.path.rstrip("/")
        if response.request.method == "POST" and path.endswith("/files"):
            try:
                payload = response.json()
                file_id = payload.get("id") if isinstance(payload, dict) else None
            except ValueError:
                file_id = None
            if file_id is not None:
                self.ark_uploaded_file_ids.append(str(file_id))
        elif response.request.method == "DELETE" and "/files/" in path:
            self.ark_deleted_file_ids.append(unquote(path.rsplit("/", 1)[-1]))

    def request_ids(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for call in self.calls:
            provider = call.get("provider")
            request_id = call.get("provider_request_id")
            if not isinstance(provider, str) or not isinstance(request_id, str):
                continue
            safe_provider = _safe_diagnostic(provider) or "redacted"
            safe_request_id = _safe_diagnostic(request_id) or "redacted"
            key = (safe_provider, safe_request_id)
            if key in seen:
                continue
            seen.add(key)
            result.append({"provider": safe_provider, "request_id": safe_request_id})
        return result


def _provider_call(response: httpx.Response) -> dict[str, object]:
    payload: dict[str, Any] = {}
    if not response.is_success:
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            pass
    error_payload = payload.get("error")
    error = error_payload if isinstance(error_payload, dict) else {}
    return {
        "provider": "asr" if "openspeech" in response.request.url.host else "ark",
        "provider_status": response.headers.get("X-Api-Status-Code"),
        "provider_request_id": response.headers.get("X-Tt-Logid")
        or response.headers.get("X-Request-Id"),
        "provider_error_code": error.get("code") or payload.get("code"),
    }


def _temporary_entries(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {entry.name for entry in root.iterdir()}


def _normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _matching_candidate(
    candidates: list[AnalysisCandidate],
    checkpoint: SmokeCheckpoint,
) -> AnalysisCandidate | None:
    return next(
        (
            candidate
            for candidate in candidates
            if _candidate_name_matches(candidate, checkpoint)
            and _candidate_time_overlaps(candidate, checkpoint)
        ),
        None,
    )


def _candidate_name_matches(
    candidate: AnalysisCandidate,
    checkpoint: SmokeCheckpoint,
) -> bool:
    accepted_names = {_normalized_name(name) for name in checkpoint.accepted_action_names}
    normalized = _normalized_name(candidate.name)
    if any(accepted in normalized for accepted in accepted_names if accepted and normalized):
        return True
    drag_curl_aliases = {
        "dragcurl",
        "\u62d6\u62fd\u5f2f\u4e3e",
        "\u62d6\u62fd\u5f0f\u5f2f\u4e3e",
    }
    return bool(accepted_names & drag_curl_aliases) and (
        ("drag" in normalized or "\u62d6\u62fd" in normalized)
        and ("curl" in normalized or "\u5f2f\u4e3e" in normalized)
    )


def _candidate_time_overlaps(
    candidate: AnalysisCandidate,
    checkpoint: SmokeCheckpoint,
) -> bool:
    if candidate.segment is None:
        return False
    expected = checkpoint.expected_segment
    return (
        candidate.segment.start_seconds < expected.end_seconds
        and candidate.segment.end_seconds > expected.start_seconds
    )


def _candidate_diagnostics(
    candidates: list[AnalysisCandidate],
    checkpoint: SmokeCheckpoint,
) -> dict[str, object]:
    name_matches = [
        candidate for candidate in candidates if _candidate_name_matches(candidate, checkpoint)
    ]
    time_matches = [
        candidate for candidate in candidates if _candidate_time_overlaps(candidate, checkpoint)
    ]
    combined_matches = [
        candidate for candidate in name_matches if _candidate_time_overlaps(candidate, checkpoint)
    ]
    dual_evidence = [
        candidate
        for candidate in combined_matches
        if {evidence.type for evidence in candidate.evidence}
        >= {EvidenceType.SPEECH, EvidenceType.VISUAL}
    ]
    normalized_names = [_normalized_name(candidate.name) for candidate in candidates]
    curl_names = [name for name in normalized_names if "curl" in name or "\u5f2f\u4e3e" in name]
    dumbbell_curl_names = [
        name for name in curl_names if "dumbbell" in name or "\u54d1\u94c3" in name
    ]
    drag_curl_names = [name for name in curl_names if "drag" in name or "\u62d6\u62fd" in name]
    return {
        "candidate_count": len(candidates),
        "semantic_match_count": len(name_matches),
        "temporal_overlap_count": len(time_matches),
        "semantic_temporal_match_count": len(combined_matches),
        "dual_evidence_count": len(dual_evidence),
        "curl_family_count": len(curl_names),
        "dumbbell_curl_count": len(dumbbell_curl_names),
        "drag_curl_count": len(drag_curl_names),
    }


def _validate_checkpoint_result(
    output: PipelineOutput,
    checkpoint: SmokeCheckpoint,
    recorder: EventRecorder,
) -> None:
    candidates = output.candidates
    completed_branches = recorder.completed_branches()
    if not completed_branches:
        raise SmokeFailure("no_cloud_branch_completed")
    warning_codes = {warning.code for warning in output.warnings}
    required_warnings = {
        "speech": "speech_unavailable",
        "visual": "visual_unavailable",
    }
    for branch, warning_code in required_warnings.items():
        if branch not in completed_branches and warning_code not in warning_codes:
            raise SmokeFailure(f"{branch}_branch_missing_without_warning")
        if branch in completed_branches and warning_code in warning_codes:
            raise SmokeFailure(f"{branch}_branch_completed_with_unavailable_warning")
    if not any(
        stage == RunStage.FUSING_CANDIDATES for stage, _event_type, _data, _at in recorder.events
    ):
        raise SmokeFailure("fusion_stage_did_not_complete")
    diagnostics = _candidate_diagnostics(candidates, checkpoint)
    matching = _matching_candidate(candidates, checkpoint)
    if matching is None:
        raise SmokeFailure(
            "expected_action_or_time_intersection_missing",
            observations=diagnostics,
        )
    evidence_branches = {evidence.type.value for evidence in matching.evidence}
    if not evidence_branches or not evidence_branches <= completed_branches:
        raise SmokeFailure("candidate_evidence_does_not_match_completed_branches")
    if completed_branches != {"speech", "visual"} and not matching.needs_confirmation:
        raise SmokeFailure("partial_candidate_does_not_require_confirmation")

    if _contains_weight_field([candidate.model_dump(mode="json") for candidate in candidates]):
        raise SmokeFailure("weight_leaked_into_candidate")


def _contains_weight_field(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold().startswith("weight") or _contains_weight_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_weight_field(item) for item in value)
    return False


async def _run_source(
    *,
    settings: Settings,
    source: VideoSource,
    checkpoints: list[SmokeCheckpoint],
    pipeline_builder: PipelineBuilder,
    transport: httpx.AsyncBaseTransport | None,
    temp_root: Path,
) -> dict[str, object]:
    before_entries = _temporary_entries(temp_root)
    recorder = EventRecorder()
    audit = ProviderAudit()
    result = None
    caught: SmokeFailure | None = None
    try:
        async with build_http_client(settings, transport=transport) as http_client:
            http_client.event_hooks["response"].append(audit.observe)
            pipeline = pipeline_builder(settings, http_client, temp_root=temp_root)
            try:
                async with asyncio.timeout(settings.run_timeout_seconds):
                    result = await pipeline.analyze(
                        source,
                        None,
                        recorder.emit,
                    )
            except (PipelineFailure, ProviderError) as error:
                caught = SmokeFailure(error.code)
            except TimeoutError:
                caught = SmokeFailure(
                    "timeout",
                    observations={"timeout_seconds": settings.run_timeout_seconds},
                )
    finally:
        after_entries = _temporary_entries(temp_root)
        if after_entries != before_entries:
            caught = SmokeFailure("local_temp_cleanup_failed")
        elif not audit.ark_cleanup_matches:
            caught = SmokeFailure(
                "ark_temp_cleanup_unverified",
                observations={
                    "ark_upload_count": audit.ark_uploads,
                    "ark_delete_count": audit.ark_deletes,
                },
            )

    if caught is not None:
        caught.source_id = source.id
        caught.provider_calls = audit.calls
        raise caught
    if result is None:
        raise AssertionError("cloud smoke pipeline returned no result")
    for checkpoint_index, checkpoint in enumerate(checkpoints, start=1):
        try:
            _validate_checkpoint_result(result, checkpoint, recorder)
        except SmokeFailure as error:
            error.source_id = source.id
            error.checkpoint_index = checkpoint_index
            error.provider_calls = audit.calls
            raise
    ended = time.perf_counter()
    elapsed_seconds = ended - recorder.started
    seconds_per_video_minute = elapsed_seconds / (source.duration_seconds / 60)
    target_max = settings.analysis_latency_target_max_seconds_per_video_minute
    if seconds_per_video_minute > target_max:
        raise SmokeFailure(
            "latency_target_exceeded",
            source_id=source.id,
            provider_calls=audit.calls,
            observations={
                "seconds_per_video_minute": round(seconds_per_video_minute, 2),
                "target_max_seconds_per_video_minute": target_max,
            },
        )
    completed_branches = recorder.completed_branches()
    return {
        "status": "PASS",
        "checkpoint_count": len(checkpoints),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "seconds_per_video_minute": round(seconds_per_video_minute, 2),
        "performance_target_met": True,
        "target_max_seconds_per_video_minute": target_max,
        "branch_outcomes": {
            branch: "completed" if branch in completed_branches else "unavailable"
            for branch in ("speech", "visual")
        },
        "warning_codes": sorted(warning.code for warning in result.warnings),
        "stage_elapsed_seconds": recorder.stage_elapsed_seconds(ended),
        "branch_elapsed_seconds": recorder.branch_elapsed_seconds(),
        "versions": recorder.versions(settings),
        "provider_request_ids": audit.request_ids(),
    }


async def run_smoke(
    settings: Settings | None = None,
    *,
    catalog: SourceCatalog | None = None,
    pipeline_builder: PipelineBuilder = build_pipeline,
    transport: httpx.AsyncBaseTransport | None = None,
    temp_root: Path | None = None,
) -> dict[str, Any]:
    configured = settings or Settings()
    if configured.analysis_provider != "cloud":
        raise RuntimeError("cloud_provider_required")
    if configured.ark_api_key is None or configured.volc_asr_api_key is None:
        raise RuntimeError("cloud_credentials_missing")
    if configured.smoke_annotations_path is None:
        raise RuntimeError("smoke_annotations_required")

    try:
        manifest = load_smoke_manifest(configured.smoke_annotations_path)
    except SmokeManifestError as error:
        raise RuntimeError("smoke_annotations_invalid") from error
    controlled_catalog = catalog or build_catalog(configured)
    if not controlled_catalog.manifest_backed:
        raise RuntimeError("manifest_backed_sources_required")
    catalog_ids = {summary.id for summary in controlled_catalog.list()}
    annotation_ids = {source.source_id for source in manifest.sources}
    if catalog_ids != annotation_ids:
        raise SmokeFailure(
            "smoke_source_coverage_mismatch",
            observations={
                "controlled_source_count": len(catalog_ids),
                "annotated_source_count": len(annotation_ids),
            },
        )

    root = temp_root or PROJECT_ROOT / "tmp" / "analysis-runs"
    started = time.perf_counter()
    source_results: list[dict[str, object]] = []
    for source_annotations in manifest.sources:
        source = controlled_catalog.get(source_annotations.source_id)
        for checkpoint_index, checkpoint in enumerate(
            source_annotations.checkpoints,
            start=1,
        ):
            if (
                checkpoint.trigger_seconds > source.duration_seconds
                or checkpoint.expected_segment.end_seconds > source.duration_seconds
            ):
                raise SmokeFailure(
                    "smoke_annotation_out_of_source_bounds",
                    source_id=source.id,
                    checkpoint_index=checkpoint_index,
                )
        source_result = await _run_source(
            settings=configured,
            source=source,
            checkpoints=source_annotations.checkpoints,
            pipeline_builder=pipeline_builder,
            transport=transport,
            temp_root=root,
        )
        source_results.append(
            {
                "source_id": source.id,
                **source_result,
            }
        )
    return {
        "status": "PASS",
        "manifest_version": manifest.version,
        "source_count": len(source_results),
        "sources": source_results,
        "total_elapsed_seconds": round(time.perf_counter() - started, 2),
    }


_SAFE_RUNTIME_CODES = {
    "cloud_provider_required",
    "cloud_credentials_missing",
    "smoke_annotations_required",
    "smoke_annotations_invalid",
    "manifest_backed_sources_required",
}


async def async_main() -> int:
    try:
        result = await run_smoke()
    except SmokeFailure as error:
        output = failure_payload(error)
    except RuntimeError as error:
        code = str(error)
        output = {
            "status": "FAIL",
            "error_code": code if code in _SAFE_RUNTIME_CODES else "unexpected_error",
        }
    except Exception:
        output = {"status": "FAIL", "error_code": "unexpected_error"}
    else:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(json.dumps(output, ensure_ascii=False))
    return 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
