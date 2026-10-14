"""Authenticated immutable applicability collections and coupled reservations."""

import copy
import hashlib
import uuid
import io
import zipfile
from pathlib import Path

from decision_workbench.data import jsonl
from decision_workbench.gate_data import FIELDS, read_upload, reserve, validate_record
from decision_workbench.training.vega_contracts import validate_gate_splits
from decision_workbench.training.provenance import source_fingerprint
from .store import Conflict, digest

KIND = "gate-dataset"


class GateData:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def save(
        self, principal, project, records, provenance, dataset=None, expected_hash=None
    ):
        self.service.require(principal, ("admin", "editor"))
        questions = self.service.project(principal, project)["payload"]["questions"]
        if not isinstance(records, list) or not 1 <= len(records) <= 20000:
            raise ValueError("Provide 1–20,000 applicability examples")
        rows = copy.deepcopy(records)
        for row in rows:
            if not isinstance(row, dict) or set(row) - FIELDS:
                raise ValueError(
                    "Applicability examples accept no business labels or decision gold"
                )
            row.setdefault("id", uuid.uuid4().hex)
            row.setdefault("group_id", row["id"])
            meta = row.setdefault("metadata", {"origin": "real"})
            if not isinstance(meta, dict):
                raise ValueError("Applicability metadata must be an object")
            for key in (
                "reviewer",
                "review_note",
                "reviewed_at",
                "uploaded_by",
                "entered_by",
            ):
                meta.pop(key, None)
            meta.update(
                reviewed=False, review_status="pending", uploaded_by=principal["actor"]
            )
            validate_record(row, questions)
        if (dataset is None) != (expected_hash is None):
            raise ValueError(
                "Appending requires the current applicability version and expected hash"
            )
        if dataset:
            return self.store.append_dataset(
                principal["tenant"],
                project,
                principal["actor"],
                dataset,
                rows,
                expected_hash,
                provenance,
                kind=KIND,
            )
        return self.store.add_dataset(
            principal["tenant"],
            project,
            principal["actor"],
            rows,
            provenance,
            kind=KIND,
        )

    def examples(self, principal, project, examples, dataset=None, expected_hash=None):
        if not isinstance(examples, list) or not 1 <= len(examples) <= 50:
            raise ValueError("Enter 1–50 applicability examples")
        return self.save(
            principal,
            project,
            examples,
            {"kind": "manual-applicability"},
            dataset,
            expected_hash,
        )

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
        self.service.require(principal, ("admin", "editor"))
        questions = self.service.project(principal, project)["payload"]["questions"]
        rows = read_upload(content, filename, questions, mapping)
        return self.save(
            principal,
            project,
            rows,
            {
                "filename": Path(filename).name,
                "upload_sha256": hashlib.sha256(content).hexdigest(),
                "mapping": mapping,
            },
            dataset,
            expected_hash,
        )

    def page(self, principal, project, dataset, limit=50, offset=0):
        self.service.require(principal, ("admin", "editor", "reviewer"))
        self.service.project(principal, project)
        entity = self.store.get(
            dataset, principal["tenant"], project, KIND, include_rows=False
        )
        return {
            "dataset": entity,
            "rows": self.store.dataset_rows(
                dataset, principal["tenant"], project, limit, offset, kind=KIND
            ),
            "total": entity["payload"]["row_count"],
            "head": self.store.dataset_head(
                dataset, principal["tenant"], project, kind=KIND
            ),
            "limit": limit,
            "offset": offset,
        }

    def review(
        self,
        principal,
        project,
        dataset,
        record_id,
        negative_for,
        reason,
        status,
        expected_hash,
        note,
    ):
        self.service.require(principal, ("admin", "reviewer"))
        questions = self.service.project(principal, project)["payload"]["questions"]
        original = self.store.get(
            dataset, principal["tenant"], project, KIND, include_rows=False
        )["payload"]
        if original["sha256"] != expected_hash:
            raise Conflict(
                "Applicability hash mismatch; refresh the version being reviewed"
            )
        if (
            status not in ("pending", "approved", "rejected")
            or not isinstance(note, str)
            or not note.strip()
            or len(note) > 1000
        ):
            raise ValueError(
                "Provide an explicit review decision and note within 1,000 characters"
            )
        rows = self.store.dataset_rows(
            dataset, principal["tenant"], project, record_id=record_id, kind=KIND
        )
        if not rows:
            raise LookupError("Applicability record not found")
        row = rows[0]
        row.update(negative_for=negative_for, reason=reason)
        row["metadata"].update(
            reviewed=status == "approved",
            review_status=status,
            reviewer=principal["actor"],
            review_note=note,
        )
        validate_record(row, questions)
        return self.store.review_dataset(
            principal["tenant"],
            project,
            principal["actor"],
            dataset,
            row,
            expected_hash,
            note,
            kind=KIND,
        )

    def freeze(self, principal, project, config, frozen_binding=None):
        positive = self.store.get(
            config["snapshot"], principal["tenant"], project, "snapshot"
        )["payload"]
        if digest(positive["splits"]) != positive["sha256"]:
            raise ValueError("Positive snapshot integrity mismatch")
        ids = config["datasets"]
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 20
            or any(not isinstance(v, str) for v in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Select 1–20 unique applicability collection versions")
        from .semantic import profile_for

        profile = profile_for(principal["tenant"], project)
        if profile and profile["required"] and not config.get("semantic_audit"):
            raise ValueError(
                "Review a combined positive/applicability semantic audit before reservation"
            )
        selected = {
            "snapshot": config["snapshot"],
            "snapshot_sha256": positive["sha256"],
            "datasets": ids,
            "hashes": {
                identifier: self.store.get(
                    identifier, principal["tenant"], project, KIND, include_rows=False
                )["payload"]["sha256"]
                for identifier in ids
            },
            "source_sha256": source_fingerprint(),
        }
        if config.get("semantic_audit"):
            from .gate_semantic import binding_for, validate_binding

            selected["semantic_audit"] = config["semantic_audit"]
            binding = frozen_binding or binding_for(
                self.store,
                principal["tenant"],
                project,
                config["semantic_audit"],
                selected,
            )
            validate_binding(
                self.store, principal["tenant"], project, binding, selected
            )
            selected["semantic_contract"] = binding
        return selected

    def prepare(self, job, cancelled):
        tenant, project, actor, config = (
            job["tenant"],
            job["project"],
            job["actor"],
            job["config"],
        )
        principal = {"tenant": tenant, "actor": actor, "role": "admin"}
        frozen = self.freeze(
            principal,
            project,
            {
                "snapshot": config["snapshot"],
                "datasets": config["datasets"],
                **(
                    {"semantic_audit": config["semantic_audit"]}
                    if config.get("semantic_audit")
                    else {}
                ),
            },
            frozen_binding=config.get("semantic_contract"),
        )
        if frozen != config:
            raise ValueError(
                "Applicability reservation inputs differ from the frozen job"
            )
        positive = self.store.get(config["snapshot"], tenant, project, "snapshot")[
            "payload"
        ]
        from .semantic import verify_snapshot
        from .taxonomy import verify_snapshot as verify_taxonomy

        verify_snapshot(self.store, tenant, project, positive)
        verify_taxonomy(self.store, tenant, project, positive)
        questions = self.service.project(principal, project)["payload"]["questions"]
        rows, excluded = [], []
        for identifier in config["datasets"]:
            if cancelled.is_set():
                raise Conflict("Cancelled")
            self.store.heartbeat(
                job,
                "Checking authenticated applicability reviews and frozen positive roles",
            )
            for row in self.store.verify_dataset(
                identifier, tenant, project, kind=KIND
            ):
                validate_record(row, questions)
                if row["metadata"]["review_status"] == "rejected":
                    excluded.append(
                        {
                            "dataset": identifier,
                            "record_id": row["id"],
                            "reviewer": row["metadata"].get("reviewer"),
                            "note": row["metadata"].get("review_note"),
                            "dataset_sha256": config["hashes"][identifier],
                        }
                    )
                    continue
                validate_record(row, questions, require_review=True)
                if not row["metadata"].get("reviewer") or not row["metadata"].get(
                    "review_note"
                ):
                    raise ValueError(
                        "Approved applicability evidence lacks its authenticated review attribution"
                    )
                rows.append(row)
                if len(rows) > 20000:
                    raise ValueError(
                        "A local applicability study is bounded to 20,000 records"
                    )
        if not rows:
            raise ValueError("No approved applicability evidence remains")
        if len(rows) > 20000:
            raise ValueError("A local applicability study is bounded to 20,000 records")
        if config.get("semantic_contract"):
            from .gate_semantic import materialize

            _, splits = materialize(
                self.store,
                tenant,
                project,
                positive["splits"],
                rows,
                config["semantic_contract"],
            )
        else:
            splits = reserve(positive["splits"], rows)
        if cancelled.is_set():
            raise Conflict("Cancelled")
        payload = {
            "snapshot": config["snapshot"],
            "snapshot_sha256": positive["sha256"],
            "splits": splits,
            "sha256": digest(splits),
            "schema_sha256": positive["schema_sha256"],
            "datasets": config["datasets"],
            "dataset_hashes": config["hashes"],
            "excluded_records": excluded,
            "counts": {role: len(part) for role, part in splits.items()},
            "job_id": job["id"],
            "protocol": "coupled-applicability-v1",
            "source_sha256": config["source_sha256"],
        }
        if config.get("semantic_contract"):
            payload["semantic_contract"] = config["semantic_contract"]
        saved = self.store.add(
            "gate-snapshot", tenant, project, actor, payload, job=job
        )
        return {
            "gate_snapshot": saved["id"],
            "counts": payload["counts"],
            "excluded_count": len(excluded),
        }

    def verify_snapshot(self, tenant, project, identifier):
        value = self.store.get(identifier, tenant, project, "gate-snapshot")["payload"]
        positive = self.store.get(value["snapshot"], tenant, project, "snapshot")[
            "payload"
        ]
        questions = self.store.get(project, tenant, project, "project")["payload"][
            "questions"
        ]
        job = self.store.job(value["job_id"], tenant, project)
        if (
            job["kind"] != "gate-prepare"
            or job["config"]["snapshot"] != value["snapshot"]
            or job["config"]["snapshot_sha256"] != value["snapshot_sha256"]
            or job["config"]["datasets"] != value["datasets"]
            or job["config"]["hashes"] != value["dataset_hashes"]
            or job["config"]["source_sha256"] != value["source_sha256"]
            or job["config"].get("semantic_contract") != value.get("semantic_contract")
        ):
            raise ValueError(
                "Applicability reservation differs from its frozen preparation job"
            )
        if (
            value["protocol"] != "coupled-applicability-v1"
            or digest(value["splits"]) != value["sha256"]
            or positive["sha256"] != value["snapshot_sha256"]
            or digest(positive["splits"]) != positive["sha256"]
            or value["schema_sha256"] != digest(questions)
        ):
            raise ValueError("Coupled applicability snapshot integrity mismatch")
        validate_gate_splits(positive["splits"], value["splits"])
        original_rows = []
        for identifier in value["datasets"]:
            expected = value["dataset_hashes"][identifier]
            payload = self.store.get(
                identifier, tenant, project, KIND, include_rows=False
            )["payload"]
            if payload["sha256"] != expected:
                raise ValueError(
                    "Applicability collection hash differs from its reservation"
                )
            original_rows.extend(
                row
                for row in self.store.verify_dataset(
                    identifier, tenant, project, kind=KIND
                )
                if row["metadata"]["review_status"] != "rejected"
            )
            if len(original_rows) > 20000:
                raise ValueError("Applicability reservation exceeds its record bound")
        portable_positive = positive["splits"]
        if value.get("semantic_contract"):
            from .gate_semantic import materialize, validate_binding

            validate_binding(
                self.store,
                tenant,
                project,
                value["semantic_contract"],
                {
                    "snapshot": value["snapshot"],
                    "datasets": value["datasets"],
                    "hashes": value["dataset_hashes"],
                },
                current=False,
            )
            portable_positive, expected_negative = materialize(
                self.store,
                tenant,
                project,
                positive["splits"],
                original_rows,
                value["semantic_contract"],
            )
        else:
            expected_negative = reserve(positive["splits"], original_rows)
        if expected_negative != value["splits"] or value["counts"] != {
            role: len(part) for role, part in value["splits"].items()
        }:
            raise ValueError(
                "Applicability snapshot contents differ from its reviewed source reservation"
            )
        return {**value, "positive_splits": portable_positive}

    def archive(self, principal, project, identifier):
        self.service.require(principal, ("admin", "editor", "reviewer"))
        self.service.project(principal, project)
        value = self.verify_snapshot(principal["tenant"], project, identifier)
        job = self.store.job(value["job_id"], principal["tenant"], project)
        if (
            job["status"] != "succeeded"
            or job["result"].get("gate_snapshot") != identifier
        ):
            raise ValueError(
                "Only successfully completed applicability reservations can be exported"
            )
        positive = self.store.get(
            value["snapshot"], principal["tenant"], project, "snapshot"
        )["payload"]
        from .semantic import verify_snapshot
        from .taxonomy import verify_snapshot as verify_taxonomy

        verify_snapshot(self.store, principal["tenant"], project, positive)
        verify_taxonomy(self.store, principal["tenant"], project, positive)
        from decision_workbench.data import dumps

        buffer, files, size = io.BytesIO(), {}, 0
        with zipfile.ZipFile(
            buffer, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False
        ) as archive:
            for prefix, splits in (
                ("", value["positive_splits"]),
                ("gate_", value["splits"]),
            ):
                for role, rows in splits.items():
                    content = jsonl(rows).encode("utf-8")
                    size += len(content)
                    if size > 64 * 1024 * 1024:
                        raise ValueError(
                            "A portable applicability study is limited to 64 MiB of uncompressed records"
                        )
                    name = prefix + role + ".jsonl"
                    archive.writestr(name, content)
                    files[name] = {
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "rows": len(rows),
                        "bytes": len(content),
                    }
            archive.writestr(
                "manifest.json",
                dumps(
                    {
                        "format": "domain-applicability-inputs-v1",
                        "positive_snapshot": value["snapshot"],
                        "positive_sha256": value["snapshot_sha256"],
                        "gate_snapshot": identifier,
                        "gate_sha256": value["sha256"],
                        "schema_sha256": value["schema_sha256"],
                        "preparation_source_sha256": value["source_sha256"],
                        "files": files,
                    }
                ),
            )
        return buffer.getvalue()
