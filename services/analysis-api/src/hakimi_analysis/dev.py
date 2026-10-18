"""Loopback-only development launcher; production keeps its separate budgets."""

import os
from collections.abc import MutableMapping

import uvicorn


def configure_local_environment(environment: MutableMapping[str, str]) -> None:
    if environment.get("APP_ENV", "development") != "development":
        raise ValueError("The local launcher requires APP_ENV=development")
    defaults = {
        "APP_ENV": "development",
        "ANALYSIS_PROVIDER": "cloud",
        # Local acceptance budgets; these do not change the production entrypoint.
        "ANALYSIS_CHUNK_TIMEOUT_SECONDS": "90",
        "ANALYSIS_EVIDENCE_TIMEOUT_SECONDS": "590",
        "RUN_TIMEOUT_SECONDS": "600",
    }
    for name, value in defaults.items():
        environment.setdefault(name, value)


if __name__ == "__main__":
    configure_local_environment(os.environ)
    uvicorn.run("hakimi_analysis.main:app", host="127.0.0.1", port=8000)
