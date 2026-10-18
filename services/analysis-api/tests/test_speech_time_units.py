"""Constructed responses with semantic cases and one observed short ASR phrase."""

import json

import httpx
import pytest

from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_name", ["爬坡有氧", "爬坡有氧（20分钟）", "20分钟爬坡有氧", "爬坡有氧(20分钟)"]
)
@pytest.mark.parametrize("timestamp_case", ["contained", "other_action", "straddles", "missing"])
@pytest.mark.parametrize(
    ("text", "duration", "expected"),
    [
        ("训练后可加20分钟爬坡有氧", 20, 1200),
        ("晚可以加20分钟爬坡有氧。", 20, 1200),
        ("晚可以加最多20分钟爬坡有氧。", 20, 20),
        ("晚不可以加20分钟爬坡有氧。", 20, 20),
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
        ("最多20分钟爬坡有氧", 20, 20),
        ("接近20分钟爬坡有氧", 20, 20),
        ("爬坡有氧20分钟半", 20, 20),
        ("爬坡有氧20分钟上下", 20, 20),
        ("反向爬坡有氧20分钟", 20, 20),
    ],
)
async def test_speech_duration_repairs_only_an_unambiguous_dropped_minute_unit(
    text: str, duration: int | None, expected: int | None, timestamp_case: str, action_name: str
) -> None:
    timestamp_cases: dict[str, dict[str, float]] = {
        "contained": {"start_seconds": 60.58, "end_seconds": 62.06},
        "other_action": {"start_seconds": 10, "end_seconds": 12},
        "straddles": {"start_seconds": 59, "end_seconds": 63},
        "missing": {},
    }
    timestamps = timestamp_cases[timestamp_case]
    response = httpx.Response(
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
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as http_client:
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "action_name"),
    [
        ("训练后可加20分钟爬坡有氧", "爬坡有氧（30分钟）"),
        ("训练后可加20分钟爬坡有氧", "30分钟爬坡有氧"),
        ("训练后可加20分钟爬坡有氧", "爬坡有氧（20秒）"),
        ("训练后可加20分钟爬坡有氧", "20秒爬坡有氧"),
        ("训练后可加20分钟爬坡有氧", "爬坡有氧（最多20分钟）"),
        ("训练后可加20分钟爬坡有氧", "反向爬坡有氧（20分钟）"),
        ("训练后可加20分钟爬坡有氧", "爬坡有氧（20分钟）再跑步"),
        ("训练后可加20分钟爬坡有氧", "爬坡有氧（20分钟)"),
        ("训练后可加20分钟爬坡有氧", "爬坡有氧（20分钟）（20分钟）"),
        ("做20分钟休息", "休息（20分钟）"),
        ("做20分钟间歇", "20分钟间歇"),
        ("做20分钟组间停顿", "组间停顿(20分钟)"),
        ("做20分钟Rest", "Rest（20分钟）"),
    ],
)
async def test_a_duration_label_cannot_override_asr_action_identity_or_units(
    text: str,
    action_name: str,
) -> None:
    response = httpx.Response(
        200,
        json={
            "output_text": json.dumps(
                {
                    "signals": [
                        {
                            "action_name": action_name,
                            "duration_seconds": 20,
                            "start_seconds": 60.58,
                            "end_seconds": 62.06,
                            "evidence_text": "训练后可加20分钟爬坡有氧",
                        }
                    ]
                }
            )
        },
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as http_client:
        provider = ArkResponsesClient(
            api_key="test-key",
            model_id="test-model",
            base_url="https://ark.example/api/v3",
            http_client=http_client,
        )
        result = await provider.understand_speech(
            transcript={
                "utterances": [
                    {
                        "text": text,
                        "start_seconds": 60.58,
                        "end_seconds": 62.06,
                    }
                ]
            },
            window=Segment(start_seconds=0, end_seconds=67.83),
            instructions="Extract only explicit training parameters in seconds.",
        )
    assert result.signals[0].duration_seconds == 20
    assert result.signals[0].action_name == action_name
    candidates = fuse_candidates(
        source_id="unit-review",
        speech_signals=result.signals,
        visual_segments=[],
    )
    assert candidates[0].parameters.duration_seconds == 20
