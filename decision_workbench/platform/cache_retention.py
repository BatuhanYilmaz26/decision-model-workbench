"""Reviewed offline node-cache eviction; shared objects and registry are retained."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
import uuid

from decision_workbench.training.resources import ModelResourceLease
from .artifact_storage import ArtifactUnavailable, manifest_content, safe_name, tree
from .cache_records import FIELDS, FORMAT as RECORD_FORMAT, checked, read, record_path
from .cache_usage import maintenance
from .local_stage_cleanup import workspace
from .owned_staging import identity, linked, write_json
from .store import Conflict, digest

FORMAT = "domain-cache-retention-v1"
MAX_ENTRIES = 10000
MAX_PLAN = 32 * 1024**2


def metadata(store):
    if store._postgres is None or store.artifact_store is None:
        raise ValueError(
            "Cache eviction requires PostgreSQL and shared artifact storage; local-only models are preserved"
        )
    with store.transaction() as db:
        store._artifact_binding(db)
        for table, field, condition in (
            ("entities", "payload", "kind='model'"),
            ("jobs", "config", "json_extract(config,'$.incoming_id') IS NOT NULL"),
        ):
            size = db.execute(
                "SELECT count(*),coalesce(sum(length("
                + field
                + ")),0) FROM "
                + table
                + " WHERE "
                + condition
            ).fetchone()
            if size[0] > MAX_ENTRIES or size[0] * 256 + size[1] * 4 > MAX_PLAN:
                raise ValueError(
                    "Cache reference metadata exceeds its bounded capacity"
                )
        models = db.execute(
            "SELECT id,payload FROM entities WHERE kind='model' ORDER BY id LIMIT 10001"
        ).fetchall()
        active = db.execute(
            "SELECT model_id FROM active_models WHERE model_id IS NOT NULL ORDER BY model_id LIMIT 10001"
        ).fetchall()
        jobs = db.execute(
            "SELECT id,config,status FROM jobs WHERE json_extract(config,'$.incoming_id') IS NOT NULL ORDER BY id LIMIT 10001"
        ).fetchall()
    if max(len(models), len(active), len(jobs)) > MAX_ENTRIES:
        raise ValueError("Cache reference inventory exceeds its bounded capacity")
    known, pinned = {}, set()
    by_id = {}
    for row in models:
        model = json.loads(row["payload"])
        key = ("artifacts", model["artifact_id"])
        value = {
            "model": {name: model[name] for name in FIELDS},
            "reference": model.get("artifact_reference"),
        }
        if key in known and known[key] != value:
            raise ValueError("Conflicting registered artifact identities")
        known[key] = value
        by_id[row["id"]] = key
    for row in active:
        if row["model_id"] not in by_id:
            raise ValueError("Active model has no registry entry")
        pinned.add(by_id[row["model_id"]])
    states = []
    for row in jobs:
        config = json.loads(row["config"])
        key = ("incoming", config["incoming_id"])
        states.append(
            [
                row["id"],
                config["incoming_id"],
                config.get("incoming_reference"),
                row["status"],
            ]
        )
        if row["status"] not in {"succeeded", "failed", "cancelled"}:
            pinned.add(key)
    normalized = {
        "models": [[list(k), v] for k, v in sorted(known.items())],
        "pinned": sorted(pinned),
        "jobs": states,
    }
    return known, pinned, digest(normalized)


def snapshot(folder):
    if linked(folder) or not folder.is_dir() or any(linked(p) for p in folder.parents):
        raise ValueError("Cache directory is missing or linked")
    root = folder.resolve()
    files, directories, pending, total, modified = (
        [],
        [],
        [folder],
        0,
        folder.stat().st_mtime,
    )
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if linked(path) or not path.resolve().is_relative_to(root):
                    raise ValueError("Cache contains linked or external content")
                name = safe_name(path.relative_to(folder).as_posix())
                info = path.stat()
                item = {"path": name, "identity": [info.st_dev, info.st_ino]}
                modified = max(modified, info.st_mtime)
                if path.is_dir():
                    directories.append(item)
                    pending.append(path)
                elif path.is_file():
                    total += info.st_size
                    files.append(
                        {**item, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns}
                    )
                else:
                    raise ValueError("Cache contains a special file")
                if len(files) + len(directories) > MAX_ENTRIES:
                    raise ValueError(
                        "Cache filesystem inventory exceeds its entry limit"
                    )
    return {
        "identity": identity(folder),
        "files": sorted(files, key=lambda x: x["path"]),
        "directories": sorted(directories, key=lambda x: x["path"]),
        "bytes": total,
        "modified": modified,
    }


def local_manifest(folder, record):
    parent = folder.parent / ".dmt-manifests"
    path = parent / (record["reference"]["sha256"] + ".json")
    if (
        linked(parent)
        or linked(path)
        or not path.is_file()
        or path.stat().st_size != record["reference"]["bytes"]
    ):
        raise ValueError("Orphan cache needs its pinned local shared-manifest copy")
    with path.open("rb") as stream:
        body = stream.read(record["reference"]["bytes"] + 1)
    return manifest_content(
        body, record["namespace"], record["reference"], record["model"]
    )[0]


@contextmanager
def guards(store):
    home = workspace(store.folder)
    with maintenance(home), ModelResourceLease(home).hold(), store.artifact_guard(
        exclusive=True
    ) as live:
        yield home, live


def plan_for(store, home, min_idle_seconds, timeout):
    if (
        type(min_idle_seconds) is not int
        or not 0 <= min_idle_seconds <= 10 * 365 * 86400
    ):
        raise ValueError("Choose an idle retention period between zero and ten years")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("Choose an inventory timeout from 1 to 3600 seconds")
    began, cutoff = time.monotonic(), time.time() - min_idle_seconds

    def check():
        if time.monotonic() - began > timeout:
            raise ValueError("Cache inventory deadline exceeded; no cache removed")

    known, pinned, metadata_sha = metadata(store)
    candidates, preserved, total_entries = [], 0, 0
    for role in ("artifacts", "incoming"):
        parent = home / role
        if not parent.exists():
            continue
        if linked(parent) or parent.resolve().parent != home:
            raise ValueError("Cache parent must remain inside this node workspace")
        names = []
        for path in parent.iterdir():
            names.append(path)
            if len(names) > 100000:
                raise ValueError("Cache parent exceeds its inventory limit")
        for folder in sorted(names):
            check()
            if not re.fullmatch(r"[a-f0-9]{32}", folder.name):
                continue
            key = (role, folder.name)
            if key in pinned:
                preserved += 1
                continue
            registered = known.get(key)
            marker = record_path(folder)
            if marker.exists() or linked(marker):
                record = read(folder)
                if record["namespace"] != store.workspace_id or record[
                    "identity"
                ] != identity(folder):
                    raise ValueError(
                        "Cache marker belongs to another workspace or directory"
                    )
                if registered and any(
                    record[k] != registered[k] for k in ("reference", "model")
                ):
                    raise ValueError("Cache marker differs from the registry")
                source = "marker"
            elif registered and registered["reference"] is not None:
                record = {
                    "format": RECORD_FORMAT,
                    "namespace": store.workspace_id,
                    **registered,
                    "identity": identity(folder),
                    "last_used": folder.stat().st_mtime,
                }
                record["sha256"] = digest(record)
                checked(record, folder)
                source = "registry"
            else:
                preserved += 1
                continue
            before = snapshot(folder)
            if max(before["modified"], record["last_used"]) > cutoff:
                preserved += 1
                continue
            total_entries += len(before["files"]) + len(before["directories"])
            if total_entries > 100000 or len(candidates) >= MAX_ENTRIES:
                raise ValueError("Cache cleanup exceeds its total inventory capacity")
            if registered:
                manifest = store.artifact_store.inspect(
                    store.workspace_id, record["reference"], record["model"]
                )
                for item in manifest["files"].values():
                    check()
                    store.artifact_store.objects.available(
                        store.workspace_id + "/blobs/" + item["sha256"], item["bytes"]
                    )
            else:
                manifest = local_manifest(folder, record)
            if tree(folder, check) != manifest["files"] or snapshot(folder) != before:
                raise ValueError(
                    "Cache files differ from their pinned manifest or changed during review"
                )
            candidates.append(
                {
                    "role": role,
                    "id": folder.name,
                    "source": source,
                    "record": record,
                    "inventory": before,
                }
            )
    plan = {
        "format": FORMAT,
        "home": str(home),
        "identity": identity(home),
        "workspace": store.workspace_id,
        "metadata_sha256": metadata_sha,
        "min_idle_seconds": min_idle_seconds,
        "timeout": timeout,
        "candidates": candidates,
    }
    if len(json.dumps(plan).encode()) > MAX_PLAN:
        raise ValueError("Cache cleanup plan exceeds its byte limit")
    return plan, preserved


def summary(plan, preserved=0):
    return {
        "format": FORMAT,
        "plan_sha256": digest(plan),
        "caches": len(plan["candidates"]),
        "bytes": sum(c["inventory"]["bytes"] for c in plan["candidates"]),
        "preserved_entries": preserved,
        "min_idle_seconds": plan["min_idle_seconds"],
    }


def preview(store, min_idle_seconds=86400, timeout=300):
    with guards(store) as (home, live):
        plan, preserved = plan_for(store, home, min_idle_seconds, timeout)
        live()
        return {**summary(plan, preserved), "candidates": plan["candidates"]}


def remove_quarantine(folder, expected):
    # A resumed operation may have removed a subset. Accept only the remaining
    # reviewed files with their original identities, sizes and modification times.
    current = snapshot(folder)
    if current["identity"] != expected["identity"]:
        raise ValueError("Quarantined cache was replaced")
    for key in ("files", "directories"):
        allowed = {item["path"]: item for item in expected[key]}
        if any(allowed.get(item["path"]) != item for item in current[key]):
            raise ValueError("Quarantined cache gained or changed content")
    parent = folder.parent.resolve()
    for item in current["files"]:
        path = folder / item["path"]
        if (
            linked(folder)
            or identity(folder) != expected["identity"]
            or any(linked(p) for p in (path, *path.parents))
            or folder.resolve().parent != parent
            or not path.resolve().is_relative_to(folder.resolve())
        ):
            raise ValueError("Refusing cache removal outside the reviewed quarantine")
        info = path.stat()
        if (
            [info.st_dev, info.st_ino] != item["identity"]
            or info.st_size != item["bytes"]
            or info.st_mtime_ns != item["mtime_ns"]
        ):
            raise ValueError("Quarantined cache file changed")
        path.unlink()
    for item in sorted(
        current["directories"], key=lambda x: len(Path(x["path"]).parts), reverse=True
    ):
        path = folder / item["path"]
        if (
            any(linked(p) for p in (path, *path.parents))
            or not path.resolve().is_relative_to(folder.resolve())
            or identity(path) != item["identity"]
        ):
            raise ValueError("Quarantined directory changed")
        path.rmdir()
    if (
        linked(folder)
        or identity(folder) != expected["identity"]
        or folder.resolve().parent != parent
    ):
        raise ValueError("Quarantined cache root changed")
    folder.rmdir()


def receipt_path(home, operation):
    if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise ValueError("Invalid cache cleanup operation ID")
    parent = home / ".dmt-cache-cleanups"
    if linked(parent) or parent.resolve().parent != home:
        raise ValueError("Cache receipts must remain inside the workspace")
    parent.mkdir(exist_ok=True)
    path = parent / (operation + ".json")
    if linked(path):
        raise ValueError("Cache cleanup receipt must not be linked")
    return path


def execute(store, expected, min_idle_seconds=86400, timeout=300, operation_id=None):
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Supply the reviewed cache cleanup plan SHA-256")
    fresh = operation_id is None
    with guards(store) as (home, live):
        if fresh:
            plan, preserved = plan_for(store, home, min_idle_seconds, timeout)
            if digest(plan) != expected:
                raise Conflict(
                    "Cache or registry inputs changed; review a fresh preview"
                )
            operation_id = uuid.uuid4().hex
            value = {
                "format": FORMAT,
                "operation_id": operation_id,
                "plan": plan,
                "state": "running",
                "completed": [],
                "not_started": [],
                "created": time.time(),
            }
        else:
            path = receipt_path(home, operation_id)
            if not path.is_file() or path.stat().st_size > MAX_PLAN + 1024**2:
                raise ValueError("Cache receipt is missing or oversized")
            value = json.loads(path.read_bytes())
            plan = value["plan"]
            if (
                value.get("format") != FORMAT
                or value.get("operation_id") != operation_id
                or plan["home"] != str(home)
                or plan["identity"] != identity(home)
                or plan["workspace"] != store.workspace_id
            ):
                raise ValueError("Cache cleanup receipt binding differs")
            preserved = 0
        if digest(plan) != expected:
            raise Conflict("Cache cleanup plan hash differs")
        receipt = receipt_path(home, operation_id)
        if fresh and receipt.exists():
            raise Conflict("Cache cleanup operation identity is already in use")
        resuming = not fresh
        if value["state"] == "completed":
            return {
                **summary(plan),
                "operation_id": operation_id,
                "state": "completed",
                "completed_caches": len(value["completed"]),
                "not_started": len(value["not_started"]),
            }
        value["state"] = "running"
        write_json(receipt, value)
        try:
            for index, item in enumerate(plan["candidates"]):
                if index in value["completed"] or index in value["not_started"]:
                    continue
                live()
                parent = home / item["role"]
                if (
                    item["role"] not in {"artifacts", "incoming"}
                    or linked(parent)
                    or parent.resolve().parent != home
                    or identity(home) != plan["identity"]
                ):
                    raise ValueError("Cache parent changed")
                target = parent / item["id"]
                if not re.fullmatch(r"[a-f0-9]{32}", item["id"]):
                    raise ValueError("Invalid cache identity in receipt")
                quarantine = parent / (
                    ".dmt-cache-evict-" + operation_id + "-" + item["id"]
                )
                if not quarantine.exists():
                    if resuming:
                        if (
                            target.exists()
                            and identity(target) == item["inventory"]["identity"]
                        ):
                            value["not_started"].append(index)
                        else:
                            value["completed"].append(index)
                        write_json(receipt, value)
                        continue
                    if snapshot(target) != item["inventory"]:
                        raise ValueError("Cache changed after review")
                    target.rename(quarantine)
                if item["source"] == "marker":
                    marker = record_path(target)
                    if marker.exists():
                        if read(target) == item["record"]:
                            marker.unlink()
                        elif not resuming:
                            raise ValueError("Cache ownership changed after review")
                remove_quarantine(quarantine, item["inventory"])
                value["completed"].append(index)
                write_json(receipt, value)
            value["state"] = "completed"
            write_json(receipt, value)
        except BaseException as exc:
            value.update(state="partial", error=type(exc).__name__)
            write_json(receipt, value)
            raise RuntimeError(
                "Cache cleanup is incomplete; resume operation "
                + operation_id
                + " with its reviewed plan hash. Unstarted caches need a fresh preview."
            ) from None
        return {
            **summary(plan, preserved),
            "operation_id": operation_id,
            "state": "completed",
            "completed_caches": len(value["completed"]),
            "not_started": len(value["not_started"]),
        }
