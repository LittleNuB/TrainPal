"""Source-level release-policy fences; these do not execute GitHub Actions."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "competition-image.yml"


def test_image_verification_requires_explicit_manual_dispatch() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    triggers = workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
    assert triggers.strip() == "workflow_dispatch:"


def test_image_verification_has_no_registry_write_or_publish_path() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "contents: read" in workflow
    assert re.search(r"^\s*\S+:\s*(?:write|write-all)\s*$", workflow, re.MULTILINE) is None
    assert "secrets." not in workflow
    assert "docker/login-action" not in workflow
    assert "docker/metadata-action" not in workflow
    assert "ghcr.io" not in workflow
    assert "PUBLISH_IMAGE" not in workflow
    assert re.search(r"\bdocker\s+(?:push|login)\b", workflow) is None
    assert re.search(r"^\s*push:\s*true\s*$", workflow, re.MULTILINE) is None


def test_image_verification_keeps_local_build_and_audit_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/ci.yml" in workflow
    assert "needs: verify" in workflow
    assert "docker/build-push-action@v6" in workflow
    assert "load: true" in workflow
    assert "push: false" in workflow
    assert 'bash ./deploy/verify-competition-image.sh "$AUDIT_IMAGE"' in workflow
    assert "caddy validate" in workflow
    assert "docker compose --file deploy/compose.yml config" in workflow
