"""Leased transfer stages and bounded, nonrecursive removal of owned files."""

import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from decision_workbench.training.resources import ResourceLease
from .credentials import publish

FORMAT = "domain-owned-stage-v1"
MAX_FILES = 10000
MAX_RECORD = 16384


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def identity(path):
    value = path.stat()
    return [value.st_dev, value.st_ino]


def parent_path(parent):
    parent = Path(parent).absolute()
    if any(linked(p) for p in (parent, *parent.parents)):
        raise ValueError("Transfer staging must not traverse linked directories")
    parent.mkdir(parents=True, exist_ok=True)
    return parent.resolve()


def record_paths(parent, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid transfer-stage identity")
    return parent / (".dmt-stage-" + identifier + ".json"), parent / (
        ".dmt-stage-" + identifier + ".lock"
    )


def write_json(path, value):
    body = json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode()
    descriptor, temporary = tempfile.mkstemp(
        prefix=".dmt-stage-write-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        publish(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def checked_record(parent, record):
    if (
        not isinstance(record, dict)
        or set(record)
        != {"format", "id", "kind", "parent", "directory", "identity", "created"}
        or record["format"] != FORMAT
        or not isinstance(record["kind"], str)
        or record["kind"] not in {"download", "intake"}
        or record["parent"] != str(parent)
        or type(record["created"]) not in (int, float)
        or not 0 <= record["created"] <= 1e12
        or not math.isfinite(record["created"])
    ):
        raise ValueError("Invalid owned transfer-stage record")
    record_paths(parent, record["id"])
    prefix = ".dmt-" + record["kind"] + "-" + record["id"] + "-"
    name = record["directory"]
    if not isinstance(name, str) or not re.fullmatch(
        re.escape(prefix) + r"[a-z0-9_]{8}", name
    ):
        raise ValueError("Stage directory differs from its reserved identity")
    value = record["identity"]
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(x) is not int or x < 0 for x in value)
    ):
        raise ValueError("Invalid stage filesystem identity")
    return parent / name


def inventory(parent, record):
    """Collect exact local metadata while the caller owns the stage lease."""
    from .artifact_storage import safe_name

    folder = checked_record(parent, record)
    if (
        linked(folder)
        or folder.resolve().parent != parent
        or parent.resolve() != parent
    ):
        raise ValueError("Stage moved outside its reserved parent")
    if not folder.exists():
        return {
            "files": [],
            "directories": [],
            "missing": True,
            "bytes": 0,
            "modified": record["created"],
        }
    if not folder.is_dir() or identity(folder) != record["identity"]:
        raise ValueError("Stage directory was replaced")
    files, directories, total, latest = (
        [],
        [],
        0,
        max(record["created"], folder.stat().st_mtime),
    )
    pending = [folder]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if linked(path) or not path.resolve().is_relative_to(folder.resolve()):
                    raise ValueError("Owned stage contains linked or external content")
                relative = safe_name(path.relative_to(folder).as_posix())
                info = path.stat()
                latest = max(latest, info.st_mtime)
                if path.is_dir():
                    directories.append(
                        {"path": relative, "identity": [info.st_dev, info.st_ino]}
                    )
                    pending.append(path)
                elif path.is_file():
                    total += info.st_size
                    files.append(
                        {
                            "path": relative,
                            "identity": [info.st_dev, info.st_ino],
                            "bytes": info.st_size,
                            "mtime_ns": info.st_mtime_ns,
                        }
                    )
                else:
                    raise ValueError("Owned stage contains a special file")
                if len(files) + len(directories) > MAX_FILES:
                    raise ValueError("Owned stage exceeds the cleanup inventory limit")
    return {
        "files": sorted(files, key=lambda x: x["path"]),
        "directories": sorted(directories, key=lambda x: x["path"]),
        "missing": False,
        "bytes": total,
        "modified": latest,
    }


def remove_owned(parent, record, expected):
    """Unlink only the reviewed ordinary files; never recursively traverse deletion."""
    folder = checked_record(parent, record)
    if inventory(parent, record) != expected:
        raise ValueError("Owned stage changed after review")
    if expected["missing"]:
        return
    for item in expected["files"]:
        if (
            linked(folder)
            or folder.resolve().parent != parent
            or identity(folder) != record["identity"]
        ):
            raise ValueError("Stage root changed during cleanup")
        path = folder / item["path"]
        if any(
            linked(p) for p in (path, *path.parents)
        ) or not path.resolve().is_relative_to(folder):
            raise ValueError("Refusing cleanup outside the owned stage")
        info = path.stat()
        if (
            [info.st_dev, info.st_ino] != item["identity"]
            or info.st_size != item["bytes"]
            or info.st_mtime_ns != item["mtime_ns"]
        ):
            raise ValueError("Stage file changed during cleanup")
        path.unlink()
    for item in sorted(
        expected["directories"], key=lambda x: len(Path(x["path"]).parts), reverse=True
    ):
        path = folder / item["path"]
        if (
            linked(path)
            or not path.resolve().is_relative_to(folder)
            or identity(path) != item["identity"]
        ):
            raise ValueError("Stage directory changed during cleanup")
        path.rmdir()
    if (
        linked(folder)
        or folder.resolve().parent != parent
        or identity(folder) != record["identity"]
    ):
        raise ValueError("Stage root changed during cleanup")
    folder.rmdir()


class OwnedStage:
    def __init__(self, parent, kind):
        if not isinstance(kind, str) or kind not in {"download", "intake"}:
            raise ValueError("Unknown transfer-stage kind")
        self.parent, self.identifier = parent_path(parent), uuid.uuid4().hex
        self.record_path, self.lock_path = record_paths(self.parent, self.identifier)
        if (
            linked(self.record_path)
            or linked(self.lock_path)
            or self.record_path.exists()
            or self.lock_path.exists()
        ):
            raise ValueError("Stage identity is already reserved or linked")
        self.lease = ResourceLease(self.lock_path)
        self.lease.acquire()
        self.path, self.record = None, None
        try:
            self.path = Path(
                tempfile.mkdtemp(
                    prefix=".dmt-" + kind + "-" + self.identifier + "-", dir=self.parent
                )
            )
            prefix = ".dmt-" + kind + "-" + self.identifier + "-"
            if (
                linked(self.path)
                or self.path.resolve().parent != self.parent
                or not re.fullmatch(re.escape(prefix) + r"[a-z0-9_]{8}", self.path.name)
            ):
                self.path = None
                raise ValueError(
                    "Transfer stage was allocated outside its reserved directory"
                )
            self.record = {
                "format": FORMAT,
                "id": self.identifier,
                "kind": kind,
                "parent": str(self.parent),
                "directory": self.path.name,
                "identity": identity(self.path),
                "created": time.time(),
            }
            write_json(self.record_path, self.record)
        except BaseException:
            try:
                if self.path is not None and self.record is not None:
                    remove_owned(
                        self.parent, self.record, inventory(self.parent, self.record)
                    )
                    self.record_path.unlink(missing_ok=True)
            finally:
                self.lease.release()
            if not self.record_path.exists():
                self.lock_path.unlink(missing_ok=True)
            raise

    def close(self):
        try:
            remove_owned(self.parent, self.record, inventory(self.parent, self.record))
            self.record_path.unlink(missing_ok=True)
        finally:
            self.lease.release()
        if not self.record_path.exists():
            self.lock_path.unlink(missing_ok=True)
