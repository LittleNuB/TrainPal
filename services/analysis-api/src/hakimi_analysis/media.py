import asyncio
import math
import os
import signal
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from hakimi_analysis.runtime_cleanup import RuntimeCleanupMonitor
from hakimi_analysis.sources import MAX_ANALYZABLE_SOURCE_DURATION_SECONDS

VISUAL_SAMPLE_INTERVAL_SECONDS = 3.5
MAX_VISUAL_FRAME_COUNT = 18
PROCESS_STOP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class AnalysisWindow:
    start_seconds: float
    end_seconds: float
    expanded: bool

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def calculate_analysis_window(
    *,
    trigger_seconds: float,
    duration_seconds: float,
    expanded: bool = False,
) -> AnalysisWindow:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if trigger_seconds < 0 or trigger_seconds > duration_seconds:
        raise ValueError("trigger_seconds must be inside the source video")

    seconds_before = 30 if expanded else 15
    seconds_after = 40 if expanded else 20
    return AnalysisWindow(
        start_seconds=max(0, trigger_seconds - seconds_before),
        end_seconds=min(duration_seconds, trigger_seconds + seconds_after),
        expanded=expanded,
    )


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    directory: Path
    video_path: Path
    audio_path: Path
    window: AnalysisWindow
    contact_sheet_path: Path | None = None
    contact_sheet_timestamps: tuple[float, ...] = ()
    contact_sheet_ready: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class PreparedVisualChunk:
    video_path: Path
    window: AnalysisWindow


class MediaProcessingError(RuntimeError):
    pass


class LocalMediaProcessor:
    def __init__(
        self,
        *,
        temp_root: Path | None = None,
        command_timeout_seconds: float = 90,
        max_source_duration_seconds: float = MAX_ANALYZABLE_SOURCE_DURATION_SECONDS,
        visual_sample_interval_seconds: float = VISUAL_SAMPLE_INTERVAL_SECONDS,
        runtime_cleanup: RuntimeCleanupMonitor | None = None,
    ) -> None:
        if max_source_duration_seconds <= 0 or visual_sample_interval_seconds <= 0:
            raise ValueError("media analysis limits must be positive")
        self._temp_root = temp_root
        self._command_timeout_seconds = command_timeout_seconds
        self._max_source_duration_seconds = max_source_duration_seconds
        self._visual_sample_interval_seconds = visual_sample_interval_seconds
        self._runtime_cleanup = runtime_cleanup
        if self._temp_root is not None:
            self._temp_root.mkdir(parents=True, exist_ok=True)

    async def probe_duration(self, source_path: Path) -> float:
        return await asyncio.to_thread(probe_duration_sync, source_path)

    @asynccontextmanager
    async def prepare_source(
        self,
        source_path: Path,
        duration_seconds: float,
        *,
        start_seconds: float = 0,
        include_contact_sheet: bool = True,
    ) -> AsyncIterator[PreparedMedia]:
        if not source_path.is_file():
            raise MediaProcessingError("configured source video is unavailable")
        if duration_seconds <= 0:
            raise MediaProcessingError("source video duration must be positive")
        if duration_seconds > self._max_source_duration_seconds:
            raise MediaProcessingError("source video exceeds the full-source analysis limit")

        if start_seconds < 0:
            raise MediaProcessingError("source analysis range must not be negative")
        window = AnalysisWindow(
            start_seconds=0,
            end_seconds=duration_seconds,
            expanded=False,
        )
        extraction_window = AnalysisWindow(
            start_seconds=start_seconds,
            end_seconds=start_seconds + duration_seconds,
            expanded=False,
        )
        root = str(self._temp_root) if self._temp_root is not None else None
        with tempfile.TemporaryDirectory(prefix="hachimi-analysis-", dir=root) as temp_directory:
            directory = Path(temp_directory)
            audio_path = directory / "speech-source.wav"
            contact_sheet_path = directory / "visual-contact-sheet.jpg"
            frame_count = min(
                MAX_VISUAL_FRAME_COUNT,
                max(1, math.ceil(duration_seconds / self._visual_sample_interval_seconds)),
            )
            frame_interval_seconds = duration_seconds / frame_count
            timestamps = tuple(
                round(index * frame_interval_seconds, 3) for index in range(frame_count)
            )
            audio_task = asyncio.create_task(
                self._extract_audio(source_path, audio_path, extraction_window)
            )
            contact_sheet_kwargs: dict[str, Any] = {
                "frame_interval_seconds": frame_interval_seconds,
                "frame_count": frame_count,
            }
            if start_seconds > 0:
                contact_sheet_kwargs["window"] = extraction_window
            contact_sheet_task = (
                asyncio.create_task(
                    self._extract_contact_sheet(
                        source_path,
                        contact_sheet_path,
                        **contact_sheet_kwargs,
                    )
                )
                if include_contact_sheet
                else None
            )
            extraction_tasks = tuple(
                task for task in (audio_task, contact_sheet_task) if task is not None
            )
            try:
                await audio_task
                yield PreparedMedia(
                    directory=directory,
                    video_path=source_path,
                    audio_path=audio_path,
                    window=window,
                    contact_sheet_path=contact_sheet_path if include_contact_sheet else None,
                    contact_sheet_timestamps=timestamps if include_contact_sheet else (),
                    contact_sheet_ready=contact_sheet_task,
                )
            finally:
                for task in extraction_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*extraction_tasks, return_exceptions=True)

    async def prepare_visual_chunk(
        self,
        source_path: Path,
        window: AnalysisWindow,
        directory: Path,
        *,
        source_offset_seconds: float = 0,
    ) -> PreparedVisualChunk:
        if not source_path.is_file():
            raise MediaProcessingError("configured source video is unavailable")
        if not directory.is_dir():
            raise MediaProcessingError("analysis temporary directory is unavailable")
        if window.duration_seconds <= 0 or source_offset_seconds < 0:
            raise MediaProcessingError("visual analysis window is invalid")
        extraction_window = AnalysisWindow(
            start_seconds=source_offset_seconds + window.start_seconds,
            end_seconds=source_offset_seconds + window.end_seconds,
            expanded=False,
        )
        output_path = directory / f"visual-chunk-{round(window.start_seconds * 1000):09d}.mp4"
        await self._extract_video(source_path, output_path, extraction_window)
        return PreparedVisualChunk(
            video_path=output_path,
            window=window,
        )

    @asynccontextmanager
    async def prepare(
        self,
        source_path: Path,
        window: AnalysisWindow,
    ) -> AsyncIterator[PreparedMedia]:
        if not source_path.is_file():
            raise MediaProcessingError("configured source video is unavailable")
        if window.duration_seconds <= 0:
            raise MediaProcessingError("analysis window is empty")

        root = str(self._temp_root) if self._temp_root is not None else None
        with tempfile.TemporaryDirectory(prefix="hachimi-analysis-", dir=root) as temp_directory:
            directory = Path(temp_directory)
            video_path = directory / "visual-window.mp4"
            audio_path = directory / "speech-window.wav"
            extraction_tasks = [
                asyncio.create_task(self._extract_video(source_path, video_path, window)),
                asyncio.create_task(self._extract_audio(source_path, audio_path, window)),
            ]
            try:
                await asyncio.gather(*extraction_tasks)
            finally:
                for task in extraction_tasks:
                    # gather already forwards cancellation; let in-flight cleanup finish.
                    if not task.done() and not task.cancelling():
                        task.cancel()
                await asyncio.gather(*extraction_tasks, return_exceptions=True)
            yield PreparedMedia(
                directory=directory,
                video_path=video_path,
                audio_path=audio_path,
                window=window,
            )

    async def _extract_video(
        self,
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        await self._run_ffmpeg(
            "video",
            "-ss",
            f"{window.start_seconds:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{window.duration_seconds:.3f}",
            "-an",
            "-vf",
            "scale=w='min(1280,iw)':h=-2",
            "-r",
            "15",
            "-c:v",
            "mpeg4",
            "-b:v",
            "4000k",
            "-maxrate",
            "4000k",
            "-bufsize",
            "8000k",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        )

    async def _extract_audio(
        self,
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        await self._run_ffmpeg(
            "audio",
            "-ss",
            f"{window.start_seconds:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{window.duration_seconds:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        )

    async def _extract_contact_sheet(
        self,
        source_path: Path,
        output_path: Path,
        *,
        frame_interval_seconds: float,
        frame_count: int,
        window: AnalysisWindow | None = None,
    ) -> None:
        if window is None:
            window = AnalysisWindow(
                start_seconds=0,
                end_seconds=frame_interval_seconds * frame_count,
                expanded=False,
            )
        columns = 4
        rows = math.ceil(frame_count / columns)
        await self._run_ffmpeg(
            "contact sheet",
            "-ss",
            f"{window.start_seconds:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{window.duration_seconds:.3f}",
            "-vf",
            (
                f"fps=1/{frame_interval_seconds:.6f},scale=160:-1,"
                f"tile={columns}x{rows}:padding=2:margin=2"
            ),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(output_path),
        )

    async def _run_ffmpeg(self, operation: str, *arguments: str) -> None:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        # Uvicorn reload uses a Windows selector loop without async subprocess support.
        try:
            process = subprocess.Popen(
                (
                    executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    *arguments,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise MediaProcessingError(f"{operation} extraction could not start") from error
        if self._runtime_cleanup is not None:
            self._runtime_cleanup.register_ffmpeg_process(process)
        communication = asyncio.create_task(asyncio.to_thread(process.communicate))
        try:
            async with asyncio.timeout(self._command_timeout_seconds):
                _, _stderr = await asyncio.shield(communication)
        except TimeoutError as error:
            cleanup_error = await _stop_process(process, communication)
            if cleanup_error is not None:
                raise MediaProcessingError(
                    f"{operation} extraction cleanup failed"
                ) from cleanup_error
            raise MediaProcessingError(f"{operation} extraction timed out") from error
        except asyncio.CancelledError:
            await _stop_process(process, communication)
            raise
        finally:
            if self._runtime_cleanup is not None and process.poll() is not None:
                self._runtime_cleanup.unregister_ffmpeg_process(process)
        if process.returncode != 0:
            raise MediaProcessingError(f"{operation} extraction failed")
        output_path = Path(arguments[-1])
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise MediaProcessingError(f"{operation} extraction produced no output")


async def _stop_process(
    process: subprocess.Popen[bytes],
    communication: asyncio.Future[Any],
) -> BaseException | None:
    def stop() -> None:
        if process.poll() is None:
            try:
                process.kill()
            except OSError as kill_error:
                if process.poll() is None:
                    try:
                        fallback_signal = (
                            signal.SIGTERM if os.name == "nt" else signal.Signals(9)
                        )
                        os.kill(process.pid, fallback_signal)
                    except OSError as fallback_error:
                        if process.poll() is None:
                            raise fallback_error from kill_error
            try:
                process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    raise

    cleanup_error: BaseException | None = None
    try:
        await asyncio.to_thread(stop)
    except (OSError, subprocess.TimeoutExpired) as error:
        cleanup_error = error
    try:
        async with asyncio.timeout(PROCESS_STOP_TIMEOUT_SECONDS):
            await asyncio.shield(communication)
    except TimeoutError as error:
        communication.cancel()
        cleanup_error = cleanup_error or error
    return cleanup_error


def probe_duration_sync(source_path: Path) -> float:
    if not source_path.is_file():
        raise MediaProcessingError("configured source video is unavailable")
    reader: Any = imageio_ffmpeg.read_frames(str(source_path), pix_fmt="rgb24")
    try:
        metadata = next(reader)
        duration = float(metadata.get("duration", 0))
    finally:
        reader.close()
    if duration <= 0:
        raise MediaProcessingError("could not determine source duration")
    return duration
