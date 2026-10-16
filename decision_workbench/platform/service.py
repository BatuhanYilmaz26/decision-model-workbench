"""Authenticated application lifecycle. Identity comes from server key configuration."""

import copy
import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from decision_workbench.data import read_records, validate, validate_questions, dumps
from .store import Conflict, digest


class Service:
    def __init__(self, store, runtime=None, hosted=None):
        self.store = store
        from .cloud import Cloud

        self.cloud = Cloud(store)
        from .hosted import Hosted

        self.hosted = hosted if hosted is not None else Hosted(store)
        from .runtime_broker import RuntimeBroker

        self.runtime = runtime if runtime is not None else RuntimeBroker(store)
        from .gate_data import GateData

        self.gates = GateData(self)
        from .review_assignments import ReviewAssignments

        self.review_assignments = ReviewAssignments(self)

    @staticmethod
    def require(principal, roles):
        if principal["role"] not in roles:
            raise PermissionError("Your role cannot perform this action")

    def project(self, principal, project):
        return self.store.get(project, principal["tenant"], project, "project")

    def create_project(
        self,
        principal,
        name,
        description,
        questions,
        parent_project=None,
        data_requirements="",
    ):
        self.require(principal, ("admin", "editor"))
        if (
            not name.strip()
            or len(name) > 120
            or not description.strip()
            or len(description) > 12000
        ):
            raise ValueError(
                "Provide a project name and a domain/policy description (up to 12,000 characters)"
            )
        if not isinstance(data_requirements, str) or len(data_requirements) > 4000:
            raise ValueError("Data requirements must be text up to 4,000 characters")
        validate_questions(questions)
        project = uuid.uuid4().hex
        parent = None
        if parent_project:
            self.require(principal, ("admin",))
            parent = self.project(principal, parent_project)["payload"]
        payload = {
            "data_requirements": data_requirements.strip(),
            "parent_project": parent_project,
            "name": name.strip(),
            "description": description.strip(),
            "questions": questions,
            "policy_version": parent["policy_version"] + 1 if parent else 1,
            "schema_sha256": digest(questions),
        }
        return self.store.add(
            "project",
            principal["tenant"],
            project,
            principal["actor"],
            payload,
            project,
        )

    def inspect_upload(self, principal, project, content, filename, worksheet=None):
        self.require(principal, ("admin", "editor", "reviewer"))
        self.project(principal, project)
        from decision_workbench.intake import inspect_table

        return inspect_table(content, filename, worksheet)

    def upload(
        self,
        principal,
        project,
        content,
        filename,
        mapping=None,
        dataset=None,
        expected_hash=None,
    ):
        self.require(principal, ("admin", "editor"))
        spec = self.project(principal, project)["payload"]
        from decision_workbench.intake import check_upload, map_table

        check_upload(content, filename)
        try:
            rows = (
                map_table(content, filename, spec["questions"], mapping)
                if mapping is not None
                else read_records(
                    content, filename, spec, allow_missing_labels=True, max_rows=20000
                )
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "Input does not match the project format; check column mappings and JSON records"
            ) from exc
        for row in rows:
            if row["questions"] != spec["questions"]:
                raise ValueError("Uploaded questions differ from the project schema")
            if len(dumps(row["state"]).encode()) > 64000:
                raise ValueError("Each input state is limited to 64 KB")
            row["metadata"]["reviewed"] = False
            row["metadata"]["review_status"] = "pending"
            row["metadata"]["uploaded_by"] = principal["actor"]
        return self.save_dataset(
            principal,
            project,
            rows,
            {
                "filename": Path(filename).name,
                "upload_sha256": hashlib.sha256(content).hexdigest(),
                "mapping": mapping,
            },
            allow_missing_labels=True,
            dataset=dataset,
            expected_hash=expected_hash,
        )

    def save_examples(
        self, principal, project, examples, dataset=None, expected_hash=None
    ):
        self.require(principal, ("admin", "editor"))
        questions = self.project(principal, project)["payload"]["questions"]
        from decision_workbench.data import hard_gold

        if not isinstance(examples, list) or not 1 <= len(examples) <= 50:
            raise ValueError("Provide 1–50 examples")
        rows = []
        for example in examples:
            if not isinstance(example, dict) or set(example) - {
                "id",
                "group_id",
                "state",
                "labels",
                "origin",
            }:
                raise ValueError(
                    "Examples accept id, group_id, state, labels and origin"
                )
            labels = example.get("labels", {})
            if not isinstance(labels, dict) or not set(labels) <= set(questions):
                raise ValueError("Example labels must use project questions")
            record_id = example.get("id") or uuid.uuid4().hex
            row = {
                "id": record_id,
                "group_id": example.get("group_id") or record_id,
                "state": example.get("state"),
                "questions": questions,
                "gold": hard_gold({k: questions[k] for k in labels}, labels),
                "metadata": {
                    "origin": example.get("origin", "synthetic"),
                    "reviewed": False,
                    "entered_by": principal["actor"],
                    "review_status": "pending",
                },
            }
            validate(row, allow_missing_labels=True)
            if len(dumps(row["state"]).encode()) > 64000:
                raise ValueError("Each input state is limited to 64 KB")
            rows.append(row)
        return self.save_dataset(
            principal,
            project,
            rows,
            {"kind": "manual-examples"},
            allow_missing_labels=True,
            dataset=dataset,
            expected_hash=expected_hash,
        )

    def dataset_quality(self, principal, project, dataset):
        self.require(principal, ("admin", "editor", "reviewer"))
        questions = self.project(principal, project)["payload"]["questions"]
        payload = self.store.get(
            dataset, principal["tenant"], project, "dataset", include_rows=False
        )["payload"]
        from decision_workbench.quality import quality_report

        report = quality_report(
            self.store.verify_dataset(dataset, principal["tenant"], project), questions
        )
        return {
            "dataset_id": dataset,
            "dataset_sha256": payload["sha256"],
            "report": report,
        }

    def save_dataset(
        self,
        principal,
        project,
        rows,
        provenance,
        job=None,
        *,
        allow_missing_labels=False,
        dataset=None,
        expected_hash=None,
    ):
        if not rows or len(rows) > 20000:
            raise ValueError("A local dataset requires 1–20,000 rows")
        for row in rows:
            validate(row, allow_missing_labels=allow_missing_labels)
        if (dataset is None) != (expected_hash is None):
            raise ValueError(
                "Appending requires both a dataset version and its expected hash"
            )
        if dataset is not None:
            self.require(principal, ("admin", "editor"))
            questions = self.project(principal, project)["payload"]["questions"]
            if any(
                r["questions"] != questions
                or r["metadata"]["reviewed"]
                or r["metadata"].get("review_status", "pending") != "pending"
                for r in rows
            ):
                raise ValueError(
                    "Appended records must match the schema and remain pending review"
                )
            return self.store.append_dataset(
                principal["tenant"],
                project,
                principal["actor"],
                dataset,
                rows,
                expected_hash,
                provenance,
            )
        return self.store.add_dataset(
            principal["tenant"], project, principal["actor"], rows, provenance, job=job
        )

    def review(
        self,
        principal,
        project,
        dataset,
        record_id,
        gold,
        approved,
        expected_hash,
        note,
        status=None,
        assignment_revision=None,
    ):
        self.require(principal, ("admin", "reviewer"))
        self.project(principal, project)
        original = self.store.get(
            dataset, principal["tenant"], project, "dataset", include_rows=False
        )["payload"]
        if original["sha256"] != expected_hash:
            raise Conflict("Dataset hash mismatch; refresh the version being reviewed")
        if not note.strip() or len(note) > 1000:
            raise ValueError("Record a review note (up to 1,000 characters)")
        matches = self.store.dataset_rows(
            dataset, principal["tenant"], project, record_id=record_id
        )
        selected = matches[0] if matches else None
        if selected is None:
            raise LookupError("Record not found")
        status = status or ("approved" if approved else "pending")
        if (
            status not in ("pending", "approved", "rejected")
            or (status == "approved") != approved
        ):
            raise ValueError("Review decision conflicts with the approved flag")
        selected["gold"] = gold
        selected["metadata"].update(
            reviewed=approved,
            review_status=status,
            reviewer=principal["actor"],
            review_note=note,
        )
        validate(selected, allow_missing_labels=not approved)
        return self.store.review_dataset(
            principal["tenant"],
            project,
            principal["actor"],
            dataset,
            selected,
            expected_hash,
            note,
            assignment_revision=assignment_revision,
            reviewer_role=principal["role"],
        )

    def submit(self, principal, project, kind, config, key, authorize=None):
        self.require(principal, ("admin", "editor"))
        self.project(principal, project)
        config = copy.deepcopy(config)
        allowed = {
            "gate-prepare": {"snapshot", "datasets", "semantic_audit"},
            "gate-semantic-audit": {"snapshot", "datasets", "threshold"},
            "prepare": {
                "datasets",
                "base_snapshot",
                "split_strategy",
                "semantic_audit",
                "reservation_policy",
                "reservation_approved",
                "expected_reservation_sha256",
            },
            "semantic-audit": {"datasets", "base_snapshot", "threshold"},
            "generate": {
                "snapshot",
                "count",
                "seed_count",
                "coverage",
                "data_approved",
                "cost_approved",
                "expected_preview_sha256",
                "retry_of",
            },
            "train": {
                "snapshot",
                "backend",
                "model",
                "revision",
                "epochs",
                "rank",
                "batch_size",
                "grad_accum",
                "max_length",
                "full_precision",
                "train_encoder",
                "max_steps",
            },
        }
        from .vega_training import INPUTS as VEGA_INPUTS

        allowed["train"] |= VEGA_INPUTS
        allowed["hosted-evaluate"] = {"snapshot", "profile_id", "data_approved"}
        allowed["taxonomy-discover"] = {
            "dataset",
            "record_ids",
            "question_id",
            "profile_id",
            "data_approved",
            "cost_approved",
        }
        allowed["remote-study"] = allowed["train"] | {
            "provider",
            "region",
            "gpu",
            "image_digest",
        }
        if kind not in allowed or set(config) - allowed[kind]:
            raise ValueError("Unexpected job configuration fields")
        validate_db = None
        if kind == "gate-semantic-audit":
            self.require(principal, ("admin",))
            threshold = config.get("threshold")
            if (
                set(config) != allowed[kind]
                or type(threshold) not in (int, float)
                or not math.isfinite(threshold)
                or not 0.5 <= threshold <= 1
            ):
                raise ValueError(
                    "A combined audit needs a positive snapshot, applicability versions and finite threshold in [0.5,1]"
                )
            from .gate_semantic import freeze

            config = freeze(self, principal, project, config)
        elif kind == "gate-prepare":
            if not {"snapshot", "datasets"} <= set(config):
                raise ValueError(
                    "Applicability reservation needs a positive snapshot and collection versions"
                )
            config = self.gates.freeze(principal, project, config)
        elif kind == "taxonomy-discover":
            self.require(principal, ("admin",))
            if (
                set(config) != allowed[kind]
                or config["data_approved"] is not True
                or config["cost_approved"] is not True
            ):
                raise ValueError(
                    "Owner approval is required for the selected examples and billable discovery request"
                )
            from .taxonomy import selection
            from .taxonomy_provider import profile_for, public_profile, body_for
            from decision_workbench.training.provenance import source_fingerprint

            spec, sha, rows = selection(
                self.store,
                principal["tenant"],
                project,
                config["dataset"],
                config["record_ids"],
                config["question_id"],
            )
            p = profile_for(principal["tenant"], project, config["profile_id"])
            body_for(spec, config["question_id"], rows, p)
            config.update(
                dataset_sha256=sha,
                profile=public_profile(p),
                source_sha256=source_fingerprint(),
            )
        elif kind in ("prepare", "semantic-audit"):
            from decision_workbench.temporal import STRATEGIES

            if config.get("split_strategy", "auto") not in STRATEGIES:
                raise ValueError("Choose auto, group-random or chronological splitting")
            ids = config.get("datasets")
            if (
                not isinstance(ids, list)
                or not 1 <= len(ids) <= 20
                or len(set(ids)) != len(ids)
            ):
                raise ValueError("Choose 1–20 unique immutable dataset versions")
            if config.get("base_snapshot"):
                base = self.store.get(
                    config["base_snapshot"], principal["tenant"], project, "snapshot"
                )["payload"]
                from decision_workbench.temporal import audit_splits

                mode = audit_splits(base["splits"])["strategy"]
                if config.get("split_strategy", "auto") not in ("auto", mode):
                    raise ValueError(
                        "A synthetic extension must preserve the reserved split strategy"
                    )
            config["hashes"] = {
                i: self.store.get(
                    i, principal["tenant"], project, "dataset", include_rows=False
                )["payload"]["sha256"]
                for i in ids
            }
            from .semantic import profile_for, public_profile, freeze_review

            profile = profile_for(principal["tenant"], project)
            if kind == "semantic-audit":
                self.require(principal, ("admin",))
                threshold = config.get("threshold")
                if profile is None:
                    raise ValueError(
                        "Operator must configure a scoped local semantic profile"
                    )
                if (
                    type(threshold) not in (int, float)
                    or not math.isfinite(threshold)
                    or not 0.5 <= threshold <= 1
                ):
                    raise ValueError(
                        "Choose a finite semantic threshold between 0.5 and 1"
                    )
                from decision_workbench.training.provenance import source_fingerprint

                config["profile"] = public_profile(profile)
                config["source_sha256"] = source_fingerprint()
            elif config.get("semantic_audit"):
                config["semantic_contract"] = freeze_review(
                    self.store,
                    principal["tenant"],
                    project,
                    config["semantic_audit"],
                    config,
                )
            elif profile and profile["required"]:
                raise ValueError(
                    "Review a semantic audit covering these exact inputs before preparation"
                )
        elif kind == "generate":
            from .generation_plans import freeze

            config, validate_db = freeze(
                self, principal, project, config, key, authorize
            )
        elif kind == "hosted-evaluate":
            if set(config) != {
                "snapshot",
                "profile_id",
                "data_approved",
            } or not isinstance(config["profile_id"], str):
                raise ValueError(
                    "Hosted evaluation requires a snapshot, profile identifier and explicit data approval"
                )
            self.require(principal, ("admin",))
            if config.get("data_approved") is not True:
                raise ValueError(
                    "Owner approval is required for provider study data and future released inference"
                )
            self.store.get(config["snapshot"], principal["tenant"], project, "snapshot")
            from .hosted import profile_for, public_profile

            config["profile"] = public_profile(
                profile_for(principal["tenant"], config["profile_id"])
            )
            config["backend"] = "hosted"
        elif kind in ("train", "remote-study"):
            if config.get("backend") == "vega":
                from .vega_training import validate_references

                validate_references(config)
            if kind == "remote-study":
                from .remote import validate_location

                self.require(principal, ("admin",))
                validate_location(config)
                if config.get("backend") == "cpu-baseline":
                    raise ValueError("Remote studies require native Laya or Unsloth")
            self.store.get(config["snapshot"], principal["tenant"], project, "snapshot")
            if config.get("backend") not in (
                "cpu-baseline",
                "unsloth",
                "laya-native",
                "vega",
            ):
                raise ValueError("Select cpu-baseline, unsloth, laya-native or vega")
            if config["backend"] == "vega":
                from .vega_training import freeze, INPUTS

                location = (
                    {
                        k: config[k]
                        for k in ("provider", "region", "gpu", "image_digest")
                    }
                    if kind == "remote-study"
                    else {}
                )
                if set(config) - INPUTS - set(location):
                    raise ValueError("Training options do not apply to Vega")
                config = {
                    **freeze(
                        self,
                        principal,
                        project,
                        {k: v for k, v in config.items() if k in INPUTS},
                    ),
                    **location,
                }
            elif set(config) & {
                "gate_snapshot",
                "backbone_model",
                "backbone_revision",
                "learning_rate",
                "gate_weight",
                "offline",
            }:
                raise ValueError("Vega options do not apply to this backend")
            if config["backend"] not in ("cpu-baseline", "vega"):
                unsupported = (
                    {"rank", "max_steps", "full_precision"}
                    if config["backend"] == "laya-native"
                    else {"train_encoder"}
                )
                if set(config) & unsupported:
                    raise ValueError(
                        "Training options do not apply to the selected backend"
                    )
                if not re.fullmatch(r"[a-f0-9]{40}", config.get("revision", "")):
                    raise ValueError(
                        "Pin the remote model to a 40-character Hub revision"
                    )
                if not re.fullmatch(
                    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.get("model", "")
                ):
                    raise ValueError(
                        "Use a Hub model identifier, not a filesystem path"
                    )
                for name, low, high in [
                    ("epochs", 1, 20),
                    ("rank", 1, 128),
                    ("batch_size", 1, 32),
                    ("grad_accum", 1, 128),
                ]:
                    value = config.get(
                        name,
                        {"epochs": 2, "rank": 16, "batch_size": 2, "grad_accum": 4}[
                            name
                        ],
                    )
                    if type(value) is not int or not low <= value <= high:
                        raise ValueError(f"{name} must be between {low} and {high}")
                    if name != "rank" or config["backend"] == "unsloth":
                        config[name] = value
                if config.get("max_length") is not None and (
                    type(config["max_length"]) is not int
                    or not 64 <= config["max_length"] <= 16384
                ):
                    raise ValueError("max_length must be between 64 and 16384 tokens")
                if config.get("max_steps") is not None and (
                    type(config["max_steps"]) is not int
                    or not 1 <= config["max_steps"] <= 100000
                ):
                    raise ValueError("max_steps must be between 1 and 100000")
                for flag in ("full_precision", "train_encoder"):
                    if flag in config and type(config[flag]) is not bool:
                        raise ValueError(flag + " must be a boolean")
        else:
            raise ValueError("Unknown job kind")
        reservation_fields = {
            "reservation_policy",
            "reservation_approved",
            "expected_reservation_sha256",
        }
        if kind == "prepare" and set(config) & reservation_fields:
            if not reservation_fields <= set(config):
                raise ValueError(
                    "Provide a reservation policy, preview identity and explicit approval together"
                )
            from .reservations import freeze

            config, validate_db = freeze(
                self, principal, project, config, key, authorize
            )
        return self.store.submit(
            principal["tenant"],
            project,
            principal["actor"],
            kind,
            config,
            key,
            validate_db=validate_db,
            authorize=authorize if kind == "generate" else None,
        )

    def activate(
        self, principal, project, model_id, expected_revision, note, authorize=None
    ):
        self.require(principal, ("admin",))
        spec = self.project(principal, project)["payload"]
        model = self.store.get(model_id, principal["tenant"], project, "model")[
            "payload"
        ]
        if (
            self.store.job(model["job_id"], principal["tenant"], project)["status"]
            != "succeeded"
        ):
            raise ValueError(
                "Only a successfully completed training or evaluation job may be released"
            )
        if model["backend"] not in (
            "cpu-baseline",
            "unsloth",
            "laya-native",
            "hosted",
            "vega",
        ):
            raise ValueError("Unsupported serving backend")
        if model["schema_sha256"] != spec["schema_sha256"]:
            raise ValueError("Model schema differs from the project")
        if set(model["report"].get("test", {})) != set(spec["questions"]):
            raise ValueError("Evaluation does not cover every project question")
        if not note.strip() or len(note) > 1000:
            raise ValueError("Record a release or rollback reason")
        config = model["configuration"]
        if not config.get("release_policy_id"):
            raise ValueError(
                "This legacy run has no frozen acceptance policy; retrain in a successor project"
            )
        policy = self.store.get(
            config["release_policy_id"], principal["tenant"], project, "release-policy"
        )["payload"]
        gates = policy["criteria"]
        if (
            digest(gates) != policy["sha256"]
            or policy["sha256"] != config["release_policy_sha256"]
        ):
            raise ValueError("Acceptance policy integrity check failed")
        for metrics in model["report"]["test"].values():
            if (
                metrics["accuracy"] < gates["min_accuracy"]
                or metrics["macro_f1"] < gates["min_macro_f1"]
                or metrics["ece_10_equal_width_bins"] > gates["max_ece"]
                or metrics["count"] < gates["min_test_rows"]
                or any(n == 0 for n in metrics["test_class_counts"].values())
            ):
                raise ValueError(
                    "Model fails its frozen accuracy, F1, calibration, sample-count or test-class coverage gates"
                )
        if gates.get("error_policy") is not None:
            from decision_workbench.training.error_policy import release_failures

            failures = release_failures(
                model["report"], spec["questions"], gates["error_policy"]
            )
            if failures:
                raise ValueError(
                    "Model fails frozen domain error criteria: " + "; ".join(failures)
                )
        if model["backend"] == "vega":
            from .vega_training import release_gates

            release_gates(model["report"], gates, spec["questions"])
        if model["backend"] == "cpu-baseline":
            self.checked_bundle(model)
        elif model["backend"] == "hosted":
            from .hosted import checked_hosted, profile_for, credential

            bundle = checked_hosted(self.store, model, spec["questions"])
            credential(
                profile_for(
                    principal["tenant"], bundle["profile"]["id"], bundle["profile"]
                )
            )
        else:
            self.checked_export(model, spec["questions"])
            self.runtime.preload(model_id, model, spec["questions"])
        if authorize is not None:
            authorize()
        return self.store.activate(
            principal["tenant"],
            project,
            principal["actor"],
            model_id,
            expected_revision,
            note,
        )

    def configure_release_policy(
        self,
        principal,
        project,
        min_accuracy,
        min_macro_f1,
        max_ece,
        min_test_rows,
        expected_id,
        min_training_examples_per_class=5,
        min_calibration_examples_per_class=1,
        vega=None,
        error_policy=None,
    ):
        self.require(principal, ("admin",))
        self.project(principal, project)
        from .acceptance import normalize_criteria

        criteria = normalize_criteria(
            self.project(principal, project)["payload"]["questions"],
            min_accuracy,
            min_macro_f1,
            max_ece,
            min_test_rows,
            min_training_examples_per_class,
            min_calibration_examples_per_class,
            vega,
            error_policy,
        )
        return self.store.set_release_policy(
            principal["tenant"], project, principal["actor"], criteria, expected_id
        )

    def checked_bundle(self, model):
        folder = (self.store.folder / "artifacts" / model["artifact_id"]).resolve()
        if folder.parent != (self.store.folder / "artifacts").resolve():
            raise ValueError("Invalid artifact reference")
        body = (folder / "model.json").read_bytes()
        if hashlib.sha256(body).hexdigest() != model["model_sha256"]:
            raise ValueError("Model integrity check failed")
        return json.loads(body)

    def checked_export(self, model, questions):
        from decision_workbench.training.artifacts import verify_manifest

        folder = (self.store.folder / "artifacts" / model["artifact_id"]).resolve()
        if folder.parent != (self.store.folder / "artifacts").resolve():
            raise ValueError("Invalid artifact reference")
        verify_manifest(folder, model["model_sha256"], model["backend"], questions)
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        if report != model["report"]:
            raise ValueError("Export report differs from the registered evaluation")
        if model["backend"] == "vega":
            from .vega_training import checked_report, checked_model

            checked_report(report, model["configuration"])
            checked_model(self, model, questions)
        if (
            report.get("reload_equivalence") is not True
            or report.get("export_qualification", {}).get("passed") is not True
        ):
            raise ValueError("Export lacks actual runtime reload qualification")
        return folder

    def predict(self, principal, project, state, threshold, request_key=None):
        self.require(principal, ("admin", "editor", "reviewer", "inference"))
        spec = self.project(principal, project)["payload"]
        if (
            not isinstance(state, (str, dict, list))
            or not state
            or len(dumps(state).encode()) > 64000
        ):
            raise ValueError("Provide nonempty text or JSON state under 64 KB")
        if (
            not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or not 0 <= threshold <= 1
        ):
            raise ValueError("Threshold must be in [0,1]")
        active = self.store.active(principal["tenant"], project)
        if not active["model_id"]:
            raise Conflict("No released model; train, evaluate and activate one first")
        model = self.store.get(
            active["model_id"], principal["tenant"], project, "model"
        )["payload"]
        if model["schema_sha256"] != spec["schema_sha256"]:
            raise ValueError("Released model schema differs from the project")
        if model["backend"] == "cpu-baseline":
            from .baseline import predict

            answers = predict(self.checked_bundle(model), state, threshold)
        elif model["backend"] in ("unsloth", "laya-native", "vega"):
            answers = self.runtime.predict(
                active["model_id"], model, spec["questions"], state, threshold
            )
        elif model["backend"] == "hosted":
            from .hosted import checked_hosted
            from .hosted_evaluation import calibrated

            if request_key is not None and (
                not isinstance(request_key, str) or not 8 <= len(request_key) <= 128
            ):
                raise ValueError("Use an 8–128 character decision idempotency key")
            bundle = checked_hosted(self.store, model, spec["questions"])
            provider_result = self.hosted.call(
                principal,
                project,
                bundle["profile"],
                state,
                spec["questions"],
                "inference:" + (request_key or uuid.uuid4().hex),
                {
                    "model_id": active["model_id"],
                    "release_revision": active["revision"],
                    "threshold": threshold,
                },
            )
            answers = calibrated(
                provider_result["answers"],
                spec["questions"],
                bundle["temperatures"],
                threshold,
            )
        else:
            raise ValueError("Unsupported serving backend")
        return {
            "model_id": active["model_id"],
            "release_revision": active["revision"],
            "schema_sha256": model["schema_sha256"],
            "answers": answers,
            "abstained": any(a["abstained"] for a in answers.values()),
            **(
                {
                    "provider": {
                        **provider_result["identity"],
                        "call_id": provider_result["call_id"],
                        "cached": provider_result["cached"],
                    }
                }
                if model["backend"] == "hosted"
                else {}
            ),
        }
