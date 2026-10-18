"""Malformed model time ranges must fail at the Provider boundary, not in fusion."""

import json
from pathlib import Path

import httpx
import pytest

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(5, 5), (8, 5)])
@pytest.mark.parametrize("input_kind", ["video", "contact_sheet"])
async def test_invalid_visual_time_range_cannot_escape_to_candidate_fusion(
    tmp_path: Path, start: float, end: float, input_kind: str
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic-media")

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "segments": [
                            {
                                "action_name": "合成动作",
                                "start_seconds": start,
                                "end_seconds": end,
                                "visual_cue": "合成观察",
                            }
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
        )
        with pytest.raises(ProviderSchemaError):
            if input_kind == "video":
                await provider.locate_visual(
                    video_path=video,
                    window=Segment(start_seconds=0, end_seconds=10),
                    instructions="synthetic",
                )
            else:
                await provider.locate_visual_contact_sheet(
                    image_path=video,
                    frame_times_seconds=(0, 5),
                    window=Segment(start_seconds=0, end_seconds=10),
                    instructions="synthetic",
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(5, 5), (8, 5)])
@pytest.mark.parametrize("select_quotes", [False, True])
async def test_invalid_speech_time_range_cannot_escape_to_candidate_fusion(
    start: float, end: float, select_quotes: bool
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "signals": [
                            {
                                "action_name": "合成动作",
                                "start_seconds": start,
                                "end_seconds": end,
                                "evidence_text": "合成证据",
                            }
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            select_speech_quotes=select_quotes,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.understand_speech(
                transcript={"utterances": []},
                window=Segment(start_seconds=0, end_seconds=10),
                instructions="synthetic",
            )
