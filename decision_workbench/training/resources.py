"""Single-host OS lease shared by model training and resident inference."""

import os
import time
from pathlib import Path
from contextlib import contextmanager


class ResourceBusy(RuntimeError):
    pass


class ResourceLease:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None
        self.windows_job = None

    def acquire(self, timeout=0, cancelled=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        if file.seek(0, 2) == 0:
            file.write(b"0")
            file.flush()
        deadline = time.monotonic() + timeout
        while True:
            locked = False
            try:
                file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    from .windows_job import previous_job_active

                    if previous_job_active(self.path):
                        raise BlockingIOError(
                            "Previous model process job is still active"
                        )
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.file = file
                return
            except (OSError, BlockingIOError):
                if locked:
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                if (
                    cancelled is not None and cancelled.is_set()
                ) or time.monotonic() >= deadline:
                    file.close()
                    raise ResourceBusy("GPU resource is busy or the wait was cancelled")
                if cancelled is not None:
                    cancelled.wait(0.1)
                else:
                    time.sleep(0.1)

    def stop_children(self):
        if self.windows_job is not None:
            job, self.windows_job = self.windows_job, None
            job.close()
            job.wait_empty()

    def release(self):
        if self.file is not None:
            file = self.file
            self.file = None
            try:
                self.stop_children()
                file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            finally:
                file.close()

    @contextmanager
    def hold(self, timeout=0, cancelled=None):
        self.acquire(timeout, cancelled)
        try:
            yield self
        finally:
            self.release()
