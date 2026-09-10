"""Semantic reproductions, not retained historical Provider responses."""

import json

import httpx
import pytest
import respx

from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("text", "duration", "expected"),
    [
        ("训练后可加20分钟爬坡有氧", 20, 1200),
        ("训练后可加20分钟爬坡有氧", 1200, 1200),
        ("训练后可加20秒爬坡有氧", 20, 20),
        ("训练后可加20分钟爬坡有氧", None, None),
        ("先休息20分钟", 20, 20),
        ("做15到20分钟爬坡有氧", 20, 20),
        ("做20分钟有氧，休息30秒", 20, 20),
        ("不要做20分钟有氧", 20, 20),
        ("做1小时20分钟有氧", 20, 20),
        ("20分钟后开始有氧", 20, 20),
        ("这段视频长20分钟", 20, 20),
        ("先做20分钟跑步，再做爬坡有氧", 20, 20),
        ("爬坡有氧，跑步20分钟", 20, 20),
    ],
)
async def test_speech_duration_repairs_only_an_unambiguous_dropped_minute_unit(
    text: str, duration: int | None, expected: int | None
) -> None:
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "signals": [
                            {
                                "action_name": "爬坡有氧",
                                "duration_seconds": duration,
                                "start_seconds": 60.58,
                                "end_seconds": 62.06,
                                "evidence_text": "可加20分钟爬坡有氧",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        provider = ArkResponsesClient(
            api_key="test-key",
            model_id="test-model",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await provider.understand_speech(
            transcript={
                "text": text,
                "utterances": [
                    {
                        "text": text,
                        "start_seconds": 60.58,
                        "end_seconds": 62.06,
                    }
                ],
            },
            window=Segment(start_seconds=0, end_seconds=67.83),
            instructions="Extract only explicit training parameters in seconds.",
        )

    assert result.signals[0].duration_seconds == expected
    candidates = fuse_candidates(
        source_id="unit-review",
        speech_signals=result.signals,
        visual_segments=[],
    )
    assert candidates[0].parameters.duration_seconds == expected
