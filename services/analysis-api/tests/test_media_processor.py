import asyncio
import subprocess
import sys
import threading
import time
from itertools import pairwise
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import pytest

import hakimi_analysis.media as media_module
from hakimi_analysis.media import AnalysisWindow, LocalMediaProcessor, MediaProcessingError
from hakimi_analysis.runtime_cleanup import RuntimeCleanupMonitor


async def create_synthetic_video(path: Path) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x101820:s=320x568:r=15",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=16000",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-y",
        str(path),
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")


async def create_colour_transition_video(path: Path) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=64x64:r=10:d=1.5",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=10:d=1.5",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "mpeg4",
        "-g",
        "100",
        "-pix_fmt",
        "yuv420p",
        "-y",
        str(path),
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")


def test_full_source_prepares_when_event_loop_has_no_async_subprocess_support(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    asyncio.run(create_synthetic_video(source))
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    loop = asyncio.new_event_loop()

    async def unsupported_subprocess_exec(*_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError

    loop.subprocess_exec = unsupported_subprocess_exec  # type: ignore[assignment]

    async def prepare() -> None:
        async with processor.prepare_source(source, duration_seconds=2) as prepared:
            assert prepared.audio_path.is_file()
            assert prepared.contact_sheet_ready is not None
            await prepared.contact_sheet_ready
            assert prepared.contact_sheet_path is not None
            assert prepared.contact_sheet_path.is_file()

    try:
        loop.run_until_complete(prepare())
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_prepared_media_is_bounded_and_removed_after_use(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    await create_synthetic_video(source)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    duration = await processor.probe_duration(source)
    assert 1.9 <= duration <= 2.1

    prepared_directory: Path | None = None
    async with processor.prepare(
        source,
        AnalysisWindow(start_seconds=0.25, end_seconds=1.75, expanded=False),
    ) as prepared:
        prepared_directory = prepared.directory
        assert prepared.video_path.is_file()
        assert prepared.audio_path.is_file()
        assert prepared.window.start_seconds == 0.25

    assert prepared_directory is not None
    assert not prepared_directory.exists()


@pytest.mark.asyncio
async def test_full_source_reuses_video_and_only_cleans_transient_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    await create_synthetic_video(source)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    prepared_directory: Path | None = None
    async with processor.prepare_source(source, duration_seconds=2) as prepared:
        prepared_directory = prepared.directory
        assert prepared.video_path == source
        assert prepared.audio_path.is_file()
        assert prepared.contact_sheet_path is not None
        assert prepared.contact_sheet_ready is not None
        await prepared.contact_sheet_ready
        assert prepared.contact_sheet_path.is_file()
        assert prepared.contact_sheet_timestamps[0] == 0
        assert prepared.contact_sheet_timestamps[-1] < 2
        assert prepared.window == AnalysisWindow(
            start_seconds=0,
            end_seconds=2,
            expanded=False,
        )

    assert source.is_file()
    assert prepared_directory is not None
    assert not prepared_directory.exists()


@pytest.mark.asyncio
async def test_full_source_sampling_covers_the_supported_sixty_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    captured: dict[str, float | int] = {}

    async def fake_audio(
        _source_path: Path,
        output_path: Path,
        _window: AnalysisWindow,
    ) -> None:
        output_path.write_bytes(b"audio")

    async def fake_contact_sheet(
        _source_path: Path,
        output_path: Path,
        *,
        frame_interval_seconds: float,
        frame_count: int,
    ) -> None:
        captured.update(interval=frame_interval_seconds, count=frame_count)
        output_path.write_bytes(b"image")

    monkeypatch.setattr(processor, "_extract_audio", fake_audio)
    monkeypatch.setattr(processor, "_extract_contact_sheet", fake_contact_sheet)

    async with processor.prepare_source(source, duration_seconds=60) as prepared:
        assert prepared.contact_sheet_ready is not None
        await prepared.contact_sheet_ready
        timestamps = prepared.contact_sheet_timestamps

    assert len(timestamps) == 18
    assert captured == {"interval": 60 / 18, "count": 18}
    assert max(second - first for first, second in pairwise(timestamps)) <= 3.5
    assert 60 - timestamps[-1] <= 3.5


@pytest.mark.asyncio
async def test_full_source_yields_audio_while_contact_sheet_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    release_contact_sheet = asyncio.Event()

    async def fake_audio(
        _source_path: Path,
        output_path: Path,
        _window: AnalysisWindow,
    ) -> None:
        output_path.write_bytes(b"audio")

    async def fake_contact_sheet(
        _source_path: Path,
        output_path: Path,
        *,
        frame_interval_seconds: float,
        frame_count: int,
    ) -> None:
        del frame_interval_seconds, frame_count
        await release_contact_sheet.wait()
        output_path.write_bytes(b"image")

    monkeypatch.setattr(processor, "_extract_audio", fake_audio)
    monkeypatch.setattr(processor, "_extract_contact_sheet", fake_contact_sheet)

    async with processor.prepare_source(source, duration_seconds=30) as prepared:
        assert prepared.audio_path.is_file()
        assert prepared.contact_sheet_path is not None
        assert prepared.contact_sheet_ready is not None
        assert not prepared.contact_sheet_ready.done()
        assert not prepared.contact_sheet_path.exists()
        release_contact_sheet.set()
        await prepared.contact_sheet_ready
        assert prepared.contact_sheet_path.is_file()


@pytest.mark.asyncio
async def test_full_source_analysis_rejects_video_beyond_supported_duration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    with pytest.raises(MediaProcessingError):
        async with processor.prepare_source(source, duration_seconds=60.1):
            pass


@pytest.mark.asyncio
async def test_visual_window_uses_builtin_mpeg4_encoder_for_accurate_seeking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "window.mp4"
    source.write_bytes(b"not-read")
    processor = LocalMediaProcessor()
    captured: tuple[str, ...] = ()

    async def capture(_operation: str, *arguments: str) -> None:
        nonlocal captured
        captured = arguments

    monkeypatch.setattr(processor, "_run_ffmpeg", capture)
    await processor._extract_video(
        source,
        output,
        AnalysisWindow(start_seconds=5, end_seconds=12, expanded=False),
    )

    assert "libx264" not in captured
    assert captured[captured.index("-c:v") + 1] == "mpeg4"
    assert captured[captured.index("-pix_fmt") + 1] == "yuv420p"
    assert "min(1280,iw)" in captured[captured.index("-vf") + 1]
    assert captured[captured.index("-r") + 1] == "15"
    assert captured[captured.index("-b:v") + 1] == "4000k"
    assert captured[captured.index("-maxrate") + 1] == "4000k"
    assert captured[captured.index("-bufsize") + 1] == "8000k"


@pytest.mark.asyncio
async def test_visual_window_starts_at_requested_non_keyframe_content(tmp_path: Path) -> None:
    source = tmp_path / "colour-transition.mp4"
    output = tmp_path / "window.mp4"
    await create_colour_transition_video(source)
    processor = LocalMediaProcessor()
    await processor._extract_video(
        source,
        output,
        AnalysisWindow(start_seconds=1.75, end_seconds=2.25, expanded=False),
    )
    reader: Any = imageio_ffmpeg.read_frames(str(output), pix_fmt="rgb24")
    try:
        metadata = next(reader)
        first_frame = next(reader)
    finally:
        reader.close()

    assert 0.4 <= float(metadata["duration"]) <= 0.6
    red = first_frame[0]
    blue = first_frame[2]
    assert blue > 180
    assert red < 80


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "termination_delays",
    [(0, 0), (0.15, 0), (0, 0.15)],
    ids=["immediate", "slow-video-stop", "slow-audio-stop"],
)
async def test_prepared_media_and_ffmpeg_are_stopped_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination_delays: tuple[float, float],
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    both_started = asyncio.Event()
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def create_blocking_process(*_args: Any, **_kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        real_kill = process.kill
        # Exercise different OS process-stop speeds without changing the media processor.
        delay = termination_delays[len(processes)]

        def delayed_kill() -> None:
            time.sleep(delay)
            real_kill()

        monkeypatch.setattr(process, "kill", delayed_kill)
        processes.append(process)
        if len(processes) == 2:
            both_started.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", create_blocking_process)
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: sys.executable)
    temp_root = tmp_path / "runs"
    runtime_cleanup = RuntimeCleanupMonitor(
        temp_root,
        descendant_ffmpeg_probe=lambda: set(),
    )
    processor = LocalMediaProcessor(
        temp_root=temp_root,
        runtime_cleanup=runtime_cleanup,
    )
    prepared_directories: list[Path] = []

    async def prepare() -> None:
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ) as prepared:
            prepared_directories.append(prepared.directory)
            raise AssertionError("cancelled extraction must not yield prepared media")

    task = asyncio.create_task(prepare())
    await asyncio.wait_for(both_started.wait(), timeout=1)
    active = runtime_cleanup.snapshot()
    assert active.ffmpeg_process_count == 2
    assert active.residue_count == 1
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)
    assert not any(temp_root.iterdir())
    assert runtime_cleanup.snapshot().clean is True
    assert prepared_directories == []


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["cancel", "timeout"])
async def test_cleanup_probe_stays_dirty_when_ffmpeg_cannot_be_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination: str,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    both_started = threading.Event()

    class StuckProcess:
        returncode = None

        def __init__(self, process_id: int) -> None:
            self.pid = process_id
            self._release_communication = threading.Event()

        def poll(self) -> None:
            return None

        def communicate(self) -> tuple[bytes, bytes]:
            self._release_communication.wait(timeout=1)
            return b"", b""

        def kill(self) -> None:
            self._release_communication.set()
            raise OSError("simulated ffmpeg cleanup failure")

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(
                "ffmpeg",
                timeout if timeout is not None else 0,
            )

    processes: list[StuckProcess] = []

    def create_stuck_process(*_args: Any, **_kwargs: Any) -> StuckProcess:
        process = StuckProcess(10_000 + len(processes))
        processes.append(process)
        if len(processes) == 2:
            both_started.set()
        return process

    def reject_fallback_kill(_process_id: int, _signal: int) -> None:
        raise OSError("simulated fallback cleanup failure")

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", create_stuck_process)
    monkeypatch.setattr("hakimi_analysis.media.os.kill", reject_fallback_kill)
    temp_root = tmp_path / "runs"
    runtime_cleanup = RuntimeCleanupMonitor(
        temp_root,
        descendant_ffmpeg_probe=lambda: set(),
    )
    processor = LocalMediaProcessor(
        temp_root=temp_root,
        runtime_cleanup=runtime_cleanup,
        command_timeout_seconds=0.05 if termination == "timeout" else 60,
    )

    async def prepare() -> None:
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ):
            raise AssertionError("stuck extraction must not yield prepared media")

    task = asyncio.create_task(prepare())
    assert await asyncio.to_thread(both_started.wait, 1)
    if termination == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(MediaProcessingError, match="extraction cleanup failed"):
            await task

    assert len(processes) == 2
    snapshot = runtime_cleanup.snapshot()
    assert snapshot.ffmpeg_process_count == 2
    assert snapshot.clean is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "termination_delays",
    [(0.0, 0.0), (0.2, 0.05), (0.05, 0.2)],
    ids=["immediate", "slow-video-stop", "slow-audio-stop"],
)
async def test_command_timeout_stops_ffmpeg_and_removes_prepared_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination_delays: tuple[float, float],
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def create_blocking_process(*_args: Any, **_kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        real_kill = process.kill
        delay = termination_delays[len(processes)]

        def delayed_kill() -> None:
            time.sleep(delay)
            real_kill()

        monkeypatch.setattr(process, "kill", delayed_kill)
        processes.append(process)
        return process

    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: sys.executable)
    monkeypatch.setattr(subprocess, "Popen", create_blocking_process)
    temp_root = tmp_path / "runs"
    runtime_cleanup = RuntimeCleanupMonitor(
        temp_root,
        descendant_ffmpeg_probe=lambda: set(),
    )
    processor = LocalMediaProcessor(
        temp_root=temp_root,
        runtime_cleanup=runtime_cleanup,
        command_timeout_seconds=0.05,
    )

    with pytest.raises(MediaProcessingError, match="extraction timed out"):
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ):
            raise AssertionError("timed-out extraction must not yield prepared media")

    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)
    assert not any(temp_root.iterdir())
    assert runtime_cleanup.snapshot().clean is True


@pytest.mark.asyncio
async def test_kill_failure_uses_native_fallback_and_removes_prepared_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    children: list[subprocess.Popen[bytes]] = []
    waited_pids: list[int] = []
    real_popen = subprocess.Popen

    class KillFailsProcess:
        def __init__(self) -> None:
            self._child = real_popen(
                (sys.executable, "-c", "import time; time.sleep(30)"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            children.append(self._child)

        @property
        def returncode(self) -> int | None:
            return self._child.returncode

        @property
        def pid(self) -> int:
            return self._child.pid

        def poll(self) -> int | None:
            return self._child.poll()

        def communicate(self) -> tuple[bytes, bytes]:
            return self._child.communicate()

        def kill(self) -> None:
            raise OSError("simulated termination failure")

        def wait(self, timeout: float | None = None) -> int:
            waited_pids.append(self._child.pid)
            return self._child.wait(timeout=timeout)

    def create_unstoppable_process(*_args: Any, **_kwargs: Any) -> KillFailsProcess:
        return KillFailsProcess()

    monkeypatch.setattr(media_module, "PROCESS_STOP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: sys.executable)
    monkeypatch.setattr(subprocess, "Popen", create_unstoppable_process)
    processor = LocalMediaProcessor(
        temp_root=tmp_path / "runs",
        command_timeout_seconds=0.05,
    )

    async def prepare() -> None:
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ):
            raise AssertionError("timed-out extraction must not yield prepared media")

    with pytest.raises(MediaProcessingError, match="extraction timed out"):
        await asyncio.wait_for(prepare(), timeout=1)

    assert len(children) == 2
    assert sorted(waited_pids) == sorted(child.pid for child in children)
    assert all(child.returncode is not None for child in children)
    assert not any((tmp_path / "runs").iterdir())


@pytest.mark.asyncio
async def test_failed_extraction_stops_the_other_ffmpeg_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    processes: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen
    call_count = 0

    def create_test_process(*_args: Any, **_kwargs: Any) -> subprocess.Popen[bytes]:
        nonlocal call_count
        call_count += 1
        code = (
            "import sys, time; time.sleep(0.1); sys.exit(1)"
            if call_count == 1
            else "import time; time.sleep(30)"
        )
        process = real_popen(
            (sys.executable, "-c", code),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", create_test_process)
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")

    with pytest.raises(MediaProcessingError):
        async with processor.prepare(
            source,
            AnalysisWindow(start_seconds=0, end_seconds=1, expanded=False),
        ):
            raise AssertionError("failed extraction must not yield prepared media")

    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)
    assert not any((tmp_path / "runs").iterdir())


@pytest.mark.asyncio
async def test_visual_chunk_preserves_the_continuous_video_instead_of_a_sparse_sheet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    work = tmp_path / "work"
    work.mkdir()
    processor = LocalMediaProcessor(temp_root=tmp_path / "runs")
    extracted_windows: list[AnalysisWindow] = []

    async def extract_video(
        source_path: Path,
        output_path: Path,
        window: AnalysisWindow,
    ) -> None:
        assert source_path == source
        extracted_windows.append(window)
        output_path.write_bytes(b"continuous-video-chunk")

    async def reject_sparse_sheet(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("visual chunks must not be converted to sparse contact sheets")

    monkeypatch.setattr(processor, "_extract_video", extract_video)
    monkeypatch.setattr(processor, "_extract_contact_sheet", reject_sparse_sheet)
    window = AnalysisWindow(start_seconds=50, end_seconds=110, expanded=False)

    prepared = await processor.prepare_visual_chunk(
        source,
        window,
        work,
        source_offset_seconds=10,
    )

    assert prepared.video_path.read_bytes() == b"continuous-video-chunk"
    assert extracted_windows == [
        AnalysisWindow(start_seconds=60, end_seconds=120, expanded=False)
    ]
