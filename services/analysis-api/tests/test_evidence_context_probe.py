"""Synthetic evidence at the approved Provider -> fusion / HTTP boundary."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import Segment, Transcript, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion

ROOT = Path(__file__).resolve().parents[3]
SENTENCE = "做反向飞鸟时，胸部贴住靠垫，不要耸肩。"


def load_probe() -> ModuleType:
    path = ROOT / "services/analysis-api/scripts/evidence_context_probe.py"
    spec = importlib.util.spec_from_file_location("evidence_context_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run_probe(
    *, enriched: bool = True, sentence: str = SENTENCE,
    support: str = "做反向飞鸟时", tip: str = "胸部贴住靠垫",
    visual: list[VisualSegment] | None = None, failure: str | None = None, **options: Any,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    wire: dict[str, Any] = {}
    transcript = Transcript(text=sentence, utterances=[{
        "text": sentence, "start_seconds": 1, "end_seconds": 8,
    }])

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["text"]["format"]["name"] == "training_speech_understanding":
            output = {"signals": [{
                "action_name": "反向飞鸟", "start_seconds": 1, "end_seconds": 8,
                "evidence_text": support, "segment_role": "teaching_demo", "sets": 3,
                "tips": [{"text": tip, "category": "setup", "evidence": {
                    "type": "speech", "start_seconds": 2, "end_seconds": 5,
                }}],
            }]}
        else:
            wire.update(payload)
            if failure == "timeout":
                raise TimeoutError("synthetic private content")
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "schema":
                return httpx.Response(200, json={"output_text": "invalid synthetic content"})
            output = {"groups": [{
                "member_ids": ["speech-1"], "name": "反向飞鸟",
                "relation": "same_demonstration", "content_role": "exercise",
                "related_member_id": None, "accepted_tip_ids": ["speech-1-tip-1"],
            }]}
        return httpx.Response(200, json={"output_text": json.dumps(output)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="synthetic", model_id="synthetic", base_url="https://offline.test",
            http_client=client, retry_delays=(),
        )
        speech = await model.understand_speech(
            transcript=transcript.model_dump(), window=Segment(start_seconds=0, end_seconds=10),
            instructions="Synthetic test only",
        )
        if enriched:
            tool = load_probe()
            binding = tool.EvidenceBinding(run_id="run-1", source_id="source-1", version="v1")
            batch = tool.EvidenceBatch(
                binding=binding, transcript=transcript, speech=speech.signals, visual=visual or [],
            )
            result, receipt = await tool.run_context_probe(
                batch=batch, expected_binding=binding, model=model,
                instructions="Synthetic test only", **options,
            )
        else:
            result = await SemanticCandidateFusion(model=model, instructions="Synthetic").run(
                source_id="source-1", speech_signals=speech.signals, visual_segments=[],
            )
            receipt = {}
    return result, receipt, wire


def envelope(wire: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(wire["input"][0]["content"][0]["text"])["observations"][0]
    return value


@pytest.mark.asyncio
async def test_full_speech_context_reaches_http_without_changing_source_tip_or_parameters() -> None:
    baseline, _, old = await run_probe(enriched=False)
    assert SENTENCE not in json.dumps(old, ensure_ascii=False)
    result, receipt, wire = await run_probe()
    context = envelope(wire)["evidence_context"]
    record = context["records"][0]
    assert record["text"] == SENTENCE
    assert record["producer"] == "asr_transcript_unverified"
    assert record["start_seconds"] == 1 and record["end_seconds"] == 8
    assert context["links"]["speech-1"]["support_refs"] == [record["id"]]
    assert context["links"]["speech-1"]["attribution"] == "not_reviewed"
    assert result == baseline
    assert receipt["status"] == "context_delivered"
    assert SENTENCE not in json.dumps(receipt, ensure_ascii=False)


@pytest.mark.asyncio
async def test_visual_tail_and_non_training_transition_are_not_invented_or_truncated() -> None:
    description = "合成画面描述。" * 100 + "尾部：字幕仍是上一动作收尾，归属不清。"
    visual = [VisualSegment(
        action_name="反向飞鸟", start_seconds=1, end_seconds=8,
        visual_cue=description, segment_role="teaching_demo",
    ), VisualSegment(
        action_name=None, start_seconds=8, end_seconds=9,
        visual_cue="画面转场，未观察下一动作。", is_training_content=False,
    )]
    _, _, wire = await run_probe(visual=visual)
    context = envelope(wire)["evidence_context"]
    records = [r for r in context["records"] if r["producer"] == "visual_model_unverified"]
    assert [r["text"] for r in records] == [description, "画面转场，未观察下一动作。"]
    assert envelope(wire)["observations"][1]["description"] == description
    assert context["gaps"] == ["visual_caption_anchors_unavailable",
                               "visual_chapter_anchors_unavailable",
                               "visual_boundary_coverage_unavailable"]
    assert context["links"]["visual-1"]["support_refs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sentence,support,tip", [
    ("做反向飞鸟，不要耸肩。", "反向飞鸟", "耸肩"),
    ("如果做左侧动作，保持左手不动；下一个动作再换右手。", "左侧动作", "保持左手不动"),
    ("当前做划船，下一个反向飞鸟要胸部贴住靠垫。", "反向飞鸟", "胸部贴住靠垫"),
])
async def test_substring_is_traceability_not_approval_and_full_sentence_survives(
    sentence: str, support: str, tip: str,
) -> None:
    _, _, wire = await run_probe(sentence=sentence, support=support, tip=tip)
    context = envelope(wire)["evidence_context"]
    assert context["records"][0]["text"] == sentence
    link = context["links"]["speech-1"]
    assert link["support_match"] == "exact_substring_only"
    assert link["tips"]["speech-1-tip-1"]["content_match"] == "exact_substring_only"
    assert link["attribution"] == "not_reviewed"


@pytest.mark.asyncio
async def test_hallucinated_support_is_not_sent_as_source_evidence() -> None:
    _, _, wire = await run_probe(support="不存在的支持原句")
    link = envelope(wire)["evidence_context"]["links"]["speech-1"]
    assert link["support_refs"] == []
    assert link["support_match"] == "unmatched"
    assert "不存在的支持原句" not in json.dumps(wire, ensure_ascii=False)


@pytest.mark.asyncio
async def test_budget_refuses_whole_context_instead_of_cutting_tail() -> None:
    result, receipt, wire = await run_probe(sentence=SENTENCE + "长" * 25000 + "但仅用于上一动作")
    assert not wire
    assert receipt["status"] == "context_budget_exceeded"
    assert result.candidates and not result.candidates[0].tips


async def inspect_batch(
    *, change: dict[str, Any] | None = None, binding_change: dict[str, str] | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    tool = load_probe()
    binding = tool.EvidenceBinding(run_id="run-1", source_id="source-1", version="v1")
    data: dict[str, Any] = {
        "binding": binding,
        "transcript": {"text": "", "utterances": [
            {"text": "上一动作结束。", "start_seconds": 4, "end_seconds": 6},
            {"text": SENTENCE, "start_seconds": 11, "end_seconds": 14},
            {"text": "下一个动作是划船。", "start_seconds": 15, "end_seconds": 18},
            {"text": "远处无关内容", "start_seconds": 80, "end_seconds": 90},
        ]},
        "speech": [{"action_name": "反向飞鸟", "evidence_text": "做反向飞鸟时",
                    "start_seconds": 10, "end_seconds": 15}], "visual": [],
    }
    data.update(change or {})
    batch = tool.EvidenceBatch(**data)
    original = batch.model_dump_json()
    expected = binding.model_copy(update=binding_change or {})
    wire: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        wire.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps({"groups": [{
            "member_ids": ["speech-1"], "name": "反向飞鸟",
            "relation": "same_demonstration", "content_role": "exercise",
            "related_member_id": None, "accepted_tip_ids": [],
        }]})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(api_key="synthetic", model_id="synthetic",
                                   base_url="https://offline.test", http_client=client,
                                   retry_delays=())
        try:
            result, receipt = await tool.run_context_probe(
                batch=batch, expected_binding=expected, model=model, instructions="Synthetic",
            )
        except ValueError:
            assert not wire, "invalid input must fail before HTTP"
            raise
    assert batch.model_dump_json() == original
    return result, receipt, wire


@pytest.mark.asyncio
async def test_neighbors_preserve_transition_and_gap_without_sending_unrelated_transcript() -> None:
    _, _, wire = await inspect_batch()
    context = envelope(wire)["evidence_context"]
    assert [r["text"] for r in context["records"]] == [
        "上一动作结束。", SENTENCE, "下一个动作是划船。",
    ]
    assert context["continuity"] == "unverified"
    assert "transcript_time_gap" in context["gaps"]
    assert context["records"][1]["start_seconds"] == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["run_id", "source_id", "version"])
async def test_cross_run_source_and_stale_version_rejected_before_http(field: str) -> None:
    with pytest.raises(ValueError, match="evidence_binding_mismatch"):
        await inspect_batch(binding_change={field: "different"})


@pytest.mark.asyncio
async def test_source_ids_change_when_evidence_changes_and_all_refs_resolve() -> None:
    _, _, first = await inspect_batch()
    _, _, second = await inspect_batch(change={"transcript": {"text": "", "utterances": [
        {"text": SENTENCE + "第二轮。", "start_seconds": 11, "end_seconds": 14},
    ]}})
    a, b = (envelope(w)["evidence_context"] for w in [first, second])
    assert set(r["id"] for r in a["records"]).isdisjoint(r["id"] for r in b["records"])
    for context in [a, b]:
        assert set(context["links"]["speech-1"]["support_refs"]) <= {
            r["id"] for r in context["records"]
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(14, 11), (11, 11), (float("nan"), 14)])
async def test_invalid_evidence_time_rejected_before_http(start: float, end: float) -> None:
    with pytest.raises(ValueError):
        await inspect_batch(change={"transcript": {"text": "", "utterances": [
            {"text": SENTENCE, "start_seconds": start, "end_seconds": end},
        ]}})


@pytest.mark.asyncio
async def test_missing_legacy_context_remains_missing_without_rewriting_input() -> None:
    _, receipt, wire = await inspect_batch(change={"transcript": {"text": "", "utterances": []}})
    context = envelope(wire)["evidence_context"]
    assert context["records"] == []
    assert context["gaps"] == ["source_context_missing"]
    assert context["links"]["speech-1"]["support_match"] == "unmatched"
    assert receipt["semantic_quality_evaluated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "schema"])
async def test_technical_failure_is_not_reported_as_quality_success(failure: str) -> None:
    result, receipt, _ = await run_probe(failure=failure)
    assert receipt["status"] == "context_not_delivered"
    assert receipt["semantic_quality_evaluated"] is False
    assert result.candidates and not result.candidates[0].tips
    assert "synthetic private" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_cancel_is_not_hidden_by_probe() -> None:
    with pytest.raises(asyncio.CancelledError):
        await run_probe(failure="cancel")


@pytest.mark.asyncio
async def test_tip_references_follow_filtered_public_observations_not_raw_list_positions() -> None:
    _, _, wire = await inspect_batch(change={"speech": [{
        "action_name": "反向飞鸟", "evidence_text": "反向飞鸟",
        "start_seconds": 10, "end_seconds": 15,
        "tips": [{"text": text, "category": "setup", "evidence": {
            "type": "speech", "start_seconds": 11, "end_seconds": 14,
        }} for text in ["保证必瘦", "胸部贴住靠垫", "胸部贴住靠垫"]],
    }]})
    data = envelope(wire)
    assert [t["text"] for t in data["observations"][0]["tip_evidence"]] == ["胸部贴住靠垫"]
    links = data["evidence_context"]["links"]["speech-1"]["tips"]
    assert set(links) == {"speech-1-tip-1"}
    assert links["speech-1-tip-1"]["content_match"] == "exact_substring_only"
    assert links["speech-1-tip-1"]["content_refs"]


@pytest.mark.asyncio
async def test_overlapping_utterances_do_not_create_a_false_time_gap() -> None:
    _, _, wire = await inspect_batch(change={"transcript": {"text": "", "utterances": [
        {"text": SENTENCE, "start_seconds": 1, "end_seconds": 20},
        {"text": "重叠语句甲", "start_seconds": 5, "end_seconds": 6},
        {"text": "重叠语句乙", "start_seconds": 10, "end_seconds": 11},
    ]}})
    context = envelope(wire)["evidence_context"]
    assert "transcript_time_gap" not in context["gaps"]
    assert context["continuity"] == "unverified"
