import asyncio
import base64
import gzip
import json
import struct
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import hakimi_analysis.providers.ark as ark_module
from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.asr import VolcAsrClient
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError


@pytest.mark.asyncio
async def test_asr_maps_word_timestamps_to_the_source_video_clock(tmp_path: Path) -> None:
    audio = tmp_path / "window.wav"
    pcm_bytes = _write_pcm_wav(audio, frames=3200)

    observed: dict[str, Any] = {"audio_packets": []}

    async def handler(websocket: ServerConnection) -> None:
        assert websocket.request is not None
        observed["headers"] = dict(websocket.request.headers)
        config_frame = await websocket.recv()
        assert isinstance(config_frame, bytes)
        observed["config"] = _decode_client_config_frame(config_frame)
        await websocket.send(_server_result_frame({"result": {}}, sequence=1, final=False))

        while True:
            audio_frame = await websocket.recv()
            assert isinstance(audio_frame, bytes)
            payload, final = _decode_client_audio_frame(audio_frame)
            observed["audio_packets"].append(payload)
            if final:
                break

        await websocket.send(
            _server_result_frame(
                {
                    "result": {
                        "text": "做拖拽弯举",
                        "utterances": [
                            {
                                "text": "做拖拽弯举",
                                "start_time": 500,
                                "end_time": 1700,
                                "words": [
                                    {
                                        "text": " ",
                                        "start_time": -1,
                                        "end_time": -1,
                                    },
                                    {
                                        "text": "拖拽弯举",
                                        "start_time": 700,
                                        "end_time": 1600,
                                    },
                                ],
                            }
                        ],
                    }
                },
                sequence=2,
                final=True,
            )
        )

    def add_log_id(
        _connection: ServerConnection,
        _request: object,
        response: Any,
    ) -> Any:
        response.headers["X-Tt-Logid"] = "speech-log-id"
        return response

    async with serve(handler, "127.0.0.1", 0, process_response=add_log_id) as server:
        port = server.sockets[0].getsockname()[1]
        client = VolcAsrClient(
            api_key="test-asr-key",
            resource_id="volc.seedasr.sauc.duration",
            url=f"ws://127.0.0.1:{port}/api/v3/sauc/bigmodel_nostream",
            chunk_duration_ms=100,
            pace_audio=False,
        )
        transcript = await client.recognize(
            audio_path=audio,
            window_start_seconds=15,
            request_id="run-123",
        )

    assert transcript.provider_request_id == "speech-log-id"
    assert transcript.utterances[0].start_seconds == 15.5
    assert len(transcript.utterances[0].words) == 1
    assert transcript.utterances[0].words[0].text == "拖拽弯举"
    assert transcript.utterances[0].words[0].end_seconds == 16.6
    headers = observed["headers"]
    assert headers["x-api-key"] == "test-asr-key"
    assert headers["x-api-resource-id"] == "volc.seedasr.sauc.duration"
    assert headers["x-api-connect-id"] == "run-123"
    assert observed["config"] == {
        "user": {"uid": "hachimi-analysis"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "show_utterances": True,
            "result_type": "full",
        },
    }
    assert b"".join(observed["audio_packets"]) == pcm_bytes


def _server_result_frame(payload: dict[str, object], *, sequence: int, final: bool) -> bytes:
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    flags = 0b0011 if final else 0b0001
    return (
        bytes([0x11, 0x90 | flags, 0x11, 0x00])
        + struct.pack(">iI", sequence, len(compressed))
        + compressed
    )


def _decode_client_config_frame(frame: bytes) -> dict[str, object]:
    assert frame[:4] == bytes([0x11, 0x10, 0x11, 0x00])
    payload_size = struct.unpack(">I", frame[4:8])[0]
    assert len(frame) == 8 + payload_size
    payload = gzip.decompress(frame[8:])
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)
    return parsed


def _decode_client_audio_frame(frame: bytes) -> tuple[bytes, bool]:
    assert frame[0] == 0x11
    assert frame[1] in {0x20, 0x22}
    assert frame[2:] != b""
    assert frame[2:4] == bytes([0x01, 0x00])
    payload_size = struct.unpack(">I", frame[4:8])[0]
    assert len(frame) == 8 + payload_size
    return gzip.decompress(frame[8:]), frame[1] == 0x22


def _write_pcm_wav(path: Path, *, frames: int) -> bytes:
    pcm_bytes = b"\x01\x02" * frames
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_bytes)
    return pcm_bytes


@pytest.mark.asyncio
async def test_asr_retries_a_retryable_provider_failure_once(tmp_path: Path) -> None:
    audio = tmp_path / "window.wav"
    _write_pcm_wav(audio, frames=1600)

    connection_count = 0

    async def handler(websocket: ServerConnection) -> None:
        nonlocal connection_count
        connection_count += 1
        config_frame = await websocket.recv()
        assert isinstance(config_frame, bytes)
        if connection_count == 1:
            await websocket.send(_server_error_frame(55000031))
            return

        await websocket.send(_server_result_frame({"result": {}}, sequence=1, final=False))
        audio_frame = await websocket.recv()
        assert isinstance(audio_frame, bytes)
        _, final = _decode_client_audio_frame(audio_frame)
        assert final
        await websocket.send(
            _server_result_frame(
                {"result": {"text": "弯举", "utterances": []}},
                sequence=2,
                final=True,
            )
        )

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = VolcAsrClient(
            api_key="test-asr-key",
            resource_id="volc.seedasr.sauc.duration",
            url=f"ws://127.0.0.1:{port}/api/v3/sauc/bigmodel_nostream",
            pace_audio=False,
            retry_delays=(0,),
        )
        transcript = await client.recognize(
            audio_path=audio,
            window_start_seconds=0,
            request_id="run-retry",
        )

    assert connection_count == 2
    assert transcript.text == "弯举"


def _server_error_frame(code: int) -> bytes:
    payload = json.dumps({"message": "redacted by client"}).encode("utf-8")
    return bytes([0x11, 0xF0, 0x10, 0x00]) + struct.pack(">II", code, len(payload)) + payload


@pytest.mark.asyncio
async def test_asr_does_not_retry_an_invalid_resource_grant(tmp_path: Path) -> None:
    audio = tmp_path / "window.wav"
    _write_pcm_wav(audio, frames=1600)

    connection_count = 0

    async def handler(websocket: ServerConnection) -> None:
        nonlocal connection_count
        connection_count += 1
        await websocket.recv()
        await websocket.send(_server_error_frame(45000030))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = VolcAsrClient(
            api_key="test-asr-key",
            resource_id="volc.seedasr.sauc.duration",
            url=f"ws://127.0.0.1:{port}/api/v3/sauc/bigmodel_nostream",
            pace_audio=False,
            retry_delays=(0, 0),
        )
        with pytest.raises(ProviderError) as raised:
            await client.recognize(
                audio_path=audio,
                window_start_seconds=0,
                request_id="run-invalid-grant",
            )

    assert connection_count == 1
    assert raised.value.code == "configuration_error"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_cancelling_asr_closes_the_stream(tmp_path: Path) -> None:
    audio = tmp_path / "window.wav"
    _write_pcm_wav(audio, frames=16000)

    first_audio_received = asyncio.Event()
    connection_closed = asyncio.Event()

    async def handler(websocket: ServerConnection) -> None:
        await websocket.recv()
        await websocket.send(_server_result_frame({"result": {}}, sequence=1, final=False))
        await websocket.recv()
        first_audio_received.set()
        try:
            while True:
                await websocket.recv()
        except ConnectionClosed:
            connection_closed.set()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = VolcAsrClient(
            api_key="test-asr-key",
            resource_id="volc.seedasr.sauc.duration",
            url=f"ws://127.0.0.1:{port}/api/v3/sauc/bigmodel_nostream",
            chunk_duration_ms=100,
            pace_audio=True,
        )
        task = asyncio.create_task(
            client.recognize(
                audio_path=audio,
                window_start_seconds=0,
                request_id="run-cancelled",
            )
        )
        await asyncio.wait_for(first_audio_received.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(connection_closed.wait(), timeout=1)


@pytest.mark.asyncio
@respx.mock
async def test_ark_inline_video_uses_visual_model_without_remote_file_upload(
    tmp_path: Path,
) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    response = respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-123",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "segments": [
                                            {
                                                "action_name": "Drag Curl",
                                                "start_seconds": 26,
                                                "end_seconds": 36,
                                                "visual_cue": "哑铃沿躯干后拉",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ],
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-lite-test",
            visual_model_id="doubao-mini-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await client.locate_visual(
            video_path=video,
            window=Segment(start_seconds=0, end_seconds=39),
            instructions="visual skill contract",
        )

    assert result.segments[0].action_name == "Drag Curl"
    assert result.segments[0].start_seconds == 26
    assert result.segments[0].end_seconds == 36
    assert response.called
    response_payload = json.loads(response.calls[0].request.content)
    assert response_payload["model"] == "doubao-mini-test"
    assert response_payload["store"] is False
    assert response_payload["thinking"] == {"type": "disabled"}
    evidence_type = response_payload["text"]["format"]["schema"]["$defs"]["EvidenceSpan"][
        "properties"
    ]["type"]
    assert evidence_type == {"type": "string", "const": "visual"}
    content = response_payload["input"][0]["content"]
    assert content[0]["type"] == "input_video"
    assert content[0]["fps"] == 1
    assert content[0]["video_url"].startswith("data:video/mp4;base64,")
    encoded_video = content[0]["video_url"].partition(",")[2]
    assert base64.b64decode(encoded_video) == b"video-bytes"
    metadata = json.loads(content[1]["text"])
    assert metadata["clip_timeline"] == {"start_seconds": 0.0, "end_seconds": 39.0}
    assert "clip-local" in metadata["time_rule"]
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ark_inline_video_rejects_source_absolute_times_from_the_model(
    tmp_path: Path,
) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-source-clock",
                "output_text": json.dumps(
                    {
                        "segments": [
                            {
                                "action_name": "Drag Curl",
                                "start_seconds": 41,
                                "end_seconds": 51,
                                "visual_cue": "dumbbell movement",
                            }
                        ]
                    }
                ),
            },
        )
    )

    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        with pytest.raises(ProviderSchemaError):
            await client.locate_visual(
                video_path=video,
                window=Segment(start_seconds=0, end_seconds=39),
                instructions="visual skill contract",
            )


@pytest.mark.asyncio
@respx.mock
async def test_ark_contact_sheet_uses_visual_model_without_file_upload(
    tmp_path: Path,
) -> None:
    image = tmp_path / "contact-sheet.jpg"
    image.write_bytes(b"jpeg-test-bytes")
    response = respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-contact-sheet",
                "output_text": json.dumps(
                    {
                        "segments": [
                            {
                                "action_name": "Drag Curl",
                                "start_seconds": 41,
                                "end_seconds": 51,
                                "visual_cue": "dumbbell movement",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        )
    )

    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-lite-test",
            visual_model_id="doubao-mini-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await client.locate_visual_contact_sheet(
            image_path=image,
            frame_times_seconds=(0, 5, 10),
            window=Segment(start_seconds=0, end_seconds=55),
            instructions="visual skill contract",
        )

    assert result.segments[0].action_name == "Drag Curl"
    response_payload = json.loads(response.calls[0].request.content)
    assert response_payload["model"] == "doubao-mini-test"
    assert response_payload["store"] is False
    assert response_payload["thinking"] == {"type": "disabled"}
    content = response_payload["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/jpeg;base64,")
    metadata = json.loads(content[1]["text"])
    assert metadata["analysis_scope"] == "full_source"
    assert "legacy_trigger_seconds" not in metadata
    assert "trigger_seconds" not in metadata
    assert metadata["contact_sheet"] == {
        "columns": 4,
        "frame_times_seconds": [0, 5, 10],
        "order": "row_major",
    }
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("evidence_type", ["speech", "visual"])
async def test_visual_tips_keep_their_source_type_guard(evidence_type: str, tmp_path: Path) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    response = respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "segments": [
                            {
                                "action_name": "侧平举",
                                "start_seconds": 2,
                                "end_seconds": 8,
                                "visual_cue": "画面字幕提示动作要点",
                                "tips": [
                                    {
                                        "text": "保持肩部稳定",
                                        "category": "setup",
                                        "evidence": {
                                            "type": evidence_type,
                                            "start_seconds": 3,
                                            "end_seconds": 5,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-key",
            model_id="test-model",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await client.locate_visual(
            video_path=video,
            window=Segment(start_seconds=0, end_seconds=10),
            instructions="visual skill contract",
        )
    assert len(result.segments) == 1
    assert result.segments[0].action_name == "侧平举"
    assert len(result.segments[0].tips) == (1 if evidence_type == "visual" else 0)
    assert len(response.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ark_inline_video_creates_no_remote_file_when_result_is_invalid(
    tmp_path: Path,
) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={"id": "resp-invalid", "output_text": '{"segments": [{"action_name": 7}]}'},
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        with pytest.raises(ProviderSchemaError):
            await client.locate_visual(
                video_path=video,
                window=Segment(start_seconds=15, end_seconds=54),
                instructions="visual skill contract",
            )

    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ark_inline_video_rejects_oversize_chunk_before_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "window.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setattr(ark_module, "MAX_INLINE_VIDEO_BYTES", len(b"video-bytes") - 1)

    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        with pytest.raises(ProviderError) as failure:
            await client.locate_visual(
                video_path=video,
                window=Segment(start_seconds=15, end_seconds=54),
                instructions="visual skill contract",
            )

    assert failure.value.code == "media_error"
    assert failure.value.retryable is False
    assert len(respx.calls) == 0


@pytest.mark.asyncio
@respx.mock
async def test_ark_speech_result_is_validated_as_json() -> None:
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-speech",
                "output_text": json.dumps(
                    {
                        "signals": [
                            {
                                "action_name": "交叉弯举",
                                "sets": 3,
                                "reps": 10,
                                "duration_seconds": None,
                                "rest_seconds": 60,
                                "start_seconds": 20,
                                "end_seconds": 28,
                                "evidence_text": "做三组交叉弯举",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = ArkResponsesClient(
            api_key="test-ark-key",
            model_id="doubao-test",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await client.understand_speech(
            transcript={
                "text": "做三组交叉弯举",
                "utterances": [{"text": "做三组交叉弯举", "start_seconds": 20, "end_seconds": 28}],
            },
            window=Segment(start_seconds=15, end_seconds=35),
            instructions="speech skill contract",
        )

    assert result.signals[0].sets == 3
    assert result.signals[0].reps == 10
