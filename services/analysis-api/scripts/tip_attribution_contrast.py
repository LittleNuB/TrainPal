"""Opt-in, one-shot text experiment; never imported by the application.

No credentials/config discovery, CLI network entry point or persistence. The
caller must separately obtain paid-call approval and inject an HTTP client.
"""

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

import httpx

from hakimi_analysis.models import SourceTip, VisualSegment
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.semantic_fusion import (
    SemanticCandidateFusion,
    SemanticFusionResult,
    SemanticGroup,
    SemanticGrouping,
)

FIXTURE = Path(__file__).parent / "fixtures/tip_attribution_contrast.json"
CASE_IDS = ("ambiguous_transition", "previous_action_closing_caption")
ARMS = ("nested", "flat", "flat_fixed_groups")
FLAT_FORMAT = """

Experimental input format only: observations contains one envelope with actions
and tips. Interpret actions as the original observations. Each standalone tip has
upstream_observation_id, preserving the original, unverified ownership hypothesis.
Only action IDs are grouping members; tip IDs are eligible under the same own-member
rules as before. This layout changes no source facts or attribution requirements.
"""
FIXED_TASK = """

For this controlled trial only, fixed_groups supplies an oracle action grouping.
Do not perform action grouping or rename actions. Return exactly those groups,
with their member_ids, name, relation, content_role and related_member_id unchanged.
Your only decision is accepted_tip_ids for each group, under the source-tip rules
above. The supplied fixed groups do not establish that any tip belongs to them.
"""


def load_case(case_id: str) -> dict[str, Any]:
    if case_id not in CASE_IDS:
        raise ValueError("unknown_contrast_case")
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _valid_raw_format(payload: Any) -> bool:
    """Audit before production's tolerant defaults/choice clearing; retain no text."""
    try:
        text = payload.get("output_text")
        if not isinstance(text, str):
            # The two documented Ark response envelopes, using the same first-text
            # precedence as the production adapter (not a replacement parser).
            text = next(
                c["text"] for o in payload.get("output", []) if isinstance(o, dict)
                for c in o.get("content", []) if isinstance(c, dict)
                and c.get("type") == "output_text" and isinstance(c.get("text"), str)
            )
        raw = json.loads(text)
        for group in raw["groups"]:
            if set(group) != set(SemanticGroup.model_fields):
                return False
            selected = group["accepted_tip_ids"]
            if not isinstance(selected, list) or len(selected) > 3 or any(
                not isinstance(value, str) for value in selected
            ):
                return False
        SemanticGrouping.model_validate_json(text, strict=True)
        return True
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
        return False


class _AuditTransport(httpx.AsyncBaseTransport):
    """Forward through the explicitly supplied client without changing its hooks."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.output_format_valid: bool | None = None
        self.requests = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.requests != 1:
            raise RuntimeError("trial_repeated_http_request")
        response = await self.client.send(request, follow_redirects=False)
        if response.is_success:
            try:
                self.output_format_valid = _valid_raw_format(response.json())
            except ValueError:
                self.output_format_valid = False
        return response


def _groups(grouping: SemanticGrouping) -> list[tuple[object, ...]]:
    return sorted(
        (tuple(sorted(g.member_ids)), g.relation, g.content_role, g.related_member_id or "")
        for g in grouping.groups
    )


def _selections(grouping: SemanticGrouping) -> Counter[tuple[tuple[str, ...], str]]:
    return Counter(
        (tuple(sorted(g.member_ids)), tip_id)
        for g in grouping.groups for tip_id in g.accepted_tip_ids
    )


def _score(
    case: dict[str, Any], grouping: SemanticGrouping | None, result: SemanticFusionResult,
) -> dict[str, Any]:
    expected = SemanticGrouping.model_validate({"groups": case["groups"]})
    actual_shapes = Counter(
        _canonical([[c.segment.start_seconds, c.segment.end_seconds],
                    c.parameters.model_dump(mode="json", exclude_none=True)])
        for c in result.candidates
    )
    # Normalize numeric spans through the public source models, not string formatting.
    expected_shapes = Counter(
        _canonical([[float(x) for x in o["segment"]], o["parameters"]])
        for o in case["expected_outputs"]
    )
    expected_tips = Counter(
        _canonical([[float(x) for x in o["segment"]],
                    SourceTip.model_validate(tip).model_dump(mode="json")])
        for o in case["expected_outputs"] for tip in o["tips"]
    )
    actual_tips = Counter(
        _canonical([[c.segment.start_seconds, c.segment.end_seconds],
                    tip.model_dump(mode="json")])
        for c in result.candidates for tip in c.tips
    )
    checks = {
        "groups_match": grouping is not None and _groups(grouping) == _groups(expected),
        "selections_match": grouping is not None
        and _selections(grouping) == _selections(expected),
        "actions_parameters_ranges_match": actual_shapes == expected_shapes,
        "unexpected_tips": sum((actual_tips - expected_tips).values()),
        "missing_required_tips": sum((expected_tips - actual_tips).values()),
    }
    checks["passed"] = (
        checks["groups_match"] and checks["selections_match"]
        and checks["actions_parameters_ranges_match"]
        and checks["unexpected_tips"] == 0 and checks["missing_required_tips"] == 0
    )
    return checks


class _TrialModel:
    def __init__(self, delegate: ArkResponsesClient, arm: str, case: dict[str, Any]) -> None:
        self.delegate = delegate
        self.arm = arm
        self.fixed_groups = [
            {k: v for k, v in g.items() if k != "accepted_tip_ids"} for g in case["groups"]
        ]
        self.grouping: SemanticGrouping | None = None
        self.request_hash: str | None = None
        self.calls = 0

    async def group_action_evidence(
        self, *, observations: list[dict[str, object]], instructions: str,
    ) -> SemanticGrouping:
        self.calls += 1
        if self.calls != 1:
            raise RuntimeError("trial_repeated_request")
        if self.arm != "nested":
            envelope: dict[str, object] = {
                "actions": [{k: v for k, v in o.items() if k != "tip_evidence"}
                            for o in observations],
                "tips": [{**tip, "upstream_observation_id": o["id"]}
                         for o in observations
                         for tip in cast(list[dict[str, object]], o["tip_evidence"])],
            }
            instructions += FLAT_FORMAT
            if self.arm == "flat_fixed_groups":
                envelope["fixed_groups"] = self.fixed_groups
                instructions += FIXED_TASK
            observations = [envelope]
        self.request_hash = _fingerprint([instructions, observations])
        self.grouping = await self.delegate.group_action_evidence(
            observations=observations, instructions=instructions,
        )
        return self.grouping


class TipAttributionTrial:
    """One explicit experiment cell using production parsing and final filtering."""

    def __init__(
        self, *, case_id: str, arm: str, http_client: httpx.AsyncClient,
        api_key: str, model_id: str, base_url: str, instructions: str,
    ) -> None:
        if arm not in ARMS:
            raise ValueError("unknown_contrast_arm")
        self._case = load_case(case_id)
        self._arm = arm
        self._instructions = instructions
        self._used = False
        self._transport = _AuditTransport(http_client)
        self._client = httpx.AsyncClient(transport=self._transport, timeout=20)
        self._model = _TrialModel(ArkResponsesClient(
            api_key=api_key, model_id=model_id, base_url=base_url,
            http_client=self._client, retry_delays=(),
        ), arm, self._case)

    async def run(self) -> dict[str, Any]:
        if self._used:
            raise RuntimeError("trial_already_used")
        self._used = True
        started = time.monotonic()
        try:
            async with self._client:
                result = await SemanticCandidateFusion(
                    model=self._model, instructions=self._instructions, timeout_seconds=20,
                ).run(
                    source_id="synthetic-tip-contrast", speech_signals=[],
                    visual_segments=[VisualSegment.model_validate(v) for v in self._case["visual"]],
                )
            checks = _score(self._case, self._model.grouping, result)
            unavailable = any(k.startswith("unavailable_") for k in result.diagnostics)
            fixed_drift = False
            if self._arm == "flat_fixed_groups" and self._model.grouping is not None:
                actual = [g.model_dump(exclude={"accepted_tip_ids"})
                          for g in self._model.grouping.groups]
                fixed_drift = sorted(map(_canonical, actual)) != sorted(
                    map(_canonical, self._model.fixed_groups)
                )
            invalid_format = self._transport.output_format_valid is False
            if fixed_drift or unavailable or invalid_format:
                checks["passed"] = False
            return {
                "case": self._case["id"], "arm": self._arm,
                "status": "unavailable" if unavailable else
                "invalid_output_format" if invalid_format else
                "fixed_group_drift" if fixed_drift else
                "passed" if checks["passed"] else "quality_failed",
                **checks,
                "diagnostics": dict(result.diagnostics),
                "logical_requests": self._model.calls,
                "http_requests": self._transport.requests,
                "output_format_valid": self._transport.output_format_valid,
                "request_sha256": self._model.request_hash,
                "case_sha256": _fingerprint(self._case),
                "seconds": round(time.monotonic() - started, 3),
            }
        finally:
            self._model.grouping = None
