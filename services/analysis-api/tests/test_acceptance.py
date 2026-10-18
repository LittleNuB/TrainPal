"""The isolated acceptance app, with only external Provider HTTP replaced."""

import json
import subprocess
from pathlib import Path

import httpx
import imageio_ffmpeg
import pytest
from asgi_lifespan import LifespanManager
from websockets.asyncio.server import ServerConnection, serve

from hakimi_analysis.acceptance import build_acceptance_app, configure_acceptance_environment
from hakimi_analysis.settings import Settings

VECTOR = json.loads(
    (
        Path(__file__).resolve().parents[3] / "contracts/fixtures/training-reminders-v1.1.json"
    ).read_text(encoding="utf-8")
)[0]


@pytest.fixture
def built_web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<div id="app">synthetic-web</div>', encoding="utf-8")
    return root


async def test_acceptance_serves_the_plan_skill_only_after_an_explicit_request(
    built_web_root: Path,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": VECTOR["expected_fingerprint"],
                        "rankings": [
                            {"action_id": "synthetic-action", "tip_ids": ["synthetic-tip"]}
                        ],
                    }
                )
            },
        )

    settings = Settings(
        _env_file=None,
        app_env="development",
        analysis_provider="cloud",
        ark_api_key="synthetic",
        ark_base_url="https://provider.invalid",
        ark_model_id="existing-text-model",
        web_static_root=built_web_root,
    )
    app = build_acceptance_app(settings=settings, provider_transport=httpx.MockTransport(respond))
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:15177"
        ) as client,
    ):
        capabilities = await client.get("/api/v1/capabilities")
        assert capabilities.json()["local_analysis_max_seconds"] == 300
        assert requests == []
        response = await client.post(
            "/api/v1/training-reminders",
            json=VECTOR["request"],
            headers={"Origin": "http://127.0.0.1:15177"},
        )
    assert response.status_code == 200
    assert response.json()["mode"] == "model"
    assert response.json()["input_fingerprint"] == VECTOR["expected_fingerprint"]
    assert response.json()["reminders"][0]["tip_id"] == "synthetic-tip"
    assert len(requests) == 1
    assert requests[0].url == "https://provider.invalid/responses"
    assert json.loads(requests[0].content)["model"] == "existing-text-model"


@pytest.mark.parametrize(
    ("app_env", "analysis_provider"),
    [("production", "cloud"), ("test", "cloud"), ("test", "test")],
)
def test_acceptance_refuses_production_and_synthetic_analysis_profiles(
    app_env: str, analysis_provider: str
) -> None:
    settings = Settings(_env_file=None, app_env=app_env, analysis_provider=analysis_provider)
    with pytest.raises(ValueError, match=r"development.*cloud"):
        build_acceptance_app(settings=settings)


async def test_acceptance_keeps_the_web_and_api_on_one_origin_without_provider_calls(
    tmp_path: Path,
) -> None:
    web_root = tmp_path / "dist"
    (web_root / "assets").mkdir(parents=True)
    (web_root / "index.html").write_text('<div id="app">synthetic-web</div>', encoding="utf-8")
    (web_root / "assets" / "app.js").write_text("// synthetic asset", encoding="utf-8")

    def unexpected_provider(request: httpx.Request) -> httpx.Response:
        raise AssertionError("opening the acceptance app must not call a Provider")

    app = build_acceptance_app(
        settings=Settings(_env_file=None, web_static_root=web_root, app_release_sha="a" * 40),
        provider_transport=httpx.MockTransport(unexpected_provider),
    )
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:15177"
        ) as client,
    ):
        home = await client.get("/")
        assert home.status_code == 200
        assert "synthetic-web" in home.text
        assert (await client.get("/train")).text == home.text
        assert (await client.get("/assets/app.js")).text == "// synthetic asset"
        assert (await client.get("/api/v1/health")).json() == {
            "status": "ok",
            "release_sha": "a" * 40,
        }
        assert (await client.get("/api/v1/not-real")).status_code == 404
        assert (await client.get("/assets/not-real.js")).status_code == 404
        blocked = await client.post(
            "/api/v1/training-reminders",
            json=VECTOR["request"],
            headers={"Origin": "http://localhost:5173"},
        )
        assert blocked.status_code == 403
        rules = await client.post("/api/v1/training-reminders", json=VECTOR["request"])
        assert rules.json()["fallback_reason"] == "model_unavailable"


@pytest.mark.parametrize(
    ("ark_key", "asr_key"),
    [
        (None, None),
        ("", ""),
        (" \t", " \n"),
        ("synthetic", ""),
        ("synthetic", " \t"),
        ("", "synthetic"),
        (" \t", "synthetic"),
    ],
)
async def test_acceptance_reports_missing_analysis_credentials_and_releases_the_upload(
    tmp_path: Path,
    built_web_root: Path,
    ark_key: str | None,
    asr_key: str | None,
) -> None:
    video = tmp_path / "synthetic.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:r=1",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            "-an",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    requests: list[httpx.Request] = []

    def unexpected_provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("missing credentials must not call a Provider")

    app = build_acceptance_app(
        settings=Settings(
            _env_file=None,
            ark_api_key=ark_key,
            ark_base_url="https://provider.invalid",
            volc_asr_api_key=asr_key,
            volc_asr_url="ws://127.0.0.1:1",
            web_static_root=built_web_root,
        ),
        provider_transport=httpx.MockTransport(unexpected_provider),
    )
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:15177"
        ) as client,
    ):
        for source_id in (
            "local:62f31c4b-bd1c-4b6a-8ad8-c6121b56135a",
            "local:a55df85a-e80d-4c85-807f-cd4a9c26f7e5",
        ):
            created = await client.post(
                "/api/v1/analysis-runs/local",
                data={"local_source_id": source_id},
                files={"media": ("synthetic.mp4", video.read_bytes(), "video/mp4")},
            )
            assert created.status_code == 202
            run_id = created.json()["id"]
            events = await client.get(f"/api/v1/analysis-runs/{run_id}/events")
            assert "configuration_error" in events.text
            failed = (await client.get(f"/api/v1/analysis-runs/{run_id}")).json()
            assert failed["status"] == "failed"
            assert failed["candidates"] == []
            assert failed["error"]["code"] == "configuration_error"
            cleanup = await client.get("/api/v1/runtime-cleanup")
            assert cleanup.status_code == 200
            assert cleanup.json()["clean"] is True
            assert cleanup.json()["residue_count"] == 0
        if ark_key is None or not ark_key.strip():
            rules = await client.post("/api/v1/training-reminders", json=VECTOR["request"])
            assert rules.json()["fallback_reason"] == "model_unavailable"
    assert requests == []


async def test_acceptance_runs_real_media_and_provider_boundaries_without_automatic_retry(
    tmp_path: Path,
    built_web_root: Path,
) -> None:
    video = tmp_path / "synthetic-audio.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:r=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    visual_requests: list[httpx.Request] = []
    speech_connections: list[None] = []

    def unavailable_visual(request: httpx.Request) -> httpx.Response:
        visual_requests.append(request)
        return httpx.Response(503, json={"error": "synthetic unavailable"})

    async def unavailable_speech(websocket: ServerConnection) -> None:
        speech_connections.append(None)
        await websocket.close(code=1011)

    async with serve(unavailable_speech, "127.0.0.1", 0) as speech:
        port = speech.sockets[0].getsockname()[1]
        app = build_acceptance_app(
            settings=Settings(
                _env_file=None,
                ark_api_key="synthetic",
                ark_base_url="https://provider.invalid",
                volc_asr_api_key="synthetic",
                volc_asr_url=f"ws://127.0.0.1:{port}",
                web_static_root=built_web_root,
            ),
            provider_transport=httpx.MockTransport(unavailable_visual),
        )
        async with (
            LifespanManager(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:15177"
            ) as client,
        ):
            created = await client.post(
                "/api/v1/analysis-runs/local",
                data={"local_source_id": "local:62f31c4b-bd1c-4b6a-8ad8-c6121b56135a"},
                files={"media": ("synthetic.mp4", video.read_bytes(), "video/mp4")},
            )
            assert created.status_code == 202
            run_id = created.json()["id"]
            await client.get(f"/api/v1/analysis-runs/{run_id}/events")
            result = (await client.get(f"/api/v1/analysis-runs/{run_id}")).json()
            assert result["status"] == "completed"
            assert result["coverage_status"] == "insufficient"
            assert result["empty_reason"] == "insufficient_evidence"
            assert result["candidates"] == []
            assert result["processed_seconds"] == 0
            assert len(result["coverage_gaps"]) == 1
            assert result["coverage_gaps"][0]["retryable"] is True
            cleanup = (await client.get("/api/v1/runtime-cleanup")).json()
            assert cleanup["clean"] is True
    assert len(visual_requests) == 1
    assert len(speech_connections) == 1


async def test_acceptance_keeps_bounded_clip_adjustment_and_local_gymti_available(
    built_web_root: Path,
) -> None:
    requests: list[httpx.Request] = []

    def select_existing_clip(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"output_text": '{"option_id":"breathing"}'})

    app = build_acceptance_app(
        settings=Settings(
            _env_file=None,
            ark_api_key="synthetic",
            ark_base_url="https://provider.invalid",
            web_static_root=built_web_root,
        ),
        provider_transport=httpx.MockTransport(select_existing_clip),
    )
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://127.0.0.1:15177"
        ) as client,
    ):
        selected = await client.post(
            "/api/v1/playback-adjustments",
            json={
                "action_name": "俯卧撑",
                "instruction": "重看呼吸讲解",
                "options": [
                    {"id": "full", "label": "完整教学", "start_seconds": 0, "end_seconds": 20},
                    {"id": "breathing", "label": "呼吸讲解", "start_seconds": 3, "end_seconds": 5},
                ],
            },
        )
        assert selected.status_code == 200
        assert selected.json() == {"option_id": "breathing"}
        question = await client.post(
            "/api/v1/gymti/next-question",
            json={
                "questionnaire_version": "gymti-questionnaire.v1",
                "scoring_version": "gymti-questionnaire.v1",
                "answered": [],
                "candidate_question_ids": ["q01_energy_after_work"],
            },
        )
        assert question.status_code == 200
        assert question.json()["question_id"] == "q01_energy_after_work"
        assert question.json()["source"] == "local_fallback"
    assert len(requests) == 1


def test_acceptance_launch_configuration_is_separate_and_preserves_explicit_budgets() -> None:
    environment: dict[str, str] = {"RUN_TIMEOUT_SECONDS": "420"}
    configure_acceptance_environment(environment)
    assert environment == {
        "APP_ENV": "development",
        "ANALYSIS_PROVIDER": "cloud",
        "ANALYSIS_CHUNK_TIMEOUT_SECONDS": "90",
        "ANALYSIS_EVIDENCE_TIMEOUT_SECONDS": "590",
        "RUN_TIMEOUT_SECONDS": "420",
    }


@pytest.mark.parametrize("environment", [{"APP_ENV": "production"}, {"ANALYSIS_PROVIDER": "test"}])
def test_acceptance_launcher_rejects_other_profiles_without_mutating_them(
    environment: dict[str, str],
) -> None:
    original = environment.copy()
    with pytest.raises(ValueError, match=r"development.*cloud"):
        configure_acceptance_environment(environment)
    assert environment == original


def test_acceptance_refuses_to_start_without_a_built_frontend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pnpm build"):
        build_acceptance_app(
            settings=Settings(_env_file=None, web_static_root=tmp_path / "missing")
        )
