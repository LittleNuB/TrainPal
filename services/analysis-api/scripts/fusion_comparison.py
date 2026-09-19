"""Opt-in local evaluation helper, never imported by the application.

Compare one real grouping with the pre-ADR-0056 ordinal policy in memory.
Only bounded, content-free receipts survive the call; no disk or network API here.
"""

import copy
import importlib.util
import re
import sys
from collections import Counter
from types import ModuleType
from typing import Any

import hakimi_analysis.semantic_fusion as current

BASELINE_POLICY = "a294f0f:sequence-identity"


def _legacy_identity(label: str) -> tuple[str, str]:
    match = re.fullmatch(r"(?:动作|#)?\s*([0-9]+|[零一二三四五六七八九十])", label.strip())
    if match is None:
        return ("literal", label)
    number = match.group(1)
    value = int(number) if number.isascii() else "零一二三四五六七八九十".index(number)
    return ("number", str(value))


def _baseline_module() -> ModuleType:
    # A separate module namespace isolates the frozen ordinal policy. All other
    # fusion code is identical to the running version; this is not a full old-build A/B.
    name = "_trainpal_offline_ordinal_baseline"
    spec = importlib.util.spec_from_file_location(name, current.__file__)
    if spec is None or spec.loader is None:
        raise RuntimeError("baseline_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    setattr(module, "_sequence_identity", _legacy_identity)
    return module


class _CaptureModel:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.grouping: Any = None
        self.observations: list[dict[str, Any]] = []
        self.calls = 0

    async def group_action_evidence(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls != 1:
            raise RuntimeError("repeated_fusion_request")
        self.observations = copy.deepcopy(kwargs["observations"])
        self.grouping = await self.delegate.group_action_evidence(**kwargs)
        return self.grouping


class _ReplayModel:
    def __init__(self, capture: _CaptureModel) -> None:
        self.capture = capture

    async def group_action_evidence(self, **kwargs: Any) -> Any:
        if kwargs["observations"] != self.capture.observations:
            raise RuntimeError("comparison_input_mismatch")
        return self.capture.grouping.model_copy(deep=True)


def _summary(result: Any) -> dict[str, Any]:
    return {
        "candidates": len(result.candidates),
        "pending": sum(c.needs_confirmation for c in result.candidates),
        "parameter_conflict_groups": sum(bool(c.parameter_conflicts) for c in result.candidates),
        "diagnostics": dict(result.diagnostics),
    }


def _label_shapes(capture: _CaptureModel) -> dict[str, int]:
    labels = {o["id"]: o.get("sequence_label") for o in capture.observations}
    counts: Counter[str] = Counter()
    for group in capture.grouping.groups:
        if group.content_role != "exercise":
            continue
        values = [value for i in group.member_ids if isinstance(value := labels.get(i), str)]
        old = {_legacy_identity(value) for value in values}
        new = {current._sequence_identity(value) for value in values}
        if len(old) > 1 and len(new) <= 1:
            counts["format_equivalence_resolved"] += 1
        if len(new) <= 1:
            continue
        if len({value for kind, value in new if kind == "number"}) > 1:
            counts["different_parsed_numbers"] += 1
        if any(kind == "literal" for kind, _ in new):
            counts["unparsed_identity_present"] += 1
    return dict(counts)


class SameInputFusionReview:
    """One-shot adapter around the agreed fusion seam, for private evaluation only."""

    def __init__(self, delegate: current.SemanticCandidateFusion) -> None:
        self._delegate = delegate
        self._baseline = _baseline_module()
        self._used = False
        self.receipt: dict[str, Any] = {}

    async def run(self, **kwargs: Any) -> current.SemanticFusionResult:
        if self._used:
            raise RuntimeError("review_already_used")
        self._used = True
        original_model = self._delegate._model
        capture = _CaptureModel(original_model)
        self._delegate._model = capture
        try:
            result = await self._delegate.run(**kwargs)
            self.receipt = {
                "baseline_policy": BASELINE_POLICY,
                "current": _summary(result),
                "semantic_model_calls": capture.calls,
                "comparison": "unavailable",
            }
            if capture.grouping is None:
                return result
            baseline = self._baseline.SemanticCandidateFusion(
                model=_ReplayModel(capture),
                instructions=self._delegate._instructions,
                timeout_seconds=self._delegate._timeout_seconds,
            )
            old_result = await baseline.run(**kwargs)
            structural_failure = any(
                key in result.diagnostics
                for key in (
                    "unavailable_membership",
                    "unavailable_reference_target",
                )
            )
            self.receipt.update(
                {
                    "baseline": _summary(old_result),
                    "comparison": "complete",
                    "proposed_groups": len(capture.grouping.groups),
                    "label_shapes": {} if structural_failure else _label_shapes(capture),
                    "label_shapes_available": not structural_failure,
                    "same_candidates": [c.model_dump() for c in result.candidates]
                    == [c.model_dump() for c in old_result.candidates],
                }
            )
            return result
        finally:
            self._delegate._model = original_model
            capture.grouping = None
            capture.observations.clear()
