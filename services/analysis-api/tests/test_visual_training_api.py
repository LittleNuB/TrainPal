"""Synthetic evidence at the HTTP seam; never retains real user media."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_analysis_api import make_test_access, source_catalog, wait_for_status
from test_orchestrated_pipeline import EmptyAsr, FakeAsr, FakeMediaProcessor

from hakimi_analysis.app import create_app
from hakimi_analysis.orchestration import OrchestratedAnalysisPipeline, SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.settings import PROJECT_ROOT


async def analyze_visual(
    tmp_path: Path,
    segments: list[dict[str, Any]],
    signals: list[dict[str, Any]] | None = None,
    *,
    check_visual_schema: bool = False,
    following_chunks: list[list[dict[str, Any]]] | None = None,
    source_duration_seconds: float = 54,
) -> list[dict[str, Any]]:
    chunks = [segments, *(following_chunks or [])]
    for index in range(1, len(chunks) + 1):
        (tmp_path / f"chunk-{index}.mp4").write_bytes(b"synthetic-video")
    visual_responses = iter(chunks)

    def respond(request: httpx.Request) -> httpx.Response:
        if check_visual_schema:
            schema = json.loads(request.content)["text"]["format"]["schema"]
            for definition in schema["$defs"].values():
                if "properties" in definition:
                    assert set(definition["required"]) == set(definition["properties"])
        is_speech = (
            json.loads(request.content)["text"]["format"]["name"] == "training_speech_understanding"
        )
        payload = (
            {"signals": signals or []}
            if is_speech
            else {"segments": next(visual_responses)}
        )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(payload)}],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider_http:
        pipeline = OrchestratedAnalysisPipeline(
            media=FakeMediaProcessor(tmp_path),
            asr=FakeAsr() if signals else EmptyAsr(),
            ark=ArkResponsesClient(
                api_key="test",
                model_id="test",
                base_url="https://provider.test",
                http_client=provider_http,
                retry_delays=(),
            ),
            skills=SkillRepository.load(PROJECT_ROOT / "skills"),
        )
        app = create_app(
            catalog=source_catalog(tmp_path, duration_seconds=source_duration_seconds),
            pipeline=pipeline,
            access=make_test_access(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            created = await client.post(
                "/api/v1/analysis-runs", json={"source_id": "legacy-arm-workout"}
            )
            result = await wait_for_status(client, created.json()["id"], "completed")
        assert result["coverage_status"] == "complete"
        return result["candidates"]  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_visible_training_parameters_survive_the_analysis_api(tmp_path: Path) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [
            {
                "action_name": "反握腕弯举",
                "start_seconds": 5,
                "end_seconds": 10,
                "visual_cue": "字幕明确标注训练要求",
                "segment_role": "teaching_demo",
                "text_parameters": {"mode": "reps", "sets": 3, "reps": 10, "reps_max": 12},
            }
        ],
    )
    assert len(candidates) == 1
    assert candidates[0]["parameters"] == {
        "mode": "reps",
        "sets": 3,
        "reps": 10,
        "reps_max": 12,
        "duration_seconds": None,
        "rest_seconds": None,
    }
    assert candidates[0]["needs_confirmation"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("first_name,later_name", [
    ("腕弯举", "腕弯举"), ("腕弯举", "Wrist Curl"), ("Wrist Curl", "腕弯举"),
])
async def test_overlapping_video_chunks_keep_complementary_training_parameters(
    tmp_path: Path, first_name: str, later_name: str,
) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [{
            "action_name": first_name,
            "start_seconds": 50,
            "end_seconds": 59,
            "visual_cue": "画面标注三组",
            "text_parameters": {"mode": "reps", "sets": 3},
        }],
        following_chunks=[[{
            "action_name": later_name,
            "start_seconds": 5,
            "end_seconds": 15,
            "visual_cue": "画面随后标注十至十二次，休息六十秒",
            "text_parameters": {
                "mode": "reps", "reps": 10, "reps_max": 12, "rest_seconds": 60,
            },
        }]],
        source_duration_seconds=70,
    )
    assert len(candidates) == 1
    assert candidates[0]["name"] == "腕弯举"
    assert candidates[0]["segment"] == {"start_seconds": 50, "end_seconds": 65}
    assert candidates[0]["parameters"] == {
        "mode": "reps", "sets": 3, "reps": 10, "reps_max": 12,
        "duration_seconds": None, "rest_seconds": 60,
    }
    assert candidates[0]["needs_confirmation"] is True


@pytest.mark.asyncio
async def test_conflicting_video_chunks_preserve_both_values_and_require_confirmation(
    tmp_path: Path,
) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [{
            "action_name": "腕弯举", "start_seconds": 42, "end_seconds": 59,
            "visual_cue": "两组", "segment_role": "teaching_demo",
            "text_parameters": {"mode": "reps", "sets": 2, "reps": 10},
        }],
        [{
            "action_name": "腕弯举", "start_seconds": 42, "end_seconds": 49,
            "evidence_text": "两组十次", "segment_role": "teaching_demo",
            "sets": 2, "reps": 10,
        }],
        following_chunks=[[{
            "action_name": "腕弯举", "start_seconds": 5, "end_seconds": 15,
            "visual_cue": "三组", "segment_role": "teaching_demo",
            "text_parameters": {"mode": "reps", "sets": 3, "reps": 10},
        }]],
        source_duration_seconds=70,
    )
    assert [candidate["parameters"]["sets"] for candidate in candidates] == [2, 3]
    assert all(candidate["needs_confirmation"] for candidate in candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("later_name", ["反向腕弯举", "Zottman Wrist Curl"])
async def test_action_modifiers_are_not_erased_by_cross_chunk_name_matching(
    tmp_path: Path, later_name: str,
) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [{
            "action_name": "Wrist Curl", "start_seconds": 50, "end_seconds": 59,
            "visual_cue": "第一种握法",
        }],
        following_chunks=[[{
            "action_name": later_name, "start_seconds": 5, "end_seconds": 15,
            "visual_cue": "不同握法",
        }]],
        source_duration_seconds=70,
    )
    assert [candidate["name"] for candidate in candidates] == ["腕弯举", later_name]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_parameters,later_parameters,expected", [
    ({"mode": "duration", "sets": 3}, {"mode": "duration", "duration_seconds": 30},
     [{"mode": "duration", "sets": 3, "duration_seconds": 30}]),
    ({"mode": "reps", "reps": 10}, {"mode": "duration", "duration_seconds": 30},
     [{"mode": "reps", "reps": 10}, {"mode": "duration", "duration_seconds": 30}]),
    ({"mode": "reps", "reps": 10, "reps_max": 12},
     {"mode": "reps", "reps": 10, "reps_max": 15},
     [{"mode": "reps", "reps": 10, "reps_max": 12},
      {"mode": "reps", "reps": 10, "reps_max": 15}]),
    ({"mode": "reps", "reps": 10}, {"duration_seconds": 30},
     [{"mode": "reps", "reps": 10}, {"duration_seconds": 30}]),
])
async def test_cross_chunk_merge_preserves_modes_and_conflicting_ranges(
    tmp_path: Path, first_parameters: dict[str, Any], later_parameters: dict[str, Any],
    expected: list[dict[str, Any]],
) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [{
            "action_name": "腕弯举", "start_seconds": 50, "end_seconds": 59,
            "visual_cue": "前半段参数", "text_parameters": first_parameters,
        }],
        following_chunks=[[{
            "action_name": "腕弯举", "start_seconds": 5, "end_seconds": 15,
            "visual_cue": "后半段参数", "text_parameters": later_parameters,
        }]],
        source_duration_seconds=70,
    )
    assert [
        {key: value for key, value in candidate["parameters"].items() if value is not None}
        for candidate in candidates
    ] == expected
    assert all(candidate["needs_confirmation"] for candidate in candidates)


@pytest.mark.asyncio
async def test_promotional_speech_does_not_become_a_training_action(tmp_path: Path) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [],
        [
            {
                "action_name": "平台提示",
                "start_seconds": 42,
                "end_seconds": 44,
                "evidence_text": "推广片尾",
                "is_training_content": False,
            }
        ],
    )
    assert candidates == []


@pytest.mark.asyncio
async def test_visual_request_requires_explicit_classification_and_naming(tmp_path: Path) -> None:
    await analyze_visual(tmp_path, [], check_visual_schema=True)


@pytest.mark.asyncio
async def test_numbered_sections_remain_separate_and_intro_is_excluded(tmp_path: Path) -> None:
    segments = [
        {
            "action_name": "Wrist Curl",
            "start_seconds": 0,
            "end_seconds": 5,
            "visual_cue": "opening teaser",
            "is_training_content": False,
        }
    ]
    for index, (start, end) in enumerate([(5, 10), (10, 16), (16, 26), (26, 36)], 1):
        segments.append(
            {
                "action_name": "Wrist Curl",
                "start_seconds": start,
                "end_seconds": end,
                "visual_cue": "separately numbered demonstration",
                "sequence_label": str(index),
            }
        )
    candidates = await analyze_visual(tmp_path, segments)
    assert [(c["name"], c["segment"]) for c in candidates] == [
        ("腕弯举", {"start_seconds": 5, "end_seconds": 10}),
        ("腕弯举", {"start_seconds": 10, "end_seconds": 16}),
        ("腕弯举", {"start_seconds": 16, "end_seconds": 26}),
        ("腕弯举", {"start_seconds": 26, "end_seconds": 36}),
    ]


@pytest.mark.asyncio
async def test_speech_name_does_not_erase_visible_parameters(tmp_path: Path) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [
            {
                "action_name": "拖拽弯举",
                "start_seconds": 41,
                "end_seconds": 51,
                "visual_cue": "明确字幕",
                "segment_role": "teaching_demo",
                "text_parameters": {"mode": "reps", "sets": 3, "reps": 10, "reps_max": 12},
            }
        ],
        [
            {
                "action_name": "拖拽弯举",
                "start_seconds": 42,
                "end_seconds": 49,
                "evidence_text": "拖拽弯举",
                "segment_role": "teaching_demo",
            }
        ],
    )
    assert candidates[0]["parameters"]["sets"] == 3
    assert candidates[0]["parameters"]["reps_max"] == 12


@pytest.mark.asyncio
async def test_conflicting_spoken_and_visible_values_require_confirmation(tmp_path: Path) -> None:
    candidates = await analyze_visual(
        tmp_path,
        [
            {
                "action_name": "拖拽弯举",
                "start_seconds": 41,
                "end_seconds": 51,
                "visual_cue": "明确字幕",
                "segment_role": "teaching_demo",
                "text_parameters": {"mode": "reps", "sets": 3, "reps": 12},
            }
        ],
        [
            {
                "action_name": "拖拽弯举",
                "start_seconds": 42,
                "end_seconds": 49,
                "evidence_text": "两组八次",
                "sets": 2,
                "reps": 8,
                "segment_role": "teaching_demo",
            }
        ],
    )
    assert candidates[0]["parameters"]["reps"] == 8
    assert candidates[0]["needs_confirmation"] is True
