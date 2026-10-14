"""Real Windows job/process trees with sleepers; no model/GPU efficacy claim."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from decision_workbench.training.process_guard import (
    arm_parent_guard,
    leased_process_options,
)
from decision_workbench.training.resources import ResourceLease, ResourceBusy
from decision_workbench.training.windows_job import active_processes, kernel


def wait_file(path, seconds=15):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(str(path))
        time.sleep(0.01)


def child(folder, mode):
    if mode == "before-arm":
        (folder / "ready.json").write_text(json.dumps({"child": os.getpid()}))
        wait_file(folder / "continue")
    arm_parent_guard()
    (folder / "work-started").touch()
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (folder / "ready.json").write_text(
        json.dumps({"child": os.getpid(), "grandchild": grandchild.pid})
    )
    wait_file(folder / "finish", seconds=30)
    (folder / "complete").touch()


def parent(folder, mode):
    with ResourceLease(folder / "gpu.lock").hold() as lease:
        options = leased_process_options(lease)
        process = subprocess.Popen(
            [sys.executable, __file__, "child", str(folder), mode], **options
        )
        wait_file(folder / "ready.json")
        print(
            json.dumps(
                {
                    "job": lease.windows_job.name,
                    **json.loads((folder / "ready.json").read_text()),
                }
            ),
            flush=True,
        )
        if mode == "normal":
            assert process.wait(timeout=40) == 0
        else:
            time.sleep(40)


def scenario(folder, mode):
    dll = kernel()
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dll.WaitForSingleObject.restype = wintypes.DWORD
    dll.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    dll.TerminateProcess.restype = wintypes.BOOL
    process = subprocess.Popen(
        [sys.executable, __file__, "parent", str(folder), mode],
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    handles, retained = [], None
    begin = time.monotonic()
    try:
        result = json.loads(process.stdout.readline())
        for key in ["child", "grandchild"]:
            if key in result:
                handle = dll.OpenProcess(0x100001, False, result[key])
                assert handle, "Cannot observe own child process"
                handles.append(handle)
        if mode == "held-reference":
            retained = dll.OpenJobObjectW(0x4, False, result["job"])
            initial_active = active_processes(result["job"])
            # Virtual-environment redirectors can add processes to the same
            # owned tree. All active members matter, not exactly two PIDs.
            assert retained and initial_active >= 2
        if mode == "normal":
            (folder / "finish").touch()
            assert process.wait(timeout=15) == 0
        else:
            process.kill()
            process.wait(timeout=15)
            if mode == "before-arm":
                assert dll.WaitForSingleObject(handles[0], 0) == 258
                (folder / "continue").touch()
            if mode == "held-reference":
                assert all(dll.WaitForSingleObject(h, 0) == 258 for h in handles)
                try:
                    ResourceLease(folder / "gpu.lock").acquire(timeout=0.15)
                except ResourceBusy:
                    pass
                else:
                    raise AssertionError(
                        "Capacity admitted while prior job still active"
                    )
                dll.CloseHandle(retained)
                retained = None
        assert all(
            dll.WaitForSingleObject(h, 10000) == 0 for h in handles
        ), "Owned process tree survived"
        with ResourceLease(folder / "gpu.lock").hold():
            pass
        if mode == "before-arm":
            assert not (folder / "work-started").exists()
        else:
            assert (folder / "work-started").exists()
        assert (folder / "complete").exists() == (mode == "normal")
        print(
            json.dumps(
                {
                    "passed": True,
                    "mode": mode,
                    "process_count": len(handles),
                    "owned_tree_ended": True,
                    "lease_free": True,
                    "complete_marker": (folder / "complete").exists(),
                    "seconds": time.monotonic() - begin,
                }
            )
        )
    finally:
        if retained:
            dll.CloseHandle(retained)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)
        for h in handles:
            if dll.WaitForSingleObject(h, 0) == 258:
                dll.TerminateProcess(h, 1)
                assert dll.WaitForSingleObject(h, 10000) == 0
            dll.CloseHandle(h)
        process.stdout.close()


if __name__ == "__main__":
    action, folder, mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    {"parent": parent, "child": child, "scenario": scenario}[action](folder, mode)
