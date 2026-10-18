"""Flat repetition-purpose pilot at the external Provider HTTP boundary."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment, VisualLocalizationResult
from hakimi_analysis.orchestration import SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderSchemaError


def flat_observation() -> dict[str, Any]:
    return {
        "action_name": "合成教学动作",
        "start_seconds": 0,
        "end_seconds": 10,
        "visual_cue": "合成可见教学",
        "segment_role": "teaching_demo",
        "sequence_label": "动作一",
        "is_training_content": True,
        "text_parameters": {
            "mode": "reps",
            "sets": 4,
            "reps": 8,
            "reps_max": 12,
            "duration_seconds": None,
            "rest_seconds": 60,
        },
        "tips": [
            {
                "text": "保持动作平稳",
                "category": "rhythm",
                "evidence": {"type": "visual", "start_seconds": 1, "end_seconds": 2},
            }
        ],
        "repetition_purpose": "training_target",
        "repetition_caption": "每组八到十二次，选择适合自己的重量",
        "repetition_start_seconds": 2,
        "repetition_end_seconds": 4,
    }


@pytest.mark.asyncio
async def test_flat_contract_preserves_action_range_parameters_and_tip_source(
    tmp_path: Path,
) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps({"segments": [flat_observation()]}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=True,
            visual_parameter_context_format="flat",
        )
        result = await provider.locate_visual(
            video_path=video,
            window=Segment(start_seconds=0, end_seconds=40),
            instructions=SkillRepository.load(
                Path(__file__).resolve().parents[3] / "skills"
            ).visual_instructions,
        )
    item = result.segments[0]
    assert (item.action_name, item.sequence_label, item.start_seconds, item.end_seconds) == (
        "合成教学动作",
        "动作一",
        0,
        10,
    )
    assert item.text_parameters is not None
    assert item.text_parameters.model_dump(exclude_none=True) == {
        "mode": "reps",
        "sets": 4,
        "reps": 8,
        "reps_max": 12,
        "rest_seconds": 60,
    }
    assert item.tips[0].model_dump(mode="json") == flat_observation()["tips"][0]
    assert "repetition_caption" not in result.model_dump_json()
    schema = sent[0]["text"]["format"]["schema"]
    assert "repetition_purpose" in json.dumps(schema)
    assert "repetition_context" not in json.dumps(schema)


@pytest.mark.asyncio
async def test_flat_contract_rejects_numeric_string_instead_of_coercing_it(tmp_path: Path) -> None:
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"synthetic")
    item = flat_observation()
    item["repetition_start_seconds"] = "2"

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": json.dumps({"segments": [item]}),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=True,
            visual_parameter_context_format="flat",
        )
        with pytest.raises(ProviderSchemaError):
            await provider.locate_visual(
                video_path=video,
                window=Segment(start_seconds=0, end_seconds=40),
                instructions="synthetic",
            )


async def run_flat(
    tmp_path: Path,
    item: dict[str, Any],
    *,
    response_overrides: dict[str, Any] | None = None,
    input_kind: str = "video",
    window: Segment | None = None,
    suffix: str = "",
    branch_source: bool = False,
    base_override: str | None = None,
) -> tuple[VisualLocalizationResult, dict[str, Any]]:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        body = {
            "status": "completed",
            "store": False,
            "text": payload["text"],
            "output_text": json.dumps({"segments": [item]}),
        }
        body.update(response_overrides or {})
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=True,
            visual_parameter_context_format="flat_branch_source" if branch_source else "flat",
        )
        base = SkillRepository.load(
            Path(__file__).resolve().parents[3] / "skills"
        ).visual_instructions
        if base_override is not None:
            base = base_override
        bounds = window or Segment(start_seconds=0, end_seconds=40)
        if input_kind == "video":
            result = await provider.locate_visual(
                video_path=media, window=bounds, instructions=base + suffix
            )
        else:
            result = await provider.locate_visual_contact_sheet(
                image_path=media,
                window=bounds,
                frame_times_seconds=(bounds.start_seconds, bounds.end_seconds),
                instructions=base + suffix,
            )
    return result, sent[0]


@pytest.mark.asyncio
async def test_branch_bound_visual_source_preserves_caption_and_time(tmp_path: Path) -> None:
    item = flat_observation()
    del item["tips"][0]["evidence"]["type"]
    result, sent = await run_flat(tmp_path, item, branch_source=True)
    assert result.segments[0].tips[0].model_dump(mode="json") == {
        "text": "保持动作平稳",
        "category": "rhythm",
        "evidence": {"type": "visual", "start_seconds": 1, "end_seconds": 2},
    }
    evidence_schema = sent["text"]["format"]["schema"]["$defs"]["EvidenceSpan"]
    assert set(evidence_schema["properties"]) == {"start_seconds", "end_seconds"}
    assert set(evidence_schema["required"]) == {"start_seconds", "end_seconds"}
    assert 'type:"visual"' not in sent["instructions"]
    assert "source type is assigned by this video-only Provider branch" in sent["instructions"]


@pytest.mark.asyncio
async def test_original_flat_contract_still_rejects_missing_source_type(tmp_path: Path) -> None:
    item = flat_observation()
    del item["tips"][0]["evidence"]["type"]
    with pytest.raises(ProviderSchemaError):
        await run_flat(tmp_path, item)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["text", "category", "start", "end", "purpose_end", "numeric_string", "visual", "speech"],
)
async def test_branch_binding_does_not_supply_model_content_or_accept_a_claimed_type(
    tmp_path: Path, fault: str
) -> None:
    item = flat_observation()
    evidence = item["tips"][0]["evidence"]
    del evidence["type"]
    if fault in {"text", "category"}:
        del item["tips"][0][fault]
    elif fault in {"start", "end"}:
        del evidence[f"{fault}_seconds"]
    elif fault == "purpose_end":
        del item["repetition_end_seconds"]
    elif fault == "numeric_string":
        evidence["start_seconds"] = "1"
    else:
        evidence["type"] = fault
    with pytest.raises(ProviderSchemaError):
        await run_flat(tmp_path, item, branch_source=True)


@pytest.mark.asyncio
async def test_branch_binding_contact_sheet_matches_old_result_and_preserves_suffix(
    tmp_path: Path,
) -> None:
    item = flat_observation()
    item.update(
        start_seconds=50, end_seconds=60, repetition_start_seconds=52, repetition_end_seconds=54
    )
    item["tips"][0]["evidence"].update(start_seconds=51, end_seconds=52)
    suffix = '\nCaller marker: "repetition_context": null\n'
    previous, _ = await run_flat(
        tmp_path,
        item,
        input_kind="contact_sheet",
        window=Segment(start_seconds=50, end_seconds=90),
        suffix=suffix,
    )
    del item["tips"][0]["evidence"]["type"]
    bound, sent = await run_flat(
        tmp_path,
        item,
        input_kind="contact_sheet",
        window=Segment(start_seconds=50, end_seconds=90),
        suffix=suffix,
        branch_source=True,
    )
    assert bound == previous
    assert sent["instructions"].endswith(suffix)
    assert "frame sheets" in sent["instructions"]
    assert "time_rule" in sent["instructions"]


@pytest.mark.asyncio
async def test_branch_binding_rejects_ambiguous_prompt_contract(tmp_path: Path) -> None:
    from hakimi_analysis.providers.base import ProviderError

    item = flat_observation()
    del item["tips"][0]["evidence"]["type"]
    with pytest.raises(ProviderError) as raised:
        await run_flat(
            tmp_path,
            item,
            branch_source=True,
            suffix=(
                '\nand `evidence` {type:"visual",start_seconds,end_seconds} in clip-local time'
            ),
        )
    assert raised.value.code == "configuration_error"


@pytest.mark.asyncio
async def test_branch_binding_rejects_base_drift_even_when_suffix_quotes_old_contract(
    tmp_path: Path,
) -> None:
    from hakimi_analysis.providers.base import ProviderError

    base = SkillRepository.load(
        Path(__file__).resolve().parents[3] / "skills"
    ).visual_instructions
    item = flat_observation()
    del item["tips"][0]["evidence"]["type"]
    with pytest.raises(ProviderError) as raised:
        await run_flat(
            tmp_path,
            item,
            branch_source=True,
            base_override=base.replace("in clip-local time", "in the request time coordinate"),
            suffix=(
                '\nCaller quote: and `evidence` {type:"visual",start_seconds,end_seconds}'
                ' in clip-local time'
            ),
        )
    assert raised.value.code == "configuration_error"


@pytest.mark.asyncio
async def test_flat_contract_does_not_accept_incomplete_response(tmp_path: Path) -> None:
    with pytest.raises(ProviderSchemaError):
        await run_flat(tmp_path, flat_observation(), response_overrides={"status": "incomplete"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"store": True},
        {"store": None},
        {"text": {}},
        {"output": [{"content": [{"type": "refusal", "refusal": "synthetic"}]}]},
        {"error": {"code": "synthetic"}},
        {"incomplete_details": {"reason": "synthetic"}},
    ],
)
async def test_flat_contract_requires_assessable_matching_response(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    with pytest.raises(ProviderSchemaError):
        await run_flat(tmp_path, flat_observation(), response_overrides=overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["load", "duration", "none", "uncertain"])
async def test_flat_contract_keeps_parameter_meaning(tmp_path: Path, case: str) -> None:
    item = flat_observation()
    expected: dict[str, Any] | None
    if case == "load":
        item["repetition_purpose"] = "load_selection_condition"
        expected = {"sets": 4, "rest_seconds": 60}
    elif case == "duration":
        item["repetition_purpose"] = "load_selection_condition"
        item["text_parameters"].update(
            mode="duration", reps=None, reps_max=None, duration_seconds=30
        )
        expected = {"mode": "duration", "sets": 4, "duration_seconds": 30, "rest_seconds": 60}
    else:
        item["text_parameters"] = None
        if case == "none":
            for field in ("purpose", "caption", "start_seconds", "end_seconds"):
                item["repetition_" + field] = None
        else:
            item["repetition_purpose"] = "uncertain"
        expected = None
    result, _ = await run_flat(tmp_path, item)
    parameters = result.segments[0].text_parameters
    assert (parameters.model_dump(exclude_none=True) if parameters else None) == expected
    assert result.segments[0].tips[0].text == "保持动作平稳"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "missing",
        "old_object",
        "array",
        "unknown",
        "blank",
        "long",
        "reversed",
        "outside",
        "uncertain_reps",
        "no_context_reps",
        "missing_mode",
        "too_many_tips",
        "speech_tip",
        "non_json_number",
        "nan",
        "negative_infinity",
    ],
)
async def test_flat_contract_does_not_repair_invalid_context_or_source(
    tmp_path: Path, fault: str
) -> None:
    item = flat_observation()
    if fault == "partial":
        item["repetition_caption"] = None
    elif fault == "missing":
        del item["repetition_caption"]
    elif fault == "old_object":
        item["repetition_context"] = None
    elif fault == "array":
        item["repetition_purpose"] = ["training_target"]
    elif fault == "unknown":
        item["repetition_purpose"] = "synthetic_unknown"
    elif fault == "blank":
        item["repetition_caption"] = "  "
    elif fault == "long":
        item["repetition_caption"] = "合" * 121
    elif fault == "reversed":
        item["repetition_end_seconds"] = 1
    elif fault == "outside":
        item["repetition_end_seconds"] = 11
    elif fault == "uncertain_reps":
        item["repetition_purpose"] = "uncertain"
    elif fault == "no_context_reps":
        for field in ("purpose", "caption", "start_seconds", "end_seconds"):
            item["repetition_" + field] = None
    elif fault == "missing_mode":
        del item["text_parameters"]["mode"]
    elif fault == "too_many_tips":
        item["tips"] *= 4
    elif fault == "speech_tip":
        item["tips"][0]["evidence"]["type"] = "speech"
    elif fault == "non_json_number":
        item["tips"][0]["evidence"]["end_seconds"] = float("inf")
    elif fault == "nan":
        item["tips"][0]["evidence"]["end_seconds"] = float("nan")
    elif fault == "negative_infinity":
        item["tips"][0]["evidence"]["end_seconds"] = float("-inf")
    with pytest.raises(ProviderSchemaError):
        await run_flat(tmp_path, item)


@pytest.mark.asyncio
async def test_flat_contact_sheet_keeps_absolute_time_and_caller_suffix(tmp_path: Path) -> None:
    item = flat_observation()
    item.update(
        start_seconds=50, end_seconds=60, repetition_start_seconds=52, repetition_end_seconds=54
    )
    item["tips"][0]["evidence"].update(start_seconds=51, end_seconds=52)
    suffix = '\nCaller marker: "repetition_context": null\n'
    result, sent = await run_flat(
        tmp_path,
        item,
        input_kind="contact_sheet",
        window=Segment(start_seconds=50, end_seconds=90),
        suffix=suffix,
    )
    assert result.segments[0].start_seconds == 50
    assert result.segments[0].tips[0].evidence.start_seconds == 51
    assert sent["instructions"].endswith(suffix)
    assert "time_rule" in sent["instructions"]
    assert "不要复制示例字幕或时间" in sent["instructions"]
    assert "Every distinct numbered exercise" in sent["instructions"]
    assert "Never merge across" in sent["instructions"]
    assert "at most three short action" in sent["instructions"]
    assert (
        "Return approximate absolute source-video seconds."
        in sent["input"][0]["content"][1]["text"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("branch_source", [False, True])
async def test_flat_format_requires_explicit_purpose_opt_in(branch_source: bool) -> None:
    async with httpx.AsyncClient() as http:
        from hakimi_analysis.providers.base import ProviderError

        with pytest.raises(ProviderError) as raised:
            ArkResponsesClient(
                api_key="synthetic",
                model_id="synthetic",
                base_url="https://synthetic.test",
                http_client=http,
                visual_parameter_context_format="flat_branch_source" if branch_source else "flat",
            )
    assert raised.value.code == "configuration_error"
