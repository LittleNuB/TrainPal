import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from test_semantic_contiguous_teaching import teaching

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


def load_tool() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "fusion_comparison.py"
    spec = importlib.util.spec_from_file_location("fusion_comparison_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_same_input_review_proves_format_change_without_second_provider_request() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert calls == 1, "offline comparison must not send another Provider request"
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": ["visual-1", "visual-2"],
                                "name": "合成动作",
                                "relation": "same_demonstration",
                            }
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="synthetic",
                model_id="test",
                base_url="https://ark.test",
                http_client=client,
            ),
            instructions="Synthetic review",
        )
        audit = load_tool().SameInputFusionReview(fusion)
        result = await audit.run(
            source_id="synthetic",
            speech_signals=[],
            visual_segments=[teaching(0, 20, "4"), teaching(10, 30, "第四个动作")],
        )

    assert len(result.candidates) == 1
    assert calls == 1
    assert audit.receipt["baseline"]["candidates"] == 2
    assert audit.receipt["current"]["candidates"] == 1
    assert audit.receipt["label_shapes"] == {"format_equivalence_resolved": 1}
    assert "第四个动作" not in json.dumps(audit.receipt, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels,counts,candidates",
    [
        (["动作四", "#4"], {}, 1),
        (["第四个动作", "第5个动作"], {"different_parsed_numbers": 1}, 2),
        (["private-left", "private-right"], {"unparsed_identity_present": 1}, 2),
        (["4", "第4轮"], {"unparsed_identity_present": 1}, 2),
        (
            ["4", "5", "private-label"],
            {"different_parsed_numbers": 1, "unparsed_identity_present": 1},
            3,
        ),
    ],
)
async def test_review_classifies_identity_shapes_without_saving_labels(
    labels: list[str],
    counts: dict[str, int],
    candidates: int,
) -> None:
    response = {
        "groups": [
            {
                "member_ids": [f"visual-{i + 1}" for i in range(len(labels))],
                "name": "private-model-name",
                "relation": "same_demonstration",
            }
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps(response)}),
        )
    ) as client:
        audit = load_tool().SameInputFusionReview(
            SemanticCandidateFusion(
                model=ArkResponsesClient(
                    api_key="synthetic",
                    model_id="test",
                    base_url="https://ark.test",
                    http_client=client,
                ),
                instructions="synthetic",
            )
        )
        result = await audit.run(
            source_id="private-source",
            speech_signals=[],
            visual_segments=[teaching(0, 20, label) for label in labels],
        )
    assert len(result.candidates) == candidates
    assert audit.receipt["baseline"]["candidates"] == candidates
    assert audit.receipt["same_candidates"] is True
    assert audit.receipt["label_shapes"] == counts
    assert "private-" not in json.dumps(audit.receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["cancel", "timeout", "provider", "membership", "budget"])
async def test_review_failure_is_honest_and_cannot_accidentally_rerun(case: str) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if case == "cancel":
            raise asyncio.CancelledError
        if case == "timeout":
            raise TimeoutError
        if case == "provider":
            return httpx.Response(503)
        if case == "budget":
            raise AssertionError("budget failure must not request a model")
        return httpx.Response(200, json={"output_text": json.dumps({"groups": []})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        audit = load_tool().SameInputFusionReview(
            SemanticCandidateFusion(
                model=ArkResponsesClient(
                    api_key="synthetic",
                    model_id="test",
                    base_url="https://ark.test",
                    http_client=client,
                    retry_delays=(),
                ),
                instructions="synthetic",
            )
        )
        args = dict(
            source_id="synthetic",
            speech_signals=[],
            visual_segments=[teaching(0, 20)],
            remaining_seconds=0 if case == "budget" else 5,
        )
        if case == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await audit.run(**args)
            assert audit.receipt == {}
        else:
            result = await audit.run(**args)
            assert len(result.candidates) == 1
            assert result.candidates[0].needs_confirmation
            if case == "membership":
                assert audit.receipt["label_shapes_available"] is False
                assert audit.receipt["label_shapes"] == {}
            else:
                assert audit.receipt["comparison"] == "unavailable"
                assert "baseline" not in audit.receipt
        with pytest.raises(RuntimeError, match="review_already_used"):
            await audit.run(**args)


@pytest.mark.asyncio
async def test_cancel_restores_the_normal_fusion_entry_point() -> None:
    calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": ["visual-1"],
                                "name": "合成动作",
                                "relation": "same_demonstration",
                            }
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="synthetic",
                model_id="test",
                base_url="https://ark.test",
                http_client=client,
            ),
            instructions="synthetic",
        )
        audit = load_tool().SameInputFusionReview(fusion)
        with pytest.raises(asyncio.CancelledError):
            await audit.run(
                source_id="synthetic", speech_signals=[], visual_segments=[teaching(0, 20)]
            )
        result = await fusion.run(
            source_id="synthetic", speech_signals=[], visual_segments=[teaching(0, 20)]
        )
    assert len(result.candidates) == 1
    assert calls == 2


@pytest.mark.asyncio
async def test_all_comparison_counters_stay_bounded_at_maximum_input() -> None:
    groups = [
        {
            "member_ids": [f"visual-{i}", f"visual-{i + 1}"],
            "name": "合成动作",
            "relation": "same_demonstration",
        }
        for i in range(1, 201, 2)
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"output_text": json.dumps({"groups": groups})},
            )
        )
    ) as client:
        audit = load_tool().SameInputFusionReview(
            SemanticCandidateFusion(
                model=ArkResponsesClient(
                    api_key="synthetic",
                    model_id="test",
                    base_url="https://ark.test",
                    http_client=client,
                ),
                instructions="synthetic",
            )
        )
        await audit.run(
            source_id="synthetic",
            speech_signals=[],
            visual_segments=[teaching(0, 20, label) for label in ["4", "第四个动作"] * 100],
        )
    assert audit.receipt["label_shapes"] == {"format_equivalence_resolved": 100}
    assert audit.receipt["baseline"]["candidates"] == 200
    assert audit.receipt["current"]["candidates"] == 100
