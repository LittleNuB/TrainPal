"""Constructed responses with semantic cases and one observed short ASR phrase."""

import json

import httpx
import pytest
import respx

from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("timestamp_case", ["contained", "other_action", "straddles", "missing"])
@pytest.mark.parametrize(
    ("text", "duration", "expected", "action_name"),
    [
        ("训练后可加20分钟爬坡有氧", 20, 1200, "爬坡有氧"),
        ("晚可以加20分钟爬坡有氧。", 20, 1200, "爬坡有氧"),
        ("晚可以加最多20分钟爬坡有氧。", 20, 20, "爬坡有氧"),
        ("晚不可以加20分钟爬坡有氧。", 20, 20, "爬坡有氧"),
        ("训练后可加20分钟爬坡有氧", 1200, 1200, "爬坡有氧"),
        ("训练后可加20秒爬坡有氧", 20, 20, "爬坡有氧"),
        ("训练后可加20分钟爬坡有氧", None, None, "爬坡有氧"),
        ("先休息20分钟", 20, 20, "爬坡有氧"),
        ("做15到20分钟爬坡有氧", 20, 20, "爬坡有氧"),
        ("做20分钟有氧，休息30秒", 20, 20, "爬坡有氧"),
        ("不要做20分钟有氧", 20, 20, "爬坡有氧"),
        ("做1小时20分钟有氧", 20, 20, "爬坡有氧"),
        ("20分钟后开始有氧", 20, 20, "爬坡有氧"),
        ("这段视频长20分钟", 20, 20, "爬坡有氧"),
        ("先做20分钟跑步，再做爬坡有氧", 20, 20, "爬坡有氧"),
        ("爬坡有氧，跑步20分钟", 20, 20, "爬坡有氧"),
        ("最多20分钟爬坡有氧", 20, 20, "爬坡有氧"),
        ("接近20分钟爬坡有氧", 20, 20, "爬坡有氧"),
        ("爬坡有氧20分钟半", 20, 20, "爬坡有氧"),
        ("爬坡有氧20分钟上下", 20, 20, "爬坡有氧"),
        ("反向爬坡有氧20分钟", 20, 20, "爬坡有氧"),
        ("做20分钟休息", 20, 20, "休息"),
        ("做20分钟组间间歇", 20, 20, "组间间歇"),
        ("做20分钟Rest", 20, 20, "Rest"),
    ],
)
async def test_speech_duration_repairs_only_an_unambiguous_dropped_minute_unit(
    text: str, duration: int | None, expected: int | None, action_name: str, timestamp_case: str
) -> None:
    timestamp_cases: dict[str, dict[str, float]] = {
        "contained": {"start_seconds": 60.58, "end_seconds": 62.06},
        "other_action": {"start_seconds": 10, "end_seconds": 12},
        "straddles": {"start_seconds": 59, "end_seconds": 63},
        "missing": {},
    }
    timestamps = timestamp_cases[timestamp_case]
    respx.post("https://ark.example/api/v3/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "signals": [
                            {
                                "action_name": action_name,
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
                        **timestamps,
                    }
                ],
            },
            window=Segment(start_seconds=0, end_seconds=67.83),
            instructions="Extract only explicit training parameters in seconds.",
        )

    expected_value = expected if timestamp_case == "contained" else duration
    assert result.signals[0].duration_seconds == expected_value
    candidates = fuse_candidates(
        source_id="unit-review",
        speech_signals=result.signals,
        visual_segments=[],
    )
    assert candidates[0].parameters.duration_seconds == expected_value
