import json

import httpx
import pytest

from hakimi_analysis.models import Segment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import SemanticCandidateFusion


@pytest.mark.asyncio
async def test_only_exact_timed_speech_tips_reach_the_candidate() -> None:
    tips = [
        {
            "text": text,
            "category": "breathing",
            "evidence": {
                "type": "speech",
                "start_seconds": start,
                "end_seconds": end,
            },
        }
        for text, start, end in [("推起时呼气", 3, 5), ("每天练十组", 3, 5), ("推起时呼气", 20, 22)]
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        schema = json.loads(request.content)["text"]["format"]["name"]
        result = {
            "signals": [
                {
                    "action_name": "俯卧撑",
                    "start_seconds": 1,
                    "end_seconds": 10,
                    "evidence_text": "俯卧撑",
                    "tips": tips,
                }
            ]
        }
        if schema == "semantic_action_grouping":
            result = {
                "groups": [
                    {
                        "member_ids": ["speech-1"],
                        "name": "俯卧撑",
                        "relation": "same_demonstration",
                        "accepted_tip_ids": ["speech-1-tip-1"],
                    }
                ]
            }
        return httpx.Response(200, json={"output_text": json.dumps(result)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ArkResponsesClient(
            api_key="test", model_id="test", base_url="https://test", http_client=client
        )
        speech = await model.understand_speech(
            transcript={
                "text": "俯卧撑，推起时呼气",
                "utterances": [{"text": "推起时呼气", "start_seconds": 3, "end_seconds": 5}],
            },
            window=Segment(start_seconds=0, end_seconds=30),
            instructions="fixture",
        )
        result = await SemanticCandidateFusion(model=model, instructions="fixture").run(
            source_id="source", speech_signals=speech.signals, visual_segments=[]
        )
    assert [tip.text for tip in result.candidates[0].tips] == ["推起时呼气"]
    assert result.candidates[0].tips[0].evidence.start_seconds == 3
