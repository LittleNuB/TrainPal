"""Request-local visual context, kept separate from training action membership."""

import json
from dataclasses import dataclass

from hakimi_analysis.models import VisualSegment
from hakimi_analysis.visual_evidence import OrganizedVisualEvidence


@dataclass(frozen=True, repr=False)
class VisualSourceContext:
    """Immutable memory-only copy bound to source, organizer input and projected evidence."""

    source_id: str
    binding_id: str
    visual_snapshot: tuple[str, ...]
    payload: str

    @classmethod
    def bind(cls, *, source_id: str, evidence: OrganizedVisualEvidence) -> "VisualSourceContext":
        # Revalidate mutable models before freezing a request-local copy.
        evidence = OrganizedVisualEvidence.model_validate(evidence.model_dump())
        if not evidence.independent_captions:
            raise ValueError("visual_context_requires_independent_captions")
        payload = {
            "context_only": True,
            "binding_id": evidence.binding_id,
            "non_training_observations": [
                item.reading.model_dump(mode="json")
                for item in evidence.observations
                if not item.reading.is_training_content
            ],
            "unassigned_captions": [
                j.model_dump(mode="json") for j in evidence.unassigned_judgments
            ],
            "uncertain_point_captions": [
                p.model_dump(mode="json") for p in evidence.point_captions
            ],
        }
        return cls(
            source_id=source_id,
            binding_id=evidence.binding_id,
            visual_snapshot=tuple(v.model_dump_json() for v in evidence.to_visual_segments()),
            payload=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        )

    def prepare(
        self, *, source_id: str, binding_id: str | None, visual_segments: list[VisualSegment]
    ) -> dict[str, object] | None:
        if (
            self.source_id != source_id
            or not binding_id
            or self.binding_id != binding_id
            or self.visual_snapshot != tuple(v.model_dump_json() for v in visual_segments)
        ):
            return None
        return dict(json.loads(self.payload))
