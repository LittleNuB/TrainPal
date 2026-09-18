import asyncio
import json

import httpx
import pytest

from hakimi_analysis.models import CandidateParameters, SegmentRole, SpeechSignal, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


@pytest.mark.asyncio
async def test_content_decisions_exclude_setup_and_recap_but_keep_warmup_and_later_execution() -> (
    None
):
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        assert [item["id"] for item in payload["observations"]] == [
            "visual-1",
            "visual-3",
            "visual-4",
            "visual-6",
        ]
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": ["visual-1"],
                                "name": "肩部热身",
                                "relation": "same_demonstration",
                            },
                            {"member_ids": ["visual-3"], "name": None, "relation": "uncertain"},
                            {
                                "member_ids": ["visual-4"],
                                "name": "坐姿划船",
                                "relation": "same_demonstration",
                            },
                            {
                                "member_ids": ["visual-6"],
                                "name": "坐姿划船",
                                "relation": "same_demonstration",
                            },
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test",
                model_id="text",
                base_url="https://ark.test",
                http_client=client,
            ),
            instructions="Group supplied evidence; keep distinct executions separate.",
        )
        result = await fusion.run(
            source_id="role-fixture",
            speech_signals=[],
            visual_segments=[
                VisualSegment(
                    action_name="肩部热身",
                    start_seconds=0,
                    end_seconds=5,
                    visual_cue="开头已在示范完整热身运动",
                    is_training_content=True,
                ),
                VisualSegment(
                    action_name="调整配重",
                    start_seconds=5,
                    end_seconds=8,
                    visual_cue="仅更换插销，没有示范动作",
                    is_training_content=False,
                ),
                VisualSegment(
                    action_name=None,
                    start_seconds=8,
                    end_seconds=10,
                    visual_cue="运动身份不清楚",
                    is_training_content=True,
                ),
                VisualSegment(
                    action_name="坐姿划船",
                    start_seconds=10,
                    end_seconds=20,
                    visual_cue="第一轮真实执行",
                    is_training_content=True,
                ),
                VisualSegment(
                    action_name="坐姿划船",
                    start_seconds=21,
                    end_seconds=24,
                    visual_cue="总结蒙太奇复用此前画面",
                    is_training_content=False,
                ),
                VisualSegment(
                    action_name="坐姿划船",
                    start_seconds=30,
                    end_seconds=40,
                    visual_cue="下一轮真实执行",
                    is_training_content=True,
                ),
            ],
        )

    assert [(item.name, item.segment.start_seconds) for item in result.candidates] == [
        ("肩部热身", 0),
        ("待确认动作", 8),
        ("坐姿划船", 10),
        ("坐姿划船", 30),
    ]
    assert result.candidates[1].needs_confirmation is True


@pytest.mark.asyncio
async def test_semantic_fusion_unites_different_names_and_preserves_both_sources() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["store"] is False
        assert [part["type"] for part in body["input"][0]["content"]] == ["input_text"]
        assert "private transcript fragment" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": ["speech-1", "visual-1"],
                                "name": "标准俯卧撑",
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
                api_key="test",
                model_id="existing-text-model",
                base_url="https://ark.test",
                http_client=client,
            ),
            instructions="Only group the supplied evidence.",
        )
        result = await fusion.run(
            source_id="source-a",
            speech_signals=[
                SpeechSignal(
                    action_name="俯卧撑",
                    start_seconds=11,
                    end_seconds=19,
                    evidence_text="private transcript fragment",
                    reps=12,
                    sets=3,
                    segment_role=SegmentRole.TEACHING_DEMO,
                )
            ],
            visual_segments=[
                VisualSegment(
                    action_name="标准俯卧撑",
                    start_seconds=10,
                    end_seconds=20,
                    visual_cue="双手撑地屈肘后推起",
                    sequence_label="1",
                    segment_role=SegmentRole.TEACHING_DEMO,
                )
            ],
        )

    assert result.warnings == []
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "标准俯卧撑"
    assert candidate.parameters.reps == 12
    assert candidate.parameters.sets == 3
    assert candidate.segment.start_seconds == 10
    assert candidate.segment.end_seconds == 20
    assert [span.type for span in candidate.evidence] == ["speech", "visual"]
    assert candidate.needs_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "member_ids", [["speech-1"], ["speech-1", "speech-1"], ["speech-1", "invented"]]
)
async def test_invalid_grouping_never_drops_or_duplicates_source_observations(
    member_ids: list[str],
) -> None:
    response = {
        "groups": [{"member_ids": member_ids, "name": "俯卧撑", "relation": "same_demonstration"}]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps(response)}),
        )
    ) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group all supplied IDs once.",
        )
        result = await fusion.run(
            source_id="source-a",
            speech_signals=[
                SpeechSignal(
                    action_name="俯卧撑",
                    start_seconds=1,
                    end_seconds=10,
                    evidence_text="fixture",
                    reps=8,
                )
            ],
            visual_segments=[
                VisualSegment(
                    action_name="标准俯卧撑", start_seconds=1, end_seconds=10, visual_cue="fixture"
                )
            ],
        )
    assert len(result.candidates) == 2
    assert len({item.id for item in result.candidates}) == 2
    assert result.candidates[0].parameters.reps == 8
    assert all(item.needs_confirmation for item in result.candidates)
    assert [warning.code for warning in result.warnings] == ["semantic_fusion_unavailable"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "difference",
    [
        "later_execution",
        "numbered_action",
        "parameters",
        "repetition_range",
        "partial_mode",
        "implicit_modes",
        "uncertain",
    ],
)
async def test_model_cannot_collapse_separate_executions_numbers_or_conflicting_values(
    difference: str,
) -> None:
    first = VisualSegment(
        action_name="俯卧撑",
        start_seconds=1,
        end_seconds=10,
        visual_cue="fixture",
        sequence_label="1",
        text_parameters=CandidateParameters(mode="reps", reps=8),
    )
    second = first.model_copy(deep=True)
    if difference == "later_execution":
        second.start_seconds, second.end_seconds = 20, 30
    elif difference == "numbered_action":
        second.sequence_label = "2"
    elif difference == "parameters":
        second.text_parameters = CandidateParameters(mode="reps", reps=12)
    elif difference == "repetition_range":
        second.text_parameters = CandidateParameters(mode="reps", reps=8, reps_max=12)
    elif difference in {"partial_mode", "implicit_modes"}:
        second.text_parameters = CandidateParameters(duration_seconds=30)
        if difference == "implicit_modes":
            first.text_parameters = CandidateParameters(reps=8)
    response = {
        "groups": [
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "俯卧撑",
                "relation": "uncertain" if difference == "uncertain" else "same_demonstration",
            }
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps(response)}),
        )
    ) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group evidence.",
        )
        result = await fusion.run(
            source_id="source-a", speech_signals=[], visual_segments=[first, second]
        )
    if difference in {"partial_mode", "implicit_modes"}:
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.parameters.mode is None
        assert candidate.parameters.reps is None
        assert candidate.parameters.duration_seconds is None
        assert candidate.parameter_conflicts[0].field == "mode"
        alternatives = candidate.parameter_conflicts[0].alternatives
        assert alternatives[0].parameters.reps == 8
        assert alternatives[1].parameters.duration_seconds == 30
    elif difference in {"parameters", "repetition_range"}:
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.parameters.reps is None
        assert candidate.parameters.reps_max is None
        conflict = candidate.parameter_conflicts[0]
        assert conflict.field == "reps"
        assert conflict.alternatives[0].parameters.reps == 8
        assert conflict.alternatives[1].parameters.reps == (12 if difference == "parameters" else 8)
        assert len(candidate.evidence) == 2
    else:
        assert len(result.candidates) == 2
        assert result.candidates[0].parameters.reps == 8
        assert result.candidates[1].parameters.reps == 8
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "bad_schema", "provider_error"])
async def test_model_failure_preserves_candidates_with_an_explicit_warning(failure: str) -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            await asyncio.sleep(10)
        if failure == "provider_error":
            return httpx.Response(503)
        return httpx.Response(200, json={"output_text": "not structured JSON"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test",
                model_id="text",
                base_url="https://ark.test",
                http_client=client,
                retry_delays=(),
            ),
            instructions="Group evidence.",
            timeout_seconds=0.02,
        )
        result = await fusion.run(
            source_id="source-a",
            speech_signals=[
                SpeechSignal(
                    action_name="俯卧撑",
                    start_seconds=1,
                    end_seconds=10,
                    evidence_text="fixture",
                    reps=8,
                )
            ],
            visual_segments=[],
        )
    assert len(result.candidates) == 1
    assert result.candidates[0].name == "俯卧撑"
    assert result.candidates[0].parameters.reps == 8
    assert result.candidates[0].needs_confirmation
    assert result.warnings[0].code == "semantic_fusion_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("4", "动作四", 1),
        ("#4", "动作4", 1),
        ("动作四", "动作五", 2),
        ("动作四（左）", "动作四（右）", 2),
        ("A4", "B4", 2),
        ("第1轮动作4", "第2轮动作4", 2),
    ],
)
async def test_same_action_number_in_different_notation_does_not_block_model_merge(
    left: str,
    right: str,
    expected: int,
) -> None:
    response = {
        "groups": [
            {
                "member_ids": ["visual-1", "visual-2"],
                "name": "大剪刀下拉",
                "relation": "same_demonstration",
            }
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps(response)})
        )
    ) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group evidence.",
        )
        result = await fusion.run(
            source_id="source-a",
            speech_signals=[],
            visual_segments=[
                VisualSegment(
                    action_name="大剪刀下拉",
                    start_seconds=47,
                    end_seconds=57,
                    sequence_label=left,
                    visual_cue="fixture",
                ),
                VisualSegment(
                    action_name="大剪刀下拉",
                    start_seconds=50,
                    end_seconds=57.5,
                    sequence_label=right,
                    visual_cue="fixture",
                ),
            ],
        )
    assert len(result.candidates) == expected
    assert sum(len(item.evidence) for item in result.candidates) == 2
    assert bool(result.warnings) is (expected == 2)


@pytest.mark.asyncio
async def test_oversized_input_is_retained_without_sending_an_unbounded_model_request() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("oversized semantic input must not be transmitted")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group evidence.",
        )
        result = await fusion.run(
            source_id="source-a",
            speech_signals=[],
            visual_segments=[
                VisualSegment(
                    action_name="俯卧撑",
                    start_seconds=index,
                    end_seconds=index + 1,
                    visual_cue="fixture",
                )
                for index in range(201)
            ],
        )
    assert len(result.candidates) == 201
    assert result.warnings[0].code == "semantic_fusion_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   ", "Push-up"])
async def test_unusable_model_name_preserves_source_name_and_requires_confirmation(
    name: str,
) -> None:
    response = {
        "groups": [
            {"member_ids": ["speech-1", "visual-1"], "name": name, "relation": "same_demonstration"}
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"output_text": json.dumps(response)})
        )
    ) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="text", base_url="https://ark.test", http_client=client
            ),
            instructions="Group evidence.",
        )
        result = await fusion.run(
            source_id="source-a",
            speech_signals=[
                SpeechSignal(
                    action_name="俯卧撑",
                    start_seconds=1,
                    end_seconds=10,
                    evidence_text="fixture",
                    segment_role=SegmentRole.TEACHING_DEMO,
                )
            ],
            visual_segments=[
                VisualSegment(
                    action_name="标准俯卧撑",
                    start_seconds=1,
                    end_seconds=10,
                    visual_cue="fixture",
                    segment_role=SegmentRole.TEACHING_DEMO,
                )
            ],
        )
    assert result.candidates[0].name == "标准俯卧撑"
    assert result.candidates[0].needs_confirmation
