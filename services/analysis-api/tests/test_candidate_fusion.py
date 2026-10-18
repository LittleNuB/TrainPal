from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import Segment, SegmentRole, SpeechSignal, VisualSegment


def test_overlapping_speech_and_visual_evidence_become_one_candidate() -> None:
    candidates = fuse_candidates(
        source_id="legacy-arm-workout",
        speech_signals=[
            SpeechSignal(
                action_name="拖拽弯举",
                sets=3,
                reps=10,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=42,
                end_seconds=49,
                evidence_text="接下来做 drag curl",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=41,
                end_seconds=51,
                visual_cue="双臂沿躯干向后拖动哑铃",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name == "拖拽弯举"
    assert candidate.segment is not None
    assert candidate.segment.start_seconds == 41
    assert candidate.segment.end_seconds == 51
    assert candidate.parameters.sets == 3
    assert candidate.parameters.reps == 10
    assert [item.type for item in candidate.evidence] == ["speech", "visual"]
    assert candidate.needs_confirmation is False


def test_same_action_within_one_sampling_step_is_fused() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="Drag Curl",
                sets=None,
                reps=10,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=42,
                end_seconds=46,
                evidence_text="drag curl",
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=48,
                end_seconds=54,
                visual_cue="dumbbells move behind the torso",
            )
        ],
    )

    assert len(candidates) == 1
    assert [item.type for item in candidates[0].evidence] == ["speech", "visual"]
    assert candidates[0].segment is not None
    assert candidates[0].segment.start_seconds == 42
    assert candidates[0].segment.end_seconds == 54


def test_single_branch_evidence_is_returned_as_needing_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="交叉弯举",
                sets=None,
                reps=None,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=8,
                end_seconds=14,
                evidence_text="交叉弯举",
            )
        ],
        visual_segments=[],
    )

    assert len(candidates) == 1
    assert candidates[0].name == "交叉弯举"
    assert candidates[0].needs_confirmation is True
    assert [item.type for item in candidates[0].evidence] == ["speech"]


def test_overlapping_different_actions_remain_separate_candidates() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="锤式弯举",
                sets=3,
                reps=10,
                duration_seconds=None,
                rest_seconds=60,
                start_seconds=42,
                end_seconds=49,
                evidence_text="做锤式弯举",
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="Drag Curl",
                start_seconds=41,
                end_seconds=51,
                visual_cue="哑铃沿躯干向后拖动",
            )
        ],
    )

    assert len(candidates) == 2
    assert [candidate.name for candidate in candidates] == ["拖拽弯举", "锤式弯举"]
    assert all(candidate.needs_confirmation for candidate in candidates)
    assert all(len(candidate.evidence) == 1 for candidate in candidates)


def test_no_evidence_produces_no_candidates() -> None:
    assert fuse_candidates(source_id="source-a", speech_signals=[], visual_segments=[]) == []


def test_overlapping_duplicate_speech_signals_become_one_deterministic_candidate() -> None:
    sparse = SpeechSignal(
        action_name="Squat",
        sets=None,
        reps=None,
        duration_seconds=None,
        rest_seconds=None,
        start_seconds=10,
        end_seconds=18,
        evidence_text="squat",
    )
    parameterized = SpeechSignal(
        action_name="squat",
        sets=3,
        reps=10,
        duration_seconds=None,
        rest_seconds=30,
        start_seconds=12,
        end_seconds=20,
        evidence_text="three sets of ten squats",
    )

    forward = fuse_candidates(
        source_id="source-a",
        speech_signals=[sparse, parameterized],
        visual_segments=[],
    )
    reversed_order = fuse_candidates(
        source_id="source-a",
        speech_signals=[parameterized, sparse],
        visual_segments=[],
    )

    assert len(forward) == 1
    assert forward == reversed_order
    assert forward[0].segment == Segment(start_seconds=10, end_seconds=20)
    assert forward[0].parameters.sets == 3
    assert forward[0].parameters.reps == 10


def test_conflicting_internal_segment_roles_require_confirmation_without_being_exposed() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=None,
                reps=None,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=5,
                end_seconds=10,
                evidence_text="跟我做深蹲",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=10,
                visual_cue="讲解站姿并示范一次",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].needs_confirmation is True
    assert "segment_role" not in candidates[0].model_dump()


def test_teaching_demo_length_does_not_become_a_training_duration() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=None,
                reps=None,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=5,
                end_seconds=25,
                evidence_text="下面示范深蹲动作",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
        visual_segments=[
            VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=25,
                visual_cue="连续示范深蹲",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].parameters.mode is None
    assert candidates[0].parameters.duration_seconds is None
    assert candidates[0].needs_confirmation is False


def test_speech_only_internal_segment_role_still_needs_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[
            SpeechSignal(
                action_name="深蹲",
                sets=3,
                reps=10,
                duration_seconds=None,
                rest_seconds=None,
                start_seconds=5,
                end_seconds=10,
                evidence_text="跟我一起做深蹲",
                segment_role=SegmentRole.FOLLOW_ALONG,
            )
        ],
        visual_segments=[],
    )

    assert candidates[0].needs_confirmation is True
    assert "segment_role" not in candidates[0].model_dump()


def test_visual_only_internal_segment_role_still_needs_confirmation() -> None:
    candidates = fuse_candidates(
        source_id="source-a",
        speech_signals=[],
        visual_segments=[
            VisualSegment(
                action_name="深蹲",
                start_seconds=5,
                end_seconds=10,
                visual_cue="面对镜头逐步讲解动作",
                segment_role=SegmentRole.TEACHING_DEMO,
            )
        ],
    )

    assert candidates[0].needs_confirmation is True
    assert "segment_role" not in candidates[0].model_dump()
