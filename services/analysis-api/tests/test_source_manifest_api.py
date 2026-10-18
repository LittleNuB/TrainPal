import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import build_catalog
from hakimi_analysis.pipeline import PipelineOutput
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import (
    SourceCatalog,
    SourceManifestError,
    VideoSource,
    load_source_manifest,
)


class EmptyPipeline:
    async def analyze(
        self,
        source: VideoSource,
        trigger_seconds: float,
        emit: object,
    ) -> PipelineOutput:
        return PipelineOutput(candidates=[], empty_reason="no_evidence")


def test_competition_manifest_registers_five_neutral_opaque_sources_in_duration_order() -> None:
    manifest_path = Path(__file__).parents[3] / "competition" / "media-manifest.json"

    manifest = load_source_manifest(manifest_path)
    serialized = manifest_path.read_text(encoding="utf-8")
    raw_manifest = json.loads(serialized)

    assert len(manifest.sources) == 5
    assert all(
        set(source) == {"id", "title", "media_path", "duration_seconds", "sha256"}
        for source in raw_manifest["sources"]
    )
    assert {source.title for source in manifest.sources} == {"快速体验视频"}
    assert [source.duration_seconds for source in manifest.sources] == [
        54.87,
        67.83,
        100.43,
        108.72,
        207.77,
    ]
    assert [source.sha256 for source in manifest.sources] == [
        "c78463a5837f3340e21ff28904b6ad20a858e1b6229351324280c2923af37584",
        "c3c670e2fb4602806a2ac77c20a18cf2a12747acacd184e3f2de6946410d09a5",
        "33e2013ce0ecec2633d04a90a47954dcf5b71a289c0a10fe43dbe3cd72d065e3",
        "8e29cf4ecf244d4c9fcb89a0f6342706a56dd7a96df49ac83e29cec1034d89a5",
        "6a799122757fcc7bcfa8ec52076891e51e3fb6879eb54dd6b81262bd0b7df9f4",
    ]
    assert all(re.fullmatch(r"[0-9a-f]{24}", source.id) for source in manifest.sources)
    assert all(
        re.fullmatch(r"media/[0-9a-f]{32}\.mp4", source.media_path)
        for source in manifest.sources
    )
    assert "video1" not in serialized
    assert "健身视频" not in serialized
    assert all(token not in serialized for token in ("-68s", "-100s", "-108s", "-208s"))


def write_manifest(tmp_path: Path, *, media_path: str = "competition/arm-01.mp4") -> Path:
    media_root = tmp_path / "media"
    source_path = media_root / "competition" / "arm-01.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"team-owned-video")
    second_source_path = media_root / "competition" / "arm-02.mp4"
    second_source_path.write_bytes(b"team-owned-video-2")
    manifest_path = tmp_path / "sources.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "arm-01",
                        "title": "手臂训练 01",
                        "media_path": media_path,
                        "duration_seconds": 54.4,
                        "sha256": hashlib.sha256(b"team-owned-video").hexdigest(),
                        "origin_url": "https://www.douyin.com/video/123",
                    },
                    {
                        "id": "arm-02",
                        "title": "手臂训练 02",
                        "media_path": "competition/arm-02.mp4",
                        "duration_seconds": 48,
                        "sha256": hashlib.sha256(b"team-owned-video-2").hexdigest(),
                        "origin_url": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", ["test", "production"])
async def test_manifest_sources_expose_origin_and_redirect_to_controlled_cdn(
    tmp_path: Path,
    app_env: str,
) -> None:
    manifest_path = write_manifest(tmp_path)
    catalog = SourceCatalog.from_manifest(
        manifest_path=manifest_path,
        media_root=tmp_path / "media",
        public_media_base_url="https://media.example.com/hachimi/",
        duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
    )
    app = create_app(catalog=catalog, pipeline=EmptyPipeline(), app_env=app_env)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    ) as client:
        sources = await client.get("/api/v1/sources")
        media = await client.get("/api/v1/sources/arm-01/media")

    assert sources.json() == [
        {
            "id": "arm-01",
            "title": "手臂训练 01",
            "media_url": "/api/v1/sources/arm-01/media",
            "duration_seconds": 54.4,
            "origin_url": "https://www.douyin.com/video/123",
        },
        {
            "id": "arm-02",
            "title": "手臂训练 02",
            "media_url": "/api/v1/sources/arm-02/media",
            "duration_seconds": 48.0,
            "origin_url": None,
        },
    ]
    assert media.status_code == 307
    assert media.headers["location"] == "https://media.example.com/hachimi/competition/arm-01.mp4"


async def test_development_manifest_media_plays_from_the_validated_local_cache(
    tmp_path: Path,
) -> None:
    catalog = SourceCatalog.from_manifest(
        manifest_path=write_manifest(tmp_path),
        media_root=tmp_path / "media",
        public_media_base_url="https://recording.invalid/media",
        duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
    )
    app = create_app(catalog=catalog, app_env="development")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:15180",
        follow_redirects=False,
    ) as client:
        sources = (await client.get("/api/v1/sources")).json()
        media = await client.get(sources[0]["media_url"], headers={"Range": "bytes=0-3"})
        missing = await client.get("/api/v1/sources/not-in-manifest/media")

    assert media.status_code == 206
    assert media.content == b"team"
    assert media.headers["content-type"] == "video/mp4"
    assert "location" not in media.headers
    assert missing.status_code == 404


def test_manifest_rejects_media_longer_than_full_source_boundary(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sources"][0]["duration_seconds"] = 300
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceManifestError):
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 300.1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manifest_configuration",
    [
        {},
        {
            "source_manifest_path": "missing-sources.json",
            "source_media_root": "media",
        },
        {
            "source_manifest_path": "missing-sources.json",
            "source_media_root": "media",
            "public_media_base_url": "https://media.example.com/hachimi/",
        },
    ],
)
async def test_production_never_exposes_legacy_media_when_manifest_is_unavailable(
    tmp_path: Path,
    manifest_configuration: dict[str, Any],
) -> None:
    legacy_path = tmp_path / "legacy-unlicensed.mp4"
    legacy_path.write_bytes(b"legacy-local-video")
    resolved_configuration = {
        key: tmp_path / value if key != "public_media_base_url" else value
        for key, value in manifest_configuration.items()
    }
    settings = Settings(
        _env_file=None,
        app_env="production",
        hakimi_demo_video_path=legacy_path,
        **resolved_configuration,
    )
    catalog = build_catalog(settings)
    app = create_app(catalog=catalog, pipeline=EmptyPipeline(), app_env="production")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
    ) as client:
        sources = await client.get("/api/v1/sources")
        media = await client.get("/api/v1/sources/legacy-arm-workout/media")

    assert catalog.manifest_backed is False
    assert sources.status_code == 200
    assert sources.json() == []
    assert media.status_code == 404


def test_manifest_rejects_media_paths_outside_the_controlled_root(tmp_path: Path) -> None:
    manifest_path = write_manifest(tmp_path, media_path="../private.mp4")

    with pytest.raises(SourceManifestError):
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
        )


def test_manifest_rejects_origin_url_userinfo_without_echoing_credentials(
    tmp_path: Path,
) -> None:
    manifest_path = write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sources"][0]["origin_url"] = "https://demo-user:demo-password@www.douyin.com/video/123"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceManifestError) as failure:
        SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=tmp_path / "media",
            public_media_base_url="https://media.example.com/hachimi/",
            duration_probe=lambda path: 48 if path.name == "arm-02.mp4" else 54.4,
        )

    assert "demo-user" not in str(failure.value)
    assert "demo-password" not in str(failure.value)
