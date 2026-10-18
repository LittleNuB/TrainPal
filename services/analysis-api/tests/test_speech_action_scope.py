import json

import httpx
import pytest

from hakimi_analysis.fusion import fuse_candidates
from hakimi_analysis.models import (
    Segment,
    SegmentRole,
    SpeechSignal,
    TranscriptUtterance,
    VisualSegment,
)
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion
from hakimi_analysis.shared_source_context import SpeechSourceContext


def context_signal() -> SpeechSignal:
    return SpeechSignal(
        action_name="器械准备阶段",
        evidence_text="先借助两种器械做准备，再开始正文。",
        start_seconds=1,
        end_seconds=6,
        content_scope="general_context",
    )


async def test_general_narration_is_context_without_a_third_execution() -> None:
    speech = [context_signal()]
    visual = [
        VisualSegment(
            action_name=name,
            start_seconds=start,
            end_seconds=end,
            visual_cue="独立教学动作",
            segment_role=SegmentRole.TEACHING_DEMO,
        )
        for name, start, end in [("抬臂", 1, 3), ("屈膝", 3, 6)]
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        assert [item["id"] for item in payload["observations"]] == ["visual-1", "visual-2"]
        assert payload["source_context"]["utterances"][0]["text"] == speech[0].evidence_text
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "groups": [
                            {
                                "member_ids": [f"visual-{index}"],
                                "name": name,
                                "relation": "same_demonstration",
                                "content_role": "exercise",
                                "related_member_id": None,
                                "accepted_tip_ids": [],
                            }
                            for index, name in enumerate(("抬臂", "屈膝"), 1)
                        ]
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await SemanticCandidateFusion(
            model=ArkResponsesClient(
                api_key="test", model_id="test", base_url="https://ark.test", http_client=client
            ),
            instructions="Group observations",
            include_speech_context=True,
        ).run(
            source_id="synthetic",
            speech_signals=speech,
            visual_segments=visual,
            speech_context=SpeechSourceContext.bind(
                source_id="synthetic",
                speech_signals=speech,
                visual_segments=visual,
                utterances=[
                    TranscriptUtterance(
                        text=speech[0].evidence_text, start_seconds=1, end_seconds=6
                    )
                ],
            ),
        )
    assert [candidate.name for candidate in result.candidates] == ["抬臂", "屈膝"]
    assert all(candidate.needs_confirmation for candidate in result.candidates)
    assert all(len(candidate.evidence) == 1 for candidate in result.candidates)


@pytest.mark.parametrize(
    "protected",
    ["sets", "reps", "duration_seconds", "rest_seconds", "follow_along", "individual_action"],
)
def test_scope_does_not_discard_prescriptions_rounds_or_concrete_actions(protected: str) -> None:
    signal = context_signal()
    if protected == "follow_along":
        signal.segment_role = SegmentRole.FOLLOW_ALONG
    elif protected == "individual_action":
        signal.content_scope = "individual_action"
    else:
        setattr(signal, protected, 5)
    candidates = fuse_candidates(source_id="synthetic", speech_signals=[signal], visual_segments=[])
    assert len(candidates) == 1
    if protected not in ("follow_along", "individual_action"):
        assert getattr(candidates[0].parameters, protected) == 5


async def test_fusion_model_override_does_not_retarget_speech() -> None:
    calls: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        schema = payload["text"]["format"]["name"]
        calls.append((schema, payload["model"]))
        result: dict[str, object] = (
            {"signals": []} if schema == "training_speech_understanding" else {"groups": []}
        )
        if schema == "contextual_action_grouping":
            result["shared_sources"] = []
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test",
            model_id="speech-model",
            fusion_model_id="fusion-model",
            base_url="https://ark.test",
            http_client=client,
        )
        await model.group_action_evidence(observations=[], instructions="Group")
        await model.group_action_evidence_with_context(
            observations=[], source_context={}, instructions="Group"
        )
        await model.understand_speech(
            transcript={"text": "", "utterances": []},
            window=Segment(start_seconds=0, end_seconds=5),
            instructions="Read",
        )
    assert calls == [
        ("semantic_action_grouping", "fusion-model"),
        ("contextual_action_grouping", "fusion-model"),
        ("training_speech_understanding", "speech-model"),
    ]
