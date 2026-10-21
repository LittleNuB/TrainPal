import subprocess
import sys
from pathlib import Path

import pytest

from hakimi_analysis.ai_budget import AiBudgetLedger
from hakimi_analysis.portfolio_runtime import portfolio_runtime_guard

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux production process locks")


def test_crashed_process_media_is_removed_at_exclusive_restart_and_budget_survives(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "analysis-runs"
    budget = tmp_path / "budget" / "ai.sqlite"
    AiBudgetLedger(budget).reserve(123, kind="fixture", model="fixture")
    script = """
import os,sys
from pathlib import Path
from hakimi_analysis.portfolio_runtime import portfolio_runtime_guard
root, budget = map(Path, sys.argv[1:])
with portfolio_runtime_guard(root, budget):
    (root / 'interrupted-upload').write_bytes(b'synthetic')
    (root / 'run').mkdir()
    (root / 'run' / 'audio.wav').write_bytes(b'synthetic')
    os._exit(9)
"""
    child = subprocess.run([sys.executable, "-c", script, str(runtime), str(budget)], check=False)
    assert child.returncode == 9
    assert len(list(runtime.iterdir())) == 2
    with portfolio_runtime_guard(runtime, budget):
        assert list(runtime.iterdir()) == []
        assert AiBudgetLedger(budget).snapshot()["used_micro_yuan"] == 123
        (runtime / "active-source").write_bytes(b"synthetic")
        with pytest.raises(BlockingIOError), portfolio_runtime_guard(runtime, budget):
            pass
        assert (runtime / "active-source").is_file()


def test_recovery_does_not_follow_media_symlinks_or_accept_a_symlinked_root(tmp_path: Path) -> None:
    outside = tmp_path / "preserved"
    outside.mkdir()
    (outside / "keep").write_bytes(b"fixture")
    runtime = tmp_path / "analysis-runs"
    runtime.mkdir()
    (runtime / "link").symlink_to(outside, target_is_directory=True)
    budget = tmp_path / "budget" / "ai.sqlite"
    with portfolio_runtime_guard(runtime, budget):
        assert not list(runtime.iterdir())
    assert (outside / "keep").read_bytes() == b"fixture"
    runtime.rmdir()
    runtime.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError), portfolio_runtime_guard(runtime, budget):
        pass
    assert (outside / "keep").is_file()


def test_runtime_lock_survives_exec_into_the_worker(tmp_path: Path) -> None:
    runtime = tmp_path / "analysis-runs"
    budget = tmp_path / "budget" / "ai.sqlite"
    script = """
import os,sys
from pathlib import Path
from hakimi_analysis.portfolio_runtime import portfolio_runtime_guard
with portfolio_runtime_guard(Path(sys.argv[1]), Path(sys.argv[2])):
    worker = "import time; print('ready', flush=True); time.sleep(30)"
    os.execv(sys.executable, [sys.executable, '-c', worker])
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(runtime), str(budget)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        with pytest.raises(BlockingIOError), portfolio_runtime_guard(runtime, budget):
            pass
    finally:
        child.kill()
        child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()
    with portfolio_runtime_guard(runtime, budget):
        assert not list(runtime.iterdir())
