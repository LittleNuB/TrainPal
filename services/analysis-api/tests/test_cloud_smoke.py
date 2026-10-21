import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, Unpack

import httpx
import pytest
from pydantic import SecretStr

from hakimi_analysis.ai_budget import AiBudgetLedger
from hakimi_analysis.cloud_smoke import (
    SmokeFailure,
    SmokeManifestError,
    _run_source,
    failure_payload,
    load_smoke_manifest,
    run_smoke,
)
from hakimi_analysis.models import (
    AnalysisCandidate,
    AnalysisWarning,
    CandidateParameters,
    EvidenceSpan,
    EvidenceType,
    RunStage,
    Segment,
)
from hakimi_analysis.pipeline import EmitCallback, PipelineFailure, PipelineOutput
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import SourceCatalog, VideoSource


class SuccessfulPipelineOptions(TypedDict, total=False):
    delete_upload: bool
    evidence_types: tuple[EvidenceType, ...]
    candidate_name: str
    candidate_segment: tuple[float, float] | None
    leave_temp_file: bool
    fail_before_upload: bool
    delete_file_id_override: str | None
    missing_branch: str | None
    declare_missing_warning: bool
    delay_seconds: float


def write_smoke_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "smoke-annotations.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def valid_manifest() -> dict[str, Any]:
    return {
        "version": 1,
        "sources": [
            {
                "source_id": "arm-workout-01",
                "checkpoints": [
                    {
                        "trigger_seconds": 45,
                        "accepted_action_names": ["Drag Curl", "拖拽弯举"],
                        "expected_segment": {
                            "start_seconds": 41,
                            "end_seconds": 51,
                        },
                    }
                ],
            }
        ],
    }


def test_smoke_manifest_loads_versioned_sanitized_source_annotations(
    tmp_path: Path,
) -> None:
    manifest = load_smoke_manifest(write_smoke_manifest(tmp_path, valid_manifest()))

    assert manifest.version == 1
    assert manifest.sources[0].source_id == "arm-workout-01"
    assert manifest.sources[0].checkpoints[0].accepted_action_names == [
        "Drag Curl",
        "拖拽弯举",
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(version=2),
        lambda payload: payload["sources"].append(payload["sources"][0]),
        lambda payload: payload["sources"][0].update(transcript="private"),
    ],
)
def test_smoke_manifest_rejects_unsupported_duplicate_or_content_fields(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    payload = valid_manifest()
    mutate(payload)

    with pytest.raises(SmokeManifestError):
        load_smoke_manifest(write_smoke_manifest(tmp_path, payload))


def test_smoke_manifest_rejects_blank_semantic_aliases(tmp_path: Path) -> None:
    payload = valid_manifest()
    sources = payload["sources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    checkpoints = source["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoint = checkpoints[0]
    assert isinstance(checkpoint, dict)
    checkpoint["accepted_action_names"] = ["  "]

    with pytest.raises(SmokeManifestError):
        load_smoke_manifest(write_smoke_manifest(tmp_path, payload))


def test_smoke_failure_payload_allows_only_sanitized_diagnostics() -> None:
    payload = failure_payload(
        SmokeFailure(
            "expected_action_missing",
            source_id="arm-workout-01",
            checkpoint_index=2,
            provider_calls=[
                {
                    "provider": "ark",
                    "provider_status": "500",
                    "provider_request_id": "request-safe-123",
                    "provider_error_code": "bad\nraw provider body",
                    "raw_response": "private model response",
                }
            ],
            observations={
                "ark_upload_count": 1,
                "candidate_count": 2,
                "candidate_name": "private candidate",
            },
        )
    )

    assert payload == {
        "status": "FAIL",
        "error_code": "expected_action_missing",
        "source_id": "arm-workout-01",
        "checkpoint": 2,
        "provider_diagnostics": [
            {
                "provider": "ark",
                "provider_status": "500",
                "provider_request_id": "request-safe-123",
                "provider_error_code": "redacted",
            }
        ],
        "safe_observations": {"ark_upload_count": 1, "candidate_count": 2},
    }


class SuccessfulCloudPipeline:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        calls: list[str],
        *,
        delete_upload: bool = True,
        evidence_types: tuple[EvidenceType, ...] = (
            EvidenceType.SPEECH,
            EvidenceType.VISUAL,
        ),
        candidate_name: str = "Cable Drag Curl",
        candidate_segment: tuple[float, float] | None = None,
        temp_root: Path | None = None,
        leave_temp_file: bool = False,
        fail_before_upload: bool = False,
        missing_branch: str | None = None,
        declare_missing_warning: bool = True,
        delete_file_id_override: str | None = None,
        delay_seconds: float = 0,
    ) -> None:
        self._http_client = http_client
        self._calls = calls
        self._delete_upload = delete_upload
        self._evidence_types = evidence_types
        self._candidate_name = candidate_name
        self._candidate_segment = candidate_segment
        self._temp_root = temp_root
        self._leave_temp_file = leave_temp_file
        self._fail_before_upload = fail_before_upload
        self._delete_file_id_override = delete_file_id_override
        self._missing_branch = missing_branch
        self._declare_missing_warning = declare_missing_warning

        self._delay_seconds = delay_seconds

    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float | None,
        emit: EmitCallback,
    ) -> PipelineOutput:
        self._calls.append(source.id)
        await emit(RunStage.PREPARING_MEDIA, "stage.changed", {})
        await asyncio.sleep(self._delay_seconds)
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "speech", "skill_version": "1.3.0"},
        )
        await emit(
            RunStage.ANALYZING_EVIDENCE,
            "branch.started",
            {"branch": "visual", "skill_version": "1.3.0"},
        )
        if self._fail_before_upload:
            raise PipelineFailure("provider_error", "private provider detail", retryable=True)
        upload = await self._http_client.post("https://ark.example/api/v3/files")
        if self._delete_upload:
            file_id = self._delete_file_id_override or str(upload.json()["id"])
            await self._http_client.delete(f"https://ark.example/api/v3/files/{file_id}")
        if self._missing_branch != "speech":
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "branch.completed",
                {"branch": "speech", "evidence_count": 1},
            )
        if self._missing_branch != "visual":
            await emit(
                RunStage.ANALYZING_EVIDENCE,
                "branch.completed",
                {"branch": "visual", "evidence_count": 1},
            )
        await emit(
            RunStage.FUSING_CANDIDATES,
            "stage.changed",
            {"skill_version": "1.4.0"},
        )
        legacy_trigger = (
            trigger_seconds
            if trigger_seconds is not None
            else (45 if source.id.endswith("01") else 30)
        )
        start, end = self._candidate_segment or (legacy_trigger - 4, legacy_trigger + 6)
        if self._leave_temp_file and self._temp_root is not None:
            self._temp_root.mkdir(parents=True, exist_ok=True)
            (self._temp_root / "leftover").mkdir()
        warnings: list[AnalysisWarning] = []
        if self._declare_missing_warning:
            if self._missing_branch == "speech":
                warnings.append(
                    AnalysisWarning(code="speech_unavailable", message="speech unavailable")
                )
            elif self._missing_branch == "visual":
                warnings.append(
                    AnalysisWarning(code="visual_unavailable", message="visual unavailable")
                )

        return PipelineOutput(
            candidates=[
                AnalysisCandidate(
                    id=f"candidate-{source.id}",
                    name=self._candidate_name if source.id.endswith("01") else "平板支撑",
                    source_id=source.id,
                    segment=Segment(start_seconds=start, end_seconds=end),
                    parameters=CandidateParameters(),
                    evidence=[
                        EvidenceSpan(
                            type=evidence_type,
                            start_seconds=start,
                            end_seconds=end,
                        )
                        for evidence_type in self._evidence_types
                    ],
                    needs_confirmation=self._missing_branch is not None,
                )
            ],
            warnings=warnings,
        )


@pytest.mark.asyncio
async def test_cloud_smoke_runs_every_annotated_controlled_source_without_content_output(
    tmp_path: Path,
) -> None:
    annotations = valid_manifest()
    annotations["sources"][0]["checkpoints"].append(
        {
            "trigger_seconds": 46,
            "accepted_action_names": ["Drag Curl", "拖拽弯举"],
            "expected_segment": {"start_seconds": 41, "end_seconds": 51},
        }
    )
    annotations["sources"].append(
        {
            "source_id": "core-workout-02",
            "checkpoints": [
                {
                    "trigger_seconds": 30,
                    "accepted_action_names": ["Plank", "平板支撑"],
                    "expected_segment": {"start_seconds": 25, "end_seconds": 36},
                }
            ],
        }
    )
    annotation_path = write_smoke_manifest(tmp_path, annotations)
    catalog = SourceCatalog(
        [
            VideoSource("arm-workout-01", "Arm", tmp_path / "arm.mp4", 54),
            VideoSource("core-workout-02", "Core", tmp_path / "core.mp4", 60),
        ],
        manifest_backed=True,
    )
    pipeline_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"id": f"file-{len(pipeline_calls)}"},
                headers={"X-Request-Id": "unsafe private request id"},
            )
        return httpx.Response(
            200,
            json={"deleted": True},
            headers={"X-Request-Id": f"ark-delete-{len(pipeline_calls)}"},
        )

    def pipeline_builder(
        settings: Settings,
        http_client: httpx.AsyncClient,
        *,
        temp_root: Path | None = None,
    ) -> SuccessfulCloudPipeline:
        del settings
        assert temp_root is not None
        return SuccessfulCloudPipeline(http_client, pipeline_calls)

    settings = Settings(
        _env_file=None,
        analysis_provider="cloud",
        ark_api_key=SecretStr("test-only-ark"),
        volc_asr_api_key=SecretStr("test-only-asr"),
        smoke_annotations_path=annotation_path,
        run_timeout_seconds=2,
    )
    result = await run_smoke(
        settings,
        catalog=catalog,
        pipeline_builder=pipeline_builder,
        transport=httpx.MockTransport(handler),
        temp_root=tmp_path / "analysis-runs",
    )

    assert pipeline_calls == ["arm-workout-01", "core-workout-02"]
    assert result["status"] == "PASS"
    assert [source["source_id"] for source in result["sources"]] == [
        "arm-workout-01",
        "core-workout-02",
    ]
    assert all("seconds_per_video_minute" in source for source in result["sources"])
    serialized = json.dumps(result, ensure_ascii=False)
    assert all(source["performance_target_met"] for source in result["sources"])
    assert all(source["warning_codes"] == [] for source in result["sources"])
    assert "拖拽弯举" not in serialized
    assert "平板支撑" not in serialized
    assert "test-only" not in serialized
    assert "unsafe private request id" not in serialized
    assert "redacted" in serialized


async def run_single_source_fixture(
    tmp_path: Path,
    *,
    target_max_seconds_per_video_minute: float = 15.0,
    **pipeline_options: Unpack[SuccessfulPipelineOptions],
) -> dict[str, object]:
    annotation_path = write_smoke_manifest(tmp_path, valid_manifest())
    catalog = SourceCatalog(
        [VideoSource("arm-workout-01", "Arm", tmp_path / "arm.mp4", 54)],
        manifest_backed=True,
    )
    pipeline_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "file-1"})
        return httpx.Response(200, json={"deleted": True})

    def pipeline_builder(
        settings: Settings,
        http_client: httpx.AsyncClient,
        *,
        temp_root: Path | None = None,
    ) -> SuccessfulCloudPipeline:
        del settings
        return SuccessfulCloudPipeline(
            http_client,
            pipeline_calls,
            temp_root=temp_root,
            **pipeline_options,
        )

    settings = Settings(
        _env_file=None,
        analysis_provider="cloud",
        ark_api_key=SecretStr("test-only-ark"),
        volc_asr_api_key=SecretStr("test-only-asr"),
        smoke_annotations_path=annotation_path,
        run_timeout_seconds=2,
        analysis_latency_target_max_seconds_per_video_minute=target_max_seconds_per_video_minute,
    )
    return await run_smoke(
        settings,
        catalog=catalog,
        pipeline_builder=pipeline_builder,
        transport=httpx.MockTransport(handler),
        temp_root=tmp_path / "analysis-runs",
    )


@pytest.mark.asyncio
async def test_cloud_smoke_accepts_tokenized_drag_curl_alias(tmp_path: Path) -> None:
    result = await run_single_source_fixture(
        tmp_path,
        candidate_name="Drag Dumbbell Curl",
    )
    assert result["status"] == "PASS"


@pytest.mark.asyncio
async def test_cloud_smoke_accepts_declared_single_branch_success(tmp_path: Path) -> None:
    result = await run_single_source_fixture(
        tmp_path,
        missing_branch="speech",
        evidence_types=(EvidenceType.VISUAL,),
    )
    sources = result["sources"]
    assert isinstance(sources, list)
    source_result = sources[0]
    assert isinstance(source_result, dict)
    assert source_result["branch_outcomes"] == {
        "speech": "unavailable",
        "visual": "completed",
    }
    assert source_result["warning_codes"] == ["speech_unavailable"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pipeline_options", "expected_code"),
    [
        (
            {
                "missing_branch": "speech",
                "declare_missing_warning": False,
                "evidence_types": (EvidenceType.VISUAL,),
            },
            "speech_branch_missing_without_warning",
        ),
        (
            {
                "missing_branch": "speech",
                "evidence_types": (EvidenceType.SPEECH,),
            },
            "candidate_evidence_does_not_match_completed_branches",
        ),
    ],
)
async def test_cloud_smoke_rejects_inconsistent_partial_success(
    tmp_path: Path,
    pipeline_options: SuccessfulPipelineOptions,
    expected_code: str,
) -> None:
    with pytest.raises(SmokeFailure) as failure:
        await run_single_source_fixture(tmp_path, **pipeline_options)

    assert failure.value.code == expected_code


@pytest.mark.asyncio
async def test_cloud_smoke_rejects_latency_above_target(tmp_path: Path) -> None:
    with pytest.raises(SmokeFailure) as failure:
        await run_single_source_fixture(
            tmp_path,
            target_max_seconds_per_video_minute=0.001,
            delay_seconds=0.03,
        )

    assert failure.value.code == "latency_target_exceeded"
    payload = failure_payload(failure.value)
    safe_observations = payload["safe_observations"]
    assert isinstance(safe_observations, dict)
    assert safe_observations["target_max_seconds_per_video_minute"] == 0.001
    assert safe_observations["seconds_per_video_minute"] > 0.001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pipeline_options", "expected_code"),
    [
        ({"candidate_name": "Unrelated Action"}, "expected_action_or_time_intersection_missing"),
        ({"candidate_name": "Curl"}, "expected_action_or_time_intersection_missing"),
        ({"candidate_name": "弯举"}, "expected_action_or_time_intersection_missing"),
        (
            {"candidate_segment": (1, 10)},
            "expected_action_or_time_intersection_missing",
        ),
    ],
)
async def test_cloud_smoke_rejects_wrong_semantics_or_time(
    tmp_path: Path,
    pipeline_options: SuccessfulPipelineOptions,
    expected_code: str,
) -> None:
    with pytest.raises(SmokeFailure) as failure:
        await run_single_source_fixture(tmp_path, **pipeline_options)

    assert failure.value.code == expected_code
    assert failure.value.source_id == "arm-workout-01"
    assert failure.value.observations["candidate_count"] == 1


@pytest.mark.asyncio
async def test_cloud_smoke_reports_only_safe_aggregate_mismatch_counts(
    tmp_path: Path,
) -> None:
    with pytest.raises(SmokeFailure) as semantic_failure:
        await run_single_source_fixture(tmp_path, candidate_name="Unrelated Action")
    with pytest.raises(SmokeFailure) as temporal_failure:
        await run_single_source_fixture(tmp_path, candidate_segment=(1, 10))

    assert semantic_failure.value.observations == {
        "candidate_count": 1,
        "semantic_match_count": 0,
        "temporal_overlap_count": 1,
        "semantic_temporal_match_count": 0,
        "dual_evidence_count": 0,
        "curl_family_count": 0,
        "dumbbell_curl_count": 0,
        "drag_curl_count": 0,
    }
    assert temporal_failure.value.observations == {
        "candidate_count": 1,
        "semantic_match_count": 1,
        "temporal_overlap_count": 0,
        "semantic_temporal_match_count": 0,
        "dual_evidence_count": 0,
        "curl_family_count": 1,
        "dumbbell_curl_count": 0,
        "drag_curl_count": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pipeline_options", "expected_code"),
    [
        ({"delete_upload": False}, "ark_temp_cleanup_unverified"),
        ({"delete_file_id_override": "wrong-file"}, "ark_temp_cleanup_unverified"),
        ({"leave_temp_file": True}, "local_temp_cleanup_failed"),
    ],
)
async def test_cloud_smoke_requires_provider_and_local_temp_cleanup(
    tmp_path: Path,
    pipeline_options: SuccessfulPipelineOptions,
    expected_code: str,
) -> None:
    with pytest.raises(SmokeFailure) as failure:
        await run_single_source_fixture(tmp_path, **pipeline_options)

    assert failure.value.code == expected_code


@pytest.mark.asyncio
async def test_cloud_smoke_preserves_safe_provider_failure_before_any_upload(
    tmp_path: Path,
) -> None:
    with pytest.raises(SmokeFailure) as failure:
        await run_single_source_fixture(tmp_path, fail_before_upload=True)

    assert failure.value.code == "provider_error"


@pytest.mark.asyncio
async def test_cloud_smoke_requires_annotations_for_every_controlled_source(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        analysis_provider="cloud",
        ark_api_key=SecretStr("test-only-ark"),
        volc_asr_api_key=SecretStr("test-only-asr"),
        smoke_annotations_path=write_smoke_manifest(tmp_path, valid_manifest()),
    )
    catalog = SourceCatalog(
        [
            VideoSource("arm-workout-01", "Arm", tmp_path / "arm.mp4", 54),
            VideoSource("missing-annotation-02", "Core", tmp_path / "core.mp4", 60),
        ],
        manifest_backed=True,
    )

    with pytest.raises(SmokeFailure) as failure:
        await run_smoke(settings, catalog=catalog)

    assert failure.value.code == "smoke_source_coverage_mismatch"
    assert failure_payload(failure.value)["safe_observations"] == {
        "controlled_source_count": 2,
        "annotated_source_count": 1,
    }


@pytest.mark.asyncio
async def test_cloud_smoke_does_not_bypass_exhausted_model_budget(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite"
    ledger = AiBudgetLedger(path)
    ledger.reserve(5_000_000, kind="fixture", model="fixture")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    class PaidPipeline:
        def __init__(self, client: httpx.AsyncClient) -> None:
            self.client = client

        async def analyze(
            self, source: VideoSource, trigger_seconds: float | None, emit: EmitCallback
        ) -> PipelineOutput:
            await self.client.post(
                "https://ark.cn-beijing.volces.com/api/v3/responses",
                json={"model": "doubao-seed-2-1-pro-260915", "input": "fixture"},
            )
            return PipelineOutput()

    def builder(
        settings: Settings, http_client: httpx.AsyncClient, *, temp_root: Path | None = None
    ) -> PaidPipeline:
        return PaidPipeline(http_client)

    with pytest.raises(SmokeFailure) as error:
        await _run_source(
            settings=Settings(_env_file=None, ai_budget_db_path=path),
            source=VideoSource(
                id="fixture", title="fixture", path=tmp_path / "fixture.mp4", duration_seconds=60
            ),
            checkpoints=[],
            pipeline_builder=builder,
            transport=httpx.MockTransport(handler),
            temp_root=tmp_path / "runs",
        )
    assert error.value.code == "ai_budget_exhausted"
    assert requests == []
    assert ledger.snapshot()["used_micro_yuan"] == 5_000_000
