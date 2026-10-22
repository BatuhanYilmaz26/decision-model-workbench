"""Private ownership records for verified shared-storage cache directories."""

import json
import math
from pathlib import Path
import re
import time

from .owned_staging import identity, linked, write_json
from .store import digest

FORMAT = "domain-registered-cache-v1"
FIELDS = ("artifact_id", "backend", "model_sha256", "schema_sha256")
MAX_RECORD = 16384


def record_path(folder):
    if not re.fullmatch(r"[a-f0-9]{32}", folder.name):
        raise ValueError("Invalid cache directory identity")
    return folder.parent / (".dmt-cache-" + folder.name + ".json")


def checked(value, folder):
    from .artifact_storage import reference_key

    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "format",
            "namespace",
            "model",
            "reference",
            "identity",
            "last_used",
            "sha256",
        }
        or value["format"] != FORMAT
        or not isinstance(value["model"], dict)
        or set(value["model"]) != set(FIELDS)
        or value["model"]["artifact_id"] != folder.name
        or not isinstance(value["identity"], list)
        or len(value["identity"]) != 2
        or any(type(n) is not int or n < 0 for n in value["identity"])
        or type(value["last_used"]) not in (float, int)
        or not 0 <= value["last_used"] <= 1e12
        or not math.isfinite(value["last_used"])
        or value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"})
    ):
        raise ValueError("Invalid registered-cache ownership record")
    reference_key(value["namespace"], value["reference"])
    return value


def read(folder):
    path = record_path(folder)
    if linked(path) or not path.is_file() or path.stat().st_size > MAX_RECORD:
        raise ValueError("Cache ownership record is missing, linked or oversized")
    with path.open("rb") as stream:
        body = stream.read(MAX_RECORD + 1)
    if len(body) > MAX_RECORD:
        raise ValueError("Cache ownership record exceeds its size limit")
    return checked(json.loads(body), folder)


def remember(namespace, reference, model, folder, manifest_body=None):
    folder = Path(folder).absolute()
    if folder.parent.name not in {"artifacts", "incoming"} or not re.fullmatch(
        r"[a-f0-9]{32}", folder.name
    ):
        return
    if any(linked(p) for p in (folder, *folder.parents)):
        raise ValueError("Cache ownership must not traverse linked directories")
    if manifest_body is not None:
        from .artifact_storage import manifest_content
        from .credentials import publish
        import os
        import tempfile

        manifest_content(manifest_body, namespace, reference, model)
        parent = folder.parent / ".dmt-manifests"
        if linked(parent):
            raise ValueError("Cache manifest directory must not be linked")
        parent.mkdir(exist_ok=True)
        target = parent / (reference["sha256"] + ".json")
        if linked(target):
            raise ValueError("Cache manifest must not be linked")
        descriptor, temporary = tempfile.mkstemp(prefix=".dmt-index-", dir=parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(manifest_body)
                stream.flush()
                os.fsync(stream.fileno())
            publish(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    value = {
        "format": FORMAT,
        "namespace": namespace,
        "model": {key: model[key] for key in FIELDS},
        "reference": reference,
        "identity": identity(folder),
        "last_used": time.time(),
    }
    value["sha256"] = digest(value)
    checked(value, folder)
    path = record_path(folder)
    if path.exists() or linked(path):
        old = read(folder)
        if any(old[key] != value[key] for key in ("namespace", "model", "reference")):
            raise ValueError("Cache ownership binding differs from the verified export")
    write_json(path, value)
