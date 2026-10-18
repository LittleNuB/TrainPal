"""Offline HTTP-boundary checks; these do not measure model recognition quality."""

import json
from typing import Any

import httpx
import pytest

from hakimi_analysis.exercise_references import ExerciseReferenceCatalog
from hakimi_analysis.models import SpeechSignal
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


async def run_fusion(
    *,
    enabled: bool,
    name: str = "反向飞鸟",
    accepted_tip_ids: list[str] | None = None,
    count: int = 1,
) -> tuple[SemanticFusionResult, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [f"speech-{i}"],
                                "name": name,
                                "relation": "same_demonstration",
                                "accepted_tip_ids": accepted_tip_ids or [],
                            }
                            for i in range(1, count + 1)
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test",
            http_client=client,
            retry_delays=(),
        )
        fusion = SemanticCandidateFusion(
            model=model,
            instructions="synthetic",
            reference_catalog=ExerciseReferenceCatalog.bundled() if enabled else None,
        )
        result = await fusion.run(
            source_id="synthetic",
            speech_signals=[
                SpeechSignal(
                    action_name=name,
                    start_seconds=i * 10,
                    end_seconds=i * 10 + 5,
                    evidence_text=name,
                    reps=12,
                    sets=4,
                )
                for i in range(count)
            ],
            visual_segments=[],
        )
    return result, requests


@pytest.mark.asyncio
async def test_opt_in_adds_bounded_reference_context_without_changing_source_output() -> None:
    baseline, baseline_requests = await run_fusion(enabled=False)
    assisted, assisted_requests = await run_fusion(enabled=True)
    assert assisted == baseline
    assert len(baseline_requests) == len(assisted_requests) == 1
    original = json.loads(baseline_requests[0]["input"][0]["content"][0]["text"])["observations"][0]
    enriched = json.loads(assisted_requests[0]["input"][0]["content"][0]["text"])["observations"][0]
    reference = enriched.pop("exercise_reference")
    assert enriched == original
    assert reference["reference_only"] is True
    assert reference["source_commit"] == ExerciseReferenceCatalog.bundled().source_commit
    assert len(reference["candidates"]) == 4
    assert set(reference["candidates"][0]) == {
        "source_id",
        "source_name",
        "name_zh",
        "equipment",
        "discriminators",
        "review_status",
    }
    assert "参考资料不是视频证据" in assisted_requests[0]["instructions"]


@pytest.mark.asyncio
async def test_unknown_variant_gets_no_generic_reference() -> None:
    result, requests = await run_fusion(enabled=True, name="单臂反向飞鸟")
    observation = json.loads(requests[0]["input"][0]["content"][0]["text"])["observations"][0]
    assert "exercise_reference" not in observation
    assert result.candidates[0].name == "单臂反向飞鸟"


@pytest.mark.asyncio
async def test_no_reference_match_leaves_the_entire_model_request_unchanged() -> None:
    _, baseline = await run_fusion(enabled=False, name="单臂反向飞鸟")
    _, assisted = await run_fusion(enabled=True, name="单臂反向飞鸟")
    assert assisted == baseline


@pytest.mark.asyncio
async def test_reference_id_cannot_be_selected_as_a_video_reminder() -> None:
    result, _ = await run_fusion(enabled=True, accepted_tip_ids=["0383"])
    assert result.candidates[0].tips == []
    assert result.candidates[0].parameters.reps == 12
    assert result.candidates[0].parameters.sets == 4
    assert result.candidates[0].segment.start_seconds == 0
    assert result.candidates[0].segment.end_seconds == 5


@pytest.mark.asyncio
async def test_enriched_input_budget_fails_closed_without_truncating_observations() -> None:
    baseline, baseline_requests = await run_fusion(enabled=False, count=40)
    assisted, assisted_requests = await run_fusion(enabled=True, count=40)
    assert len(baseline_requests) == 1
    assert assisted_requests == []
    assert assisted.diagnostics == {"unavailable_input_budget": 1}
    assert len(assisted.candidates) == len(baseline.candidates) == 40
    assert all(
        candidate.needs_confirmation and not candidate.tips for candidate in assisted.candidates
    )
    assert [c.segment for c in assisted.candidates] == [c.segment for c in baseline.candidates]
    assert [c.parameters for c in assisted.candidates] == [
        c.parameters for c in baseline.candidates
    ]
