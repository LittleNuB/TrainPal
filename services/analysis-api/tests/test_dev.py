import pytest

from hakimi_analysis.dev import configure_local_environment


def test_local_profile_has_time_for_real_visual_chunks() -> None:
    environment: dict[str, str] = {}
    configure_local_environment(environment)
    assert environment["APP_ENV"] == "development"
    assert environment["ANALYSIS_PROVIDER"] == "cloud"
    assert environment["ANALYSIS_CHUNK_TIMEOUT_SECONDS"] == "90"
    assert environment["ANALYSIS_EVIDENCE_TIMEOUT_SECONDS"] == "590"
    assert environment["RUN_TIMEOUT_SECONDS"] == "600"


def test_local_profile_preserves_explicit_timeout_overrides() -> None:
    environment = {"RUN_TIMEOUT_SECONDS": "300"}
    configure_local_environment(environment)
    assert environment["RUN_TIMEOUT_SECONDS"] == "300"


@pytest.mark.parametrize("app_env", ["production", "test"])
def test_local_launcher_cannot_be_used_for_other_environments(app_env: str) -> None:
    with pytest.raises(ValueError, match="development"):
        configure_local_environment({"APP_ENV": app_env})
