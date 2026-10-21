"""Exclusive startup recovery for the portfolio's dedicated transient directory."""

import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def portfolio_runtime_guard(runtime_root: Path, budget_path: Path) -> Iterator[None]:
    if sys.platform != "linux":
        raise ValueError("portfolio deployment requires Linux")
    import fcntl

    if (
        not runtime_root.is_absolute()
        or not budget_path.is_absolute()
        or runtime_root.name != "analysis-runs"
        or runtime_root.resolve() != runtime_root
        or budget_path.resolve().is_relative_to(runtime_root)
    ):
        raise ValueError("unsafe portfolio runtime directory")
    budget_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    # This lock is separate from media and survives exec into the single worker.
    # A second container cannot erase a running instance's source files.
    with (budget_path.parent / "runtime.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_inheritable(lock.fileno(), True)
        for entry in runtime_root.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                entry.unlink()
            else:
                shutil.rmtree(entry)
        yield
