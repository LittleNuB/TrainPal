import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import imageio_ffmpeg

from hakimi_analysis.settings import PROJECT_ROOT

E2E_SOURCE_DURATIONS = (2, 3, 4, 5, 6)


async def create_video(
    output: Path, *, duration_seconds: int = 4, dimensions: str = "390x694"
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x52bac9:s={dimensions}:r=15,drawgrid=w=iw/6:h=ih/10:t=2:c=white@0.4",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=16000",
        "-t",
        str(duration_seconds),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-y",
        str(output),
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("synthetic video generation failed") from RuntimeError(
            stderr.decode("utf-8", errors="replace")
        )


async def prepare_e2e_media() -> None:
    output_root = PROJECT_ROOT / "tmp" / "e2e-sources"
    manifest_path = PROJECT_ROOT / "tmp" / "e2e-source-manifest.json"
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, object]] = []

    for index, duration_seconds in enumerate(E2E_SOURCE_DURATIONS, start=1):
        source_id = f"e2e-source-{index:02d}"
        output = output_root / f"{source_id}.mp4"
        await create_video(output, duration_seconds=duration_seconds)
        sources.append(
            {
                "id": source_id,
                "title": f"Private E2E source {index:02d}",
                "media_path": output.name,
                "duration_seconds": duration_seconds,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )

    manifest_path.write_text(
        json.dumps({"version": 1, "sources": sources}, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(output_root / "e2e-source-03.mp4", PROJECT_ROOT / "tmp" / "e2e-source.mp4")
    await create_video(PROJECT_ROOT / "tmp" / "e2e-landscape.mp4", dimensions="960x540")


if __name__ == "__main__":
    asyncio.run(prepare_e2e_media())
