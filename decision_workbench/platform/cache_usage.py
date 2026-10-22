"""OS-leased node users; maintenance cannot race service startup or file readers."""

from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import re
import threading
import uuid

from decision_workbench.training.resources import ResourceLease, ResourceBusy
from .owned_staging import linked

_local = threading.local()


def paths(home):
    home = Path(home).absolute()
    if any(linked(p) for p in (home, *home.parents)) or not home.is_dir():
        raise ValueError("Cache use requires an existing unlinked workspace")
    home = home.resolve()
    users, gate = home / ".dmt-cache-users", home / ".dmt-cache-maintenance.lock"
    if linked(users) or linked(gate):
        raise ValueError("Cache lease controls must not be linked")
    users.mkdir(exist_ok=True)
    return users, gate


@contextmanager
def use(home):
    users, gate = paths(home)
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    key = str(users)
    if key in held:
        yield
        return
    marker = users / (uuid.uuid4().hex + ".lock")
    lease = ResourceLease(marker)
    try:
        with ResourceLease(gate).hold(timeout=1):
            if marker.exists() or linked(marker):
                raise ValueError("Cache user identity is already reserved")
            lease.acquire()
    except ResourceBusy:
        from .artifact_storage import ArtifactUnavailable

        raise ArtifactUnavailable(
            "This node is undergoing cache maintenance; retry after the operator restarts it"
        ) from None
    held[key] = True
    try:
        yield
    finally:
        del held[key]
        # A concurrently starting maintenance operation sees the still-held
        # lease and refuses; keep that protection until we own the gate.
        try:
            with ResourceLease(gate).hold(timeout=2):
                lease.release()
                marker.unlink(missing_ok=True)
        finally:
            lease.release()


@contextmanager
def maintenance(home):
    users, gate = paths(home)
    with ResourceLease(gate).hold(timeout=1):
        count = 0
        for marker in users.iterdir():
            count += 1
            if (
                count > 10000
                or linked(marker)
                or not marker.is_file()
                or not re.fullmatch(r"[a-f0-9]{32}\.lock", marker.name)
                or marker.stat().st_size != 1
            ):
                raise ValueError("Invalid or oversized cache-user lease directory")
            try:
                with ResourceLease(marker).hold() as lease:
                    lease.file.seek(0)
                    if lease.file.read(2) != b"0":
                        raise ValueError(
                            "Unknown cache-user lease contents; file preserved"
                        )
            except ResourceBusy:
                raise ResourceBusy(
                    "Stop this node's API, worker, warmers and cache readers before maintenance"
                ) from None
            marker.unlink()
        yield


def uses_cache(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        store = getattr(self, "store", self)
        with use(store.folder):
            return function(self, *args, **kwargs)

    return call
