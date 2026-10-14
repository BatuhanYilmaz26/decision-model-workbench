"""Isolated Linux process tree for real lease/crash tests; no model simulation claim."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from decision_workbench.training.process_guard import (
    arm_parent_guard,
    leased_process_options,
)
from decision_workbench.training.resources import ResourceLease, ResourceBusy


def wait_file(path, seconds=10):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(str(path))
        time.sleep(0.01)


def active(pid):
    path = Path(f"/proc/{pid}/stat")
    return path.exists() and path.read_text().rsplit(")", 1)[1].split()[0] != "Z"


def child(folder, mode):
    fd = int(os.environ["DMT_LINUX_LEASE_FD"])
    if mode == "before-arm":
        (folder / "ready").write_text("{}")
        wait_file(folder / "continue")
    arm_parent_guard()
    death = ctypes.c_int()
    assert ctypes.CDLL(None).prctl(2, ctypes.byref(death), 0, 0, 0) == 0
    (folder / "ready").write_text(
        json.dumps(
            {
                "signal": death.value,
                "fd_inheritable": os.get_inheritable(fd),
                "guard_env_cleared": not any(
                    k.startswith("DMT_LINUX_") for k in os.environ
                ),
            }
        )
    )
    (folder / "work-started").touch()
    wait_file(folder / "finish", seconds=30)


def parent(folder, mode):
    with ResourceLease(folder / "gpu.lock").hold() as lease:
        process = None

        def launch():
            nonlocal process
            process = subprocess.Popen(
                [sys.executable, __file__, "child", str(folder), mode],
                **leased_process_options(lease),
            )
            wait_file(folder / "ready")
            if mode == "thread-exit":
                wait_file(folder / "continue")

        thread = None
        if mode == "thread-exit":
            thread = threading.Thread(target=launch)
            thread.start()
            wait_file(folder / "ready")
        else:
            launch()
        print(json.dumps({"pid": process.pid}), flush=True)
        if mode == "normal":
            process.wait(timeout=30)
        else:
            time.sleep(30)


def scenario(mode):
    # This one short-lived fixture process becomes subreaper; it changes no
    # server/host setting and reaps only its own exact child PID.
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        parent_process = subprocess.Popen(
            [sys.executable, __file__, "parent", name, mode],
            stdout=subprocess.PIPE,
            text=True,
        )
        pid = None
        try:
            pid = json.loads(parent_process.stdout.readline())["pid"]
            ready = json.loads((folder / "ready").read_text())
            if mode == "normal":
                assert ready == {
                    "signal": 9,
                    "fd_inheritable": False,
                    "guard_env_cleared": True,
                }
                (folder / "finish").touch()
                assert parent_process.wait(timeout=10) == 0
            elif mode == "thread-exit":
                (folder / "continue").touch()
            else:
                parent_process.kill()
                parent_process.wait(timeout=10)
                if mode == "before-arm":
                    assert active(pid)
                    try:
                        ResourceLease(folder / "gpu.lock").acquire()
                    except ResourceBusy:
                        pass
                    else:
                        raise AssertionError(
                            "Inherited lease was released before unarmed child exit"
                        )
                    (folder / "continue").touch()
            deadline = time.monotonic() + 10
            while active(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not active(pid), "Child survived supervision boundary"
            if mode == "thread-exit":
                assert (
                    parent_process.poll() is None
                ), "Whole parent died rather than creating thread"
                parent_process.kill()
                parent_process.wait(timeout=10)
            if mode != "normal":
                os.waitpid(pid, 0)
            with ResourceLease(folder / "gpu.lock").hold():
                pass
            if mode == "before-arm":
                assert not (folder / "work-started").exists()
            print(
                json.dumps(
                    {
                        "passed": True,
                        "mode": mode,
                        "child_ended": True,
                        "lease_free_after_exit": True,
                        "model_work_started": (folder / "work-started").exists(),
                    }
                )
            )
        finally:
            if parent_process.poll() is None:
                parent_process.kill()
                parent_process.wait(timeout=10)
            if pid and active(pid):
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            parent_process.stdout.close()


if __name__ == "__main__":
    if sys.argv[1] == "scenario":
        scenario(sys.argv[2])
    else:
        {"parent": parent, "child": child}[sys.argv[1]](Path(sys.argv[2]), sys.argv[3])
