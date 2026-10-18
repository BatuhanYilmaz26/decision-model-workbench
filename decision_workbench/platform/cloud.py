"""Owner-approved, immutable remote-run histories; local job completion is not cloud completion."""

import copy
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from decision_workbench.training.artifacts import file_sha
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.resources import ResourceLease
from .cloud_aws import CloudUnavailable, TERMINAL, public_profile
from .cloud_providers import (
    validate_profile,
    gateway_from_operator,
    valid_job_id,
    artifact_location,
)
from .remote import study_entity, study_archive, stage_import
from .store import Conflict, digest, encode

ACTIONS = ("submit", "observe", "stop", "abandon", "retrieve")
FINISHED = TERMINAL | {"abandoned"}


def profiles():
    filename = os.environ.get("DMT_CLOUD_PROFILES_FILE")
    if not filename:
        return []
    try:
        with Path(filename).open("rb") as stream:
            body = stream.read(262145)
        value = json.loads(body)
        if (
            len(body) > 262144
            or not isinstance(value, dict)
            or set(value) != {"format", "profiles"}
            or value["format"] != "domain-cloud-profiles-v1"
            or not isinstance(value["profiles"], list)
            or len(value["profiles"]) > 100
        ):
            raise ValueError()
        ids = set()
        for p in value["profiles"]:
            validate_profile(p)
            if p["id"] in ids:
                raise ValueError()
            ids.add(p["id"])
        return value["profiles"]
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise CloudUnavailable(
            "Cloud profile configuration is unavailable or invalid"
        ) from None


class Cloud:
    def __init__(self, store, gateway_factory=None):
        self.store = store
        self.gateway_factory = gateway_factory or gateway_from_operator

    @staticmethod
    def owner(principal):
        if principal["role"] != "admin":
            raise PermissionError("An owner must approve cloud data and compute")

    def profile(self, principal, identifier, expected=None):
        self.owner(principal)
        for p in profiles():
            if p["id"] == identifier and principal["tenant"] in p["tenants"]:
                if expected is not None and public_profile(p) != expected:
                    raise CloudUnavailable(
                        "Cloud profile changed; inspect the frozen run and use operator recovery"
                    )
                return p
        raise CloudUnavailable("Cloud profile is unavailable for this workspace")

    def available(self, principal):
        self.owner(principal)
        return [
            public_profile(p) for p in profiles() if principal["tenant"] in p["tenants"]
        ]

    def get(self, principal, project, identifier):
        self.owner(principal)
        entity = self.store.get(identifier, principal["tenant"], project, "cloud-run")
        with self.store.transaction() as db:
            event = self.latest(db, entity)
        return {**entity, "state": event["payload"]}

    @staticmethod
    def latest(db, run):
        row = db.execute(
            "SELECT * FROM entities WHERE tenant=? AND project=? AND kind='cloud-event' "
            "AND json_extract(payload,'$.run_id')=? ORDER BY json_extract(payload,'$.revision') DESC LIMIT 1",
            (run["tenant"], run["project"], run["id"]),
        ).fetchone()
        if not row:
            raise ValueError("Cloud run has no durable state")
        return {**dict(row), "payload": json.loads(row["payload"])}

    def event(
        self, principal, project, identifier, revision, action, changes, job=None
    ):
        run = self.store.get(identifier, principal["tenant"], project, "cloud-run")
        with self.store.transaction() as db:
            if job:
                self.store._assert_owner(db, job)
            previous = self.latest(db, run)
            old = previous["payload"]
            if old["revision"] != revision:
                raise Conflict("Cloud run changed; refresh its state")
            value = {
                **old,
                **copy.deepcopy(changes),
                "revision": revision + 1,
                "action": action,
                "previous_sha256": digest(old),
                "job_id": job["id"] if job else None,
            }
            identifier_event = uuid.uuid4().hex
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    identifier_event,
                    "cloud-event",
                    run["tenant"],
                    project,
                    encode(value),
                    time.time(),
                ),
            )
            self.store.audit(
                db,
                run["tenant"],
                project,
                principal["actor"],
                "cloud." + action,
                identifier,
            )
        return value

    def create(
        self,
        principal,
        project,
        study,
        profile_id,
        data_approved,
        compute_approved,
        key,
    ):
        self.owner(principal)
        if data_approved is not True or compute_approved is not True:
            raise ValueError(
                "Approve both cloud data transfer and paid compute explicitly"
            )
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError("Use an 8–128 character idempotency key")
        study_value = study_entity(self.store, principal["tenant"], project, study)[
            "payload"
        ]
        request = study_value["request"]
        config = request["configuration"]
        if config.get("backend") == "vega":
            raise ValueError(
                "Managed cloud launch requires a qualified Vega eight-file runner/image; use operator training and admission"
            )
        p = self.profile(principal, profile_id)
        from .cloud_training_profile import training_backends

        if config["backend"] not in training_backends(p):
            raise ValueError(
                "The approved image profile does not declare this training backend"
            )
        from decision_workbench.training.d3_profiles import (
            BACKENDS as D3_BACKENDS,
            get_profile,
        )

        if config["backend"] in D3_BACKENDS:
            native = get_profile(config["backend"])
            minimum = native.weight_bytes + native.readout_bytes
            if p["max_artifact_bytes"] <= minimum:
                raise ValueError(
                    "Cloud artifact limit cannot hold this D3 variant's frozen weights and readout"
                )
        if config["backend"] == "perplexity-native":
            from decision_workbench.training.perplexity_profile import WEIGHT_BYTES

            if p["max_artifact_bytes"] < WEIGHT_BYTES + 128 * 1024**2:
                raise ValueError(
                    "Cloud artifact limit cannot hold the complete native Perplexity export"
                )
            from .cloud_training_profile import require_perplexity_resources

            require_perplexity_resources(p)
        regional = [
            x
            for x in profiles()
            if (x["provider"], x["account_id"], x["region"])
            == (p["provider"], p["account_id"], p["region"])
        ]
        max_inflight = min(x["max_inflight"] for x in regional)
        max_runs_day = min(x["max_runs_day"] for x in regional)
        if any(
            config[k] != p[k] for k in ("provider", "region", "gpu", "image_digest")
        ):
            raise ValueError("Cloud profile differs from the frozen study resources")
        if (
            request["source_sha256"] != source_fingerprint()
            or p["source_sha256"] != request["source_sha256"]
        ):
            raise ValueError(
                "Qualified cloud image/package source differs from the frozen study"
            )
        run_id = hashlib.sha256(("cloud-run:" + study).encode()).hexdigest()[:32]
        value = {
            "id": run_id,
            "study": study,
            "profile": public_profile(p),
            "archive_sha256": study_value["archive_sha256"],
            "request_sha256": study_value["request_sha256"],
            "data_approved": True,
            "compute_approved": True,
            "request_key": key,
        }
        initial = {
            "run_id": run_id,
            "revision": 1,
            "action": "approve",
            "previous_sha256": None,
            "phase": "ready",
            "create_attempted": False,
            "upload_attempted": False,
            "artifact_uri": None,
            "provider_job_id": None,
            "job_id": None,
        }
        with self.store.transaction() as db:
            old = db.execute("SELECT * FROM entities WHERE id=?", (run_id,)).fetchone()
            if old:
                if (
                    old["kind"] != "cloud-run"
                    or old["tenant"] != principal["tenant"]
                    or old["project"] != project
                    or json.loads(old["payload"]) != value
                ):
                    raise Conflict(
                        "This study already has a different cloud approval; inspect the original run"
                    )
            else:
                rows = db.execute(
                    "SELECT r.id,(SELECT json_extract(e.payload,'$.phase') FROM entities e "
                    "WHERE e.kind='cloud-event' AND e.tenant=r.tenant AND e.project=r.project "
                    "AND json_extract(e.payload,'$.run_id')=r.id ORDER BY json_extract(e.payload,'$.revision') DESC LIMIT 1) AS phase,r.created "
                    "FROM entities r WHERE r.kind='cloud-run' AND json_extract(r.payload,'$.profile.account_id')=? "
                    "AND json_extract(r.payload,'$.profile.region')=? AND json_extract(r.payload,'$.profile.provider')=?",
                    (p["account_id"], p["region"], p["provider"]),
                ).fetchall()
                if sum(r["phase"] not in FINISHED for r in rows) >= max_inflight:
                    raise Conflict(
                        "Cloud account/region has unresolved run capacity; observe or recover the original run"
                    )
                if (
                    sum(r["created"] >= time.time() - 86400 for r in rows)
                    >= max_runs_day
                ):
                    raise Conflict(
                        "Cloud account/region rolling run quota is exhausted"
                    )
                now = time.time()
                db.execute(
                    "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    (
                        run_id,
                        "cloud-run",
                        principal["tenant"],
                        project,
                        encode(value),
                        now,
                    ),
                )
                db.execute(
                    "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        "cloud-event",
                        principal["tenant"],
                        project,
                        encode(initial),
                        now,
                    ),
                )
                self.store.audit(
                    db,
                    principal["tenant"],
                    project,
                    principal["actor"],
                    "approve.cloud",
                    run_id,
                )
        return self.get(principal, project, run_id)

    def queue(self, principal, project, identifier, action, revision, key):
        self.owner(principal)
        run = self.get(principal, project, identifier)
        if (
            action not in ACTIONS
            or type(revision) is not int
            or revision != run["state"]["revision"]
        ):
            raise Conflict("Choose a supported cloud action with the current revision")
        self.profile(
            principal, run["payload"]["profile"]["id"], run["payload"]["profile"]
        )
        return self.store.submit(
            principal["tenant"],
            project,
            principal["actor"],
            "cloud-control",
            {"run_id": identifier, "action": action, "revision": revision},
            key,
        )

    def control(self, service, job, cancelled):
        principal = {"tenant": job["tenant"], "actor": job["actor"], "role": "admin"}
        project, identifier, action = (
            job["project"],
            job["config"]["run_id"],
            job["config"]["action"],
        )
        with ResourceLease(
            self.store.folder / "cloud-locks" / (identifier + ".lock")
        ).hold(timeout=0, cancelled=cancelled):
            run_entity = self.get(principal, project, identifier)
            state, run = run_entity["state"], run_entity["payload"]
            if state["revision"] != job["config"]["revision"]:
                raise ValueError(
                    "Cloud run changed before the queued action executed; stale action refused"
                )
            p = self.profile(principal, run["profile"]["id"], run["profile"])
            run = {
                **run,
                "input_receipt": state.get("input_receipt"),
                "provider_job_id": state.get("provider_job_id"),
                "termination_attempted": state.get("termination_attempted", False),
                "termination_acknowledged": state.get(
                    "termination_acknowledged", False
                ),
                "training_result": state.get("training_result"),
            }
            if action == "abandon":
                if state["create_attempted"] or state["phase"] in FINISHED:
                    raise ValueError(
                        "Submitted cloud compute requires terminal provider observation"
                    )
                return self.event(
                    principal,
                    project,
                    identifier,
                    state["revision"],
                    action,
                    {"phase": "abandoned"},
                    job,
                )
            if action == "submit" and (
                state["create_attempted"]
                or state["upload_attempted"]
                or state["phase"] != "ready"
            ):
                raise ValueError(
                    "Cloud submission was already attempted; observe the same job, never replay it"
                )
            if (
                action in ("observe", "stop", "retrieve")
                and not state["create_attempted"]
            ):
                raise ValueError("This cloud run has not attempted compute submission")
            if action == "stop" and state["phase"] in FINISHED:
                raise ValueError("Cloud run is already terminal")
            if action == "retrieve" and state["phase"] != "Completed":
                raise ValueError("Retrieve only a matching completed cloud job")
            gateway = self.gateway_factory(p)
            try:
                gateway.identity()
                self.store.heartbeat(job, "Executing approved cloud " + action)
                try:
                    if action == "submit":
                        archive = study_archive(
                            self.store, principal, project, run["study"]
                        )
                        state = self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            "upload-attempt",
                            {"phase": "uploading", "upload_attempted": True},
                            job,
                        )
                        uploaded = gateway.upload(run, archive)
                        state = self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            "create-attempt",
                            {
                                "phase": "submitting",
                                "create_attempted": True,
                                "input_receipt": uploaded,
                            },
                            job,
                        )
                        run["input_receipt"] = uploaded
                        result = gateway.create(run)
                        return self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            action,
                            {"phase": "submitted", **result},
                            job,
                        )
                    if action == "observe":
                        result = gateway.describe(run)
                        if (
                            state.get("provider_job_id") is not None
                            and result["provider_job_id"] != state["provider_job_id"]
                        ):
                            raise CloudUnavailable(
                                "Cloud provider job identity changed"
                            )
                        if (
                            state["phase"] in TERMINAL
                            and result["status"] != state["phase"]
                        ):
                            raise CloudUnavailable("Terminal cloud observation changed")
                        return self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            action,
                            {"phase": result["status"], **result},
                            job,
                        )
                    if action == "stop":
                        # Describe/bind the job before issuing its stop request.
                        result = gateway.describe(run)
                        if (
                            state.get("provider_job_id") is not None
                            and result["provider_job_id"] != state["provider_job_id"]
                        ):
                            raise CloudUnavailable(
                                "Cloud provider job identity changed"
                            )
                        state = self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            "observe-before-stop",
                            {"phase": result["status"], **result},
                            job,
                        )
                        if result["status"] in TERMINAL:
                            return state
                        if p["provider"] in ("runpod", "modal") and state.get(
                            "termination_attempted"
                        ):
                            raise CloudUnavailable(
                                "Cloud termination already attempted; observe its original compute"
                            )
                        state = self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            "stop-attempt",
                            {
                                "phase": "stop_requested",
                                **(
                                    {"termination_attempted": True}
                                    if p["provider"] in ("runpod", "modal")
                                    else {}
                                ),
                            },
                            job,
                        )
                        run["provider_job_id"] = result["provider_job_id"]
                        stopped = gateway.stop(run)
                        return self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            action,
                            {"phase": "stop_requested", **(stopped or {})},
                            job,
                        )
                    if action == "retrieve":
                        with self.store.transaction() as db:
                            existing = db.execute(
                                "SELECT * FROM jobs WHERE tenant=? AND project=? AND request_key=?",
                                (
                                    principal["tenant"],
                                    project,
                                    "cloud-import-" + identifier,
                                ),
                            ).fetchone()
                        if existing:
                            recovered = self.store.decode_job(existing)
                            config = recovered["config"]
                            if (
                                recovered["kind"] != "remote-import"
                                or config.get("study") != run["study"]
                                or config.get("provider_receipt", {}).get(
                                    "provider_job_id"
                                )
                                != state["provider_job_id"]
                            ):
                                raise ValueError(
                                    "Recovered cloud admission identity differs"
                                )
                            return self.event(
                                principal,
                                project,
                                identifier,
                                state["revision"],
                                "recover-admission",
                                {
                                    "phase": "Completed",
                                    "admission_job": recovered["id"],
                                    "manifest_sha256": config["manifest_sha256"],
                                },
                                job,
                            )
                        from .cloud_archive import extract_model
                        import shutil

                        target = self.store.folder / "jobs" / job["id"] / "cloud-return"
                        target.mkdir(parents=True, exist_ok=False)
                        if (
                            shutil.disk_usage(target).free
                            < 3 * p["max_artifact_bytes"] + 1024**3
                        ):
                            raise ValueError(
                                "Insufficient free storage for download, extraction and admission copy"
                            )
                        result = gateway.describe(run)
                        if (
                            state.get("provider_job_id") is not None
                            and result["provider_job_id"] != state["provider_job_id"]
                        ):
                            raise CloudUnavailable(
                                "Cloud provider job identity changed"
                            )
                        if (
                            result["status"] != "Completed"
                            or result["artifact_uri"] != state["artifact_uri"]
                        ):
                            raise CloudUnavailable(
                                "Completed cloud artifact identity changed"
                            )
                        transfer = gateway.download(
                            run, result["artifact_uri"], target / "model.tar.gz"
                        )
                        output = target / "export"
                        selected_study = study_entity(
                            self.store, principal["tenant"], project, run["study"]
                        )["payload"]["request"]
                        extract_model(
                            target / "model.tar.gz",
                            output,
                            p["max_artifact_bytes"],
                            backend=selected_study["configuration"]["backend"],
                        )
                        checksum = file_sha(output / "manifest.json")
                        receipt = {
                            "provider": p["provider"],
                            "provider_job_id": result["provider_job_id"],
                            "region": p["region"],
                            "gpu": p["gpu"],
                            "image_digest": p["image_digest"],
                            "status": "succeeded",
                            "manifest_sha256": checksum,
                        }
                        self.store.heartbeat(job)
                        admitted = stage_import(
                            service,
                            principal,
                            project,
                            run["study"],
                            output,
                            checksum,
                            receipt,
                            "cloud-import-" + identifier,
                            authorize=lambda: self.store.heartbeat(job),
                        )
                        return self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            action,
                            {
                                "phase": "Completed",
                                "transfer": transfer,
                                "admission_job": admitted["id"],
                                "manifest_sha256": checksum,
                            },
                            job,
                        )
                    raise ValueError("Unknown cloud action")
                except BaseException:
                    # A missing event after a crash also retains capacity. Do not infer provider cancellation.
                    try:
                        self.event(
                            principal,
                            project,
                            identifier,
                            state["revision"],
                            "uncertain-" + action,
                            {
                                "phase": (
                                    state["phase"]
                                    if state["phase"] in FINISHED
                                    else "unknown"
                                )
                            },
                            job,
                        )
                    except (Conflict, ValueError):
                        pass
                    raise

            finally:
                gateway.close()


def validate_history(store):
    """Recovery preserves unresolved external jobs, including attempted creations without outcomes."""
    with store.transaction() as db:
        rows = db.execute(
            "SELECT * FROM entities WHERE kind IN ('cloud-run','cloud-event') ORDER BY created,id"
        ).fetchall()
    runs, histories = {}, {}
    for row in rows:
        entity = {**dict(row), "payload": json.loads(row["payload"])}
        if row["kind"] == "cloud-run":
            runs[row["id"]] = entity
        else:
            histories.setdefault(entity["payload"]["run_id"], []).append(entity)
    for identifier, entity in runs.items():
        value = entity["payload"]
        study = study_entity(
            store, entity["tenant"], entity["project"], value["study"]
        )["payload"]
        p = value["profile"]
        validate_profile(
            {
                **p,
                "tenants": [entity["tenant"]],
                "credential_prefix": "DMT_CLOUD_RECOVERY",
            }
        )
        if (
            value["id"] != identifier
            or value["data_approved"] is not True
            or value["compute_approved"] is not True
            or value["request_sha256"] != study["request_sha256"]
            or value["archive_sha256"] != study["archive_sha256"]
            or any(
                p[k] != study["request"]["configuration"][k]
                for k in ("provider", "region", "gpu", "image_digest")
            )
            or p["source_sha256"] != study["request"]["source_sha256"]
        ):
            raise ValueError("Cloud approval/frozen study integrity check failed")
        events = sorted(
            histories.pop(identifier, []), key=lambda e: e["payload"]["revision"]
        )
        previous, create, upload = None, False, False
        if not events:
            raise ValueError("Cloud history is missing")
        for revision, event in enumerate(events, 1):
            state = event["payload"]
            if (
                (event["tenant"], event["project"])
                != (entity["tenant"], entity["project"])
                or state["revision"] != revision
                or state["previous_sha256"] != (digest(previous) if previous else None)
                or type(state["create_attempted"]) is not bool
                or type(state["upload_attempted"]) is not bool
                or create
                and not state["create_attempted"]
                or upload
                and not state["upload_attempted"]
                or state["phase"]
                not in FINISHED
                | {
                    "ready",
                    "uploading",
                    "submitting",
                    "submitted",
                    "unknown",
                    "InProgress",
                    "Stopping",
                    "stop_requested",
                }
            ):
                raise ValueError("Cloud history chain/state integrity check failed")
            if (
                revision == 1
                and (
                    state["action"] != "approve"
                    or state["phase"] != "ready"
                    or state["create_attempted"]
                    or state["upload_attempted"]
                )
                or state["create_attempted"]
                and not state["upload_attempted"]
                or state["phase"] == "abandoned"
                and state["create_attempted"]
                or state["phase"] in TERMINAL
                and (
                    not state["create_attempted"]
                    or state.get("status") != state["phase"]
                    or not valid_job_id(value, state.get("provider_job_id"))
                )
                or previous
                and previous["phase"] in FINISHED
                and state["phase"] != previous["phase"]
            ):
                raise ValueError("Cloud history lifecycle/terminal identity differs")
            if (
                previous
                and previous.get("provider_job_id") is not None
                and state.get("provider_job_id") != previous["provider_job_id"]
            ):
                raise ValueError(
                    "Cloud history cleared or changed its provider job identity"
                )
            if (
                previous
                and previous.get("input_receipt") is not None
                and state.get("input_receipt") != previous["input_receipt"]
            ):
                raise ValueError("Cloud history changed its uploaded input receipt")
            if p["provider"] == "azure" and state["create_attempted"]:
                receipt = state.get("input_receipt")
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("sha256") != value["archive_sha256"]
                    or not isinstance(receipt.get("etag"), str)
                    or not receipt["etag"]
                ):
                    raise ValueError("Azure cloud history input receipt differs")
            if p["provider"] == "runpod":
                from .cloud_runpod import validate_result, plan

                attempted = state.get("termination_attempted", False)
                acknowledged = state.get("termination_acknowledged", False)
                if (
                    type(attempted) is not bool
                    or type(acknowledged) is not bool
                    or acknowledged
                    and not attempted
                    or attempted
                    and (
                        not state["create_attempted"]
                        or not valid_job_id(value, state.get("provider_job_id"))
                    )
                ):
                    raise ValueError("RunPod termination receipt differs")
                if previous and any(
                    previous.get(k, False) and not state.get(k, False)
                    for k in ("termination_attempted", "termination_acknowledged")
                ):
                    raise ValueError("RunPod termination history cleared its receipt")
                if state["create_attempted"]:
                    receipt = state.get("input_receipt")
                    if (
                        not isinstance(receipt, dict)
                        or receipt.get("sha256") != value["archive_sha256"]
                        or not isinstance(receipt.get("etag"), str)
                        or not receipt["etag"]
                    ):
                        raise ValueError("RunPod input receipt differs")
                    plan(value)
                marker = state.get("training_result")
                if marker is not None:
                    validate_result(value, marker)
                if (
                    previous
                    and previous.get("training_result") is not None
                    and marker != previous["training_result"]
                ):
                    raise ValueError("RunPod training result history changed")
                if state["phase"] in TERMINAL and (
                    state.get("provider_status")
                    not in ("TERMINATED", "ABSENT_AFTER_ACKNOWLEDGED_TERMINATION")
                    or state["provider_status"]
                    == "ABSENT_AFTER_ACKNOWLEDGED_TERMINATION"
                    and not acknowledged
                    or state["phase"] == "Completed"
                    and (marker is None or marker["status"] != "succeeded")
                ):
                    raise ValueError(
                        "RunPod terminal history lacks bound termination/result evidence"
                    )
            if p["provider"] == "modal":
                from .cloud_modal import validate_result, plan

                attempted = state.get("termination_attempted", False)
                acknowledged = state.get("termination_acknowledged", False)
                if (
                    type(attempted) is not bool
                    or type(acknowledged) is not bool
                    or acknowledged
                    and not attempted
                    or attempted
                    and (
                        not state["create_attempted"]
                        or not valid_job_id(value, state.get("provider_job_id"))
                    )
                ):
                    raise ValueError("Modal termination receipt differs")
                if previous and any(
                    previous.get(k, False) and not state.get(k, False)
                    for k in ("termination_attempted", "termination_acknowledged")
                ):
                    raise ValueError("Modal termination history cleared its receipt")
                if state["create_attempted"]:
                    receipt = state.get("input_receipt")
                    if (
                        not isinstance(receipt, dict)
                        or set(receipt) != {"sha256", "bytes", "volume_id"}
                        or receipt["sha256"] != value["archive_sha256"]
                        or receipt["volume_id"] != p["volume_id"]
                        or type(receipt["bytes"]) is not int
                        or not 1 <= receipt["bytes"] <= 256 * 1024**2
                    ):
                        raise ValueError("Modal input receipt differs")
                    if state.get("plan_sha256") is not None and state[
                        "plan_sha256"
                    ] != digest(plan(value)):
                        raise ValueError("Modal plan receipt differs")
                marker = state.get("training_result")
                if marker is not None:
                    validate_result(value, marker)
                if (
                    previous
                    and previous.get("training_result") is not None
                    and marker != previous["training_result"]
                ):
                    raise ValueError("Modal training result history changed")
                code = state.get("exit_code")
                if state["phase"] in TERMINAL and (
                    state.get("provider_status") != "EXITED"
                    or type(code) is not int
                    or not 0 <= code <= 255
                    or state.get("plan_sha256") != digest(plan(value))
                    or state["phase"] == "Completed"
                    and (code != 0 or marker is None or marker["status"] != "succeeded")
                    or state["phase"] == "Stopped"
                    and (code not in (137, 143) or not attempted)
                    or state["phase"] == "Failed"
                    and (
                        code == 0
                        and (marker is None or marker["status"] != "failed")
                        or code in (137, 143)
                        and attempted
                    )
                ):
                    raise ValueError(
                        "Modal terminal history lacks bound exit/result evidence"
                    )
            if p["provider"] == "gcp" and state["create_attempted"]:
                from .cloud_gcp import plan

                receipt = state.get("input_receipt")
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("sha256") != value["archive_sha256"]
                ):
                    raise ValueError("Cloud history input checksum differs")
                plan({**value, "input_receipt": receipt})
            if state.get("provider_job_id") is not None and (
                not valid_job_id(value, state["provider_job_id"])
                or previous
                and previous.get("provider_job_id") is not None
                and state["provider_job_id"] != previous["provider_job_id"]
            ):
                raise ValueError("Cloud history provider job identity changed")
            if state.get("job_id"):
                job = store.job(state["job_id"], entity["tenant"], entity["project"])
                if (
                    job["kind"] != "cloud-control"
                    or job["config"]["run_id"] != identifier
                ):
                    raise ValueError("Cloud operation job identity differs")
            if state["phase"] == "Completed":
                artifact_location(value, state["artifact_uri"])
            previous, create, upload = (
                state,
                state["create_attempted"],
                state["upload_attempted"],
            )
    if histories:
        raise ValueError("Cloud history references an absent run")
