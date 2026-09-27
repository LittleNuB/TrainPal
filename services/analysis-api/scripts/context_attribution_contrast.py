"""Explicit one-shot text trial; no credentials discovery or content persistence.

Both arms use the SAME context envelope and instructions. legacy_visible is an
information-equivalent control, NOT a literal replay of the production prompt.
Only complete_context restores omitted transcript / truncated visual text.
"""

import hashlib
import json
import time
from typing import Any

import httpx
from evidence_context_probe import EvidenceBatch, EvidenceBinding, run_context_probe
from tip_attribution_contrast import TrialAuditTransport, load_case, score_trial

from hakimi_analysis.models import SpeechSignal, Transcript, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticGrouping

CASE_IDS = ("ambiguous_transition", "visual_tail_scope", "spoken_negation")
ARMS = ("legacy_visible", "complete_context")


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def load_context_case(case_id: str) -> dict[str, Any]:
    if case_id not in CASE_IDS:
        raise ValueError("unknown_context_case")
    if case_id == "spoken_negation":
        good_tip = {"text": "胸部贴住靠垫", "category": "setup", "evidence": {
            "type": "speech", "start_seconds": 22, "end_seconds": 24,
        }}
        bad_tip = {"text": "耸肩", "category": "setup", "evidence": {
            "type": "speech", "start_seconds": 24, "end_seconds": 26,
        }}
        return {
            "id": case_id, "visual": [], "wrong_tip_id": "speech-1-tip-1",
            "speech": [{"action_name": "反向飞鸟", "start_seconds": 20,
                        "end_seconds": 30, "segment_role": "teaching_demo",
                        "evidence_text": "做反向飞鸟时", "tips": [bad_tip, good_tip]}],
            "transcript": {"text": "", "utterances": [{
                "text": "做反向飞鸟时，胸部贴住靠垫，不要耸肩。",
                "start_seconds": 20, "end_seconds": 30,
            }]},
            "groups": [{"member_ids": ["speech-1"], "name": "反向飞鸟",
                        "relation": "same_demonstration", "content_role": "exercise",
                        "related_member_id": None, "accepted_tip_ids": ["speech-1-tip-2"]}],
            "expected_outputs": [{"segment": [20, 30], "parameters": {}, "tips": [good_tip]}],
        }
    case = load_case("ambiguous_transition" if case_id == "ambiguous_transition"
                     else "previous_action_closing_caption")
    case["id"] = case_id
    case["speech"] = []
    case["transcript"] = {"text": "", "utterances": []}
    case["wrong_tip_id"] = "visual-2-tip-1"
    if case_id == "visual_tail_scope":
        # Constructed stress case, not a claim that a real video had this padding.
        filler = "画面中有人与训练器械，背景无新增可读文字。" * 40
        case["visual"][0]["visual_cue"] = (
            "第三项杠铃提拉教学。" + filler
            + "150至152秒仍手持杠铃做提拉，字幕坚持完成4组，随后切镜头。"
        )
        case["visual"][1]["visual_cue"] = (
            "第四项反向飞鸟教学，170至172秒双手朝两侧打开的明确指导字幕。" + filler
            + "特别记录：150至152秒仍是上一动作杠铃提拉的收尾；坚持完成4组针对提拉，"
            "并非反向飞鸟。152秒之后才开始介绍反向飞鸟。"
        )
    return case


class _RecordedModel:
    def __init__(self, delegate: ArkResponsesClient) -> None:
        self.delegate = delegate
        self.grouping: SemanticGrouping | None = None
        self.request_hash: str | None = None

    async def group_action_evidence(
        self, *, observations: list[dict[str, object]], instructions: str,
    ) -> SemanticGrouping:
        self.request_hash = fingerprint([instructions, observations])
        self.grouping = await self.delegate.group_action_evidence(
            observations=observations, instructions=instructions,
        )
        return self.grouping


class ContextAttributionTrial:
    def __init__(
        self, *, case_id: str, arm: str, http_client: httpx.AsyncClient,
        api_key: str, model_id: str, base_url: str, instructions: str,
    ) -> None:
        if arm not in ARMS:
            raise ValueError("unknown_context_arm")
        self.case = load_context_case(case_id)
        self.arm = arm
        self.instructions = instructions
        self.used = False
        self.transport = TrialAuditTransport(http_client)
        self.client = httpx.AsyncClient(transport=self.transport, timeout=20)
        self.model = _RecordedModel(ArkResponsesClient(
            api_key=api_key, model_id=model_id, base_url=base_url,
            http_client=self.client, retry_delays=(),
        ))

    async def run(self) -> dict[str, Any]:
        if self.used:
            raise RuntimeError("trial_already_used")
        self.used = True
        started = time.monotonic()
        binding = EvidenceBinding(
            run_id="synthetic-context-trial", source_id="synthetic-context-source", version="v1",
        )
        visual = [VisualSegment.model_validate(v) for v in self.case["visual"]]
        transcript = Transcript.model_validate(self.case["transcript"])
        if self.arm == "legacy_visible":
            transcript = Transcript(text="", utterances=[])
            visual = [v.model_copy(update={"visual_cue": v.visual_cue[:600]}) for v in visual]
        batch = EvidenceBatch(
            binding=binding, transcript=transcript, visual=visual,
            speech=[SpeechSignal.model_validate(s) for s in self.case["speech"]],
        )
        try:
            async with self.client:
                result, delivery = await run_context_probe(
                    batch=batch, expected_binding=binding,
                    model=self.model, instructions=self.instructions,
                )
            checks = score_trial(self.case, self.model.grouping, result)
            unavailable = delivery["status"] != "context_delivered" or any(
                key.startswith("unavailable_") for key in result.diagnostics
            )
            invalid = self.transport.output_format_valid is not True
            if unavailable or invalid:
                checks["passed"] = False
            return {
                "case": self.case["id"], "arm": self.arm,
                "status": "invalid_output_format" if self.transport.output_format_valid is False
                else "unavailable" if unavailable or invalid else
                "passed" if checks["passed"] else "quality_failed",
                **checks, "output_format_valid": self.transport.output_format_valid,
                "http_requests": self.transport.requests,
                "request_sha256": self.model.request_hash,
                "case_sha256": fingerprint(self.case),
                "seconds": round(time.monotonic() - started, 3),
            }
        finally:
            self.model.grouping = None
