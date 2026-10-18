"""Bundled examples must satisfy the contract actually sent to the Provider."""

import json
from pathlib import Path
from typing import Literal

import httpx
import pytest
from jsonschema import Draft202012Validator

from hakimi_analysis.models import Segment
from hakimi_analysis.orchestration import SkillRepository
from hakimi_analysis.providers.ark import ArkResponsesClient


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_format", ["default", "nested", "flat", "flat_branch_source"])
@pytest.mark.parametrize("input_kind", ["video", "contact_sheet"])
async def test_bundled_visual_example_passes_sent_schema_and_application_validation(
    tmp_path: Path,
    wire_format: Literal["default", "nested", "flat", "flat_branch_source"],
    input_kind: str,
) -> None:
    media = tmp_path / "synthetic.media"
    media.write_bytes(b"synthetic")
    instructions = SkillRepository.load(
        Path(__file__).resolve().parents[3] / "skills"
    ).visual_instructions

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent_instructions = payload["instructions"]
        example_text = sent_instructions.split("```json\n", 1)[1].split("```", 1)[0]
        example = json.loads(example_text)
        Draft202012Validator(payload["text"]["format"]["schema"]).validate(example)
        assert "fictional structure example, not source evidence" in sent_instructions
        assert "Never copy its values into the analysis" in sent_instructions
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "store": False,
                "text": payload["text"],
                "output_text": example_text,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="synthetic",
            base_url="https://synthetic.test",
            http_client=http,
            retry_delays=(),
            visual_parameter_context=wire_format != "default",
            visual_parameter_context_format="nested" if wire_format == "default" else wire_format,
            clarify_visual_ordinals=True,
        )
        window = Segment(start_seconds=0, end_seconds=40)
        if input_kind == "video":
            result = await provider.locate_visual(
                video_path=media, window=window, instructions=instructions
            )
        else:
            result = await provider.locate_visual_contact_sheet(
                image_path=media,
                window=window,
                frame_times_seconds=(0, 20),
                instructions=instructions,
            )
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.start_seconds < segment.end_seconds
    assert segment.sequence_label is None
    assert segment.text_parameters is None
    assert segment.tips == []
