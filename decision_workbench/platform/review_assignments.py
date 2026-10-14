"""Owner-assigned annotation work with atomic review fencing and immutable history."""

import json
import re
import time
import uuid

from .paging import MAX_OFFSET
from .store import Conflict, digest, encode

KIND = "review-assignment"

FIELDS = {
    "root",
    "record_id",
    "revision",
    "assignee",
    "status",
    "dataset_id",
    "record_sha256",
    "previous_sha256",
    "event",
    "note",
    "updated_by",
}


def checked_payload(value):
    if not isinstance(value, dict) or set(value) != FIELDS | {"sha256"}:
        raise ValueError("Review assignment integrity check failed")
    body = {key: value[key] for key in FIELDS}
    if (
        digest(body) != value["sha256"]
        or type(value["revision"]) is not int
        or value["revision"] < 1
        or value["status"] not in ("assigned", "completed", "cancelled")
        or value["event"] not in ("assign", "cancel", "draft", "complete")
        or not isinstance(value["note"], str)
        or not value["note"].strip()
        or len(value["note"]) > 1000
        or (value["status"] == "cancelled") != (value["assignee"] is None)
        or value["status"]
        != {
            "assign": "assigned",
            "draft": "assigned",
            "complete": "completed",
            "cancel": "cancelled",
        }.get(value["event"])
        or any(
            not isinstance(value[key], str) or not 1 <= len(value[key]) <= 256
            for key in ("root", "record_id", "dataset_id", "updated_by")
        )
        or (
            value["assignee"] is not None
            and (
                not isinstance(value["assignee"], str)
                or not 1 <= len(value["assignee"]) <= 256
            )
        )
        or not isinstance(value["record_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["record_sha256"])
        or (value["revision"] == 1 and value["previous_sha256"] is not None)
        or (
            value["revision"] > 1
            and (
                not isinstance(value["previous_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["previous_sha256"])
            )
        )
    ):
        raise ValueError("Review assignment integrity check failed")
    return value


def current(db, tenant, project, root, record_id):
    row = db.execute(
        "SELECT * FROM entities WHERE kind=? AND tenant=? AND project=? AND json_extract(payload,'$.root')=? AND json_extract(payload,'$.record_id')=? ORDER BY json_extract(payload,'$.revision') DESC LIMIT 1",
        (KIND, tenant, project, root, record_id),
    ).fetchone()
    return (
        {**dict(row), "payload": checked_payload(json.loads(row["payload"]))}
        if row
        else None
    )


def append(db, store, tenant, project, actor, previous, values):
    payload = {
        **values,
        "revision": previous["payload"]["revision"] + 1 if previous else 1,
        "previous_sha256": previous["payload"]["sha256"] if previous else None,
        "updated_by": actor,
    }
    payload["sha256"] = digest(payload)
    checked_payload(payload)
    identifier = uuid.uuid4().hex
    created = time.time()
    db.execute(
        "INSERT INTO entities VALUES (?,?,?,?,?,?)",
        (identifier, KIND, tenant, project, encode(payload), created),
    )
    store.audit(
        db, tenant, project, actor, "review-assignment." + payload["event"], identifier
    )
    return {
        "id": identifier,
        "kind": KIND,
        "tenant": tenant,
        "project": project,
        "created": created,
        "payload": payload,
    }


def fence_review(db, tenant, project, root, record_id, actor, role, expected_revision):
    head = current(db, tenant, project, root, record_id)
    revision = head["payload"]["revision"] if head else 0
    active = head and head["payload"]["status"] == "assigned"
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValueError("Assignment revision must be a nonnegative integer")
    if active and actor != head["payload"]["assignee"] and role != "admin":
        raise PermissionError("This example is assigned to another reviewer")
    if (active or expected_revision is not None) and expected_revision != revision:
        raise Conflict("Review assignment changed; refresh the example before saving")
    return head if active else None


def finish_review(
    db, store, tenant, project, actor, assignment, dataset_id, selected, note
):
    if not assignment:
        return
    values = {
        key: assignment["payload"][key]
        for key in FIELDS - {"revision", "previous_sha256", "updated_by"}
    }
    complete = selected["metadata"]["review_status"] in ("approved", "rejected")
    values.update(
        dataset_id=dataset_id,
        record_sha256=digest(selected),
        note=note,
        event="complete" if complete else "draft",
        status="completed" if complete else "assigned",
    )
    append(db, store, tenant, project, actor, assignment, values)


class ReviewAssignments:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def dataset(self, principal, project, dataset):
        self.service.require(principal, ("admin", "editor", "reviewer"))
        self.service.project(principal, project)
        return self.store.get(
            dataset, principal["tenant"], project, "dataset", include_rows=False
        )["payload"]

    def assign(
        self, principal, project, dataset, targets, assignee, expected_hash, note
    ):
        self.service.require(principal, ("admin",))
        original = self.dataset(principal, project, dataset)
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise ValueError("Record an assignment reason up to 1,000 characters")
        if assignee is not None and (
            not isinstance(assignee, str)
            or not 1 <= len(assignee) <= 256
            or assignee != assignee.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in assignee)
        ):
            raise ValueError("Provide the reviewer's exact workspace actor ID")
        if not isinstance(targets, list) or not 1 <= len(targets) <= 100:
            raise ValueError("Assign 1–100 examples per request")
        ids = []
        for target in targets:
            if (
                not isinstance(target, dict)
                or set(target) != {"record_id", "expected_revision"}
                or not isinstance(target["record_id"], str)
                or not 1 <= len(target["record_id"]) <= 256
                or type(target["expected_revision"]) is not int
                or target["expected_revision"] < 0
            ):
                raise ValueError(
                    "Each target needs a record ID and nonnegative expected revision"
                )
            ids.append(target["record_id"])
        if len(set(ids)) != len(ids):
            raise ValueError("Assignment targets must be unique")
        if original["sha256"] != expected_hash:
            raise Conflict("Dataset changed; refresh its latest version")
        result = []
        with self.store.transaction() as db:
            head = db.execute(
                "SELECT version FROM dataset_heads WHERE root=?", (original["root"],)
            ).fetchone()
            if not head or head["version"] != dataset:
                raise Conflict("Dataset changed; refresh its latest version")
            for target in targets:
                rows = self.store.resolve_rows(
                    db, original, record_id=target["record_id"]
                )
                if not rows:
                    raise LookupError("Record not found")
                previous = current(
                    db,
                    principal["tenant"],
                    project,
                    original["root"],
                    target["record_id"],
                )
                revision = previous["payload"]["revision"] if previous else 0
                if revision != target["expected_revision"]:
                    raise Conflict("Assignment changed; refresh the work queue")
                if assignee is None and (
                    not previous or previous["payload"]["status"] != "assigned"
                ):
                    raise ValueError("Only assigned work can be cancelled")
                result.append(
                    append(
                        db,
                        self.store,
                        principal["tenant"],
                        project,
                        principal["actor"],
                        previous,
                        {
                            "root": original["root"],
                            "record_id": target["record_id"],
                            "assignee": assignee,
                            "status": (
                                "assigned" if assignee is not None else "cancelled"
                            ),
                            "event": "assign" if assignee is not None else "cancel",
                            "dataset_id": dataset,
                            "record_sha256": digest(rows[0]),
                            "note": note.strip(),
                        },
                    )
                )
        return {"items": result}

    def queue(
        self,
        principal,
        project,
        *,
        mine=False,
        status=None,
        dataset=None,
        record_id=None,
        limit=50,
        offset=0,
    ):
        self.service.require(principal, ("admin", "editor", "reviewer"))
        self.service.project(principal, project)
        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or type(offset) is not int
            or not 0 <= offset <= MAX_OFFSET
        ):
            raise ValueError("Invalid assignment page")
        if type(mine) is not bool or status not in (
            None,
            "assigned",
            "completed",
            "cancelled",
        ):
            raise ValueError("Invalid assignment filter")
        original = self.dataset(principal, project, dataset) if dataset else None
        where = [
            "e.kind=?",
            "e.tenant=?",
            "e.project=?",
            "NOT EXISTS (SELECT 1 FROM entities n WHERE n.kind=e.kind AND n.tenant=e.tenant AND n.project=e.project AND json_extract(n.payload,'$.root')=json_extract(e.payload,'$.root') AND json_extract(n.payload,'$.record_id')=json_extract(e.payload,'$.record_id') AND json_extract(n.payload,'$.revision')>json_extract(e.payload,'$.revision'))",
        ]
        params = [KIND, principal["tenant"], project]
        for key, value in (
            ("assignee", principal["actor"] if mine else None),
            ("status", status),
            ("root", original["root"] if original else None),
            ("record_id", record_id),
        ):
            if value is not None:
                where.append("json_extract(e.payload,'$." + key + "')=?")
                params.append(value)
        if original:
            where.append(
                "EXISTS (SELECT 1 FROM dataset_members m WHERE m.root=? AND m.record_id=json_extract(e.payload,'$.record_id') AND m.added_sequence<=?)"
            )
            params.extend((original["root"], original["sequence"]))
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT e.* FROM entities e WHERE "
                + " AND ".join(where)
                + " ORDER BY e.created DESC,e.id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                payload = checked_payload(json.loads(row["payload"]))
                head = db.execute(
                    "SELECT h.version FROM dataset_heads h JOIN entities d ON d.id=h.version WHERE h.root=? AND d.tenant=? AND d.project=? AND d.kind='dataset'",
                    (payload["root"], principal["tenant"], project),
                ).fetchone()
                if not head:
                    raise ValueError(
                        "Review assignment collection reference is invalid"
                    )
                items.append(
                    {**dict(row), "payload": payload, "dataset_head": head["version"]}
                )
        return {"items": items, "limit": limit, "offset": offset}

    def history(self, principal, project, dataset, record_id, limit=50, offset=0):
        original = self.dataset(principal, project, dataset)
        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or type(offset) is not int
            or not 0 <= offset <= MAX_OFFSET
        ):
            raise ValueError("Invalid assignment history page")
        with self.store.transaction() as db:
            if not self.store.resolve_rows(db, original, record_id=record_id):
                raise LookupError("Record not found")
            rows = db.execute(
                "SELECT * FROM entities WHERE kind=? AND tenant=? AND project=? AND json_extract(payload,'$.root')=? AND json_extract(payload,'$.record_id')=? ORDER BY json_extract(payload,'$.revision') DESC LIMIT ? OFFSET ?",
                (
                    KIND,
                    principal["tenant"],
                    project,
                    original["root"],
                    record_id,
                    limit,
                    offset,
                ),
            ).fetchall()
        return {
            "items": [
                {**dict(row), "payload": checked_payload(json.loads(row["payload"]))}
                for row in rows
            ],
            "limit": limit,
            "offset": offset,
        }


def verify_history(db, entities):
    """Validate assignment chains and their exact reviewed dataset/record references."""
    groups = {}
    by_id = {entity["id"]: entity for entity in entities}
    for entity in entities:
        if entity["kind"] != KIND:
            continue
        payload = checked_payload(json.loads(entity["payload"]))
        dataset = by_id.get(payload["dataset_id"])
        if (
            not dataset
            or dataset["kind"] != "dataset"
            or (dataset["tenant"], dataset["project"])
            != (entity["tenant"], entity["project"])
        ):
            raise ValueError("Review assignment dataset reference is invalid")
        original = json.loads(dataset["payload"])
        from .dataset_storage import DatasetStorage

        rows = DatasetStorage.resolve_rows(db, original, record_id=payload["record_id"])
        if (
            original["root"] != payload["root"]
            or not rows
            or digest(rows[0]) != payload["record_sha256"]
        ):
            raise ValueError("Review assignment record reference is invalid")
        if payload["event"] in ("draft", "complete") and (
            rows[0]["metadata"].get("reviewer") != payload["updated_by"]
            or rows[0]["metadata"].get("review_status")
            not in (
                ("pending",)
                if payload["event"] == "draft"
                else ("approved", "rejected")
            )
        ):
            raise ValueError("Review assignment completion reference is invalid")
        key = entity["tenant"], entity["project"], payload["root"], payload["record_id"]
        groups.setdefault(key, []).append(payload)
    for values in groups.values():
        previous = None
        prior = None
        for revision, value in enumerate(
            sorted(values, key=lambda v: v["revision"]), 1
        ):
            if value["revision"] != revision or value["previous_sha256"] != previous:
                raise ValueError("Review assignment history integrity check failed")
            if revision == 1 and value["event"] != "assign":
                raise ValueError("Review assignment history must start with assignment")
            if value["event"] != "assign" and (
                prior is None or prior["status"] != "assigned"
            ):
                raise ValueError("Review assignment history has an invalid transition")
            if (
                value["event"] in ("draft", "complete")
                and value["assignee"] != prior["assignee"]
            ):
                raise ValueError(
                    "Review assignment reviewer changed without reassignment"
                )
            previous = value["sha256"]
            prior = value
    return sum(map(len, groups.values()))
