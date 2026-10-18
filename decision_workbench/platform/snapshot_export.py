"""Bounded, deterministic canonical study downloads without model dependencies."""

import hashlib
import io
import zipfile

from decision_workbench.data import dumps
from decision_workbench.training.contracts import SPLITS, validate_splits
from .store import digest

MAX_BYTES = 64 * 1024**2


def study_archive(service, principal, project, identifier):
    service.require(principal, ("admin", "editor", "reviewer"))
    spec = service.project(principal, project)["payload"]
    store, tenant = service.store, principal["tenant"]
    snapshot = store.get(identifier, tenant, project, "snapshot")["payload"]
    splits = snapshot["splits"]
    questions = validate_splits(splits)
    if (
        digest(splits) != snapshot["sha256"]
        or digest(questions) != snapshot["schema_sha256"]
        or questions != spec["questions"]
        or snapshot["counts"] != {role: len(splits[role]) for role in SPLITS}
    ):
        raise ValueError(
            "Reserved snapshot integrity differs from its schema or counts"
        )
    with store.transaction() as db:
        jobs = db.execute(
            "SELECT id FROM jobs WHERE tenant=? AND project=? AND kind='prepare' AND status='succeeded' AND json_extract(result,'$.snapshot')=?",
            (tenant, project, identifier),
        ).fetchall()
    if len(jobs) != 1:
        raise ValueError("Export requires one successfully completed preparation job")
    from .semantic import verify_snapshot
    from .taxonomy import verify_snapshot as verify_taxonomy

    # Historical audit receipts must remain valid, but an installed encoder is
    # not a prerequisite for downloading already reserved canonical records.
    verify_snapshot(store, tenant, project, snapshot, require_current_profile=False)
    verify_taxonomy(store, tenant, project, snapshot)
    output, files, total = io.BytesIO(), {}, 0
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_STORED, allowZip64=False
    ) as archive:
        for role in SPLITS:
            content = bytearray()
            for row in splits[role]:
                body = (dumps(row) + "\n").encode("utf-8")
                total += len(body)
                if total > MAX_BYTES:
                    raise ValueError(
                        "Canonical study exceeds the 64 MiB download limit"
                    )
                content.extend(body)
            name = role + ".jsonl"
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
            files[name] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "rows": len(splits[role]),
            }
        manifest = {
            "format": "domain-canonical-study-v1",
            "project": project,
            "snapshot": identifier,
            "snapshot_sha256": snapshot["sha256"],
            "schema_sha256": snapshot["schema_sha256"],
            "files": files,
            "roles": "Train may contain reviewed synthetic candidates; heldouts remain reviewed real records",
        }
        info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o600 << 16
        archive.writestr(info, dumps(manifest).encode("utf-8"))
    body = output.getvalue()
    if len(body) > MAX_BYTES:
        raise ValueError(
            "Canonical study and manifest exceed the 64 MiB download limit"
        )
    with store.transaction() as db:
        store.audit(
            db,
            tenant,
            project,
            principal["actor"],
            "snapshot.export.prepared",
            identifier,
        )
    return body
