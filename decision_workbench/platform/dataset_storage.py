"""Content-addressed dataset rows and linear, immutable membership and review revisions."""

import hashlib
import json
import time
import uuid


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class DatasetStorage:
    def setup_datasets(self, db, migrate=False):
        db.execute(
            "CREATE TABLE IF NOT EXISTS record_contents (hash TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS dataset_members (root TEXT NOT NULL, ordinal INTEGER NOT NULL, record_id TEXT NOT NULL, content_hash TEXT NOT NULL, PRIMARY KEY(root,record_id), UNIQUE(root,ordinal))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS dataset_versions (id TEXT PRIMARY KEY, root TEXT NOT NULL, sequence INTEGER NOT NULL, record_id TEXT, content_hash TEXT, parent TEXT, UNIQUE(root,sequence))"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS dataset_changes ON dataset_versions(root,record_id,sequence DESC)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS dataset_heads (root TEXT PRIMARY KEY, version TEXT NOT NULL)"
        )
        if "added_sequence" not in {
            r["name"] for r in db.execute("PRAGMA table_info(dataset_members)")
        }:
            db.execute(
                "ALTER TABLE dataset_members ADD COLUMN added_sequence INTEGER NOT NULL DEFAULT 0"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS dataset_member_sequence ON dataset_members(root,added_sequence,ordinal)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS dataset_appends (version TEXT PRIMARY KEY, manifest TEXT NOT NULL)"
        )
        # Existing v1 corpora become independent immutable roots, preserving their hashes.
        if not migrate:
            return
        legacy = db.execute("SELECT * FROM entities WHERE kind='dataset'").fetchall()
        for entity in legacy:
            payload = json.loads(entity["payload"])
            if "rows" not in payload:
                continue
            rows = payload.pop("rows")
            self.insert_root(db, entity["id"], rows)
            payload.update(
                root=entity["id"],
                sequence=0,
                row_count=len(rows),
                reviewed_count=sum(bool(r["metadata"]["reviewed"]) for r in rows),
                hash_kind="rows-v1",
            )
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (encode(payload), entity["id"]),
            )

    @staticmethod
    def insert_root(db, root, rows):
        if not rows or len({r["id"] for r in rows}) != len(rows):
            raise ValueError(
                "A dataset needs nonempty rows with unique record identifiers"
            )
        manifest = []
        for ordinal, row in enumerate(rows):
            checksum = digest(row)
            db.execute(
                "INSERT OR IGNORE INTO record_contents VALUES (?,?)",
                (checksum, encode(row)),
            )
            db.execute(
                "INSERT INTO dataset_members(root,ordinal,record_id,content_hash) VALUES (?,?,?,?)",
                (root, ordinal, row["id"], checksum),
            )
            manifest.append(
                {"ordinal": ordinal, "record_id": row["id"], "content_hash": checksum}
            )
        db.execute(
            "INSERT INTO dataset_versions VALUES (?,?,0,NULL,NULL,NULL)", (root, root)
        )
        db.execute("INSERT INTO dataset_heads VALUES (?,?)", (root, root))
        return digest(manifest)

    def add_dataset(
        self,
        tenant,
        project,
        actor,
        rows,
        provenance,
        job=None,
        *,
        kind="dataset",
        finalize=None,
    ):
        self.dataset_kind(kind)
        entity_id = uuid.uuid4().hex
        with self.transaction() as db:
            if job:
                self._assert_owner(db, job)
            checksum = self.insert_root(db, entity_id, rows)
            payload = {
                "sha256": checksum,
                "hash_kind": "manifest-v2",
                "provenance": provenance,
                "root": entity_id,
                "sequence": 0,
                "row_count": len(rows),
                "reviewed_count": sum(bool(r["metadata"]["reviewed"]) for r in rows),
                "rejected_count": sum(
                    r["metadata"].get("review_status") == "rejected" for r in rows
                ),
            }
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (entity_id, kind, tenant, project, encode(payload), time.time()),
            )
            self.audit(db, tenant, project, actor, "create." + kind, entity_id)
            if finalize:
                finalize(db, entity_id, payload)
            if job:
                self._assert_owner(db, job)
        return self.get(entity_id, tenant, project, kind, include_rows=False)

    @staticmethod
    def dataset_kind(kind):
        if kind not in ("dataset", "gate-dataset"):
            raise ValueError("Unsupported immutable collection kind")

    @staticmethod
    def resolve_rows(db, payload, limit=None, offset=0, record_id=None):
        from .paging import validate_offset, validate_page

        validate_offset(offset)
        if limit is not None:
            validate_page(limit, offset, 20000)
        filters = " AND m.record_id=?" if record_id is not None else ""
        params = [
            payload["root"],
            payload["sequence"],
            payload["root"],
            payload["sequence"],
        ]
        if record_id is not None:
            params.append(record_id)
        paging = " LIMIT ? OFFSET ?" if limit is not None else ""
        if limit is not None:
            params.extend((limit, offset))
        refs = db.execute(
            "SELECT m.ordinal,m.record_id,coalesce((SELECT v.content_hash FROM dataset_versions v WHERE v.root=? AND v.record_id=m.record_id AND v.sequence<=? ORDER BY v.sequence DESC LIMIT 1),m.content_hash) AS hash FROM dataset_members m WHERE m.root=? AND m.added_sequence<=?"
            + filters
            + " ORDER BY m.ordinal"
            + paging,
            params,
        ).fetchall()
        result = []
        for ref in refs:
            content = db.execute(
                "SELECT payload FROM record_contents WHERE hash=?", (ref["hash"],)
            ).fetchone()
            if content is None:
                raise ValueError("Dataset record integrity check failed")
            row = json.loads(content["payload"])
            if digest(row) != ref["hash"] or row["id"] != ref["record_id"]:
                raise ValueError("Dataset record integrity check failed")
            result.append(row)
        return result

    def dataset_rows(
        self,
        entity_id,
        tenant,
        project,
        limit=None,
        offset=0,
        record_id=None,
        *,
        kind="dataset",
    ):
        self.dataset_kind(kind)
        payload = self.get(entity_id, tenant, project, kind, include_rows=False)[
            "payload"
        ]
        with self.transaction() as db:
            return self.resolve_rows(db, payload, limit, offset, record_id)

    def verify_dataset(self, entity_id, tenant, project, *, kind="dataset"):
        self.dataset_kind(kind)
        payload = self.get(entity_id, tenant, project, kind, include_rows=False)[
            "payload"
        ]
        with self.transaction() as db:
            root_row = db.execute(
                "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind=?",
                (payload["root"], tenant, project, kind),
            ).fetchone()
            if root_row is None:
                raise ValueError("Dataset lineage integrity check failed")
            root = json.loads(root_row["payload"])
            if root["hash_kind"] == "rows-v1":
                checksum = digest(self.resolve_rows(db, root))
            else:
                manifest = [
                    dict(r)
                    for r in db.execute(
                        "SELECT ordinal,record_id,content_hash FROM dataset_members WHERE root=? AND added_sequence=0 ORDER BY ordinal",
                        (payload["root"],),
                    )
                ]
                checksum = digest(manifest)
            if checksum != root["sha256"]:
                raise ValueError("Dataset manifest integrity check failed")
            changes = db.execute(
                "SELECT v.*,e.payload FROM dataset_versions v JOIN entities e ON e.id=v.id WHERE v.root=? AND v.sequence>0 AND v.sequence<=? AND e.tenant=? AND e.project=? AND e.kind=? ORDER BY v.sequence",
                (payload["root"], payload["sequence"], tenant, project, kind),
            ).fetchall()
            expected_count = root["row_count"]
            parent = payload["root"]
            for sequence, change in enumerate(changes, start=1):
                version = json.loads(change["payload"])
                additions = [
                    dict(r)
                    for r in db.execute(
                        "SELECT ordinal,record_id,content_hash FROM dataset_members WHERE root=? AND added_sequence=? ORDER BY ordinal",
                        (payload["root"], sequence),
                    )
                ]
                append = db.execute(
                    "SELECT manifest FROM dataset_appends WHERE version=?",
                    (change["id"],),
                ).fetchone()
                if append:
                    if (
                        not additions
                        or json.loads(append["manifest"]) != additions
                        or change["record_id"] is not None
                        or change["content_hash"] is not None
                        or version["hash_kind"] != "append-v3"
                    ):
                        raise ValueError("Dataset append integrity check failed")
                    if [m["ordinal"] for m in additions] != list(
                        range(expected_count, expected_count + len(additions))
                    ):
                        raise ValueError(
                            "Dataset append ordering integrity check failed"
                        )
                    checksum = digest(
                        {
                            "parent_sha256": checksum,
                            "operation": "append",
                            "members": additions,
                        }
                    )
                    expected_count += len(additions)
                else:
                    if (
                        additions
                        or not db.execute(
                            "SELECT 1 FROM dataset_members WHERE root=? AND record_id=? AND added_sequence<?",
                            (payload["root"], change["record_id"], sequence),
                        ).fetchone()
                    ):
                        raise ValueError(
                            "Dataset review membership integrity check failed"
                        )
                    checksum = digest(
                        {
                            "parent_sha256": checksum,
                            "record_id": change["record_id"],
                            "content_sha256": change["content_hash"],
                        }
                    )
                if (
                    version["root"] != payload["root"]
                    or version["sequence"] != sequence
                    or version["row_count"] != expected_count
                ):
                    raise ValueError("Dataset revision metadata integrity check failed")
                if (
                    change["sequence"] != sequence
                    or change["parent"] != parent
                    or version["sha256"] != checksum
                ):
                    raise ValueError("Dataset revision integrity check failed")
                parent = change["id"]
            if parent != entity_id or checksum != payload["sha256"]:
                raise ValueError("Dataset revision integrity check failed")
            resolved = self.resolve_rows(db, payload)
            if len(resolved) != expected_count:
                raise ValueError("Dataset membership integrity check failed")
            return resolved

    def append_dataset(
        self,
        tenant,
        project,
        actor,
        dataset,
        rows,
        expected_hash,
        provenance,
        *,
        kind="dataset",
    ):
        from .store import Conflict

        self.dataset_kind(kind)
        original = self.get(dataset, tenant, project, kind, include_rows=False)[
            "payload"
        ]
        if original["sha256"] != expected_hash:
            raise Conflict("Dataset hash mismatch; refresh the collection version")
        if not rows or len({r["id"] for r in rows}) != len(rows):
            raise ValueError("Append needs nonempty rows with unique identifiers")
        entity_id = uuid.uuid4().hex
        with self.transaction() as db:
            head = db.execute(
                "SELECT version FROM dataset_heads WHERE root=?", (original["root"],)
            ).fetchone()
            if not head or head["version"] != dataset:
                raise Conflict(
                    "Another user advanced this collection; refresh its latest version"
                )
            if original["row_count"] + len(rows) > 20000:
                raise ValueError("A local collection is limited to 20,000 rows")
            sequence = original["sequence"] + 1
            manifest = []
            for ordinal, row in enumerate(rows, start=original["row_count"]):
                if db.execute(
                    "SELECT 1 FROM dataset_members WHERE root=? AND record_id=?",
                    (original["root"], row["id"]),
                ).fetchone():
                    raise ValueError(
                        "A record identifier already exists in this collection: "
                        + row["id"]
                    )
                checksum = digest(row)
                db.execute(
                    "INSERT OR IGNORE INTO record_contents VALUES (?,?)",
                    (checksum, encode(row)),
                )
                db.execute(
                    "INSERT INTO dataset_members VALUES (?,?,?,?,?)",
                    (original["root"], ordinal, row["id"], checksum, sequence),
                )
                manifest.append(
                    {
                        "ordinal": ordinal,
                        "record_id": row["id"],
                        "content_hash": checksum,
                    }
                )
            checksum = digest(
                {
                    "parent_sha256": expected_hash,
                    "operation": "append",
                    "members": manifest,
                }
            )
            payload = {
                **original,
                "sequence": sequence,
                "sha256": checksum,
                "hash_kind": "append-v3",
                "row_count": original["row_count"] + len(rows),
                "reviewed_count": original["reviewed_count"]
                + sum(bool(r["metadata"]["reviewed"]) for r in rows),
                "rejected_count": original.get("rejected_count", 0)
                + sum(r["metadata"].get("review_status") == "rejected" for r in rows),
                "provenance": {
                    "parent": dataset,
                    "append": provenance,
                    "added_count": len(rows),
                },
            }
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (entity_id, kind, tenant, project, encode(payload), time.time()),
            )
            db.execute(
                "INSERT INTO dataset_versions VALUES (?,?,?,NULL,NULL,?)",
                (entity_id, original["root"], sequence, dataset),
            )
            db.execute(
                "INSERT INTO dataset_appends VALUES (?,?)",
                (entity_id, encode(manifest)),
            )
            db.execute(
                "UPDATE dataset_heads SET version=? WHERE root=?",
                (entity_id, original["root"]),
            )
            self.audit(db, tenant, project, actor, "append." + kind, entity_id)
        return self.get(entity_id, tenant, project, kind, include_rows=False)

    def review_dataset(
        self,
        tenant,
        project,
        actor,
        dataset,
        selected,
        expected_hash,
        note,
        *,
        kind="dataset",
        assignment_revision=None,
        reviewer_role=None,
    ):
        from .store import Conflict

        self.dataset_kind(kind)
        original = self.get(dataset, tenant, project, kind, include_rows=False)[
            "payload"
        ]
        if original["sha256"] != expected_hash:
            raise Conflict("Dataset hash mismatch; refresh the version being reviewed")
        entity_id = uuid.uuid4().hex
        content_hash = digest(selected)
        with self.transaction() as db:
            head = db.execute(
                "SELECT version FROM dataset_heads WHERE root=?", (original["root"],)
            ).fetchone()
            if not head or head["version"] != dataset:
                raise Conflict(
                    "Another reviewer advanced this dataset; refresh its latest version"
                )
            before = self.resolve_rows(db, original, record_id=selected["id"])
            if not before:
                raise LookupError("Record not found")
            from .review_assignments import fence_review, finish_review

            assignment = (
                fence_review(
                    db,
                    tenant,
                    project,
                    original["root"],
                    selected["id"],
                    actor,
                    reviewer_role,
                    assignment_revision,
                )
                if kind == "dataset"
                else None
            )
            sequence = original["sequence"] + 1
            checksum = digest(
                {
                    "parent_sha256": expected_hash,
                    "record_id": selected["id"],
                    "content_sha256": content_hash,
                }
            )
            payload = {
                **original,
                "sequence": sequence,
                "sha256": checksum,
                "hash_kind": "revision-v2",
                "reviewed_count": original["reviewed_count"]
                + int(bool(selected["metadata"]["reviewed"]))
                - int(bool(before[0]["metadata"]["reviewed"])),
                "rejected_count": original.get("rejected_count", 0)
                + int(selected["metadata"].get("review_status") == "rejected")
                - int(before[0]["metadata"].get("review_status") == "rejected"),
                "provenance": {
                    "parent": dataset,
                    "reviewed_record": selected["id"],
                    "note": note,
                },
            }
            db.execute(
                "INSERT OR IGNORE INTO record_contents VALUES (?,?)",
                (content_hash, encode(selected)),
            )
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (entity_id, kind, tenant, project, encode(payload), time.time()),
            )
            db.execute(
                "INSERT INTO dataset_versions VALUES (?,?,?,?,?,?)",
                (
                    entity_id,
                    original["root"],
                    sequence,
                    selected["id"],
                    content_hash,
                    dataset,
                ),
            )
            db.execute(
                "UPDATE dataset_heads SET version=? WHERE root=?",
                (entity_id, original["root"]),
            )
            self.audit(
                db, tenant, project, actor, "review." + kind + ":" + note, entity_id
            )
            finish_review(
                db, self, tenant, project, actor, assignment, entity_id, selected, note
            )
        return self.get(entity_id, tenant, project, kind, include_rows=False)

    def dataset_head(self, entity_id, tenant, project, *, kind="dataset"):
        self.dataset_kind(kind)
        payload = self.get(entity_id, tenant, project, kind, include_rows=False)[
            "payload"
        ]
        with self.transaction() as db:
            head = db.execute(
                "SELECT version FROM dataset_heads WHERE root=?", (payload["root"],)
            ).fetchone()
        return head["version"]
