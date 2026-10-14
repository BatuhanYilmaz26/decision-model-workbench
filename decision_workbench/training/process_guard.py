"""Pre-import model-child ownership for Linux and Windows.

The child arms this before importing model packages. The inherited flock file
description keeps capacity reserved even if the parent dies before arming.
Windows children join a parent-owned kill-on-close process job; admission
checks job activity as well as the platform file lock. Other operating
systems retain their existing supervised cancellation path.
"""

import ctypes
import os
import signal
import stat
import sys
import threading

_KEYS = ("DMT_LINUX_PARENT_PID", "DMT_LINUX_PARENT_TID", "DMT_LINUX_LEASE_FD")
_lease_file = None
_WINDOWS_KEY = "DMT_WINDOWS_MODEL_JOB"


def leased_process_options(lease, environment=None):
    """Create platform ownership metadata for a child under a held lease."""
    env = dict(os.environ if environment is None else environment)
    for key in _KEYS:
        env.pop(key, None)
    env.pop(_WINDOWS_KEY, None)
    if sys.platform == "win32":
        if lease.file is None or lease.file.closed:
            raise ValueError("Child launch requires a held resource lease")
        from .windows_job import WindowsJob

        if lease.windows_job is not None:
            raise ValueError("This held lease already owns a model process job")
        lease.windows_job = WindowsJob(lease.path)
        env[_WINDOWS_KEY] = lease.windows_job.name
        return {"env": env, "creationflags": 0x08000000}  # CREATE_NO_WINDOW
    if sys.platform != "linux":
        return {"env": env}
    if lease.file is None or lease.file.closed:
        raise ValueError("Child launch requires a held resource lease")
    fd = lease.file.fileno()
    env.update(zip(_KEYS, map(str, (os.getpid(), threading.get_native_id(), fd))))
    return {"env": env, "pass_fds": (fd,)}


def arm_parent_guard():
    """Fail before model imports if requested ownership cannot be established.

    Standalone runners have no guard metadata and retain their own resource
    contract. Linux protects the direct child; Windows contains its ordinary
    CreateProcess descendants in the parent-owned job.
    """
    global _lease_file
    values = [os.environ.pop(key, None) for key in _KEYS]
    windows = os.environ.pop(_WINDOWS_KEY, None)
    if windows is not None:
        if sys.platform != "win32" or any(value is not None for value in values):
            raise RuntimeError("Invalid Windows child supervision metadata")
        from .windows_job import join_parent_job

        join_parent_job(windows)
        return True
    if all(value is None for value in values):
        return False
    if sys.platform != "linux" or any(value is None for value in values):
        raise RuntimeError("Invalid Linux child supervision metadata")
    try:
        parent, thread, fd = map(int, values)
        if parent <= 0 or thread <= 0 or fd < 3:
            raise ValueError("Invalid supervision identity")
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Inherited resource lease must be a regular file")
        parent_task = f"/proc/{parent}/task/{thread}"
        if os.getppid() != parent or not os.path.isdir(parent_task):
            raise RuntimeError("Supervising parent ended before child initialization")
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        if prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "Parent-death protection unavailable")
        # The parent may die before prctl; the kernel cannot signal that event
        # retroactively. Also check the creating thread, not only its process.
        if os.getppid() != parent or not os.path.isdir(parent_task):
            raise RuntimeError("Supervising parent ended during child initialization")
        os.set_inheritable(fd, False)
        _lease_file = os.fdopen(fd, "a+b")
        return True
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Linux child resource protection could not be established"
        ) from exc
