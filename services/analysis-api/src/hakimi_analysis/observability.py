import json
import logging

from hakimi_analysis.fusion_diagnostics import FUSION_DIAGNOSTIC_CODES

ALLOWED_LOG_FIELDS = frozenset(
    {
        "run_id",
        "source_id",
        "stage",
        "elapsed_ms",
        "model_version",
        "skill_version",
        "provider_request_id",
        "error_code",
        "fusion_diagnostics",
    }
)


def log_safe_fields(logger: logging.Logger, **fields: object) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    payload = {
        key: value
        for key, value in fields.items()
        if key in ALLOWED_LOG_FIELDS and key != "fusion_diagnostics" and value is not None
    }
    diagnostics = fields.get("fusion_diagnostics")
    if isinstance(diagnostics, dict):
        payload["fusion_diagnostics"] = {
            code: count
            for code, count in diagnostics.items()
            if isinstance(code, str)
            and code in FUSION_DIAGNOSTIC_CODES
            and type(count) is int
            and 0 <= count <= 200
        }
    logger.info(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
