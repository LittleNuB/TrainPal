import asyncio
import gzip
import json
import struct
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from hakimi_analysis.ai_budget import AiBudgetError, AiBudgetLedger
from hakimi_analysis.models import Transcript, TranscriptUtterance, TranscriptWord
from hakimi_analysis.providers.base import ProviderError

_FULL_CLIENT_REQUEST = 0b0001
_AUDIO_ONLY_CLIENT_REQUEST = 0b0010
_FULL_SERVER_RESPONSE = 0b1001
_ERROR_SERVER_RESPONSE = 0b1111
_JSON_SERIALIZATION = 0b0001
_GZIP_COMPRESSION = 0b0001
_HAS_SEQUENCE = 0b0001
_IS_FINAL = 0b0010
_RESOURCE_GRANT_ERROR = 45_000_030
_PACKET_WAIT_TIMEOUT_ERROR = 45_000_081
_INTERNAL_ERROR_PREFIX = "55"


class VolcAsrClient:
    def __init__(
        self,
        *,
        api_key: str,
        resource_id: str,
        url: str,
        chunk_duration_ms: int = 4000,
        pace_audio: bool = True,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
        budget: AiBudgetLedger | None = None,
    ) -> None:
        if chunk_duration_ms <= 0:
            raise ValueError("chunk_duration_ms must be positive")
        self._api_key = api_key
        self._resource_id = resource_id
        self._url = url
        self._chunk_duration_ms = chunk_duration_ms
        self._pace_audio = pace_audio
        self._retry_delays = retry_delays
        self._budget = budget

    async def recognize(
        self,
        *,
        audio_path: Path,
        window_start_seconds: float,
        request_id: str,
    ) -> Transcript:
        chunks = await asyncio.to_thread(
            _read_pcm_chunks,
            audio_path,
            self._chunk_duration_ms,
        )
        if self._budget and (
            self._resource_id != "volc.seedasr.sauc.duration"
            or self._url != "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
        ):
            raise AiBudgetError("ai_budget_unavailable")
        if not chunks:
            return Transcript(text="", utterances=[], provider_request_id=None)

        attempts = len(self._retry_delays) + 1
        for attempt in range(attempts):
            # 16kHz mono PCM16; official on-demand ASR price is 4.5 yuan/hour.
            charge = ((sum(len(chunk) for chunk in chunks) + 31_999) // 32_000) * 1_250
            reservation = (
                self._budget.reserve(charge, kind="asr", model=self._resource_id)
                if self._budget
                else None
            )
            try:
                result = await self._recognize_once(
                    chunks=chunks,
                    window_start_seconds=window_start_seconds,
                    connect_id=request_id if attempt == 0 else str(uuid4()),
                )
                if self._budget and reservation:
                    self._budget.settle(reservation, charge)
                return result
            except ProviderError as error:
                if not error.retryable or attempt == attempts - 1:
                    raise
            except InvalidStatus as error:
                mapped = _handshake_error(error)
                if not mapped.retryable or attempt == attempts - 1:
                    raise mapped from error
            except (ConnectionClosed, OSError, TimeoutError) as error:
                if attempt == attempts - 1:
                    raise ProviderError(
                        "provider_error",
                        "语音识别服务暂时不可用",
                        retryable=True,
                    ) from error
            await asyncio.sleep(self._retry_delays[attempt])
        raise AssertionError("retry loop exhausted")

    async def _recognize_once(
        self,
        *,
        chunks: list[bytes],
        window_start_seconds: float,
        connect_id: str,
    ) -> Transcript:
        async with connect(
            self._url,
            additional_headers={
                "X-Api-Key": self._api_key,
                "X-Api-Resource-Id": self._resource_id,
                "X-Api-Connect-Id": connect_id,
            },
            open_timeout=15,
            close_timeout=5,
            ping_interval=None,
        ) as websocket:
            response = websocket.response
            provider_request_id = (
                response.headers.get("X-Tt-Logid") if response is not None else None
            )
            await websocket.send(_config_frame())
            await _receive_frame(websocket, provider_request_id)

            receive_task = asyncio.create_task(
                _receive_final_result(websocket, provider_request_id)
            )
            try:
                for index, chunk in enumerate(chunks):
                    final = index == len(chunks) - 1
                    await websocket.send(_audio_frame(chunk, final=final))
                    if self._pace_audio and not final:
                        await asyncio.sleep(self._chunk_duration_ms / 1000)
                result = await receive_task
            finally:
                if not receive_task.done():
                    receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)

        return _to_transcript(
            result,
            window_start_seconds=window_start_seconds,
            provider_request_id=provider_request_id,
        )


def _config_frame() -> bytes:
    payload = {
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
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(encoded)
    header = _header(
        message_type=_FULL_CLIENT_REQUEST,
        flags=0,
        serialization=_JSON_SERIALIZATION,
        compression=_GZIP_COMPRESSION,
    )
    return header + struct.pack(">I", len(compressed)) + compressed


def _audio_frame(audio: bytes, *, final: bool) -> bytes:
    compressed = gzip.compress(audio)
    header = _header(
        message_type=_AUDIO_ONLY_CLIENT_REQUEST,
        flags=_IS_FINAL if final else 0,
        serialization=0,
        compression=_GZIP_COMPRESSION,
    )
    return header + struct.pack(">I", len(compressed)) + compressed


def _header(*, message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes(
        [
            0x11,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        ]
    )


async def _receive_final_result(
    websocket: ClientConnection,
    provider_request_id: str | None,
) -> dict[str, Any]:
    while True:
        payload, final = await _receive_frame(websocket, provider_request_id)
        if final:
            return payload


async def _receive_frame(
    websocket: ClientConnection,
    provider_request_id: str | None,
) -> tuple[dict[str, Any], bool]:
    message = await websocket.recv(decode=False)
    if not isinstance(message, bytes):
        raise ProviderError(
            "schema_error",
            "语音识别返回格式无效",
            retryable=False,
            request_id=provider_request_id,
        )
    return _parse_server_frame(message, provider_request_id)


def _parse_server_frame(
    frame: bytes,
    provider_request_id: str | None,
) -> tuple[dict[str, Any], bool]:
    try:
        if len(frame) < 8:
            raise ValueError("frame is too short")
        version = frame[0] >> 4
        header_size = (frame[0] & 0x0F) * 4
        message_type = frame[1] >> 4
        flags = frame[1] & 0x0F
        compression = frame[2] & 0x0F
        if version != 1 or header_size < 4 or len(frame) < header_size + 4:
            raise ValueError("invalid frame header")

        offset = header_size
        if message_type == _ERROR_SERVER_RESPONSE:
            if len(frame) < offset + 8:
                raise ValueError("invalid error frame")
            provider_code = struct.unpack(">I", frame[offset : offset + 4])[0]
            _raise_provider_error(provider_code, provider_request_id)

        if message_type != _FULL_SERVER_RESPONSE:
            raise ValueError("unexpected server message type")
        if flags & _HAS_SEQUENCE:
            offset += 4
        if len(frame) < offset + 4:
            raise ValueError("missing payload size")
        payload_size = struct.unpack(">I", frame[offset : offset + 4])[0]
        offset += 4
        payload = frame[offset:]
        if len(payload) != payload_size:
            raise ValueError("payload size mismatch")
        if compression == _GZIP_COMPRESSION:
            payload = gzip.decompress(payload)
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise TypeError("server payload must be an object")
        return parsed, bool(flags & _IS_FINAL)
    except ProviderError:
        raise
    except (gzip.BadGzipFile, json.JSONDecodeError, struct.error, TypeError, ValueError) as error:
        raise ProviderError(
            "schema_error",
            "语音识别返回格式无效",
            retryable=False,
            request_id=provider_request_id,
        ) from error


def _raise_provider_error(provider_code: int, request_id: str | None) -> None:
    code_text = str(provider_code)
    retryable = provider_code == _PACKET_WAIT_TIMEOUT_ERROR or code_text.startswith(
        _INTERNAL_ERROR_PREFIX
    )
    code = "configuration_error" if provider_code == _RESOURCE_GRANT_ERROR else "provider_error"
    raise ProviderError(
        code,
        "语音识别服务暂时不可用",
        retryable=retryable,
        request_id=request_id,
    )


def _handshake_error(error: InvalidStatus) -> ProviderError:
    response = error.response
    status_code = response.status_code
    provider_request_id = response.headers.get("X-Tt-Logid")
    retryable = status_code in {408, 429} or status_code >= 500
    code = "configuration_error" if status_code in {401, 403} else "provider_error"
    return ProviderError(
        code,
        "语音识别服务暂时不可用",
        retryable=retryable,
        request_id=provider_request_id,
    )


def _read_pcm_chunks(audio_path: Path, chunk_duration_ms: int) -> list[bytes]:
    frames_per_chunk = 16000 * chunk_duration_ms // 1000
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getframerate() != 16000
                or wav_file.getcomptype() != "NONE"
            ):
                raise ValueError("unsupported WAV format")
            chunks: list[bytes] = []
            while chunk := wav_file.readframes(frames_per_chunk):
                chunks.append(chunk)
            return chunks
    except (EOFError, wave.Error, ValueError) as error:
        raise ProviderError(
            "media_error",
            "语音片段格式无效",
            retryable=False,
        ) from error


def _to_transcript(
    payload: dict[str, Any],
    *,
    window_start_seconds: float,
    provider_request_id: str | None,
) -> Transcript:
    try:
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise TypeError("result must be an object")
        utterances = [
            TranscriptUtterance(
                text=str(item.get("text", "")),
                start_seconds=window_start_seconds + float(item["start_time"]) / 1000,
                end_seconds=window_start_seconds + float(item["end_time"]) / 1000,
                words=_to_transcript_words(
                    item.get("words", []),
                    window_start_seconds=window_start_seconds,
                ),
            )
            for item in result.get("utterances", [])
        ]
        return Transcript(
            text=str(result.get("text", "")),
            utterances=utterances,
            provider_request_id=provider_request_id,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ProviderError(
            "schema_error",
            "语音识别返回格式无效",
            retryable=False,
            request_id=provider_request_id,
        ) from error


def _to_transcript_words(
    raw_words: object,
    *,
    window_start_seconds: float,
) -> list[TranscriptWord]:
    if not isinstance(raw_words, list):
        raise TypeError("words must be a list")
    words: list[TranscriptWord] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            raise TypeError("word must be an object")
        start_time = float(raw_word["start_time"])
        end_time = float(raw_word["end_time"])
        if start_time < 0 or end_time < 0:
            continue
        words.append(
            TranscriptWord(
                text=str(raw_word.get("text", "")),
                start_seconds=window_start_seconds + start_time / 1000,
                end_seconds=window_start_seconds + end_time / 1000,
            )
        )
    return words
