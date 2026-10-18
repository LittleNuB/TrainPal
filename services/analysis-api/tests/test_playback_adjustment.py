import io
import json
import wave
from pathlib import Path

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.models import Transcript
from hakimi_analysis.playback import PlaybackService
from hakimi_analysis.providers.ark import ArkResponsesClient


def test_playback_contract_documents_bounded_json_and_wav_requests() -> None:
    schema = create_app().openapi()
    selection = schema["paths"]["/api/v1/playback-adjustments"]["post"]["requestBody"]
    payload = selection["content"]["application/json"]["schema"]
    assert payload["properties"]["instruction"]["maxLength"] == 240
    assert payload["properties"]["options"]["maxItems"] == 16
    assert payload["properties"]["options"]["items"]["properties"]["id"]["maxLength"] == 64
    voice = schema["paths"]["/api/v1/playback-voice"]["post"]["requestBody"]
    assert voice["content"]["audio/wav"]["schema"]["format"] == "binary"


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,status", [("breathing", 200), ("invented", 422), (None, 200)])
async def test_playback_http_only_returns_existing_range_ids(
    selected: str | None, status: int
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps({"option_id": selected})})
        )
    ) as provider:
        app = create_app(
            playback_service=PlaybackService(
                model=ArkResponsesClient(
                    api_key="test",
                    model_id="existing",
                    base_url="https://test",
                    http_client=provider,
                )
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/playback-adjustments",
                json={
                    "action_name": "俯卧撑",
                    "instruction": "重看呼吸讲解",
                    "options": [
                        {"id": "full", "label": "完整教学", "start_seconds": 0, "end_seconds": 20},
                        {
                            "id": "breathing",
                            "label": "推起时呼气",
                            "start_seconds": 3,
                            "end_seconds": 5,
                        },
                    ],
                },
            )
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"option_id": selected}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_playback_http_fails_closed_without_provider_and_rejects_cross_origin() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/playback-adjustments", content="{}")
        assert response.status_code == 503
        response = await client.post(
            "/api/v1/playback-adjustments", content="{}", headers={"Origin": "https://evil.test"}
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_voice_uses_short_pcm_and_deletes_temporary_audio_on_success_and_failure() -> None:
    paths: list[Path] = []

    class Asr:
        async def recognize(
            self, *, audio_path: Path, window_start_seconds: float, request_id: str
        ) -> Transcript:
            paths.append(audio_path)
            assert audio_path.is_file()
            if len(paths) == 2:
                raise TimeoutError()
            return Transcript(text="重看呼吸讲解", utterances=[])

    audio = io.BytesIO()
    with wave.open(audio, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\x00\x00" * 1600)
    async with httpx.AsyncClient() as provider:
        app = create_app(
            playback_service=PlaybackService(
                model=ArkResponsesClient(
                    api_key="test", model_id="test", base_url="https://test", http_client=provider
                ),
                asr=Asr(),
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.post("/api/v1/playback-voice", content=b"not-wav")
            ).status_code == 422
            response = await client.post("/api/v1/playback-voice", content=audio.getvalue())
            assert response.json() == {"text": "重看呼吸讲解"}
            assert (
                await client.post("/api/v1/playback-voice", content=audio.getvalue())
            ).status_code == 503
            assert (
                await client.post("/api/v1/playback-voice", content=b"x" * 480_045)
            ).status_code == 413
    assert len(paths) == 2
    assert all(not path.parent.exists() for path in paths)
