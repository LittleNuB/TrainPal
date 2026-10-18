"""Shared speech context at the public Provider HTTP -> fusion interface."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import (
    CandidateParameters,
    SegmentRole,
    SourceTip,
    SpeechSignal,
    TranscriptUtterance,
    VisualSegment,
)
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult
from hakimi_analysis.shared_source_context import SpeechSourceContext


@pytest.mark.asyncio
async def test_one_source_statement_does_not_add_a_third_execution() -> None:
    speech = [
        SpeechSignal(
            action_name="弹力带热身与泡沫轴放松",
            start_seconds=10,
            end_seconds=14,
            segment_role="teaching_demo",
            evidence_text="先热身再放松",
        )
    ]
    visual = [
        VisualSegment(
            action_name="弹力带热身",
            start_seconds=10,
            end_seconds=12,
            segment_role="teaching_demo",
            visual_cue="弹力带教学",
        ),
        VisualSegment(
            action_name="泡沫轴放松",
            start_seconds=12,
            end_seconds=14,
            segment_role="teaching_demo",
            visual_cue="泡沫轴教学",
        ),
    ]
    original = "先做弹力带热身，然后做泡沫轴放松，这是两个不同动作。"
    context = SpeechSourceContext.bind(
        source_id="synthetic",
        speech_signals=speech,
        visual_segments=visual,
        utterances=[TranscriptUtterance(text=original, start_seconds=10, end_seconds=14)],
    )
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        for definition in payload["text"]["format"]["schema"]["$defs"].values():
            if "properties" in definition:
                assert set(definition["required"]) == set(definition["properties"])
        body = json.loads(payload["input"][0]["content"][0]["text"])
        sent.append(body)
        quote = body["source_context"]["utterances"][0]
        assert quote["text"] == original
        result = {
            "groups": [
                {
                    "member_ids": ["visual-1"],
                    "name": "弹力带热身",
                    "relation": "same_demonstration",
                    "accepted_tip_ids": [],
                },
                {
                    "member_ids": ["visual-2"],
                    "name": "泡沫轴放松",
                    "relation": "same_demonstration",
                    "accepted_tip_ids": [],
                },
            ],
            "shared_sources": [
                {
                    "member_id": "speech-1",
                    "target_member_ids": ["visual-1", "visual-2"],
                    "quote_ids": [quote["id"]],
                    "relation": "multiple_distinct_actions",
                }
            ],
        }
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            retry_delays=(),
        )
        result = await SemanticCandidateFusion(
            model=model, instructions="synthetic", source_context_model=model
        ).run(
            source_id="synthetic",
            speech_signals=speech,
            visual_segments=visual,
            speech_context=context,
        )
    assert len(sent) == 1
    assert [candidate.name for candidate in result.candidates] == ["弹力带热身", "泡沫轴放松"]
    assert [(c.segment.start_seconds, c.segment.end_seconds) for c in result.candidates] == [
        (10, 12),
        (12, 14),
    ]
    for candidate in result.candidates:
        assert candidate.needs_confirmation
        assert candidate.parameters.model_dump(exclude_none=True) == {}
        assert candidate.tips == []
        assert any(
            e.type == "speech" and e.start_seconds == 10 and e.end_seconds == 14
            for e in candidate.evidence
        )


async def run_case(
    fault: str = "none", *, enabled: bool = True, instructions: str = "synthetic",
    speech_edges: bool = False, allow_source_edges: bool = False,
) -> tuple[SemanticFusionResult, list[dict[str, Any]]]:
    speech = [
        SpeechSignal(
            action_name="两个热身动作",
            start_seconds=10,
            end_seconds=14,
            segment_role="teaching_demo",
            evidence_text="synthetic",
        )
    ]
    visual = [
        VisualSegment(
            action_name=name,
            start_seconds=start,
            end_seconds=end,
            segment_role="teaching_demo",
            visual_cue="synthetic",
        )
        for name, start, end in (("弹力带热身", 10, 12), ("泡沫轴放松", 12, 14))
    ]
    quotes = [
        TranscriptUtterance(
            text="先做弹力带热身，再做泡沫轴放松。", start_seconds=10, end_seconds=14
        )
    ]
    if speech_edges:
        speech[0].start_seconds = quotes[0].start_seconds = 9.68
        speech[0].end_seconds = quotes[0].end_seconds = 14.64
    if fault == "parameter":
        speech[0].sets = 4
        speech[0].reps = 12
    if fault == "quote_gap":
        quotes[0].end_seconds = 13
    if fault == "context_budget":
        quotes[0].text = "synthetic " * 7000
    if fault == "visual_gap":
        visual[1].start_seconds = 12.5
    if fault == "follow_along":
        visual[1].segment_role = SegmentRole.FOLLOW_ALONG
    if fault == "source_follow_along":
        speech[0].segment_role = SegmentRole.FOLLOW_ALONG
    if fault == "no_overlap":
        speech[0].end_seconds = quotes[0].end_seconds = 12
    if fault == "third_action":
        visual.append(VisualSegment(
            action_name="第三个独立动作", start_seconds=9.8, end_seconds=10,
            segment_role="teaching_demo", visual_cue="synthetic",
        ))
    if fault == "same_action":
        visual[1].action_name = visual[0].action_name
    if fault == "target_parameters":
        visual[0].text_parameters = CandidateParameters(sets=4)
        speech.append(
            SpeechSignal(
                action_name="弹力带热身",
                start_seconds=10,
                end_seconds=12,
                sets=5,
                evidence_text="synthetic",
                segment_role="teaching_demo",
            )
        )
    if fault == "reminder":
        speech[0].tips = [
            SourceTip(
                text="先做弹力带热身，再做泡沫轴放松。",
                category="setup",
                evidence={"type": "speech", "start_seconds": 10, "end_seconds": 14},
            )
        ]
        visual[0].tips = [
            SourceTip(
                text="保持平稳",
                category="rhythm",
                evidence={"type": "visual", "start_seconds": 10, "end_seconds": 11},
            )
        ]
    context = SpeechSourceContext.bind(
        source_id="foreign" if fault == "source" else "synthetic",
        speech_signals=speech,
        visual_segments=visual,
        utterances=quotes,
    )
    if fault == "stale":
        speech[0].action_name = "另一份输入"
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        body = json.loads(payload["input"][0]["content"][0]["text"])
        requests.append(payload)
        if fault == "provider_error":
            return httpx.Response(503, json={"error": "synthetic"})
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if fault == "cancel":
            raise asyncio.CancelledError
        if fault == "invalid_response":
            return httpx.Response(200, json={"output_text": "not-json"})
        groups = [
            {
                "member_ids": [f"visual-{index}"],
                "name": item.action_name,
                "relation": "uncertain"
                if fault == "target_rejected" and index == 1
                else "same_demonstration",
                "accepted_tip_ids": ["visual-1-tip-1"]
                if fault == "reminder" and index == 1
                else [],
            }
            for index, item in enumerate(visual, 1)
        ]
        if fault == "target_parameters":
            groups[0]["member_ids"] = ["visual-1", "speech-2"]
        if not enabled or fault == "not_proposed":
            if not enabled:
                assert "source_context" not in body
                assert "shared_sources" not in payload["text"]["format"]["schema"]["properties"]
            groups.append(
                {
                    "member_ids": ["speech-1"],
                    "name": speech[0].action_name,
                    "relation": "uncertain",
                    "accepted_tip_ids": [],
                }
            )
            result = {"groups": groups}
            if enabled:
                result["shared_sources"] = []
        else:
            quote_id = body["source_context"]["utterances"][0]["id"]
            targets = ["visual-1", "visual-2"]
            if fault == "missing_target":
                targets[1] = "visual-missing"
            if fault == "repeat_target":
                targets[1] = "visual-1"
            if fault == "same_group":
                groups = [
                    {
                        "member_ids": ["visual-1", "visual-2"],
                        "name": "两个热身动作",
                        "relation": "same_demonstration",
                        "accepted_tip_ids": [],
                    }
                ]
            result = {
                "groups": groups,
                "shared_sources": [
                    {
                        "member_id": "speech-1",
                        "target_member_ids": targets,
                        "quote_ids": ["old:quote-1" if fault == "quote" else quote_id],
                        "relation": "multiple_distinct_actions",
                    }
                ],
            }
            if fault == "membership_duplicate":
                result["groups"].append(
                    {
                        "member_ids": ["speech-1"],
                        "name": "两个热身动作",
                        "relation": "uncertain",
                        "accepted_tip_ids": [],
                    }
                )
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        model = ArkResponsesClient(
            api_key="test",
            model_id="test",
            base_url="https://test.invalid",
            http_client=http,
            retry_delays=(0, 0),
        )
        result = await SemanticCandidateFusion(
            model=model, instructions=instructions, source_context_model=model if enabled else None,
            allow_shared_source_edge_overhang=allow_source_edges,
        ).run(
            source_id="synthetic",
            speech_signals=speech,
            visual_segments=visual,
            speech_context=context,
        )
    return result, requests


@pytest.mark.asyncio
async def test_shared_edge_pilot_preserves_two_actions_and_complete_source() -> None:
    old, old_requests = await run_case(speech_edges=True)
    trial, trial_requests = await run_case(speech_edges=True, allow_source_edges=True)
    assert len(old_requests) == len(trial_requests) == 1
    assert len(old.candidates) == 3
    assert len(trial.candidates) == 2
    assert trial.diagnostics["shared_source_attached"] == 1
    assert [(c.name, c.segment.start_seconds, c.segment.end_seconds) for c in trial.candidates] == [
        ("弹力带热身", 10, 12), ("泡沫轴放松", 12, 14),
    ]
    for candidate in trial.candidates:
        assert candidate.needs_confirmation
        assert not candidate.tips and not candidate.parameters.model_dump(exclude_none=True)
        assert any(e.type == "speech" and e.start_seconds == 9.68 and e.end_seconds == 14.64
                   for e in candidate.evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_source_edges", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "parameter",
        "quote",
        "quote_gap",
        "visual_gap",
        "missing_target",
        "repeat_target",
        "follow_along",
        "same_action",
        "target_rejected",
        "same_group",
    ],
)
async def test_unproven_shared_statement_is_retained_instead_of_disappearing(
    fault: str, allow_source_edges: bool,
) -> None:
    result, requests = await run_case(
        fault, speech_edges=allow_source_edges, allow_source_edges=allow_source_edges,
    )
    assert len(requests) == 1
    assert len(result.candidates) == 3
    original = next(c for c in result.candidates if c.name == "两个热身动作")
    assert original.needs_confirmation
    assert result.diagnostics["shared_source_rejected"] == 1
    if fault == "parameter":
        assert original.parameters.sets == 4 and original.parameters.reps == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["source", "stale"])
async def test_stale_or_foreign_context_is_not_sent_to_provider(fault: str) -> None:
    result, requests = await run_case(fault)
    assert requests == []
    assert len(result.candidates) == 3
    assert result.diagnostics == {"unavailable_source_context": 1}


@pytest.mark.asyncio
async def test_default_contract_stays_unchanged_even_if_context_was_supplied() -> None:
    result, requests = await run_case(enabled=False)
    assert len(requests) == 1
    assert len(result.candidates) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_bundled_prompt_has_one_consistent_membership_contract(enabled: bool) -> None:
    instructions = (
        Path(__file__).resolve().parents[3] / "skills/candidate-fusion/SKILL.md"
    ).read_text(encoding="utf-8")
    result, requests = await run_case(enabled=enabled, instructions=instructions)
    sent = requests[0]["instructions"]
    if not enabled:
        assert sent == instructions
        assert len(result.candidates) == 3
        return
    assert "Every input id must appear exactly once across all groups" not in sent
    assert "Return ONLY the supplied JSON schema: groups with" not in sent
    assert "Speech training requirements and explicit follow_along observations remain" not in sent
    assert "groups.member_ids or shared_sources.member_id" in sent
    assert "top-level groups and shared_sources" in sent
    assert "Never turn an execution timeline into a catalog" in sent
    assert "Do not carry a previous action's closing instruction into the next action" in sent
    assert len(result.candidates) == 2
    assert all(c.needs_confirmation for c in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_source_edges", [False, True])
async def test_shared_reminder_is_not_moved_and_omission_is_explicit(
    allow_source_edges: bool,
) -> None:
    result, _ = await run_case(
        "reminder", speech_edges=allow_source_edges, allow_source_edges=allow_source_edges,
    )
    assert len(result.candidates) == 2
    assert [[tip.text for tip in item.tips] for item in result.candidates] == [["保持平稳"], []]
    assert result.diagnostics["shared_source_tips_unassigned"] == 1
    assert any("提醒" in warning.message for warning in result.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_source_edges", [False, True])
async def test_conflicting_target_does_not_make_shared_source_disappear(
    allow_source_edges: bool,
) -> None:
    result, _ = await run_case(
        "target_parameters", speech_edges=allow_source_edges, allow_source_edges=allow_source_edges,
    )
    assert len(result.candidates) == 3
    assert any(c.name == "两个热身动作" and c.needs_confirmation for c in result.candidates)
    target = next(c for c in result.candidates if c.name == "弹力带热身")
    assert target.parameter_conflicts


@pytest.mark.asyncio
@pytest.mark.parametrize("fault, count", [("third_action", 4), ("no_overlap", 3),
                                         ("source_follow_along", 3), ("not_proposed", 3)])
async def test_edge_pilot_does_not_invent_shared_decisions_or_ignore_other_executions(
    fault: str, count: int,
) -> None:
    result, requests = await run_case(fault, speech_edges=True, allow_source_edges=True)
    assert len(requests) == 1
    assert len(result.candidates) == count
    assert any(c.name == "两个热身动作" and c.needs_confirmation for c in result.candidates)
    assert result.diagnostics.get("shared_source_attached", 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["provider_error", "timeout", "invalid_response"])
async def test_context_failure_preserves_candidates_without_retry(fault: str) -> None:
    result, requests = await run_case(fault)
    assert len(requests) == 1
    assert len(result.candidates) == 3
    assert all(c.needs_confirmation and not c.tips for c in result.candidates)
    assert any(w.code == "semantic_fusion_unavailable" for w in result.warnings)


@pytest.mark.asyncio
async def test_context_cancellation_is_not_converted_to_success() -> None:
    with pytest.raises(asyncio.CancelledError):
        await run_case("cancel")


@pytest.mark.asyncio
async def test_large_whole_context_is_rejected_without_silently_truncating() -> None:
    result, requests = await run_case("context_budget")
    assert requests == []
    assert len(result.candidates) == 3
    assert result.diagnostics == {"unavailable_input_budget": 1}


@pytest.mark.asyncio
async def test_shared_source_cannot_also_be_an_independent_group_member() -> None:
    result, requests = await run_case("membership_duplicate")
    assert len(requests) == 1
    assert len(result.candidates) == 3
    assert result.diagnostics == {"unavailable_membership": 1}


@pytest.mark.asyncio
async def test_shared_context_logs_only_fixed_counts(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="hakimi_analysis.semantic_fusion"):
        result, _ = await run_case("reminder")
    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "hakimi_analysis.semantic_fusion"
    ]
    assert records == [
        {
            "source_id": "synthetic",
            "stage": "fusing_candidates",
            "fusion_diagnostics": result.diagnostics,
        }
    ]
    assert result.diagnostics["shared_source_attached"] == 1
    assert result.diagnostics["shared_source_tips_unassigned"] == 1
    assert all(type(value) is int and 0 <= value <= 200 for value in result.diagnostics.values())
