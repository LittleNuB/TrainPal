"""Role decisions through the real fusion/Provider seam; only HTTP is replaced."""

import json
from typing import Any

import httpx
import pytest

from hakimi_analysis.models import CandidateParameters, SegmentRole, SpeechSignal, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion, SemanticFusionResult


def group(
    ids: list[str],
    role: str = "exercise",
    target: str | None = None,
    relation: str = "same_demonstration",
) -> dict[str, Any]:
    return {
        "member_ids": ids,
        "name": "坐姿划船",
        "relation": relation,
        "content_role": role,
        "related_member_id": target,
    }


def source_visuals() -> list[VisualSegment]:
    return [
        VisualSegment(
            action_name="划船预告",
            start_seconds=4,
            end_seconds=8,
            visual_cue="开场概览蒙太奇，稍后有正式示范",
            sequence_label="2",
            segment_role=SegmentRole.TEACHING_DEMO,
            text_parameters=CandidateParameters(mode="reps", sets=3),
        ),
        VisualSegment(
            action_name="坐姿划船",
            start_seconds=20,
            end_seconds=30,
            visual_cue="完整正式动作示范",
            sequence_label="动作二",
            segment_role=SegmentRole.TEACHING_DEMO,
            text_parameters=CandidateParameters(mode="reps", reps=12),
        ),
        VisualSegment(
            action_name="划船回顾",
            start_seconds=40,
            end_seconds=45,
            visual_cue="结束总结表下复用此前示范",
            sequence_label="2",
            segment_role=SegmentRole.TEACHING_DEMO,
            text_parameters=CandidateParameters(rest_seconds=60),
        ),
    ]


async def run_roles(
    groups: list[dict[str, Any]], visuals: list[VisualSegment] | None = None, *, fail: bool = False
) -> SemanticFusionResult:
    def respond(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        schema = json.loads(request.content)["text"]["format"]["schema"]
        assert {"content_role", "related_member_id"} <= set(
            schema["$defs"]["SemanticGroup"]["required"]
        )
        return httpx.Response(200, json={"output_text": json.dumps({"groups": groups})})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        fusion = SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test",
                model_id="text",
                base_url="https://ark.test",
                http_client=client,
                retry_delays=(),
            ),
            instructions="Classify roles and group source observations.",
        )
        return await fusion.run(
            source_id="role-source",
            speech_signals=[
                SpeechSignal(
                    action_name="坐姿划船",
                    start_seconds=21,
                    end_seconds=29,
                    evidence_text="fixture",
                    reps=12,
                    segment_role=SegmentRole.TEACHING_DEMO,
                )
            ],
            visual_segments=source_visuals() if visuals is None else visuals,
        )


@pytest.mark.asyncio
async def test_preview_and_recap_are_references_without_extra_training_or_parameter_leakage() -> (
    None
):
    result = await run_roles(
        [
            group(["visual-1"], "preview", "visual-2"),
            group(["speech-1", "visual-2"]),
            group(["visual-3"], "recap", "speech-1"),
        ]
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "坐姿划船"
    assert candidate.segment.start_seconds == 20
    assert candidate.segment.end_seconds == 30
    assert candidate.parameters.reps == 12
    assert candidate.parameters.sets is None
    assert candidate.parameters.rest_seconds is None
    assert sorted((s.start_seconds, s.end_seconds) for s in candidate.evidence) == [
        (4, 8),
        (20, 30),
        (21, 29),
        (40, 45),
    ]
    assert candidate.needs_confirmation is False
    assert result.warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, "missing", "visual-1", "visual-3"])
async def test_invalid_reference_graph_retains_every_observation(target: str | None) -> None:
    result = await run_roles(
        [
            group(["visual-1"], "preview", target),
            group(["speech-1", "visual-2"]),
            group(["visual-3"], "recap", "visual-2"),
        ]
    )
    assert len(result.candidates) == 4
    assert all(c.needs_confirmation for c in result.candidates)
    assert result.warnings[0].code == "semantic_fusion_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["follow_along", "description", "number", "parameters", "time", "uncertain"]
)
async def test_unsafe_reference_is_retained_pending(problem: str) -> None:
    visuals = source_visuals()
    if problem == "follow_along":
        visuals[2].segment_role = SegmentRole.FOLLOW_ALONG
    elif problem == "description":
        visuals[2].visual_cue = " "
    elif problem == "number":
        visuals[2].sequence_label = "3"
    elif problem == "parameters":
        visuals[2].text_parameters = CandidateParameters(mode="reps", reps=15)
    elif problem == "time":
        visuals[2].start_seconds = 25
    result = await run_roles(
        [
            group(["visual-1"], "preview", "visual-2"),
            group(["speech-1", "visual-2"]),
            group(
                ["visual-3"],
                "recap",
                "visual-2",
                relation="uncertain" if problem == "uncertain" else "same_demonstration",
            ),
        ],
        visuals,
    )
    assert len(result.candidates) == 2
    assert result.candidates[1].needs_confirmation
    assert len(result.candidates[0].evidence) == 3
    assert result.warnings[0].code == "semantic_fusion_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["equipment_setup", "non_training_gesture", "uncertain"])
async def test_speech_requirements_cannot_be_silently_excluded(role: str) -> None:
    result = await run_roles([group(["speech-1"], role)], [])
    assert len(result.candidates) == 1
    assert result.candidates[0].parameters.reps == 12
    assert result.candidates[0].needs_confirmation


@pytest.mark.asyncio
async def test_rejected_primary_preserves_its_references_too() -> None:
    result = await run_roles(
        [
            group(["visual-1"], "preview", "visual-2"),
            group(["speech-1", "visual-2"], relation="uncertain"),
            group(["visual-3"], "recap", "speech-1"),
        ]
    )
    assert len(result.candidates) == 4
    assert all(c.needs_confirmation for c in result.candidates)


@pytest.mark.asyncio
async def test_provider_failure_preserves_all_sources() -> None:
    result = await run_roles([], fail=True)
    assert len(result.candidates) == 4
    assert all(c.needs_confirmation for c in result.candidates)


@pytest.mark.asyncio
async def test_reference_does_not_clear_primary_confirmation() -> None:
    result = await run_roles(
        [
            group(["speech-1"]),
            group(["visual-2"]),
            group(["visual-1"], "preview", "visual-2"),
            group(["visual-3"], "recap", "visual-2"),
        ]
    )
    assert len(result.candidates) == 2
    assert all(c.needs_confirmation for c in result.candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["equipment_setup", "non_training_gesture"])
async def test_explicit_non_training_visual_activity_is_not_a_training_item(role: str) -> None:
    visuals = source_visuals()
    visuals.append(
        VisualSegment(
            action_name=None,
            start_seconds=12,
            end_seconds=14,
            visual_cue="只调整器械插销或指向器械，没有示范训练动作",
        )
    )
    result = await run_roles(
        [
            group(["visual-1"], "preview", "visual-2"),
            group(["speech-1", "visual-2"]),
            group(["visual-3"], "recap", "visual-2"),
            group(["visual-4"], role),
        ],
        visuals,
    )
    assert len(result.candidates) == 1
    assert len(result.candidates[0].evidence) == 4
    assert result.warnings == []
