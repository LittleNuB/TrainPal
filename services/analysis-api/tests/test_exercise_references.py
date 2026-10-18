"""Reference lookup is a public, offline seam; catalog matches are not video facts."""

import pytest
from pydantic import ValidationError

from hakimi_analysis.exercise_references import ExerciseReferenceCatalog


def test_lookup_returns_all_known_variants_without_declaring_identity() -> None:
    matches = ExerciseReferenceCatalog.bundled().lookup("反向飞鸟")
    assert [item.source_id for item in matches] == ["0383", "0601", "0602", "0993"]
    assert all(item.reference_only for item in matches)
    assert all(item.review_status == "agent_reviewed_not_expert" for item in matches)


@pytest.mark.parametrize("name", ["反向蝴蝶机", "反向蝴蝶机飞鸟", "蝴蝶机反向飞鸟"])
def test_reverse_pec_deck_alias_keeps_both_machine_grips(name: str) -> None:
    catalog = ExerciseReferenceCatalog.bundled()
    assert [item.source_id for item in catalog.lookup(name)] == ["0601", "0602"]
    assert catalog.lookup(name, equipment="dumbbell") == ()
    assert catalog.lookup("单臂" + name) == ()


@pytest.mark.parametrize("name", [" PUSH-UP ", "push up", "ＰＵＳＨ－ＵＰ", "掌上压"])
def test_known_spelling_variants_keep_the_reference_chinese_name(name: str) -> None:
    matches = ExerciseReferenceCatalog.bundled().lookup(name)
    assert [(item.source_id, item.name_zh) for item in matches] == [("0662", "俯卧撑")]


def test_equipment_can_narrow_candidates_but_never_override_an_unknown_variant() -> None:
    catalog = ExerciseReferenceCatalog.bundled()
    assert [item.source_id for item in catalog.lookup("反向飞鸟", equipment="dumbbell")] == ["0383"]
    assert catalog.lookup("反向飞鸟", equipment="barbell") == ()
    assert catalog.lookup("单臂哑铃反向飞鸟", equipment="dumbbell") == ()


@pytest.mark.parametrize("name", ["", " ", "左侧死虫式", "跪姿俯卧撑", "下一组俯卧撑", "无名动作"])
def test_unknown_labels_are_not_matched_by_substring(name: str) -> None:
    assert ExerciseReferenceCatalog.bundled().lookup(name) == ()


def test_overbroad_matches_return_none_instead_of_hiding_variants() -> None:
    data = ExerciseReferenceCatalog.bundled().model_dump(mode="json")
    for record in data["records"]:
        record["aliases"] = ["合成宽泛动作"]
    catalog = ExerciseReferenceCatalog.model_validate(data)
    assert catalog.lookup("合成宽泛动作") == ()


def test_ambiguous_duplicate_source_ids_are_rejected_at_load() -> None:
    data = ExerciseReferenceCatalog.bundled().model_dump(mode="json")
    data["records"].append(data["records"][0])
    with pytest.raises(ValidationError):
        ExerciseReferenceCatalog.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [("image_url", "https://invalid.test/image.png"), ("reps", 12), ("reference_only", False)],
)
def test_catalog_rejects_media_prescriptions_and_non_reference_records(
    field: str, value: object
) -> None:
    data = ExerciseReferenceCatalog.bundled().model_dump(mode="json")
    data["records"][0][field] = value
    with pytest.raises(ValidationError):
        ExerciseReferenceCatalog.model_validate(data)
