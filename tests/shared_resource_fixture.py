"""Owned weight-free process used to verify shared lease recovery."""

from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decision_workbench.training.process_guard import (
    arm_parent_guard,
    leased_process_options,
)
from decision_workbench.training.resources import ModelResourceLease


if sys.argv[1] == "child":
    if not arm_parent_guard():
        raise RuntimeError("Expected an owned process")
    Path(sys.argv[2]).write_text("ready", encoding="utf-8")
    while True:
        time.sleep(0.1)
else:
    workspace, ready = map(Path, sys.argv[2:])
    with ModelResourceLease(workspace).hold() as lease:
        child = subprocess.Popen(
            [sys.executable, __file__, "child", str(ready)],
            **leased_process_options(lease),
        )
        try:
            child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
