"""Operator-only consistent backups and fresh-target recovery for a local workspace."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

import hashlib
import json
import math
import os
from functools import wraps
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager, closing
from pathlib import Path, PurePosixPath
from .store import WORKSPACE_SCHEMA_VERSION, Store, digest, encode

FORMAT = "domain-workspace-backup-v1"
MANIFEST = "backup-manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILES = 50000
MAX_BYTES = 500 * 1024**3


def _validated(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (sqlite3.DatabaseError, LookupError, TypeError, AttributeError) as exc:
            raise ValueError(
                "Workspace/backup is malformed; recovery validation failed"
            ) from exc

    return call


class _SnapshotStore(Store):
    """Reuse store reads without initializing or modifying a captured database."""

    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.database = self.folder / "workspace.sqlite3"

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA trusted_schema=OFF")
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.rollback()
            db.close()


def _sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _files(folder):
    if _linked(folder) or not folder.is_dir():
        raise ValueError("Backup paths must be ordinary directories")
    found = {}
    for path in folder.rglob("*"):
        if _linked(path) or not path.resolve().is_relative_to(folder):
            raise ValueError("Backup contains a linked or external path")
        if path.is_file():
            found[path.relative_to(folder).as_posix()] = path
        elif not path.is_dir():
            raise ValueError("Backup contains a special filesystem entry")
    return found


def _safe_name(name):
    if not isinstance(name, str) or len(name) > 2048 or "\\" in name:
        raise ValueError("Invalid backup file name")
    parts = PurePosixPath(name).parts
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *["COM" + str(n) for n in range(1, 10)],
        *["LPT" + str(n) for n in range(1, 10)],
    }
    if (
        not parts
        or len(parts) > 16
        or PurePosixPath(name).is_absolute()
        or PurePosixPath(name).as_posix() != name
        or any(
            p in (".", "..")
            or ":" in p
            or p.endswith((".", " "))
            or p.split(".")[0].upper() in reserved
            for p in parts
        )
        or not (
            name == "workspace.sqlite3"
            or (
                len(parts) >= 3
                and parts[0] == "artifacts"
                and re.fullmatch(r"[a-f0-9]{32}", parts[1])
            )
        )
    ):
        raise ValueError("Invalid backup file name")


@_validated
def _validate_workspace(folder):
    """Validate captured application hashes and every registered artifact, without GPU loading."""
    from decision_workbench.data import validate_questions
    from .service import Service

    store = _SnapshotStore(folder)
    with store.transaction() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Workspace database integrity check failed")
        schema = db.execute("SELECT max(version) FROM schema_version").fetchone()[0]
        if schema not in range(3, WORKSPACE_SCHEMA_VERSION + 1):
            raise ValueError(
                "Backup requires a supported workspace schema; upgrade with a compatible application first"
            )
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1"
        ).fetchone():
            raise ValueError("Unexpected executable database schema")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Workspace foreign-key integrity check failed")
        incidents = (
            [dict(row) for row in db.execute("SELECT * FROM operational_incidents")]
            if schema >= 4
            else []
        )
        if schema >= 4:
            from .operations import KINDS

            for incident in incidents:
                if (
                    incident["kind"] not in KINDS
                    or incident["severity"] != KINDS[incident["kind"]][0]
                    or incident["status"] not in ("open", "resolved")
                    or incident["revision"] < 1
                    or incident["occurrences"] < 1
                ):
                    raise ValueError("Operational incident integrity check failed")
                history = db.execute(
                    "SELECT count(*),min(revision),max(revision) FROM incident_history WHERE incident_id=?",
                    (incident["id"],),
                ).fetchone()
                if tuple(history) != (incident["revision"], 1, incident["revision"]):
                    raise ValueError(
                        "Operational incident history integrity check failed"
                    )
        entities = [dict(row) for row in db.execute("SELECT * FROM entities")]
        from .review_assignments import verify_history

        verify_history(db, entities)
        with closing(
            db.execute("SELECT hash,payload FROM record_contents")
        ) as contents:
            for row in contents:
                if digest(json.loads(row["payload"])) != row["hash"]:
                    raise ValueError("Historical record content integrity check failed")
        heads = [
            dict(row)
            for row in db.execute(
                "SELECT h.root,h.version,e.tenant,e.project,e.kind,json_extract(e.payload,'$.sequence') AS sequence FROM dataset_heads h JOIN entities e ON e.id=h.version"
            )
        ]
        datasets = [e for e in entities if e["kind"] in ("dataset", "gate-dataset")]
        if (
            len(datasets)
            != db.execute("SELECT count(*) FROM dataset_versions").fetchone()[0]
        ):
            raise ValueError("Dataset version inventory mismatch")
        for head in heads:
            if (
                head["sequence"]
                != db.execute(
                    "SELECT max(sequence) FROM dataset_versions WHERE root=?",
                    (head["root"],),
                ).fetchone()[0]
            ):
                raise ValueError("Dataset head does not include every stored revision")
        if (
            len(heads)
            != db.execute(
                "SELECT count(DISTINCT root) FROM dataset_versions"
            ).fetchone()[0]
        ):
            raise ValueError("Dataset head inventory mismatch")
        active = [dict(row) for row in db.execute("SELECT * FROM active_models")]
    lookup = {e["id"]: {**e, "payload": json.loads(e["payload"])} for e in entities}

    def scoped(identifier, scope, kind):
        item = lookup.get(identifier)
        if (
            not item
            or item["kind"] != kind
            or (item["tenant"], item["project"]) != (scope["tenant"], scope["project"])
        ):
            raise ValueError("Workspace reference crosses scope or is missing")
        return item["payload"]

    from .schema_intake import verify_history as verify_schema_history

    verify_schema_history(store, lookup.values())
    from .generation_worker import validate_history as validate_generation_history

    validate_generation_history(store, lookup)
    for entity in lookup.values():
        value = entity["payload"]
        if entity["kind"] == "schema-intake":
            continue
        project = scoped(entity["project"], entity, "project")
        if entity["kind"] == "project":
            validate_questions(value["questions"])
            if digest(value["questions"]) != value["schema_sha256"]:
                raise ValueError("Project schema integrity check failed")
            from .successors import verify_receipt

            verify_receipt(store, entity)
        elif entity["kind"] == "audit-export":
            from .audit_exports import verify_entity

            verify_entity(store, entity)
        elif entity["kind"] == "reservation-plan":
            from .reservations import load_plan

            load_plan(
                store,
                entity["tenant"],
                entity["project"],
                {"id": entity["id"], "sha256": value["sha256"]},
                require_current=False,
            )
        elif entity["kind"] == "snapshot":
            from decision_workbench.training.contracts import validate_splits

            validate_splits(value["splits"])
            from .semantic import verify_snapshot

            verify_snapshot(
                store,
                entity["tenant"],
                entity["project"],
                value,
                require_current_profile=False,
            )
            from .taxonomy import verify_snapshot as verify_taxonomy_snapshot

            verify_taxonomy_snapshot(store, entity["tenant"], entity["project"], value)
            if (
                digest(value["splits"]) != value["sha256"]
                or value["schema_sha256"] != project["schema_sha256"]
            ):
                raise ValueError("Reserved snapshot integrity check failed")
            for dataset, expected in value.get("dataset_hashes", {}).items():
                if scoped(dataset, entity, "dataset")["sha256"] != expected:
                    raise ValueError(
                        "Snapshot dataset reference integrity check failed"
                    )
        elif entity["kind"] == "release-policy":
            if digest(value["criteria"]) != value["sha256"]:
                raise ValueError("Release policy integrity check failed")
        elif entity["kind"] == "gate-dataset":
            from decision_workbench.gate_data import validate_record

            for row in store.dataset_rows(
                entity["id"], entity["tenant"], entity["project"], kind="gate-dataset"
            ):
                validate_record(row, project["questions"])
        elif entity["kind"] == "gate-snapshot":
            from .gate_data import GateData

            GateData(Service(store, runtime=object())).verify_snapshot(
                entity["tenant"], entity["project"], entity["id"]
            )
        elif entity["kind"] == "semantic-audit":
            from .semantic import validate_report, collect

            report = validate_report(value)
            job = store.job(value["job_id"], entity["tenant"], entity["project"])
            if (
                job["kind"]
                != (
                    "gate-semantic-audit"
                    if value.get("applicability")
                    else "semantic-audit"
                )
                or job["config"]["profile"] != value["profile"]
                or job["config"]["source_sha256"] != report["source_sha256"]
                or job["config"]["threshold"] != report["threshold"]
            ):
                raise ValueError("Semantic audit job identity mismatch")
            if (
                collect(store, entity["tenant"], entity["project"], job["config"])[3]
                != report["input_sha256"]
            ):
                raise ValueError("Semantic audit source integrity mismatch")
        elif entity["kind"] == "semantic-review":
            from .semantic import validate_report

            audit = scoped(value["audit_id"], entity, "semantic-audit")
            report = validate_report(audit)
            if (
                value["audit_sha256"] != audit["sha256"]
                or value["pair_id"] not in {pair["id"] for pair in report["pairs"]}
                or value["decision"] not in ("related", "independent")
                or type(value["revision"]) is not int
                or value["revision"] < 1
                or not value["note"].strip()
            ):
                raise ValueError("Semantic review integrity mismatch")
    for incident in incidents:
        target = lookup.get(incident["project"])
        scoped(
            incident["project"],
            incident,
            (
                "schema-intake"
                if target and target["kind"] == "schema-intake"
                else "project"
            ),
        )
        if incident["kind"].startswith("job_"):
            store.job(incident["target"], incident["tenant"], incident["project"])
        elif incident["target"] != (
            "workspace-backups"
            if incident["kind"] == "backup_unhealthy"
            else "worker" if incident["kind"] == "worker_unavailable" else "queue"
        ):
            raise ValueError("Operational incident target integrity check failed")
    for head in heads:
        store.verify_dataset(
            head["version"], head["tenant"], head["project"], kind=head["kind"]
        )
    from .hosted import validate_call_history

    validate_call_history(store)
    from .taxonomy import validate_history as validate_taxonomy_history
    from .taxonomy_provider import validate_history as validate_taxonomy_calls

    validate_taxonomy_history(store)
    validate_taxonomy_calls(store)
    from .cloud import validate_history

    validate_history(store)
    files = {}
    service = Service(store, runtime=object())
    models = [e for e in lookup.values() if e["kind"] == "model"]
    for entity in models:
        value = entity["payload"]
        spec = scoped(entity["project"], entity, "project")
        snapshot = scoped(value["snapshot"], entity, "snapshot")
        if (
            value["snapshot_sha256"] != snapshot["sha256"]
            or value["schema_sha256"] != spec["schema_sha256"]
        ):
            raise ValueError("Model data/schema reference integrity check failed")
        job = store.job(value["job_id"], entity["tenant"], entity["project"])
        if job["config"] != value["configuration"] or value["backend"] not in (
            "cpu-baseline",
            "laya-native",
            "unsloth",
            "vega",
            "liquid-trl",
            "liquid-unsloth",
            "liquid-omni",
            "perplexity-native",
            *D3_BACKENDS,
            "hosted",
        ):
            raise ValueError("Model training configuration integrity check failed")
        config = value["configuration"]
        if config.get("release_policy_id"):
            policy = scoped(config["release_policy_id"], entity, "release-policy")
            if policy["sha256"] != config.get("release_policy_sha256"):
                raise ValueError("Model frozen policy reference integrity check failed")
            if config.get("error_policy") != policy["criteria"].get("error_policy"):
                raise ValueError(
                    "Model error policy differs from frozen owner criteria"
                )
            if config.get("error_policy") is not None:
                from decision_workbench.training.error_policy import evaluate_report

                if value["report"].get("error_evaluation") != evaluate_report(
                    value["report"], spec["questions"], config["error_policy"]
                ):
                    raise ValueError("Model error evaluation integrity check failed")
        if not re.fullmatch(r"[a-f0-9]{32}", value["artifact_id"]):
            raise ValueError("Invalid registered artifact identifier")
        artifact = store.folder / "artifacts" / value["artifact_id"]
        if _linked(artifact.parent):
            raise ValueError("Linked artifact root is not portable")
        inventory = _files(artifact)
        if value["backend"] == "cpu-baseline":
            if set(inventory) != {"model.json", "report.json"}:
                raise ValueError("Unexpected baseline artifact inventory")
            bundle = service.checked_bundle(value)
            if (
                bundle["questions"] != spec["questions"]
                or json.loads(inventory["report.json"].read_text(encoding="utf-8"))
                != value["report"]
            ):
                raise ValueError("Baseline schema/report integrity check failed")
        elif value["backend"] == "hosted":
            from .hosted import checked_hosted

            checked_hosted(store, value, spec["questions"])
        else:
            service.checked_export(value, spec["questions"])
        for name, path in inventory.items():
            relative = "artifacts/" + value["artifact_id"] + "/" + name
            _safe_name(relative)
            files[relative] = path
    for item in active:
        model = scoped(item["model_id"], item, "model")
        if (
            store.job(model["job_id"], item["tenant"], item["project"])["status"]
            != "succeeded"
        ):
            raise ValueError("Active model has no successfully completed training job")
    return {
        "entities": len(entities),
        "projects": sum(e["kind"] == "project" for e in entities),
        "models": len(models),
        "dataset_roots": len(heads),
        "active_releases": len(active),
        **({"incidents": len(incidents)} if schema >= 4 else {}),
    }, files


def _schema(folder):
    with _SnapshotStore(folder).transaction() as db:
        return db.execute("SELECT max(version) FROM schema_version").fetchone()[0]


def _new_target(source, destination):
    if _linked(Path(source)) or _linked(Path(destination)):
        raise ValueError("Source and destination must not be linked directories")
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if destination.exists() or _linked(Path(destination)):
        raise ValueError("Recovery destinations must not already exist")
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Source and destination directories must be separate")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return source, destination


def _publish(staging, destination, marker):
    """Reserve a new target exclusively; publish its completion marker last."""
    for path in _files(staging).values():
        with path.open("rb+") as stream:
            os.fsync(stream.fileno())
    destination.mkdir(mode=0o700, exist_ok=False)
    try:
        for path in staging.iterdir():
            if path.name != marker:
                path.rename(destination / path.name)
        (staging / marker).rename(destination / marker)
        if os.name != "nt":
            descriptor = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except BaseException:
        # This target was created exclusively above; never remove a preexisting target.
        if destination.parent == staging.parent and not _linked(destination):
            shutil.rmtree(destination)
        raise


@_validated
def backup_workspace(home, destination, timeout=300):
    """Capture a live database snapshot; copy only its immutable registered exports."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Backup timeout must be finite and positive")
    source, destination = _new_target(home, destination)
    database = source / "workspace.sqlite3"
    if _linked(database) or not database.is_file():
        raise ValueError("Source workspace database is missing or linked")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(
        prefix=".dmt-backup-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        snapshot = staging / "workspace.sqlite3"
        source_db = sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, timeout=10
        )
        target_db = sqlite3.connect(snapshot)
        try:

            def progress(status, remaining, total):
                if time.monotonic() - started > timeout:
                    raise ValueError(
                        "Backup exceeded its operator deadline; no completed bundle was published"
                    )

            source_db.backup(target_db, pages=128, progress=progress, sleep=0.05)
        finally:
            source_db.close()
            target_db.close()
        # Database reads use the snapshot; artifact paths come from the source workspace.
        (staging / "artifacts").mkdir()
        snapshot_store = _SnapshotStore(staging)
        with snapshot_store.transaction() as db:
            refs = {
                json.loads(row[0])["artifact_id"]
                for row in db.execute("SELECT payload FROM entities WHERE kind='model'")
            }
        copied_files = copied_bytes = 0
        for reference in refs:
            if not isinstance(reference, str) or not re.fullmatch(
                r"[a-f0-9]{32}", reference
            ):
                raise ValueError("Invalid registered artifact identifier")
            artifact = source / "artifacts" / reference
            if _linked(artifact.parent):
                raise ValueError("Linked artifact root is not portable")
            for name, path in _files(artifact).items():
                copied_files += 1
                copied_bytes += path.stat().st_size
                if copied_files >= MAX_FILES or copied_bytes > MAX_BYTES:
                    raise ValueError("Backup exceeds local file/size limits")
                relative = "artifacts/" + reference + "/" + name
                _safe_name(relative)
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output)
                if time.monotonic() - started > timeout:
                    raise ValueError(
                        "Backup exceeded its operator deadline; no completed bundle was published"
                    )
        summary, registered = _validate_workspace(staging)
        inventory = {"workspace.sqlite3": snapshot, **registered}
        if (
            len(inventory) > MAX_FILES
            or sum(p.stat().st_size for p in inventory.values()) > MAX_BYTES
        ):
            raise ValueError("Backup exceeds local file/size limits")
        manifest = {
            "format": FORMAT,
            "schema_version": _schema(staging),
            "created": time.time(),
            "contains_all_tenants": True,
            "summary": summary,
            "files": {
                name: {"sha256": _sha(path), "bytes": path.stat().st_size}
                for name, path in sorted(inventory.items())
            },
            "excluded": [
                "credentials",
                "environment secrets",
                "model cache",
                "private job/runtime logs",
                "unregistered artifacts and unfinished checkpoints",
            ],
        }
        body = encode(manifest).encode()
        if len(body) > MAX_MANIFEST_BYTES:
            raise ValueError("Backup manifest exceeds its local size limit")
        (staging / MANIFEST).write_bytes(body)
        checksum = hashlib.sha256(body).hexdigest()
        if time.monotonic() - started > timeout:
            raise ValueError(
                "Backup exceeded its operator deadline; no completed bundle was published"
            )
        _publish(staging, destination, MANIFEST)
    return {
        "destination": str(destination),
        "manifest_sha256": checksum,
        "summary": summary,
    }


@_validated
def verify_backup(folder, manifest_sha256):
    """Verify against a checksum retained separately by the operator."""
    if _linked(Path(folder)):
        raise ValueError("Backup must not be a linked directory")
    folder = Path(folder).resolve()
    if not isinstance(manifest_sha256, str) or not re.fullmatch(
        r"[a-f0-9]{64}", manifest_sha256
    ):
        raise ValueError("Provide the trusted 64-character backup manifest checksum")
    inventory = _files(folder)
    path = inventory.get(MANIFEST)
    if (
        not path
        or path.stat().st_size > MAX_MANIFEST_BYTES
        or _sha(path) != manifest_sha256
    ):
        raise ValueError("Backup manifest checksum/completion check failed")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("schema_version") not in range(
        3, WORKSPACE_SCHEMA_VERSION + 1
    ):
        raise ValueError("Unsupported workspace backup format")
    files = manifest.get("files")
    if (
        not isinstance(files, dict)
        or "workspace.sqlite3" not in files
        or not 1 <= len(files) <= MAX_FILES
        or set(inventory) != set(files) | {MANIFEST}
    ):
        raise ValueError(
            "Backup file inventory is incomplete or contains unexpected files"
        )
    total = 0
    for name, metadata in files.items():
        _safe_name(name)
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("bytes")) is not int
            or metadata["bytes"] < 0
            or not isinstance(metadata.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"])
        ):
            raise ValueError("Invalid backup file metadata")
        total += metadata["bytes"]
        if (
            total > MAX_BYTES
            or inventory[name].stat().st_size != metadata["bytes"]
            or _sha(inventory[name]) != metadata["sha256"]
        ):
            raise ValueError("Backup file checksum/size check failed")
    if _schema(folder) != manifest["schema_version"]:
        raise ValueError("Backup schema does not match its manifest")
    summary, registered = _validate_workspace(folder)
    if (
        set(files) != {"workspace.sqlite3", *registered}
        or manifest.get("summary") != summary
    ):
        raise ValueError("Backup references do not match the captured database")
    return manifest


@_validated
def restore_workspace(backup, destination, manifest_sha256, *, timeout=None):
    """Restore to a new directory; copied nonterminal jobs require explicit resubmission."""
    if timeout is not None and (
        type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise ValueError("Restore timeout must be finite and positive")
    deadline = time.monotonic() + timeout if timeout is not None else None

    def check_deadline():
        if deadline is not None and time.monotonic() > deadline:
            raise ValueError(
                "Restore exceeded its deadline; no recovered workspace was published"
            )

    source, destination = _new_target(backup, destination)
    manifest = verify_backup(source, manifest_sha256)
    with tempfile.TemporaryDirectory(
        prefix=".dmt-restore-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        for name, metadata in manifest["files"].items():
            check_deadline()
            output = staging / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, output)
            if (
                output.stat().st_size != metadata["bytes"]
                or _sha(output) != metadata["sha256"]
            ):
                raise ValueError(
                    "Backup changed during restore; no recovered workspace was published"
                )
        _validate_workspace(staging)
        check_deadline()
        store = Store(staging)
        recovered = []
        with store.transaction() as db:
            cleared_workers = db.execute(
                "SELECT count(*) FROM worker_heartbeats"
            ).fetchone()[0]
            db.execute("DELETE FROM worker_heartbeats")
            for row in db.execute(
                "SELECT id,status FROM jobs WHERE status IN ('queued','running','cancel_requested')"
            ).fetchall():
                status = (
                    "cancelled"
                    if row["status"] == "cancel_requested"
                    else "interrupted"
                )
                db.execute(
                    "UPDATE jobs SET status=?,token=NULL,lease=NULL,progress=?,error=?,updated=? WHERE id=?",
                    (
                        status,
                        "Recovered from backup; no automatic replay",
                        "Workspace restored; inspect and explicitly submit a new run",
                        time.time(),
                        row["id"],
                    ),
                )
                recovered.append(
                    {
                        "id": row["id"],
                        "previous_status": row["status"],
                        "status": status,
                    }
                )
            for row in db.execute(
                "SELECT tenant,project FROM entities WHERE kind='project'"
            ).fetchall():
                store.audit(
                    db,
                    row["tenant"],
                    row["project"],
                    "operator",
                    "restore.workspace",
                    manifest_sha256,
                )
        receipt = {
            "format": "domain-workspace-restore-v1",
            "manifest_sha256": manifest_sha256,
            "restored": time.time(),
            "interrupted_jobs": recovered,
            "source_schema": manifest["schema_version"],
            "restored_schema": _schema(store.folder),
            "cleared_worker_heartbeats": cleared_workers,
            "summary": manifest["summary"],
        }
        (staging / "restore-receipt.json").write_text(encode(receipt), encoding="utf-8")
        check_deadline()
        _publish(staging, destination, "restore-receipt.json")
    return {"destination": str(destination), **receipt}
