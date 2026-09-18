"""One bounded presentation decision. No workout state or media editing authority."""

import asyncio
import io
import tempfile
import wave
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from hakimi_analysis.models import Segment, StrictModel, Transcript


class PlaybackOption(Segment):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)


class PlaybackRequest(StrictModel):
    action_name: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=240)
    options: list[PlaybackOption] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_options(self) -> "PlaybackRequest":
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("duplicate option identifiers")
        return self


class PlaybackChoice(StrictModel):
    option_id: str | None = Field(max_length=64)


class VoiceCommand(StrictModel):
    text: str = Field(max_length=240)


class PlaybackModel(Protocol):
    async def select_playback(self, payload: PlaybackRequest) -> PlaybackChoice: ...


class CommandAsr(Protocol):
    async def recognize(
        self, *, audio_path: Path, window_start_seconds: float, request_id: str
    ) -> Transcript: ...


class PlaybackService:
    def __init__(self, *, model: PlaybackModel, asr: CommandAsr | None = None) -> None:
        self.model = model
        self.asr = asr

    async def select(self, payload: PlaybackRequest) -> PlaybackChoice:
        async with asyncio.timeout(15):
            choice = await self.model.select_playback(payload)
        if choice.option_id is not None and choice.option_id not in {
            option.id for option in payload.options
        }:
            raise ValueError("unknown playback choice")
        return choice

    async def transcribe(self, audio: bytes) -> VoiceCommand:
        if self.asr is None:
            raise ValueError("voice unavailable")
        try:
            with wave.open(io.BytesIO(audio), "rb") as recording:
                if (
                    recording.getnchannels() != 1
                    or recording.getsampwidth() != 2
                    or recording.getframerate() != 16_000
                    or not 0 < recording.getnframes() <= 240_000
                ):
                    raise ValueError("invalid recording")
                if len(recording.readframes(recording.getnframes())) != recording.getnframes() * 2:
                    raise ValueError("incomplete recording")
        except (wave.Error, EOFError) as error:
            raise ValueError("invalid recording") from error
        # Provider needs a path; a context-managed temporary directory is always removed.
        with tempfile.TemporaryDirectory(prefix="trainpal-command-") as directory:
            path = Path(directory) / "command.wav"
            path.write_bytes(audio)
            async with asyncio.timeout(20):
                transcript = await self.asr.recognize(
                    audio_path=path, window_start_seconds=0, request_id=str(uuid4())
                )
        text = transcript.text.strip()
        if not text or len(text) > 240:
            raise ValueError("empty or oversized command")
        return VoiceCommand(text=text)
