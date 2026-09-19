import asyncio
import json
import logging

import httpx
import pytest
from test_semantic_contiguous_teaching import fuse, teaching

from hakimi_analysis.models import CandidateParameters, SegmentRole, SpeechSignal
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


@pytest.mark.asyncio
async def test_number_conflict_is_diagnosed_without_changing_retained_candidates() -> None:
    result = await fuse([teaching(0, 40, "1"), teaching(10, 30, "2")])
    assert len(result.candidates) == 2
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.diagnostics == {"group_rejected": 1, "sequence_label_conflict": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["gap", "role", "follow", "speech", "single_visual"])
async def test_time_guard_explains_the_failed_continuous_teaching_condition(case: str) -> None:
    visual = [teaching(0, 40), teaching(30, 60), teaching(50, 75)]
    speech = []
    reason = {
        "gap": "visual_chain_disconnected",
        "role": "visual_role_not_teaching",
        "follow": "follow_along_present",
        "speech": "speech_outside_visual",
        "single_visual": "visual_count_insufficient",
    }[case]
    if case == "gap":
        visual[1].start_seconds = 40
    elif case == "role":
        visual[1].segment_role = SegmentRole.UNKNOWN
    elif case == "follow":
        speech = [
            SpeechSignal(
                action_name="合成动作",
                start_seconds=1,
                end_seconds=5,
                evidence_text="private speech",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ]
    elif case == "speech":
        speech = [
            SpeechSignal(
                action_name="合成动作",
                start_seconds=80,
                end_seconds=90,
                evidence_text="private speech",
            )
        ]
    else:
        visual = [teaching(0, 75)]
        speech = [
            SpeechSignal(
                action_name="合成动作",
                start_seconds=s,
                end_seconds=e,
                evidence_text="private speech",
            )
            for s, e in [(0, 10), (60, 75)]
        ]
    result = await fuse(visual, speech)
    assert len(result.candidates) == len(visual) + len(speech)
    assert result.diagnostics == {"group_rejected": 1, "time_alignment_rejected": 1, reason: 1}


@pytest.mark.asyncio
async def test_all_applicable_causes_are_counted_once_per_group() -> None:
    visual = [teaching(0, 10, "1"), teaching(20, 30, "2"), teaching(40, 50, "3")]
    result = await fuse(
        visual,
        groups=[
            {
                "member_ids": ["visual-1", "visual-2", "visual-3"],
                "name": None,
                "relation": "uncertain",
                "content_role": "uncertain",
            }
        ],
    )
    assert len(result.candidates) == 3
    assert result.diagnostics == {
        "group_rejected": 1,
        "model_uncertain": 1,
        "content_role_rejected": 1,
        "sequence_label_conflict": 1,
        "time_alignment_rejected": 1,
        "visual_chain_disconnected": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("continuous", [False, True])
async def test_acceptance_path_and_parameter_conflict_are_not_rejections(continuous: bool) -> None:
    visual = [teaching(0, 40), teaching(30, 60)]
    if continuous:
        visual.append(teaching(50, 75))
    else:
        visual[0].segment_role = SegmentRole.UNKNOWN
    visual[0].text_parameters = CandidateParameters(mode="reps", reps=8)
    visual[-1].text_parameters = CandidateParameters(mode="reps", reps=12)
    result = await fuse(visual)
    assert len(result.candidates) == 1
    assert result.candidates[0].parameters.reps is None
    assert len(result.candidates[0].parameter_conflicts) == 1
    assert result.diagnostics == {
        "continuous_teaching_accepted" if continuous else "common_intersection_accepted": 1,
        "parameter_conflict_groups": 1,
    }


@pytest.mark.asyncio
async def test_outside_visual_boundary_is_reported_without_logging_its_identity() -> None:
    result = await fuse(
        [teaching(0, 40), teaching(30, 60), teaching(50, 75), teaching(42, 48, "2")],
        groups=[
            {
                "member_ids": ["visual-1", "visual-2", "visual-3"],
                "name": "合成动作",
                "relation": "same_demonstration",
            },
            {"member_ids": ["visual-4"], "name": "其他动作", "relation": "same_demonstration"},
        ],
    )
    assert len(result.candidates) == 4
    assert result.diagnostics == {
        "group_rejected": 1,
        "time_alignment_rejected": 1,
        "external_visual_boundary": 1,
        "common_intersection_accepted": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["attached", "target", "uncertain", "role", "label", "params", "direction"]
)
async def test_reference_outcomes_are_separate_from_exercise_conflicts(case: str) -> None:
    visual = [teaching(0, 5), teaching(10, 20)]
    reference: dict[str, object] = {
        "member_ids": ["visual-1"],
        "name": "合成预告",
        "relation": "same_demonstration",
        "content_role": "preview",
        "related_member_id": "visual-2",
    }
    target: dict[str, object] = {
        "member_ids": ["visual-2"],
        "name": "合成动作",
        "relation": "same_demonstration",
    }
    if case == "target":
        target["relation"] = "uncertain"
    elif case == "uncertain":
        reference["relation"] = "uncertain"
    elif case == "role":
        visual[0].segment_role = SegmentRole.FOLLOW_ALONG
    elif case == "label":
        visual[0].sequence_label = "2"
    elif case == "params":
        visual[0].text_parameters = CandidateParameters(reps=8)
        visual[1].text_parameters = CandidateParameters(reps=12)
    elif case == "direction":
        visual[0].start_seconds, visual[0].end_seconds = 15, 25
    result = await fuse(visual, groups=[reference, target])
    if case == "attached":
        assert len(result.candidates) == 1
        assert len(result.candidates[0].evidence) == 2
        assert result.diagnostics == {"common_intersection_accepted": 1, "reference_attached": 1}
    else:
        assert len(result.candidates) == 2
        reason = {
            "target": "reference_target_unresolved",
            "uncertain": "reference_uncertain",
            "role": "reference_role_protected",
            "label": "reference_label_conflict",
            "params": "reference_parameter_conflict",
            "direction": "reference_direction_conflict",
        }[case]
        expected = {"reference_rejected": 1, reason: 1}
        expected.update(
            {"group_rejected": 1, "model_uncertain": 1}
            if case == "target"
            else {"common_intersection_accepted": 1}
        )
        assert result.diagnostics == expected


@pytest.mark.asyncio
async def test_excluded_visual_is_counted_as_exclusion_not_merge_rejection() -> None:
    result = await fuse(
        [teaching(0, 5)],
        groups=[
            {
                "member_ids": ["visual-1"],
                "name": None,
                "relation": "same_demonstration",
                "content_role": "equipment_setup",
            }
        ],
    )
    assert result.candidates == []
    assert result.diagnostics == {"non_training_excluded": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["membership", "reference_target", "no_observations"])
async def test_global_structural_failures_have_non_content_reasons(case: str) -> None:
    visual = [teaching(0, 10)]
    groups: list[dict[str, object]] = [
        {
            "member_ids": ["not-an-input"],
            "name": "private name",
            "relation": "same_demonstration",
        }
    ]
    if case == "reference_target":
        groups[0].update(member_ids=["visual-1"], content_role="recap", related_member_id="missing")
    elif case == "no_observations":
        visual = []
    result = await fuse(visual, groups=groups)
    assert result.diagnostics == {
        "no_observations" if case == "no_observations" else f"unavailable_{case}": 1
    }
    assert len(result.candidates) == len(visual)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["input_budget", "time_budget", "timeout", "provider", "cancel"])
async def test_unavailability_reasons_and_cancellation_preserve_original_behavior(
    case: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="hakimi_analysis.semantic_fusion")

    async def respond(_: httpx.Request) -> httpx.Response:
        if case in {"input_budget", "time_budget"}:
            raise AssertionError("preflight rejection must not call the model")
        if case == "timeout":
            await asyncio.sleep(1)
        if case == "cancel":
            raise asyncio.CancelledError
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="private-test-key",
                model_id="test",
                base_url="https://ark.test",
                http_client=client,
            ),
            instructions="test",
            timeout_seconds=0.01 if case == "timeout" else 20,
        )
        visual = [teaching(0, 10) for _ in range(201 if case == "input_budget" else 1)]
        if case == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await fusion.run(source_id="synthetic", speech_signals=[], visual_segments=visual)
            assert not [r for r in caplog.records if r.name == "hakimi_analysis.semantic_fusion"]
            return
        result = await fusion.run(
            source_id="synthetic",
            speech_signals=[],
            visual_segments=visual,
            remaining_seconds=0 if case == "time_budget" else None,
        )
    assert len(result.candidates) == len(visual)
    assert all(item.needs_confirmation for item in result.candidates)
    assert result.diagnostics == {f"unavailable_{case}": 1}
    records = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "hakimi_analysis.semantic_fusion"
    ]
    assert records == [
        {
            "source_id": "synthetic",
            "stage": "fusing_candidates",
            "fusion_diagnostics": {f"unavailable_{case}": 1},
        }
    ]
    assert "private-" not in json.dumps(records)


@pytest.mark.asyncio
async def test_fusion_logs_only_safe_counts_not_observation_or_group_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    visual = [teaching(0, 20, "private-label-left"), teaching(10, 30, "private-label-right")]
    visual[0].action_name = "private-name"
    visual[0].visual_cue = "private-description"
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.semantic_fusion"):
        result = await fuse(visual)
    records = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "hakimi_analysis.semantic_fusion"
    ]
    assert records == [
        {
            "source_id": "synthetic-chunks",
            "stage": "fusing_candidates",
            "fusion_diagnostics": {"group_rejected": 1, "sequence_label_conflict": 1},
        }
    ]
    assert result.diagnostics == records[0]["fusion_diagnostics"]
    assert "private-" not in json.dumps(records)


@pytest.mark.asyncio
async def test_reason_counts_aggregate_separate_groups_and_do_not_leak_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    visual = [
        teaching(0, 20, "private-side-a"),
        teaching(10, 30, "private-side-b"),
        teaching(40, 60, "private-side-a"),
        teaching(50, 70, "private-side-b"),
    ]
    groups: list[dict[str, object]] = [
        {"member_ids": ids, "name": "private-model-name", "relation": "same_demonstration"}
        for ids in [["visual-1", "visual-2"], ["visual-3", "visual-4"]]
    ]
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.semantic_fusion"):
        result = await fuse(visual, groups=groups)
    assert len(result.candidates) == 4
    assert result.diagnostics == {"group_rejected": 2, "sequence_label_conflict": 2}
    records = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "hakimi_analysis.semantic_fusion"
    ]
    assert records[0]["fusion_diagnostics"] == {"group_rejected": 2, "sequence_label_conflict": 2}
    assert "private-" not in json.dumps(records)


@pytest.mark.asyncio
async def test_maximum_observation_count_produces_bounded_content_free_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    visual = [teaching(0, 10) for _ in range(200)]
    groups: list[dict[str, object]] = [
        {"member_ids": [f"visual-{i}"], "name": "合成动作", "relation": "same_demonstration"}
        for i in range(1, 201)
    ]
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.semantic_fusion"):
        result = await fuse(visual, groups=groups)
    assert len(result.candidates) == 200
    assert result.diagnostics == {"common_intersection_accepted": 200}
    record = next(r for r in caplog.records if r.name == "hakimi_analysis.semantic_fusion")
    assert json.loads(record.getMessage())["fusion_diagnostics"] == {
        "common_intersection_accepted": 200
    }
