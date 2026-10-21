"""Frozen remote studies and operator-staged admission; no cloud allocation."""

import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
import zipfile
from pathlib import Path
from decision_workbench.data import dumps, write_splits
from decision_workbench.training.artifacts import file_sha, verify_manifest
from decision_workbench.training.contracts import validate_splits
from decision_workbench.training.provenance import source_fingerprint
from .store import Conflict, digest

PROVIDERS = ("aws", "azure", "gcp", "modal", "runpod")
from decision_workbench.training.transfer_limits import (
    MAX_ARTIFACT_BYTES,
    transfer_seconds,
)

MAX_BYTES = MAX_ARTIFACT_BYTES
MAX_FILES = 2048


def validate_location(config):
    if config.get("provider") not in PROVIDERS:
        raise ValueError("Choose aws, azure, gcp, modal or runpod")
    for key in ("region", "gpu"):
        if not isinstance(config.get(key), str) or not 1 <= len(config[key]) <= 120:
            raise ValueError("Record bounded provider region and GPU identities")
    if not re.fullmatch(
        r"[A-Za-z0-9_./:-]+@sha256:[a-f0-9]{64}", config.get("image_digest", "")
    ):
        raise ValueError("Pin the container image to its SHA-256 digest")


def write_archive(folder, archive):
    """Stable bytes permit reconstruction from the immutable database after restore."""
    paths = [("request.json", folder / "request.json")]
    paths.extend(("data/" + p.name, p) for p in sorted((folder / "data").iterdir()))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as out:
        for name, path in paths:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            out.writestr(info, path.read_bytes())


def prepare_study(store, job, snapshot, questions, coverage, gate=None):
    config = job["config"]
    validate_splits(snapshot["splits"])
    folder = store.folder / "jobs" / job["id"] / "remote-study"
    folder.mkdir(parents=True, exist_ok=False)
    try:
        if config["backend"] == "vega":
            from decision_workbench.training.vega_remote import (
                request_for,
                write_inputs,
            )

            if gate is None:
                raise ValueError(
                    "Vega preparation requires replayed applicability evidence"
                )
            write_inputs(folder / "data", gate["positive_splits"], gate["splits"])
            request = request_for(config, job["id"])
        else:
            manifest = write_splits(snapshot["splits"], folder / "data")
            request = {
                "format": "domain-remote-study-v1",
                "study_id": job["id"],
                "tenant": job["tenant"],
                "project": job["project"],
                "snapshot_sha256": snapshot["sha256"],
                "schema_sha256": digest(questions),
                "configuration": config,
                "source_sha256": source_fingerprint(),
                "data_sha256": {k: v["sha256"] for k, v in manifest.items()},
            }
        (folder / "request.json").write_text(dumps(request), encoding="utf-8")
        archive = folder / "study.zip"
        total = sum(p.stat().st_size for p in (folder / "data").iterdir())
        if total > 256 * 1024**2:
            raise ValueError(
                "Remote input bundle exceeds the current 256 MiB transfer limit"
            )
        write_archive(folder, archive)
        store.heartbeat(job, "Sealing remote inputs; no paid resources allocated")
        entity = store.add(
            "remote-study",
            job["tenant"],
            job["project"],
            job["actor"],
            {
                "request": request,
                "request_sha256": file_sha(folder / "request.json"),
                "archive_sha256": file_sha(archive),
                "job_id": job["id"],
                "data_gate_counts": coverage,
            },
            entity_id=job["id"],
            job=job,
        )
        return {
            "remote_study": entity["id"],
            "provider": config["provider"],
            "paid_resources_created": False,
        }
    except BaseException:
        shutil.rmtree(folder)
        raise


def study_entity(store, tenant, project, study):
    entity = store.get(study, tenant, project, "remote-study")
    payload = entity["payload"]
    job = store.job(payload["job_id"], tenant, project)
    if job["kind"] != "remote-study" or job["status"] != "succeeded":
        raise ValueError("Remote study preparation has not succeeded")
    request = payload["request"]
    expected_format = (
        "domain-remote-vega-study-v1"
        if request.get("configuration", {}).get("backend") == "vega"
        else "domain-remote-study-v1"
    )
    if (
        request.get("format") != expected_format
        or request.get("study_id") != study
        or request.get("tenant") != tenant
        or request.get("project") != project
        or request["configuration"] != job["config"]
        or digest(request) != payload["request_sha256"]
    ):
        raise ValueError("Remote study request integrity check failed")
    return entity


def study_archive(store, principal, project, study):
    payload = study_entity(store, principal["tenant"], project, study)["payload"]
    path = store.folder / "jobs" / payload["job_id"] / "remote-study" / "study.zip"
    if not path.parent.exists():
        from decision_workbench.training.resources import ResourceLease

        with ResourceLease(store.folder / "remote-intake.lock").hold(timeout=30):
            if not path.parent.exists():
                snapshot = store.get(
                    payload["request"]["configuration"]["snapshot"],
                    principal["tenant"],
                    project,
                    "snapshot",
                )["payload"]
                if digest(snapshot["splits"]) != payload["request"]["snapshot_sha256"]:
                    raise ValueError(
                        "Recovered remote-study snapshot differs from its request"
                    )
                temporary = store.folder / "jobs" / ("rebuild-" + uuid.uuid4().hex)
                temporary.mkdir(parents=True)
                try:
                    if payload["request"]["configuration"]["backend"] == "vega":
                        from .service import Service
                        from .vega_training import recheck
                        from decision_workbench.training.vega_remote import write_inputs

                        replay_job = store.job(
                            payload["job_id"], principal["tenant"], project
                        )
                        gate = recheck(
                            Service(store, runtime=object()),
                            replay_job,
                            snapshot,
                            snapshot["splits"]["train"][0]["questions"],
                            require_current_source=False,
                        )
                        write_inputs(
                            temporary / "data", gate["positive_splits"], gate["splits"]
                        )
                    else:
                        split_manifest = write_splits(
                            snapshot["splits"], temporary / "data"
                        )
                        if {
                            k: v["sha256"] for k, v in split_manifest.items()
                        } != payload["request"]["data_sha256"]:
                            raise ValueError(
                                "Recovered remote input bytes differ from frozen request"
                            )
                    (temporary / "request.json").write_text(
                        dumps(payload["request"]), encoding="utf-8"
                    )
                    write_archive(temporary, temporary / "study.zip")
                    if file_sha(temporary / "study.zip") != payload["archive_sha256"]:
                        raise ValueError(
                            "Reconstructed remote archive differs from frozen checksum"
                        )
                    path.parent.parent.mkdir(parents=True, exist_ok=True)
                    temporary.rename(path.parent)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
    if (
        not path.is_file()
        or path.is_symlink()
        or file_sha(path) != payload["archive_sha256"]
    ):
        raise ValueError(
            "Remote study archive missing or corrupted; inspect operator recovery"
        )
    return path


def checked_receipt(receipt, request, expected_sha):
    fields = {
        "provider",
        "provider_job_id",
        "region",
        "gpu",
        "image_digest",
        "status",
        "manifest_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ValueError(
            "Receipt requires provider, provider_job_id, region, gpu, image_digest, status and manifest_sha256"
        )
    if (
        not isinstance(receipt["provider_job_id"], str)
        or not 1 <= len(receipt["provider_job_id"]) <= 512
    ):
        raise ValueError("Record the provider job identity")
    config = request["configuration"]
    if any(
        receipt[k] != config[k] for k in ("provider", "region", "gpu", "image_digest")
    ):
        raise ValueError("Provider receipt differs from the approved remote study")
    if receipt["status"] != "succeeded" or receipt["manifest_sha256"] != expected_sha:
        raise ValueError("Receipt does not attest successful matching export")
    return {
        **receipt,
        "verification": "operator-attested; provider control plane not queried",
    }


def validate_export(folder, expected_sha, request):
    config = request["configuration"]
    questions = request["questions"]
    manifest = verify_manifest(folder, expected_sha, config["backend"], questions)
    from decision_workbench.training.native_remote import BACKENDS

    native = config["backend"] in BACKENDS
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Remote export report must be a JSON object")
    allowed_native = set()
    if native:
        from decision_workbench.training.native_remote import (
            checked_artifact,
            export_files,
        )

        checked_artifact(folder, report, config, questions)
        allowed_native = export_files(config["backend"], manifest["checkpoint"])
    for name in manifest["files"]:
        allowed_vega_asset = config["backend"] == "vega" and (
            Path(name).suffix.lower() == ".jinja"
            or Path(name).name in ("LICENSE", "README.md")
        )
        if (
            name not in allowed_native
            and not allowed_vega_asset
            and Path(name).suffix.lower()
            not in (
                ".json",
                ".safetensors",
                ".txt",
                ".model",
                ".tiktoken",
            )
        ):
            raise ValueError("Remote export contains an unsupported/executable file")
    if config["backend"] == "vega":
        from .vega_training import checked_report

        checked_report(
            report, {**config, "remote_request_sha256": request["request_sha256"]}
        )
    if (
        report.get("source_sha256") != request["source_sha256"]
        or report.get("remote_study_sha256") != request["request_sha256"]
        or report.get("data_sha256") != request["data_sha256"]
        or report.get("backend") != config["backend"]
        or report.get("model") != config["model"]
        or report.get("revision") != config["revision"]
    ):
        raise ValueError("Export provenance does not match frozen remote study")
    if (
        report.get("reload_equivalence") is not True
        or report.get("export_qualification", {}).get("passed") is not True
    ):
        raise ValueError("Remote export lacks training-side reload qualification")
    return report, manifest


def stage_import(
    service,
    principal,
    project,
    study,
    source,
    expected_sha,
    receipt,
    key,
    authorize=None,
    origin_job=None,
):
    """Stage a verified export, then queue fenced independent qualification.

    ``authorize`` rechecks operator credentials. Worker callers use ``origin_job``
    for database ownership fencing instead of opening nested transactions from
    an authorization callback.
    """
    service.require(principal, ("admin",))
    service.project(principal, project)
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise ValueError("Retain the exact export manifest SHA-256")
    if not isinstance(key, str) or not 8 <= len(key) <= 128:
        raise ValueError("Use an 8–128 character idempotency key")
    store = service.store
    if origin_job and (
        origin_job.get("tenant") != principal["tenant"]
        or origin_job.get("project") != project
    ):
        raise ValueError("Originating job does not match the remote intake scope")
    payload = study_entity(store, principal["tenant"], project, study)["payload"]
    request = payload["request"]
    receipt = checked_receipt(receipt, request, expected_sha)
    from decision_workbench.training.resources import ResourceLease
    from .artifact_storage import linked

    signature = digest(
        {"study": study, "manifest_sha256": expected_sha, "receipt": receipt}
    )
    last_check = [0.0]

    def check(force=False):
        if not force and time.monotonic() - last_check[0] < 1:
            return
        if authorize:
            authorize()
        if origin_job:
            with store.transaction() as db:
                store._assert_owner(db, origin_job)
        last_check[0] = time.monotonic()

    def validate_origin(db):
        storage_live()
        if store.artifact_store is not None:
            store._artifact_binding(db)
        if origin_job:
            store._assert_owner(db, origin_job)

    check(True)
    with store.artifact_guard() as storage_live, ResourceLease(
        store.folder / "remote-intake.lock"
    ).hold(timeout=30):
        with store.transaction() as db:
            if store.artifact_store is not None:
                store._artifact_binding(db)
            existing = db.execute(
                "SELECT * FROM jobs WHERE tenant=? AND project=? AND request_key=?",
                (principal["tenant"], project, key),
            ).fetchone()
        if existing:
            old = store.decode_job(existing)
            if (
                old["kind"] != "remote-import"
                or old["config"].get("intake_signature") != signature
            ):
                raise Conflict("Idempotency key already belongs to a different request")
            check(True)
            return old
        stage_id = digest(
            {
                "tenant": principal["tenant"],
                "project": project,
                "request_key": key,
                "signature": signature,
            }
        )[:32]
        parent = store.folder / "incoming"
        if linked(parent):
            raise ValueError("Remote intake directory must not be linked")
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / stage_id
        stage = None
        created, submitting = False, False

        def safe_remove(path):
            if path.exists():
                if (
                    linked(path)
                    or path.resolve().parent != parent.resolve()
                    or not (
                        path.name == stage_id
                        or path.name.startswith(".dmt-intake-" + stage_id + "-")
                    )
                ):
                    raise ValueError("Refusing cleanup outside the owned remote intake")
                shutil.rmtree(path)

        questions = service.project(principal, project)["payload"]["questions"]
        checked = {
            **request,
            "questions": questions,
            "request_sha256": payload["request_sha256"],
        }
        try:
            if linked(target):
                raise ValueError("Remote intake must not be linked")
            if not target.exists():
                from .owned_staging import OwnedStage

                stage = OwnedStage(parent, "intake")
                temporary = stage.path
                source = Path(source).absolute()
                if any(linked(path) for path in (source, *source.parents)):
                    raise ValueError(
                        "Remote source must not traverse linked directories"
                    )
                if not source.is_dir():
                    raise ValueError(
                        "Choose an extracted export directory; archives are not accepted"
                    )
                total, count = 0, 0
                deadline = time.monotonic() + transfer_seconds(MAX_BYTES)
                for current, directories, filenames in os.walk(
                    source, followlinks=False
                ):
                    for name in directories + filenames:
                        candidate = Path(current) / name
                        info = candidate.lstat()
                        if linked(candidate):
                            raise ValueError("Remote export contains linked content")
                        if name in filenames and not stat.S_ISREG(info.st_mode):
                            raise ValueError("Remote export contains a nonregular file")
                    for name in filenames:
                        check()
                        count += 1
                        if count > MAX_FILES:
                            raise ValueError("Remote export exceeds file-count limit")
                        original = Path(current) / name
                        destination = temporary / original.relative_to(source)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with original.open("rb") as reader, destination.open(
                            "xb"
                        ) as writer:
                            while chunk := reader.read(1024**2):
                                check()
                                total += len(chunk)
                                if total > MAX_BYTES or time.monotonic() > deadline:
                                    raise ValueError(
                                        "Remote export exceeds byte/time limit"
                                    )
                                writer.write(chunk)
                validate_export(temporary, expected_sha, checked)
                if (
                    temporary.resolve().parent != parent.resolve()
                    or linked(target)
                    or target.resolve().parent != parent.resolve()
                ):
                    raise ValueError(
                        "Remote intake moved outside its reserved directory"
                    )
                temporary.rename(target)
                created = True
            if not created:
                validate_export(target, expected_sha, checked)
            check(True)
            config = {
                **request["configuration"],
                "study": study,
                "incoming_id": stage_id,
                "manifest_sha256": expected_sha,
                "provider_receipt": receipt,
                "intake_signature": signature,
            }
            if store.artifact_store is not None:
                intake_model = {
                    "artifact_id": stage_id,
                    "backend": config["backend"],
                    "model_sha256": expected_sha,
                    "schema_sha256": request["schema_sha256"],
                }
                config["incoming_reference"] = store.artifact_store.publish(
                    store.workspace_id,
                    digest(
                        {
                            "kind": "remote-input",
                            "tenant": principal["tenant"],
                            "project": project,
                            "request_key": key,
                            "signature": signature,
                        }
                    ),
                    target,
                    intake_model,
                    check,
                )
            if config["backend"] == "vega":
                config["remote_request_sha256"] = payload["request_sha256"]
            check(True)
            submitting = True
            return store.submit(
                principal["tenant"],
                project,
                principal["actor"],
                "remote-import",
                config,
                key,
                validate_db=validate_origin,
                authorize=authorize,
            )
        except BaseException:
            if created:
                remove = not submitting
                if submitting:
                    # A failed response after commit is not proof the job was not
                    # queued. Preserve its immutable input when storage is uncertain.
                    try:
                        with store.transaction() as db:
                            row = db.execute(
                                "SELECT * FROM jobs WHERE tenant=? AND project=? AND request_key=?",
                                (principal["tenant"], project, key),
                            ).fetchone()
                        remove = (
                            row is None
                            or store.decode_job(row)["config"].get("incoming_id")
                            != stage_id
                        )
                    except Exception:
                        remove = False
                if remove:
                    safe_remove(target)
            raise
        finally:
            if stage is not None:
                stage.close()
