"""Explicit ordinal-instruction pilot at the Provider HTTP boundary."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment
from hakimi_analysis.orchestration import SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.providers.base import ProviderError
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


@pytest.mark.asyncio
async def test_explicit_ordinal_clarity_preserves_qualified_label_and_source(
    tmp_path: Path,
) -> None:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")
    sent: list[dict[str, Any]] = []
    observation = {
        "action_name": "左侧弓步",
        "start_seconds": 2,
        "end_seconds": 7,
        "visual_cue": "动作六：左侧弓步",
        "segment_role": "teaching_demo",
        "sequence_label": "动作六 左侧",
        "is_training_content": True,
        "text_parameters": None,
        "tips": [
            {
                "text": "保持动作平稳",
                "category": "rhythm",
                "evidence": {"type": "visual", "start_seconds": 3, "end_seconds": 4},
            }
        ],
    }

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"segments": [observation]})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            clarify_visual_ordinals=True,
        )
        result = await provider.locate_visual(
            video_path=media,
            window=Segment(start_seconds=0, end_seconds=10),
            instructions=SkillRepository.load(
                Path(__file__).resolve().parents[3] / "skills"
            ).visual_instructions,
        )
    assert result.segments[0].model_dump(mode="json") == observation
    instructions = sent[0]["instructions"]
    assert "transcription of an explicitly visible exercise heading" not in instructions
    assert "Copy only the explicitly visible exercise ordinal" in instructions
    assert "Never strip a qualifier" in instructions
    assert "complete readable heading in visual_cue" in instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("mode", ["plain", "nested", "flat"])
@pytest.mark.parametrize("kind", ["video", "contact_sheet"])
async def test_clarity_preserves_other_prompt_rules_schema_and_time_contract(
    tmp_path: Path, enabled: bool, mode: str, kind: str
) -> None:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": body["text"],
                "output_text": '{"segments":[]}',
            },
        )

    base = SkillRepository.load(Path(__file__).resolve().parents[3] / "skills").visual_instructions
    suffix = "\nSynthetic caller rule."
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        for clarity in (False, enabled):
            provider = ArkResponsesClient(
                api_key="synthetic",
                model_id="synthetic",
                base_url="https://synthetic.test",
                http_client=http,
                retry_delays=(),
                clarify_visual_ordinals=clarity,
                visual_parameter_context=mode != "plain",
                visual_parameter_context_format="flat" if mode == "flat" else "nested",
            )
            if kind == "video":
                await provider.locate_visual(
                    video_path=media,
                    window=Segment(start_seconds=0, end_seconds=40),
                    instructions=base + suffix,
                )
            else:
                await provider.locate_visual_contact_sheet(
                    image_path=media,
                    frame_times_seconds=(50, 70),
                    window=Segment(start_seconds=50, end_seconds=90),
                    instructions=base + suffix,
                )
    baseline, current = sent
    if not enabled:
        assert current == baseline
        return
    original = baseline.pop("instructions")
    changed = current.pop("instructions")
    assert current == baseline
    assert original.split("- `sequence_label`", 1)[0] == changed.split("- `sequence_label`", 1)[0]
    assert original.split("- Merge only", 1)[1] == changed.split("- Merge only", 1)[1]
    assert "Synthetic caller rule." in changed
    assert "Copy only the explicitly visible exercise ordinal" in changed
    assert all(value not in changed for value in ("动作四", "高位下拉", "大剪刀"))


@pytest.mark.asyncio
async def test_unknown_visual_instruction_layout_fails_before_http(tmp_path: Path) -> None:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")
    sent: list[bool] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(True)
        return httpx.Response(200, json={"output_text": '{"segments":[]}'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            clarify_visual_ordinals=True,
        )
        with pytest.raises(ProviderError) as error:
            await provider.locate_visual(
                video_path=media,
                window=Segment(start_seconds=0, end_seconds=10),
                instructions="Unknown instructions",
            )
    assert error.value.code == "configuration_error"
    assert sent == []


@pytest.mark.asyncio
async def test_changed_ordinal_rules_are_not_silently_replaced(tmp_path: Path) -> None:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")
    base = SkillRepository.load(Path(__file__).resolve().parents[3] / "skills").visual_instructions
    changed = base.replace("- Merge only", "  Additional future identity safeguard.\n- Merge only")
    sent: list[bool] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(True)
        return httpx.Response(200, json={"output_text": '{"segments":[]}'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            clarify_visual_ordinals=True,
        )
        with pytest.raises(ProviderError) as error:
            await provider.locate_visual(
                video_path=media,
                window=Segment(start_seconds=0, end_seconds=10),
                instructions=changed,
            )
    assert error.value.code == "configuration_error"
    assert sent == []


@pytest.mark.asyncio
async def test_clarity_does_not_modify_speech_or_grouping_instructions() -> None:
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        response: dict[str, Any] = {"signals": []} if len(sent) == 1 else {"groups": []}
        return httpx.Response(200, json={"output_text": json.dumps(response)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            clarify_visual_ordinals=True,
        )
        await provider.understand_speech(
            transcript={"text": "", "utterances": []},
            window=Segment(start_seconds=0, end_seconds=10),
            instructions="speech unchanged",
        )
        await provider.group_action_evidence(observations=[], instructions="grouping unchanged")
    assert [body["instructions"] for body in sent] == ["speech unchanged", "grouping unchanged"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels",
    [
        ("动作一", "动作二"),
        ("动作六 左侧", "动作六 右侧"),
        ("动作六 第1轮", "动作六 第2轮"),
        ("动作六 宽握", "动作六 窄握"),
    ],
)
async def test_clarity_never_strips_identity_boundaries_before_fusion(
    tmp_path: Path, labels: tuple[str, str]
) -> None:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["text"]["format"]["name"] == "visual_action_localization":
            result: dict[str, Any] = {
                "segments": [
                    {
                        "action_name": "合成训练动作",
                        "start_seconds": start,
                        "end_seconds": end,
                        "visual_cue": "合成可见教学",
                        "segment_role": "teaching_demo",
                        "sequence_label": label,
                        "is_training_content": True,
                        "text_parameters": None,
                        "tips": [],
                    }
                    for label, start, end in ((labels[0], 1, 5), (labels[1], 3, 7))
                ]
            }
        else:
            observations = json.loads(body["input"][0]["content"][0]["text"])["observations"]
            assert tuple(item["sequence_label"] for item in observations) == labels
            result = {
                "groups": [
                    {
                        "member_ids": [item["id"] for item in observations],
                        "name": "合成训练动作",
                        "relation": "same_demonstration",
                        "content_role": "exercise",
                        "related_member_id": None,
                        "accepted_tip_ids": [],
                    }
                ]
            }
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    skills = SkillRepository.load(Path(__file__).resolve().parents[3] / "skills")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            clarify_visual_ordinals=True,
        )
        visual = await provider.locate_visual(
            video_path=media,
            window=Segment(start_seconds=0, end_seconds=10),
            instructions=skills.visual_instructions,
        )
        fused = await SemanticCandidateFusion(
            model=provider, instructions=skills.fusion_instructions
        ).run(source_id="synthetic", speech_signals=[], visual_segments=visual.segments)
    assert len(fused.candidates) == 2
    assert all(candidate.needs_confirmation for candidate in fused.candidates)
    assert fused.diagnostics["sequence_label_conflict"] == 1
    assert [(c.segment.start_seconds, c.segment.end_seconds) for c in fused.candidates] == [
        (1, 5),
        (3, 7),
    ]
