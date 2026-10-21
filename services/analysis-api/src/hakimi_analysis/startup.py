import argparse
import os
import subprocess
import sys
from pathlib import Path

from hakimi_analysis.media import probe_duration_sync
from hakimi_analysis.portfolio_runtime import portfolio_runtime_guard
from hakimi_analysis.settings import PROJECT_ROOT
from hakimi_analysis.sources import (
    COMPETITION_CONTROLLED_SOURCE_COUNT,
    SourceCatalog,
    SourceManifestError,
    load_source_manifest,
)
from hakimi_analysis.sync_media import MediaSyncError, sync_media


def _required_environment_path(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SourceManifestError("controlled source startup configuration is incomplete")
    return Path(value)


def _valid_cached_source_count(manifest_path: Path, target_root: Path) -> int | None:
    try:
        catalog = SourceCatalog.from_manifest(
            manifest_path=manifest_path,
            media_root=target_root,
            public_media_base_url="https://startup-cache-validation.invalid",
            duration_probe=probe_duration_sync,
        )
    except SourceManifestError:
        return None
    if catalog.source_count != COMPETITION_CONTROLLED_SOURCE_COUNT:
        return None
    return catalog.source_count


def _start(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize controlled sources before starting the application server."
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = list(arguments.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        print("controlled_source_startup_failed code=command_missing", file=sys.stderr)
        return 2

    try:
        manifest_path = _required_environment_path("SOURCE_MANIFEST_PATH")
        target_root = _required_environment_path("SOURCE_MEDIA_ROOT")
        base_url = os.environ.get("PUBLIC_MEDIA_BASE_URL", "").strip()
        manifest = load_source_manifest(manifest_path)
        if len(manifest.sources) != COMPETITION_CONTROLLED_SOURCE_COUNT:
            raise SourceManifestError("controlled source count is invalid")
        count = _valid_cached_source_count(manifest_path, target_root)
        if count is None:
            sync_media(
                manifest_path=manifest_path,
                target_root=target_root,
                base_url=base_url,
            )
            count = _valid_cached_source_count(manifest_path, target_root)
            if count is None:
                raise MediaSyncError("media_cache_invalid", exit_code=3)
    except SourceManifestError:
        print("controlled_source_startup_failed code=manifest_invalid", file=sys.stderr)
        return 2
    except MediaSyncError as error:
        print(f"controlled_source_startup_failed code={error.code}", file=sys.stderr)
        return error.exit_code

    if count != COMPETITION_CONTROLLED_SOURCE_COUNT:
        print("controlled_source_startup_failed code=source_count_invalid", file=sys.stderr)
        return 3
    print(f"controlled_source_startup_pass source_count={count}", flush=True)
    if os.name == "nt":
        try:
            return subprocess.run(command, check=False).returncode
        except OSError:
            print("controlled_source_startup_failed code=exec_failed", file=sys.stderr)
            return 4
    try:
        os.execvp(command[0], command)
    except OSError:
        print("controlled_source_startup_failed code=exec_failed", file=sys.stderr)
        return 4


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("DEPLOYMENT_PROFILE") != "portfolio":
        return _start(argv)
    try:
        with portfolio_runtime_guard(
            PROJECT_ROOT / "tmp" / "analysis-runs",
            _required_environment_path("AI_BUDGET_DB_PATH"),
        ):
            return _start(argv)
    except (OSError, ValueError, SourceManifestError):
        print("portfolio_startup_failed code=runtime_recovery_failed", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
