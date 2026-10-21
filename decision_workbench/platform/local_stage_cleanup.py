"""Reviewable cleanup of leased transfer stages in one local workspace cache."""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import time
import uuid

from decision_workbench.training.resources import ResourceLease, ResourceBusy
from .owned_staging import (
    checked_record,
    identity,
    inventory,
    linked,
    MAX_RECORD,
    record_paths,
    remove_owned,
    write_json,
)
from .store import Conflict, digest

FORMAT = "domain-local-stage-cleanup-v1"
MAX_STAGES = 10000


def workspace(home):
    home = Path(home).absolute()
    if any(linked(p) for p in (home, *home.parents)) or not home.is_dir():
        raise ValueError("Select an existing, unlinked local workspace directory")
    if not any(
        (home / name).is_file() and not linked(home / name)
        for name in ("workspace.sqlite3", ".dmt-postgres.json")
    ):
        raise ValueError("The selected directory has no workbench workspace marker")
    return home.resolve()


def read_record(parent, identifier):
    marker, lock = record_paths(parent, identifier)
    if (
        linked(marker)
        or linked(lock)
        or not marker.is_file()
        or (lock.exists() and not lock.is_file())
        or marker.resolve().parent != parent
    ):
        raise ValueError("Stage control files must remain inside their parent")
    with marker.open("rb") as stream:
        body = stream.read(MAX_RECORD + 1)
    if len(body) > MAX_RECORD:
        raise ValueError("Stage ownership record exceeds its limit")
    record = json.loads(body)
    if not isinstance(record, dict) or record.get("id") != identifier:
        raise ValueError("Stage record identity differs from its file name")
    checked_record(parent, record)
    return record


@contextmanager
def node_lock(home):
    path = home / ".dmt-local-cleanup.lock"
    if linked(path) or (path.exists() and not path.is_file()):
        raise ValueError("Local cleanup lock must not be linked")
    try:
        with ResourceLease(path).hold(timeout=1):
            yield
    except ResourceBusy:
        raise RuntimeError("Another local cleanup is in progress") from None


def plan_for(home, min_age_seconds):
    if type(min_age_seconds) is not int or not 0 <= min_age_seconds <= 10 * 365 * 86400:
        raise ValueError("Select a stage age between zero and ten years")
    candidates, busy, unmanaged, records, total_entries = [], 0, 0, 0, 0
    cutoff = time.time() - min_age_seconds
    for role in ("artifacts", "incoming"):
        parent = home / role
        if not parent.exists():
            continue
        if linked(parent) or parent.resolve().parent != home:
            raise ValueError("Workspace staging parent must not be linked")
        names = []
        for path in parent.iterdir():
            names.append(path)
            if len(names) > 100000:
                raise ValueError("Local staging parent exceeds its inventory limit")
        marked = set()
        for marker in sorted(names):
            match = re.fullmatch(r"\.dmt-stage-([a-f0-9]{32})\.json", marker.name)
            if not match:
                continue
            records += 1
            if records > MAX_STAGES:
                raise ValueError("Too many ownership records; no stages removed")
            identifier = match[1]
            _, lock = record_paths(parent, identifier)
            if linked(marker) or linked(lock) or (lock.exists() and not lock.is_file()):
                raise ValueError("Linked stage control files are refused")
            lease = ResourceLease(lock)
            try:
                lease.acquire()
            except ResourceBusy:
                busy += 1
                marked.update(
                    path.name
                    for path in names
                    if re.fullmatch(
                        r"\.dmt-(?:download|intake)-" + identifier + r"-[a-z0-9_]{8}",
                        path.name,
                    )
                )
                continue
            try:
                if not marker.exists():
                    continue
                record = read_record(parent, identifier)
                marked.add(record["directory"])
                value = inventory(parent, record)
                total_entries += len(value["files"]) + len(value["directories"])
                if total_entries > 100000:
                    raise ValueError(
                        "Owned staging inventory exceeds its global entry limit"
                    )
                if value["modified"] <= cutoff:
                    candidates.append(
                        {"parent": role, "record": record, "inventory": value}
                    )
            finally:
                lease.release()
        unmanaged += sum(
            1
            for path in names
            if path.name.startswith((".dmt-download-", ".dmt-intake-"))
            and path.name not in marked
        )
    plan = {
        "format": FORMAT,
        "home": str(home),
        "identity": identity(home),
        "min_age_seconds": min_age_seconds,
        "candidates": sorted(
            candidates, key=lambda item: (item["parent"], item["record"]["id"])
        ),
    }
    if len(json.dumps(plan).encode()) > 32 * 1024**2:
        raise ValueError("Local cleanup plan exceeds its size limit")
    return plan, {"busy_stages": busy, "unmanaged_entries": unmanaged}


def summary(plan, observations=None):
    return {
        "plan_sha256": digest(plan),
        "stages": len(plan["candidates"]),
        "files": sum(len(item["inventory"]["files"]) for item in plan["candidates"]),
        "bytes": sum(item["inventory"]["bytes"] for item in plan["candidates"]),
        "min_age_seconds": plan["min_age_seconds"],
        **(observations or {}),
    }


def preview(home, min_age_seconds=86400):
    home = workspace(home)
    with node_lock(home):
        plan, observations = plan_for(home, min_age_seconds)
        return {**summary(plan, observations), "candidates": plan["candidates"]}


def execute(home, expected, min_age_seconds=86400):
    home = workspace(home)
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Retain the exact reviewed local cleanup plan SHA-256")
    with node_lock(home):
        plan, observations = plan_for(home, min_age_seconds)
        if digest(plan) != expected:
            raise Conflict("Owned stages changed; review a fresh local cleanup plan")
        history = home / ".dmt-stage-cleanups"
        if linked(history) or history.resolve().parent != home:
            raise ValueError(
                "Cleanup receipts must stay in their private workspace directory"
            )
        history.mkdir(exist_ok=True)
        operation = uuid.uuid4().hex
        receipt = history / (operation + ".json")
        record = {
            "format": FORMAT,
            "operation_id": operation,
            "plan": plan,
            "state": "running",
            "completed_stages": 0,
            "created": time.time(),
        }
        write_json(receipt, record)
        try:
            for item in plan["candidates"]:
                parent = home / item["parent"]
                identifier = item["record"]["id"]
                marker, lock = record_paths(parent, identifier)
                if (
                    linked(parent)
                    or parent.resolve().parent != home
                    or identity(home) != plan["identity"]
                    or linked(lock)
                ):
                    raise ValueError("Workspace stage parent changed")
                lease = ResourceLease(lock)
                lease.acquire()
                try:
                    live = read_record(parent, identifier)
                    if live != item["record"]:
                        raise ValueError("Stage ownership changed after review")
                    remove_owned(parent, live, item["inventory"])
                    marker.unlink()
                finally:
                    lease.release()
                lock.unlink(missing_ok=True)
                record["completed_stages"] += 1
                write_json(receipt, record)
            record["state"] = "completed"
            write_json(receipt, record)
        except BaseException as exc:
            record.update(state="partial", error=type(exc).__name__)
            write_json(receipt, record)
            raise RuntimeError(
                "Local cleanup is incomplete. Inspect receipt "
                + operation
                + "; review a fresh preview of remaining owned stages before retrying"
            ) from None
        return {
            **summary(plan, observations),
            "operation_id": operation,
            "state": "completed",
            "completed_stages": record["completed_stages"],
        }
