"""Opt-in numeric-purpose contract at the real Provider HTTP seam."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment, VisualLocalizationResult
from hakimi_analysis.orchestration import SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderError, ProviderSchemaError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("input_kind", ["video", "contact_sheet"])
async def test_bundled_parameter_contract_has_one_meaning_at_provider_boundary(
    tmp_path: Path, enabled: bool, input_kind: str
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    root = Path(__file__).resolve().parents[3]
    base = SkillRepository.load(root / "skills").visual_instructions
    suffix = '\nSynthetic additional caller rule must remain.\n"text_parameters": null\n'
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": '{"segments":[]}'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=enabled,
        )
        if input_kind == "video":
            await provider.locate_visual(
                video_path=video,
                window=Segment(start_seconds=0, end_seconds=40),
                instructions=base + suffix,
            )
        else:
            await provider.locate_visual_contact_sheet(
                image_path=video,
                frame_times_seconds=(0, 20),
                window=Segment(start_seconds=0, end_seconds=40),
                instructions=base + suffix,
            )
    instructions = sent[0]["instructions"]
    if not enabled:
        assert instructions == base + suffix
        return
    assert "may contain ONLY readable instructions attached to this exercise" not in instructions
    assert "Mode is reps or duration when" not in instructions
    assert instructions.count("本次启用次数用途合同") == 1
    assert "选重量条件只记录在 repetition_context" in instructions
    assert "不能因选重量次数删掉真实训练时长" in instructions
    assert '"repetition_context": null' in instructions
    examples = [
        json.loads(block.split("```", 1)[0]) for block in instructions.split("```json\n")[1:]
    ]
    context_examples = [example for example in examples if set(example) == {"repetition_context"}]
    assert len(context_examples) == 1
    context = context_examples[0]["repetition_context"]
    assert isinstance(context, dict)
    assert set(context) == {"purpose", "caption", "segment"}
    assert context["purpose"] == "training_target"
    assert context["caption"] == "每组做六次"
    assert context["segment"] == {"start_seconds": 2, "end_seconds": 4}
    assert "不能返回数组" in instructions
    assert "不要复制示例字幕或时间" in instructions
    assert instructions.endswith(suffix)
    for rule in (
        "Every distinct numbered exercise",
        "at most three short action",
        "Never merge across",
    ):
        assert rule in instructions


@pytest.mark.asyncio
async def test_changed_bundled_parameter_rules_stop_pilot_before_request(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    base = SkillRepository.load(Path(__file__).resolve().parents[3] / "skills").visual_instructions

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("An incompatible bundled rule must stop before a Provider request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=True,
        )
        with pytest.raises(ProviderError) as raised:
            await provider.locate_visual(
                video_path=video,
                window=Segment(start_seconds=0, end_seconds=40),
                instructions=base.replace("Missing values", "Changed missing values"),
            )
    assert raised.value.code == "configuration_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["mode", "tips", "context_array", "empty_context_array"])
async def test_purpose_pilot_still_rejects_invalid_parameters_and_excess_tips(
    tmp_path: Path, fault: str
) -> None:
    item = observation("load_selection_condition", start=0, reps=8, upper=10)
    if fault == "mode":
        item["text_parameters"]["mode"] = None
    elif fault == "tips":
        item["tips"] *= 4
    else:
        item["repetition_context"] = (
            [item["repetition_context"]] if fault == "context_array" else []
        )
    with pytest.raises(ProviderSchemaError):
        await analyze_response(tmp_path, [item])


def observation(
    purpose: str, *, start: float, reps: int, upper: int | None = None
) -> dict[str, Any]:
    return {
        "action_name": "合成教学动作",
        "start_seconds": start,
        "end_seconds": start + 10,
        "visual_cue": "合成可见教学",
        "segment_role": "teaching_demo",
        "sequence_label": "动作一",
        "is_training_content": True,
        "text_parameters": {"mode": "reps", "sets": 4, "reps": reps, "reps_max": upper},
        "tips": [
            {
                "text": "保持动作平稳",
                "category": "rhythm",
                "evidence": {
                    "type": "visual",
                    "start_seconds": start + 1,
                    "end_seconds": start + 2,
                },
            }
        ],
        "repetition_context": {
            "purpose": purpose,
            "caption": "每组完成十次，共四组"
            if purpose == "training_target"
            else "选择能完成八到十次的重量",
            "segment": {"start_seconds": start + 2, "end_seconds": start + 4},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["video", "contact_sheet"])
async def test_explicit_purpose_keeps_training_range_but_not_load_selection(
    tmp_path: Path, input_kind: str
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic-media")
    source = [
        observation(
            "training_target", start=50 if input_kind == "contact_sheet" else 0, reps=8, upper=12
        ),
        observation(
            "load_selection_condition",
            start=70 if input_kind == "contact_sheet" else 20,
            reps=8,
            upper=10,
        ),
    ]
    source[0]["repetition_context"]["caption"] = "每组做八到十二次，共四组，选择适合自己的重量"
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"segments": source})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=True,
        )
        if input_kind == "video":
            result = await provider.locate_visual(
                video_path=video,
                window=Segment(start_seconds=0, end_seconds=40),
                instructions="base",
            )
        else:
            result = await provider.locate_visual_contact_sheet(
                image_path=video,
                frame_times_seconds=(50, 70),
                window=Segment(start_seconds=50, end_seconds=90),
                instructions="base",
            )
    assert len(sent) == 1
    assert "与当前观察及请求 time_rule 使用同一时间坐标" in sent[0]["instructions"]
    assert "使用当前片段内秒数" not in sent[0]["instructions"]
    if input_kind == "contact_sheet":
        metadata = json.loads(sent[0]["input"][0]["content"][1]["text"])
        assert metadata["time_rule"] == "Return approximate absolute source-video seconds."
        assert metadata["contact_sheet"]["frame_times_seconds"] == [50, 70]
    assert "repetition_context" in json.dumps(sent[0]["text"]["format"]["schema"])
    first, second = result.segments
    assert first.text_parameters is not None
    assert (
        first.text_parameters.reps,
        first.text_parameters.reps_max,
        first.text_parameters.sets,
    ) == (8, 12, 4)
    assert second.text_parameters is not None
    assert second.text_parameters.model_dump(exclude_none=True) == {"sets": 4}
    for actual, original in zip(result.segments, source, strict=True):
        assert actual.action_name == original["action_name"]
        assert actual.start_seconds == original["start_seconds"]
        assert actual.end_seconds == original["end_seconds"]
        assert actual.tips[0].text == "保持动作平稳"
        assert actual.sequence_label == "动作一"
    assert "repetition_context" not in result.model_dump_json()
    assert "caption" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_blank_caption_cannot_support_numeric_purpose(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic-media")
    source = observation("training_target", start=0, reps=10)
    source["repetition_context"]["caption"] = " \n\t "
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"output_text": json.dumps({"segments": [source]})}
            )
        )
    ) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=True,
        )
        with pytest.raises(ProviderSchemaError):
            await provider.locate_visual(
                video_path=video,
                window=Segment(start_seconds=0, end_seconds=40),
                instructions="base",
            )


async def analyze_response(
    tmp_path: Path,
    source: list[dict[str, Any]],
    *,
    enabled: bool | None = True,
) -> tuple[VisualLocalizationResult, dict[str, Any]]:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic-media")
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"segments": source})})

    options: dict[str, Any] = {} if enabled is None else {"visual_parameter_context": enabled}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            **options,
        )
        result = await provider.locate_visual(
            video_path=video, window=Segment(start_seconds=0, end_seconds=40), instructions="base"
        )
    assert len(sent) == 1
    return result, sent[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "uncertain", "unknown", "outside", "zero", "long"])
async def test_unusable_purpose_fails_instead_of_silently_clearing_conflicting_values(
    tmp_path: Path,
    fault: str,
) -> None:
    item = observation("training_target", start=0, reps=10)
    context = item["repetition_context"]
    if fault == "missing":
        item["repetition_context"] = None
    elif fault in {"uncertain", "unknown"}:
        context["purpose"] = fault
    elif fault == "outside":
        context["segment"]["end_seconds"] = 11
    elif fault == "zero":
        context["segment"]["end_seconds"] = context["segment"]["start_seconds"]
    else:
        context["caption"] = "合成" * 61
    with pytest.raises(ProviderSchemaError):
        await analyze_response(tmp_path, [item])


@pytest.mark.asyncio
async def test_default_and_explicit_disabled_keep_original_request_and_parameters(
    tmp_path: Path,
) -> None:
    item = observation("load_selection_condition", start=0, reps=8, upper=10)
    del item["repetition_context"]
    default, default_request = await analyze_response(tmp_path, [item], enabled=None)
    disabled, disabled_request = await analyze_response(tmp_path, [item], enabled=False)
    assert default == disabled
    assert default_request == disabled_request
    assert default_request["instructions"] == "base"
    assert "repetition_context" not in json.dumps(default_request)
    assert default.segments[0].text_parameters is not None
    assert default.segments[0].text_parameters.reps == 8
    assert default.segments[0].text_parameters.reps_max == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("has_load_condition", [False, True])
async def test_no_training_repetitions_leaves_other_fields_unchanged(
    tmp_path: Path, has_load_condition: bool
) -> None:
    item = observation("training_target", start=0, reps=10)
    item["repetition_context"] = (
        {
            "purpose": "load_selection_condition",
            "caption": "选能完成八到十次的重量，每组持续三十秒",
            "segment": {"start_seconds": 2, "end_seconds": 4},
        }
        if has_load_condition
        else None
    )
    item["text_parameters"] = {
        "mode": "duration",
        "duration_seconds": 30,
        "sets": 2,
        "rest_seconds": 20,
    }
    result, _ = await analyze_response(tmp_path, [item])
    assert result.segments[0].text_parameters is not None
    assert (
        result.segments[0].text_parameters.model_dump(exclude_none=True) == item["text_parameters"]
    )


@pytest.mark.asyncio
async def test_real_training_targets_still_reach_fusion_as_a_conflict(tmp_path: Path) -> None:
    first = observation("training_target", start=0, reps=8, upper=12)
    first["repetition_context"]["caption"] = "每组做八到十二次，共四组"
    second = observation("training_target", start=2, reps=10)
    visual, _ = await analyze_response(tmp_path, [first, second])

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        assert len(body["observations"]) == 2
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [item["id"] for item in body["observations"]],
                                "name": "合成教学动作",
                                "relation": "same_demonstration",
                                "content_role": "exercise",
                                "accepted_tip_ids": [],
                            }
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="synthetic",
                model_id="synthetic",
                base_url="https://synthetic.test",
                http_client=http,
                retry_delays=(),
            ),
            instructions="fixed",
        ).run(source_id="synthetic", speech_signals=[], visual_segments=visual.segments)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.needs_confirmation
    assert candidate.parameters.reps is None
    assert len(candidate.parameter_conflicts) == 1
    assert {
        (a.parameters.reps, a.parameters.reps_max)
        for a in candidate.parameter_conflicts[0].alternatives
    } == {(8, 12), (10, None)}
