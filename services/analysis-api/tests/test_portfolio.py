from pathlib import Path
from typing import cast

import httpx
import pytest

from hakimi_analysis.ai_budget import AiBudgetError
from hakimi_analysis.app import create_app
from hakimi_analysis.playback import PlaybackService
from hakimi_analysis.sources import SourceCatalog, VideoSource


@pytest.mark.asyncio
async def test_subpath_cookie_sources_spa_refresh_and_range(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0123456789")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<main>portfolio</main>", encoding="utf-8")
    app = create_app(
        public_base_path="/trainpal",
        web_static_root=web,
        catalog=SourceCatalog(
            [VideoSource(id="fixture", title="Synthetic", path=media, duration_seconds=10)]
        ),
    )
    # The reverse proxy strips /trainpal before forwarding. The browser cookie
    # remains scoped to the public path, while ASGI admission sees /api/v1.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://site.test"
    ) as client:
        session = await client.get("/api/v1/access/session")
        assert "Path=/trainpal" in session.headers["set-cookie"]
        assert "Secure" in session.headers["set-cookie"]
        sources = (await client.get("/api/v1/sources")).json()
        assert sources[0]["media_url"] == "/trainpal/api/v1/sources/fixture/media"
        assert (await client.get("/plan")).text == "<main>portfolio</main>"
        selected = await client.get("/api/v1/sources/fixture/media", headers={"Range": "bytes=2-4"})
        assert selected.status_code == 206
        assert selected.content == b"234"
        assert selected.headers["content-range"] == "bytes 2-4/10"


@pytest.mark.asyncio
async def test_budget_error_is_actionable_without_exposing_provider_details() -> None:
    class ExhaustedPlayback:
        async def transcribe(self, body: bytes) -> None:
            raise AiBudgetError()

    app = create_app(playback_service=cast(PlaybackService, ExhaustedPlayback()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://site.test"
    ) as client:
        response = await client.post("/api/v1/playback-voice", content=b"fixture")
        assert response.status_code == 503
        assert response.json()["code"] == "ai_budget_exhausted"
        assert "继续编辑方案和训练" in response.json()["detail"]
