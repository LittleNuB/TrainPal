"""Translate source-relative spans without changing their evidence identity."""

from hakimi_analysis.models import Segment, SourceTip


def offset_span[Span: Segment](span: Span, seconds: float) -> Span:
    return span.model_copy(
        update={
            "start_seconds": span.start_seconds + seconds,
            "end_seconds": span.end_seconds + seconds,
        }
    )


def offset_tip(tip: SourceTip, seconds: float) -> SourceTip:
    return tip.model_copy(update={"evidence": offset_span(tip.evidence, seconds)})
