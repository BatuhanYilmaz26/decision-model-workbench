"""SQLite local store with immutable entities, audit, and fenced job ownership."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .dataset_storage import DatasetStorage
from .operations import OperationsStorage
from .paging import validate_page

WORKSPACE_SCHEMA_VERSION = 9


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store(DatasetStorage, OperationsStorage):
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.database = self.folder / "workspace.sqlite3"
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            current = db.execute("SELECT max(version) FROM schema_version").fetchone()[
                0
            ]
            if current is not None and current not in range(
                1, WORKSPACE_SCHEMA_VERSION + 1
            ):
                raise RuntimeError(
                    "Unsupported workspace schema version; restore a compatible application"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, kind TEXT NOT NULL, tenant TEXT NOT NULL, project TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS entity_scope ON entities(tenant,project,kind,created)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, created REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL, request_hash TEXT NOT NULL, request_key TEXT NOT NULL, status TEXT NOT NULL, token TEXT, lease REAL, progress TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(tenant,project,request_key))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS queued_jobs ON jobs(status,created)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS active_models (tenant TEXT NOT NULL, project TEXT NOT NULL, model_id TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(tenant,project))"
            )
            self.setup_datasets(db, migrate=current == 1)
            db.execute(
                "CREATE TABLE IF NOT EXISTS release_policy_heads (tenant TEXT NOT NULL, project TEXT NOT NULL, policy_id TEXT NOT NULL, PRIMARY KEY(tenant,project))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS provider_call_budget ON entities(tenant,json_extract(payload,'$.profile_id'),created) WHERE kind='provider-call'"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS provider_reconciliation_call ON entities(json_extract(payload,'$.call_id')) WHERE kind='provider-call-reconciliation'"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS cloud_event_revision ON entities(tenant,project,json_extract(payload,'$.run_id'),json_extract(payload,'$.revision')) WHERE kind='cloud-event'"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS cloud_run_budget ON entities(json_extract(payload,'$.profile.account_id'),json_extract(payload,'$.profile.region'),created) WHERE kind='cloud-run'"
            )
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (3)")
            self.setup_operations(db)
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (4)")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS semantic_review_revision ON entities(tenant,project,json_extract(payload,'$.audit_id'),json_extract(payload,'$.pair_id'),json_extract(payload,'$.revision')) WHERE kind='semantic-review'"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS taxonomy_review_revision ON entities(tenant,project,json_extract(payload,'$.proposal_id'),json_extract(payload,'$.candidate_id'),json_extract(payload,'$.revision')) WHERE kind='taxonomy-review'"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS annotation_assignment_revision ON entities(tenant,project,json_extract(payload,'$.root'),json_extract(payload,'$.record_id'),json_extract(payload,'$.revision')) WHERE kind='review-assignment'"
            )
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (5)")
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (6)")
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (7)")
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (8)")
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (9)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS generation_budget_lookup ON entities(tenant,json_extract(payload,'$.profile.base_url'),created) WHERE kind='generation-plan'"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS generation_record_lookup ON entities(tenant,project,kind,json_extract(payload,'$.plan_id')) WHERE kind IN ('generation-process','generation-response','generation-result','generation-settlement')"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS successor_request_lookup ON entities(tenant,json_extract(payload,'$.parent_project'),json_extract(payload,'$.successor.request_key_sha256')) WHERE kind='project'"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS audit_event_scope ON events(tenant,project,id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS audit_export_request_lookup ON entities(tenant,project,json_extract(payload,'$.request_key_sha256')) WHERE kind='audit-export'"
            )

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def audit(db, tenant, project, actor, action, target):
        db.execute(
            "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
            (tenant, project, actor, action, target, time.time()),
        )

    def add(
        self,
        kind,
        tenant,
        project,
        actor,
        payload,
        entity_id=None,
        job=None,
        validate_db=None,
    ):
        entity_id = entity_id or uuid.uuid4().hex
        with self.transaction() as db:
            if job:
                self._assert_owner(db, job)
            if validate_db:
                validate_db(db)
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (entity_id, kind, tenant, project, encode(payload), time.time()),
            )
            self.audit(db, tenant, project, actor, "create." + kind, entity_id)
        return self.get(entity_id, tenant, project, kind)

    def get(self, entity_id, tenant, project=None, kind=None, include_rows=True):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM entities WHERE id=? AND tenant=?", (entity_id, tenant)
            ).fetchone()
        if (
            row is None
            or (project is not None and row["project"] != project)
            or (kind is not None and row["kind"] != kind)
        ):
            raise LookupError("Resource not found")
        result = {**dict(row), "payload": json.loads(row["payload"])}
        if include_rows and row["kind"] in ("dataset", "gate-dataset"):
            result["payload"]["rows"] = self.dataset_rows(
                entity_id, tenant, row["project"], kind=row["kind"]
            )
        return result

    def list(self, tenant, project=None, kind=None, limit=100, offset=0):
        validate_page(limit, offset)
        where, params = ["tenant=?"], [tenant]
        for name, value in [("project", project), ("kind", kind)]:
            if value is not None:
                where.append(name + "=?")
                params.append(value)
        params.extend((limit, offset))
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM entities WHERE "
                + " AND ".join(where)
                + " ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        result = [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
        for entity in result:
            if entity["kind"] in ("snapshot", "gate-snapshot"):
                entity["payload"].pop("splits", None)
            if entity["kind"] == "semantic-audit":
                entity["payload"].pop("row_hashes", None)
                report = entity["payload"]["report"]
                report["candidate_pairs"] = len(report.pop("pairs"))
        return result

    def release_policy(self, tenant, project):
        with self.transaction() as db:
            row = db.execute(
                "SELECT policy_id FROM release_policy_heads WHERE tenant=? AND project=?",
                (tenant, project),
            ).fetchone()
            locked = (
                db.execute(
                    "SELECT 1 FROM jobs WHERE tenant=? AND project=? AND kind IN ('train','remote-study','hosted-evaluate') LIMIT 1",
                    (tenant, project),
                ).fetchone()
                is not None
            )
        return {
            "policy": (
                self.get(row["policy_id"], tenant, project, "release-policy")
                if row
                else None
            ),
            "locked": locked,
        }

    def set_release_policy(self, tenant, project, actor, criteria, expected_id):
        policy_id = uuid.uuid4().hex
        payload = {"criteria": criteria, "sha256": digest(criteria)}
        with self.transaction() as db:
            if db.execute(
                "SELECT 1 FROM jobs WHERE tenant=? AND project=? AND kind IN ('train','remote-study','hosted-evaluate') LIMIT 1",
                (tenant, project),
            ).fetchone():
                raise Conflict(
                    "Acceptance criteria were frozen by the first training submission; create a successor project to change them"
                )
            head = db.execute(
                "SELECT policy_id FROM release_policy_heads WHERE tenant=? AND project=?",
                (tenant, project),
            ).fetchone()
            if (head["policy_id"] if head else None) != expected_id:
                raise Conflict(
                    "Another owner changed the acceptance criteria; refresh before saving"
                )
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    policy_id,
                    "release-policy",
                    tenant,
                    project,
                    encode(payload),
                    time.time(),
                ),
            )
            db.execute(
                "INSERT INTO release_policy_heads VALUES (?,?,?) ON CONFLICT(tenant,project) DO UPDATE SET policy_id=excluded.policy_id",
                (tenant, project, policy_id),
            )
            self.audit(db, tenant, project, actor, "define.release-policy", policy_id)
        return self.release_policy(tenant, project)

    def events(self, tenant, project):
        with self.transaction() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM events WHERE tenant=? AND project=? ORDER BY id DESC LIMIT 500",
                    (tenant, project),
                )
            ]

    @staticmethod
    def decode_job(row):
        return {
            **dict(row),
            "config": json.loads(row["config"]),
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def submit(
        self,
        tenant,
        project,
        actor,
        kind,
        config,
        request_key,
        validate_db=None,
        *,
        authorize=None,
    ):
        if not isinstance(request_key, str) or not 8 <= len(request_key) <= 128:
            raise ValueError("Use an 8–128 character idempotency key")
        request_hash = digest({"kind": kind, "config": config})
        now = time.time()
        with self.transaction() as db:
            if authorize:
                authorize()
            row = db.execute(
                "SELECT * FROM jobs WHERE tenant=? AND project=? AND request_key=?",
                (tenant, project, request_key),
            ).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise Conflict(
                        "Idempotency key already belongs to a different request"
                    )
                if authorize:
                    authorize()
                return self.decode_job(row)
            pending = db.execute(
                "SELECT count(*) FROM jobs WHERE tenant=? AND status IN ('queued','running','cancel_requested')",
                (tenant,),
            ).fetchone()[0]
            if pending >= 4:
                raise Conflict("Workspace already has four pending jobs")
            if validate_db:
                validate_db(db)
            if kind in ("train", "remote-study", "hosted-evaluate"):
                policy_head = db.execute(
                    "SELECT policy_id FROM release_policy_heads WHERE tenant=? AND project=?",
                    (tenant, project),
                ).fetchone()
                if not policy_head:
                    raise Conflict(
                        "An owner must set acceptance criteria before training or test inspection"
                    )
                policy = db.execute(
                    "SELECT payload FROM entities WHERE id=?",
                    (policy_head["policy_id"],),
                ).fetchone()
                config = {
                    **config,
                    "release_policy_id": policy_head["policy_id"],
                    "release_policy_sha256": json.loads(policy["payload"])["sha256"],
                }
                error_policy = json.loads(policy["payload"])["criteria"].get(
                    "error_policy"
                )
                if error_policy is not None:
                    config["error_policy"] = error_policy
            job_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs(id,tenant,project,actor,kind,config,request_hash,request_key,status,progress,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    tenant,
                    project,
                    actor,
                    kind,
                    encode(config),
                    request_hash,
                    request_key,
                    "queued",
                    "Waiting for worker",
                    now,
                    now,
                ),
            )
            self.audit(db, tenant, project, actor, "submit." + kind, job_id)
            if authorize:
                authorize()
        return self.job(job_id, tenant, project)

    def job(self, job_id, tenant, project):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND tenant=? AND project=?",
                (job_id, tenant, project),
            ).fetchone()
        if not row:
            raise LookupError("Job not found")
        return self.decode_job(row)

    def jobs(self, tenant, project):
        with self.transaction() as db:
            return [
                self.decode_job(r)
                for r in db.execute(
                    "SELECT * FROM jobs WHERE tenant=? AND project=? ORDER BY created DESC",
                    (tenant, project),
                )
            ]

    def claim(self, lease_seconds=30):
        now = time.time()
        with self.transaction() as db:
            expired = db.execute(
                "SELECT * FROM jobs WHERE status IN ('running','cancel_requested') AND lease<?",
                (now,),
            ).fetchall()
            for r in expired:
                status = (
                    "cancelled" if r["status"] == "cancel_requested" else "interrupted"
                )
                db.execute(
                    "UPDATE jobs SET status=?, token=NULL, lease=NULL, error=?, updated=? WHERE id=?",
                    (
                        status,
                        "Worker lease expired; submit a new run after inspecting the interruption",
                        now,
                        r["id"],
                    ),
                )
                self.audit(db, r["tenant"], r["project"], "worker", status, r["id"])
                if status == "interrupted":
                    self._condition(
                        db,
                        r["tenant"],
                        r["project"],
                        "job_interrupted",
                        r["id"],
                        True,
                        now,
                    )
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE jobs SET status='running', token=?, lease=?, progress='Worker started', updated=? WHERE id=?",
                (token, now + lease_seconds, now, row["id"]),
            )
            result = self.decode_job(row)
            result.update(token=token, status="running")
            return result

    @staticmethod
    def _assert_owner(db, job):
        row = db.execute(
            "SELECT status,token,lease FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()
        if (
            not row
            or row["token"] != job["token"]
            or row["status"] != "running"
            or row["lease"] < time.time()
        ):
            raise Conflict("Job cancelled or ownership expired")

    def heartbeat(self, job, progress=None):
        with self.transaction() as db:
            self._assert_owner(db, job)
            db.execute(
                "UPDATE jobs SET lease=?, updated=?, progress=coalesce(?,progress) WHERE id=?",
                (time.time() + 30, time.time(), progress, job["id"]),
            )

    def finish(self, job, result=None, error=None):
        with self.transaction() as db:
            self._assert_owner(db, job)
            status = "failed" if error else "succeeded"
            db.execute(
                "UPDATE jobs SET status=?, result=?, error=?, token=NULL, lease=NULL, progress=?, updated=? WHERE id=?",
                (
                    status,
                    encode(result) if result else None,
                    error,
                    status.capitalize(),
                    time.time(),
                    job["id"],
                ),
            )
            if error:
                self._condition(
                    db,
                    job["tenant"],
                    job["project"],
                    "job_failed",
                    job["id"],
                    True,
                    time.time(),
                )
            self.audit(
                db,
                job["tenant"],
                job["project"],
                job["actor"],
                status + "." + job["kind"],
                job["id"],
            )

    def cancel(self, job_id, tenant, project, actor):
        with self.transaction() as db:
            row = db.execute(
                "SELECT status FROM jobs WHERE id=? AND tenant=? AND project=?",
                (job_id, tenant, project),
            ).fetchone()
            if not row:
                raise LookupError("Job not found")
            if row["status"] == "queued":
                status = "cancelled"
            elif row["status"] in ("running", "cancel_requested"):
                status = "cancel_requested"
            else:
                raise Conflict("Job is already terminal")
            db.execute(
                "UPDATE jobs SET status=?, updated=? WHERE id=?",
                (status, time.time(), job_id),
            )
            self.audit(db, tenant, project, actor, "cancel.job", job_id)
        return self.job(job_id, tenant, project)

    def acknowledge_cancel(self, job):
        with self.transaction() as db:
            db.execute(
                "UPDATE jobs SET status='cancelled',token=NULL,lease=NULL,updated=? WHERE id=? AND token=? AND status='cancel_requested'",
                (time.time(), job["id"], job["token"]),
            )

    def has_model_training(self):
        with self.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM jobs WHERE kind IN ('train','remote-import') AND status IN ('queued','running','cancel_requested') AND json_extract(config,'$.backend') IN ('unsloth','laya-native','vega','liquid-trl') LIMIT 1"
                ).fetchone()
                is not None
            )

    def active(self, tenant, project):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM active_models WHERE tenant=? AND project=?",
                (tenant, project),
            ).fetchone()
        return dict(row) if row else {"model_id": None, "revision": 0}

    def activate(self, tenant, project, actor, model_id, expected_revision, note):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM active_models WHERE tenant=? AND project=?",
                (tenant, project),
            ).fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise Conflict(
                    "Another release changed the active model; refresh before promotion"
                )
            db.execute(
                "INSERT INTO active_models VALUES (?,?,?,?) ON CONFLICT(tenant,project) DO UPDATE SET model_id=excluded.model_id,revision=excluded.revision",
                (tenant, project, model_id, revision + 1),
            )
            self.audit(db, tenant, project, actor, "activate.model:" + note, model_id)
        return self.active(tenant, project)
