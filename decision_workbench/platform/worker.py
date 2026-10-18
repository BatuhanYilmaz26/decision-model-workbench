"""Independent durable worker; long GPU work runs in a child process."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from decision_workbench.data import prepare, write_splits, jsonl, validate, state_key
from decision_workbench.temporal import audit_splits
from decision_workbench.training.contracts import validate_splits
from .store import Store, Conflict, digest
from .service import Service


class Worker:
    def __init__(self, store, hosted=None):
        self.store = store
        self.service = Service(store, hosted=hosted)
        self.worker_id = uuid.uuid4().hex
        self.current_job = None
        self.presence_active = False

    @contextmanager
    def presence(self):
        if self.presence_active:
            raise RuntimeError("Worker presence already active")
        stop = threading.Event()
        self.presence_active = True
        self.store.worker_presence(self.worker_id, "idle")

        def pulse():
            while not stop.wait(2):
                try:
                    job_id = self.current_job
                    self.store.worker_presence(
                        self.worker_id, "busy" if job_id else "idle", job_id
                    )
                except Exception:
                    # Presence expires if storage cannot be updated; never pretend healthy.
                    return

        thread = threading.Thread(
            target=pulse, daemon=True, name="domain-worker-presence"
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=12)
            self.presence_active = False
            if thread.is_alive():
                raise RuntimeError("Worker presence thread did not stop")
            self.store.worker_presence(self.worker_id, "stopped")

    def execute(self, job):
        stop = threading.Event()
        cancelled = threading.Event()

        def pulse():
            while not stop.wait(2):
                try:
                    self.store.heartbeat(job)
                except Conflict:
                    cancelled.set()
                    return

        heartbeat = threading.Thread(target=pulse, daemon=True)
        heartbeat.start()
        try:
            result = self.dispatch(job, cancelled)
            self.store.finish(job, result=result)
        except Conflict:
            self.store.acknowledge_cancel(job)
        except Exception as exc:
            log_folder = self.store.folder / "jobs" / job["id"]
            log_folder.mkdir(parents=True, exist_ok=True)
            (log_folder / "failure.log").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            # Upstream subprocess logs remain private; never expose credentials or raw samples.
            message = (
                str(exc)[:1000]
                if isinstance(exc, (ValueError, ImportError))
                else type(exc).__name__
                + ": inspect worker environment and private job log"
            )
            try:
                self.store.finish(job, error=message)
            except Conflict:
                self.store.acknowledge_cancel(job)
        finally:
            stop.set()
            heartbeat.join(timeout=3)

    def dispatch(self, job, cancelled):
        tenant, project, actor = job["tenant"], job["project"], job["actor"]
        config = job["config"]
        if job["kind"] == "schema-suggest":
            from .schema_intake import run

            return run(self, job, cancelled)
        if job["kind"] == "generate":
            from .generation_worker import run

            return run(self, job, cancelled)
        spec = self.store.get(project, tenant, project, "project")["payload"]
        if job["kind"] == "gate-prepare":
            return self.service.gates.prepare(job, cancelled)
        if job["kind"] == "cloud-control":
            return self.service.cloud.control(self.service, job, cancelled)
        if job["kind"] in ("semantic-audit", "gate-semantic-audit"):
            from .semantic import run_audit

            return run_audit(self, job, cancelled)
        if job["kind"] == "taxonomy-discover":
            from .taxonomy_provider import run_discovery

            return run_discovery(self, job, cancelled)
        if job["kind"] == "prepare":
            from .semantic import profile_for, public_profile

            semantic_profile = profile_for(tenant, project)
            if (
                semantic_profile
                and semantic_profile["required"]
                and not config.get("semantic_contract")
            ):
                raise ValueError(
                    "This project requires reviewed semantic inputs before preparation"
                )
            self.store.heartbeat(
                job, "Validating reviewed versions and reserving holdouts"
            )
            rows, exclusions = [], []
            for dataset in config["datasets"]:
                payload = self.store.get(
                    dataset, tenant, project, "dataset", include_rows=False
                )["payload"]
                if payload["sha256"] != config["hashes"][dataset]:
                    raise ValueError("Pinned dataset integrity mismatch")
                for row in self.store.verify_dataset(dataset, tenant, project):
                    validate(row, allow_missing_labels=True)
                    if row["metadata"].get("review_status") == "rejected":
                        exclusions.append(
                            {
                                "dataset": dataset,
                                "record_id": row["id"],
                                "reviewer": row["metadata"].get("reviewer"),
                                "note": row["metadata"].get("review_note"),
                                "dataset_sha256": payload["sha256"],
                            }
                        )
                    else:
                        rows.append(row)
            if not rows:
                raise ValueError(
                    "No eligible records remain after explicit review exclusions"
                )
            related_pairs = []
            if config.get("semantic_contract"):
                from .semantic import resolve_binding, collect
                from decision_workbench.semantic import attach_links

                audit, related_pairs = resolve_binding(
                    self.store, tenant, project, config["semantic_contract"]
                )
                if (
                    semantic_profile is None
                    or public_profile(semantic_profile) != audit["profile"]
                ):
                    raise ValueError("Semantic profile changed before reservation")
                _, _, audited_rows, input_sha, _ = collect(
                    self.store, tenant, project, config
                )
                if input_sha != audit["report"]["input_sha256"]:
                    raise ValueError(
                        "Semantic audit input identity differs from preparation"
                    )
                binding = {
                    key: config["semantic_contract"][key]
                    for key in ("audit_id", "audit_sha256")
                }
                rows = attach_links(rows, related_pairs, binding)
            if config.get("base_snapshot"):
                base = self.store.get(
                    config["base_snapshot"], tenant, project, "snapshot"
                )["payload"]
                from .semantic import verify_snapshot

                verify_snapshot(self.store, tenant, project, base)
                splits = copy.deepcopy(base["splits"])
                all_ids = {r["id"] for rs in splits.values() for r in rs}
                train_ids = {r["id"] for r in splits["train"]}
                heldout = [
                    r for name, rs in splits.items() if name != "train" for r in rs
                ]
                heldout_states = {state_key(r) for r in heldout}
                heldout_groups = {r["group_id"] for r in heldout}
                if len({r["id"] for r in rows}) != len(rows):
                    raise ValueError("Duplicate candidate ids")
                for r in rows:
                    validate(r, require_review=True)
                    if r["metadata"]["origin"] != "synthetic":
                        raise ValueError(
                            "Extend reserved snapshots with reviewed synthetic candidates only"
                        )
                    if (
                        r["id"] in all_ids
                        or state_key(r) in heldout_states
                        or r["group_id"] in heldout_groups
                    ):
                        raise ValueError("Candidate overlaps reserved data")
                    if not set(r["metadata"].get("seed_ids", [])) <= train_ids:
                        raise ValueError(
                            "Candidate seed lineage is outside the reserved training set"
                        )
                splits["train"].extend(rows)
            elif config.get("reservation_plan"):
                from .reservations import load_plan

                plan, splits = load_plan(
                    self.store, tenant, project, config["reservation_plan"]
                )
                expected = plan["definition"]["configuration"]
                if any(
                    config.get(k) != expected[k]
                    for k in ("datasets", "hashes", "semantic_contract")
                ):
                    raise ValueError(
                        "Preparation differs from the approved reservation plan"
                    )
            else:
                from .taxonomy import training_only_ids

                splits = prepare(
                    rows,
                    split_strategy=config.get("split_strategy", "auto"),
                    related_pairs=related_pairs,
                    training_only_ids=training_only_ids(
                        self.store, tenant, project, rows
                    ),
                )
            from decision_workbench.semantic import enforce_edges

            enforce_edges(splits, related_pairs)
            validate_splits(splits)
            temporal_report = audit_splits(splits)
            from .taxonomy import lineage, refuse_exposed_holdouts

            taxonomy_projects = lineage(self.store, tenant, project)

            def validate_reservation(db):
                from .reservations import preserve_roles

                refuse_exposed_holdouts(db, tenant, taxonomy_projects, splits)
                preserve_roles(db, tenant, project, splits)

            snapshot = self.store.add(
                "snapshot",
                tenant,
                project,
                actor,
                {
                    "splits": splits,
                    "temporal_audit": temporal_report,
                    "semantic_contract": config.get("semantic_contract"),
                    "reservation_plan": config.get("reservation_plan"),
                    "reservation_protocol": "fixed-project-roles-v1",
                    "datasets": config["datasets"],
                    "excluded_records": exclusions,
                    "dataset_hashes": config["hashes"],
                    "base_snapshot": config.get("base_snapshot"),
                    "sha256": digest(splits),
                    "counts": {k: len(v) for k, v in splits.items()},
                    "schema_sha256": spec["schema_sha256"],
                },
                job=job,
                validate_db=validate_reservation,
            )
            return {
                "snapshot": snapshot["id"],
                "counts": snapshot["payload"]["counts"],
                "excluded_count": len(exclusions),
            }
        snapshot = self.store.get(config["snapshot"], tenant, project, "snapshot")[
            "payload"
        ]
        if digest(snapshot["splits"]) != snapshot["sha256"]:
            raise ValueError("Snapshot integrity mismatch")
        validate_splits(snapshot["splits"])
        from .semantic import verify_snapshot

        verify_snapshot(self.store, tenant, project, snapshot)
        from .taxonomy import verify_snapshot as verify_taxonomy_snapshot

        verify_taxonomy_snapshot(self.store, tenant, project, snapshot)
        policy = self.store.get(
            config["release_policy_id"], tenant, project, "release-policy"
        )["payload"]
        if (
            digest(policy["criteria"]) != policy["sha256"]
            or policy["sha256"] != config["release_policy_sha256"]
        ):
            raise ValueError("Frozen data/quality policy integrity check failed")
        if config.get("error_policy") != policy["criteria"].get("error_policy"):
            raise ValueError("Domain error policy differs from frozen owner criteria")
        from decision_workbench.quality import check_class_minimums

        coverage = check_class_minimums(
            snapshot["splits"], spec["questions"], policy["criteria"]
        )
        gate = None
        if config.get("backend") in D3_BACKENDS:
            from .d3_training import recheck

            recheck(config, snapshot, spec["questions"])
        if config.get("backend") == "liquid-omni":
            from .liquid_omni_training import recheck

            recheck(config, snapshot, spec["questions"])
        if config.get("backend") in ("liquid-trl", "liquid-unsloth"):
            from .liquid_training import recheck

            recheck(config, snapshot, spec["questions"])
        if config.get("backend") == "vega":
            from .vega_training import recheck, check_negative_floor

            gate = recheck(self.service, job, snapshot, spec["questions"])
            check_negative_floor(gate, spec["questions"], policy["criteria"])
        if job["kind"] == "hosted-evaluate":
            from .hosted_evaluation import evaluate

            return evaluate(self, job, snapshot, spec["questions"], coverage, cancelled)
        if job["kind"] == "remote-study":
            from .remote import prepare_study

            return prepare_study(
                self.store, job, snapshot, spec["questions"], coverage, gate
            )
        if job["kind"] == "remote-import":
            from .remote_admission import admit

            return admit(self, job, snapshot, spec["questions"], coverage, cancelled)
        self.store.heartbeat(
            job, "Training and calibrating; reserved test remains evaluation-only"
        )
        artifact_id = uuid.uuid4().hex
        artifact_folder = self.store.folder / "artifacts" / artifact_id
        artifact_folder.parent.mkdir(parents=True, exist_ok=True)
        if config["backend"] == "cpu-baseline":
            from .baseline import train

            report = train(snapshot["splits"], spec["questions"], artifact_folder)
            checksum = hashlib.sha256(
                (artifact_folder / "model.json").read_bytes()
            ).hexdigest()
        else:
            try:
                import importlib.util

                dependency = {
                    "laya-native": "laya",
                    "unsloth": "unsloth",
                    "vega": "vegaml",
                    "liquid-trl": None,
                    "liquid-unsloth": None,
                    "liquid-omni": None,
                    **{name: None for name in D3_BACKENDS},
                }[config["backend"]]
                if dependency and importlib.util.find_spec(dependency) is None:
                    raise ValueError(
                        "This worker lacks "
                        + dependency
                        + ". Install the documented model environment; CPU baseline is a separate backend"
                    )
            except ImportError as exc:
                raise ValueError("Model worker dependencies are unavailable") from exc
            job_folder = self.store.folder / "jobs" / job["id"]
            job_folder.mkdir(parents=True, exist_ok=False)
            if config["backend"] == "vega":
                (job_folder / "data").mkdir()
                for role, records in gate["positive_splits"].items():
                    (job_folder / "data" / (role + ".jsonl")).write_bytes(
                        jsonl(records).encode("utf-8")
                    )
            else:
                write_splits(snapshot["splits"], job_folder / "data")
            runner = {
                "laya-native": "laya_runner",
                "unsloth": "unsloth_runner",
                "vega": "vega_runner",
                "liquid-trl": "liquid_runner",
                "liquid-unsloth": "liquid_runner",
                "liquid-omni": "liquid_omni_runner",
                **{name: "d3_runner" for name in D3_BACKENDS},
            }[config["backend"]]
            argv = [
                sys.executable,
                "-m",
                "decision_workbench.training." + runner,
                "--data",
                str(job_folder / "data"),
                "--model",
                config["model"],
                "--revision",
                config["revision"],
                "--out",
                str(artifact_folder),
                "--epochs",
                str(config["epochs"]),
                "--batch-size",
                str(config["batch_size"]),
            ]
            child_environment = None
            child_cwd = None
            if config["backend"] in ("liquid-omni", *D3_BACKENDS):
                from .model_environment import native_environment, model_python

                if config["backend"] in D3_BACKENDS:
                    from decision_workbench.training.d3_local import request_body

                    prefix, study_key, native_runner = "d3", "d3_study", "d3_runner"
                else:
                    from decision_workbench.training.liquid_omni_local import (
                        request_body,
                    )

                    prefix, study_key, native_runner = (
                        "omni",
                        "omni_study",
                        "liquid_omni_runner",
                    )

                child_environment, child_cwd, cache = native_environment(
                    self.store.folder, backend=config["backend"]
                )
                request_path = job_folder / (prefix + "-request.json")
                request_path.write_bytes(request_body(config[study_key]))
                argv = [
                    model_python(config["backend"], "training"),
                    "-m",
                    "decision_workbench.training." + native_runner,
                    "--data",
                    str(job_folder / "data"),
                    "--out",
                    str(artifact_folder),
                    "--local-study-request",
                    str(request_path),
                    "--cache",
                    str(cache),
                ]
                if config["backend"] in D3_BACKENDS:
                    argv.extend(["--backend", config["backend"]])
                for name in ("epochs", "batch_size", "learning_rate", "device"):
                    argv.extend(["--" + name.replace("_", "-"), str(config[name])])
                if config["max_steps"] is not None:
                    argv.extend(["--max-steps", str(config["max_steps"])])
                if config["download"]:
                    argv.append("--download")
            if config["backend"] in ("liquid-trl", "liquid-unsloth"):
                from .model_environment import liquid_environment, model_python
                from decision_workbench.training.liquid_local import request_body

                child_environment, child_cwd, cache = liquid_environment(
                    self.store.folder, backend=config["backend"]
                )
                request_path = job_folder / "liquid-request.json"
                request_path.write_bytes(request_body(config["liquid_study"]))
                argv = [
                    model_python(config["backend"], "training"),
                    "-m",
                    "decision_workbench.training.liquid_runner",
                    "--data",
                    str(job_folder / "data"),
                    "--out",
                    str(artifact_folder),
                    "--local-study-request",
                    str(request_path),
                    "--cache",
                    str(cache),
                ]
                if config["backend"] == "liquid-unsloth":
                    argv.extend(["--trainer", "unsloth"])
                for name in ("epochs", "batch_size", "rank", "learning_rate", "device"):
                    argv.extend(["--" + name.replace("_", "-"), str(config[name])])
                if config["max_steps"] is not None:
                    argv.extend(["--max-steps", str(config["max_steps"])])
                if config["download"]:
                    argv.append("--download")
            if config["backend"] == "vega":
                from decision_workbench.training.vega_local import request_body

                for role, records in gate["splits"].items():
                    (job_folder / "data" / ("gate_" + role + ".jsonl")).write_bytes(
                        jsonl(records).encode("utf-8")
                    )
                request_path = job_folder / "vega-request.json"
                request_path.write_text(request_body(config), encoding="utf-8")
                argv.extend(["--local-study-request", str(request_path)])
                for name in (
                    "backbone_model",
                    "backbone_revision",
                    "rank",
                    "learning_rate",
                    "gate_weight",
                    "max_steps",
                ):
                    argv.extend(["--" + name.replace("_", "-"), str(config[name])])
                if config["offline"]:
                    argv.append("--offline")
            else:
                argv.extend(["--grad-accum", str(config.get("grad_accum", 4))])
            if config.get("max_length"):
                argv.extend(["--max-length", str(config["max_length"])])
            if config["backend"] == "unsloth":
                argv.extend(["--rank", str(config["rank"])])
                if config.get("full_precision"):
                    argv.append("--full-precision")
                if config.get("max_steps"):
                    argv.extend(["--max-steps", str(config["max_steps"])])
            elif config.get("train_encoder"):
                argv.append("--train-encoder")
            deadline = time.monotonic() + 4 * 60 * 60
            from decision_workbench.training.resources import ResourceLease
            from decision_workbench.training.process_guard import leased_process_options

            self.store.heartbeat(job, "Waiting for exclusive model resource")
            with (
                ResourceLease(self.store.folder / "gpu.lock").hold(
                    timeout=240, cancelled=cancelled
                ) as model_lease,
                (job_folder / "training.log").open("w", encoding="utf-8") as log,
            ):
                process = subprocess.Popen(
                    argv,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    **({"cwd": child_cwd} if child_cwd is not None else {}),
                    **leased_process_options(model_lease, child_environment),
                )
                try:
                    while process.poll() is None:
                        self.store.heartbeat(
                            job,
                            config["backend"]
                            + " child process training; logs retained privately",
                        )
                        if cancelled.is_set():
                            raise Conflict("Cancelled")
                        if time.monotonic() > deadline:
                            raise ValueError(
                                "Training exceeded the four-hour local job limit"
                            )
                        cancelled.wait(1)
                    if process.returncode:
                        raise ValueError(
                            config["backend"]
                            + " run failed; inspect the private training log"
                        )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            report = json.loads(
                (artifact_folder / "report.json").read_text(encoding="utf-8")
            )
            from decision_workbench.training.artifacts import file_sha, verify_manifest

            checksum = file_sha(artifact_folder / "manifest.json")
            verify_manifest(
                artifact_folder, checksum, config["backend"], spec["questions"]
            )
            if (
                report.get("reload_equivalence") is not True
                or report.get("export_qualification", {}).get("passed") is not True
            ):
                raise ValueError(
                    "Export did not pass actual runtime reload qualification"
                )
            if config["backend"] == "vega":
                from .vega_training import checked_report

                checked_report(report, config)
            elif config["backend"] in D3_BACKENDS:
                from .d3_training import checked_artifact

                checked_artifact(artifact_folder, report, config, spec["questions"])
            elif config["backend"] == "liquid-omni":
                from .liquid_omni_training import checked_artifact

                checked_artifact(artifact_folder, report, config, spec["questions"])
            elif config["backend"] in ("liquid-trl", "liquid-unsloth"):
                from .liquid_training import checked_artifact

                checked_artifact(artifact_folder, report, config, spec["questions"])
        self.store.heartbeat(
            job, "Registering immutable artifact and evaluation report"
        )
        if config.get("error_policy") is not None:
            from decision_workbench.training.error_policy import attach_evaluation
            from decision_workbench.training.artifacts import (
                write_manifest,
                verify_manifest,
            )

            attach_evaluation(report, spec["questions"], config["error_policy"])
            (artifact_folder / "report.json").write_text(
                json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
            )
            if config["backend"] != "cpu-baseline":
                prior = json.loads((artifact_folder / "manifest.json").read_text())
                checksum = write_manifest(
                    artifact_folder,
                    config["backend"],
                    spec["questions"],
                    prior["checkpoint"],
                )
                verify_manifest(
                    artifact_folder, checksum, config["backend"], spec["questions"]
                )
        model = self.store.add(
            "model",
            tenant,
            project,
            actor,
            {
                "backend": config["backend"],
                "artifact_id": artifact_id,
                "model_sha256": checksum,
                "snapshot": config["snapshot"],
                "snapshot_sha256": snapshot["sha256"],
                "schema_sha256": spec["schema_sha256"],
                "job_id": job["id"],
                "report": report,
                "configuration": config,
                "data_gate_counts": coverage,
            },
            job=job,
        )
        return {"model": model["id"], "backend": config["backend"]}

    def once(self):
        if not self.presence_active:
            with self.presence():
                return self.once()
        job = self.store.claim()
        if job:
            self.current_job = job["id"]
            self.store.worker_presence(self.worker_id, "busy", self.current_job)
            try:
                self.execute(job)
            finally:
                self.current_job = None
                self.store.worker_presence(self.worker_id, "idle")
            return True
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default=os.environ.get("DMT_HOME", "data/workspace"))
    parser.add_argument(
        "--once", action="store_true", help="Execute at most one pending job"
    )
    args = parser.parse_args()
    worker = Worker(Store(args.home))
    try:
        with worker.presence():
            if args.once:
                worker.once()
            else:
                while True:
                    if not worker.once():
                        time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        worker.service.runtime.close()


if __name__ == "__main__":
    main()
