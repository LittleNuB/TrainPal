import pytest
from test_semantic_contiguous_teaching import fuse, teaching

from hakimi_analysis.models import CandidateParameters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "left,right",
    [
        ("动作4", "第四个动作"),
        ("#4", "第4个动作"),
        ("四", "第四动作"),
        ("4", "第4动作"),
        ("动作四", " 第 4 个 动作 "),
        ("10", "第十个动作"),
        ("12", "第12个动作"),
        ("01", "第1个动作"),
        ("第四个动作", "动作4"),
    ],
)
async def test_explicit_ordinal_spelling_keeps_one_demonstration_with_all_sources(
    left: str, right: str,
) -> None:
    visual = [teaching(0, 20, left), teaching(10, 30, right)]
    result = await fuse(visual)

    assert len(result.candidates) == 1
    assert [(e.start_seconds, e.end_seconds) for e in result.candidates[0].evidence] == [
        (0, 20), (10, 30),
    ]
    assert result.candidates[0].needs_confirmation  # Visual only stays uncertain.
    assert result.diagnostics == {"common_intersection_accepted": 1}
    assert [v.sequence_label for v in visual] == [left, right]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "left,right",
    [
        ("第四个动作", "第五个动作"),
        ("第4个动作左侧", "第4个动作右侧"),
        ("第4个动作左侧", "4"),
        ("第1轮第4个动作", "第2轮第4个动作"),
        ("第1轮第4个动作", "4"),
        ("第4组", "4"),
        ("第4次", "4"),
        ("4×10", "4"),
        ("第4个动作-变式A", "第4个动作-变式B"),
        ("第4个动作/第5个动作", "4"),
        ("第4个", "4"),
        ("第十四个动作", "14"),
        ("第4个动作？", "4"),
    ],
)
async def test_ordinal_does_not_erase_distinct_or_unsupported_identity(
    left: str, right: str,
) -> None:
    result = await fuse([teaching(0, 20, left), teaching(10, 30, right)])
    assert len(result.candidates) == 2
    assert all(c.needs_confirmation for c in result.candidates)
    assert result.diagnostics == {"group_rejected": 1, "sequence_label_conflict": 1}
    assert sum(len(c.evidence) for c in result.candidates) == 2


@pytest.mark.asyncio
async def test_ordinal_equivalence_does_not_merge_a_later_execution() -> None:
    result = await fuse([teaching(0, 10, "4"), teaching(20, 30, "第四个动作")])
    assert len(result.candidates) == 2
    assert result.diagnostics["time_alignment_rejected"] == 1
    assert "sequence_label_conflict" not in result.diagnostics


@pytest.mark.asyncio
async def test_ordinal_equivalence_does_not_override_model_separation() -> None:
    result = await fuse(
        [teaching(0, 20, "4"), teaching(10, 30, "第四个动作")],
        groups=[
            {"member_ids": ["visual-1"], "name": "正握变式", "relation": "same_demonstration"},
            {"member_ids": ["visual-2"], "name": "反握变式", "relation": "same_demonstration"},
        ],
    )
    assert [c.name for c in result.candidates] == ["正握变式", "反握变式"]


@pytest.mark.asyncio
async def test_ordinal_equivalence_does_not_override_model_uncertainty() -> None:
    result = await fuse(
        [teaching(0, 20, "4"), teaching(10, 30, "第四个动作")],
        groups=[{"member_ids": ["visual-1", "visual-2"], "name": None,
                 "relation": "uncertain"}],
    )
    assert len(result.candidates) == 2
    assert result.diagnostics == {"group_rejected": 1, "model_uncertain": 1}


@pytest.mark.asyncio
async def test_ordinal_merge_preserves_conflicting_values_and_their_sources() -> None:
    visual = [teaching(0, 20, "4"), teaching(10, 30, "第四个动作")]
    visual[0].text_parameters = CandidateParameters(mode="reps", reps=8)
    visual[1].text_parameters = CandidateParameters(mode="reps", reps=10)
    result = await fuse(visual)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.needs_confirmation
    assert candidate.parameters.reps is None
    assert [conflict.field for conflict in candidate.parameter_conflicts] == ["reps"]
    alternatives = candidate.parameter_conflicts[0].alternatives
    assert [(a.parameters.reps, a.evidence[0].start_seconds) for a in alternatives] == [
        (8, 0), (10, 10),
    ]


@pytest.mark.asyncio
async def test_ordinal_merge_of_continuous_teaching_keeps_original_playback_ranges() -> None:
    result = await fuse([
        teaching(0, 40, "4"), teaching(30, 60, "第四个动作"), teaching(50, 75, "动作四"),
    ])
    assert len(result.candidates) == 1
    assert result.diagnostics == {"continuous_teaching_accepted": 1}
    assert [(p.start_seconds, p.end_seconds) for p in result.candidates[0].playback_options] == [
        (0, 40), (30, 60), (50, 75),
    ]
    assert result.candidates[0].segment.end_seconds == 40


@pytest.mark.asyncio
@pytest.mark.parametrize("label,attached", [("第四个动作", True), ("第五个动作", False)])
async def test_reference_uses_the_same_ordinal_identity_without_changing_target(
    label: str, attached: bool,
) -> None:
    result = await fuse(
        [teaching(0, 5, label), teaching(10, 30, "4")],
        groups=[
            {"member_ids": ["visual-1"], "name": "预告", "relation": "same_demonstration",
             "content_role": "preview", "related_member_id": "visual-2"},
            {"member_ids": ["visual-2"], "name": "正式动作", "relation": "same_demonstration"},
        ],
    )
    assert len(result.candidates) == (1 if attached else 2)
    target = next(c for c in result.candidates if c.name == "正式动作")
    assert (target.segment.start_seconds, target.segment.end_seconds) == (10, 30)
    assert target.needs_confirmation  # Reference does not promote certainty.
    assert len(target.evidence) == (2 if attached else 1)
    assert result.diagnostics["reference_attached" if attached else "reference_rejected"] == 1
