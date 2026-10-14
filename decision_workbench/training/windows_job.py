"""Named Windows process jobs for model-child ownership, with no breakaway.

The parent keeps the only long-lived handle. A child joins before model
imports, then closes its temporary handle. Lease admission also checks the
previous job's active process count because Windows byte locks are not a
Linux-style inherited flock ownership mechanism.
"""

import ctypes
from ctypes import wintypes
import errno
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

NAME = re.compile(r"Local\\DMT-[0-9a-f]{32}\Z")


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        (key, ctypes.c_uint64)
        for key in [
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        ]
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        (key, ctypes.c_int64)
        for key in ["user", "kernel", "period_user", "period_kernel"]
    ] + [(key, wintypes.DWORD) for key in ["faults", "total", "active", "terminated"]]


@lru_cache(maxsize=1)
def kernel():
    if sys.platform != "win32":
        raise RuntimeError("Windows process jobs require Windows")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    declarations = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "OpenJobObjectW": (
            [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR],
            wintypes.HANDLE,
        ),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
            ],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for key, (args, result) in declarations.items():
        function = getattr(dll, key)
        function.argtypes, function.restype = args, result
    return dll


def checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def active_processes(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise OSError(errno.EINVAL, "Invalid Windows model job identity")
    dll = kernel()
    handle = dll.OpenJobObjectW(0x4, False, name)  # JOB_OBJECT_QUERY
    if not handle:
        if ctypes.get_last_error() == 2:  # ERROR_FILE_NOT_FOUND: destroyed job
            return 0
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = Accounting()
        checked(
            dll.QueryInformationJobObject(
                handle, 1, ctypes.byref(result), ctypes.sizeof(result), None
            )
        )
        return result.active
    finally:
        checked(dll.CloseHandle(handle))


def previous_job_active(lease_path):
    path = Path(lease_path).with_name(Path(lease_path).name + ".windows-job.json")
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise OSError(errno.EINVAL, "Invalid Windows model lease metadata")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except (ValueError, UnicodeError) as exc:
        raise OSError(errno.EINVAL, "Invalid Windows model lease metadata") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "job_name"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise OSError(errno.EINVAL, "Invalid Windows model lease metadata")
    return active_processes(value["job_name"])


class WindowsJob:
    def __init__(self, lease_path):
        self.name = "Local\\DMT-" + uuid.uuid4().hex
        self.dll = kernel()
        self.handle = checked(self.dll.CreateJobObjectW(None, self.name))
        try:
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                raise RuntimeError("Refuse a preexisting model process job")
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(
                self.dll.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
            path = Path(lease_path).with_name(
                Path(lease_path).name + ".windows-job.json"
            )
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump({"version": 1, "job_name": self.name}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            checked(self.dll.CloseHandle(handle))

    def wait_empty(self, seconds=10):
        deadline = time.monotonic() + seconds
        while active_processes(self.name):
            if time.monotonic() >= deadline:
                raise OSError(
                    errno.EBUSY, "Windows model job still has active processes"
                )
            time.sleep(0.01)


def join_parent_job(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise RuntimeError("Invalid Windows child job identity")
    dll = kernel()
    handle = checked(dll.OpenJobObjectW(0x1, False, name))  # ASSIGN_PROCESS only
    try:
        checked(dll.AssignProcessToJobObject(handle, dll.GetCurrentProcess()))
    finally:
        # If the parent already died, this last close terminates us before
        # model imports. The child must never retain the kill-on-close handle.
        checked(dll.CloseHandle(handle))
