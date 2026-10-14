"""Durable single-host worker presence and scoped operational incident lifecycle."""

import math
import os
import re
import threading
import time
import uuid
import logging

KINDS = {
    "backup_unhealthy": (
        "error",
        "Configured workspace backup health needs operator attention. Inspect the backup publisher, freshness and last attempt.",
    ),
    "job_failed": (
        "error",
        "Job failed. Inspect the scoped job and private worker log before submitting a new attempt.",
    ),
    "job_interrupted": (
        "error",
        "Job was interrupted. Inspect the previous attempt before explicit resubmission.",
    ),
    "job_lease_expired": (
        "error",
        "Job ownership expired. Its old worker cannot commit; inspect worker recovery and cancellation.",
    ),
    "worker_unavailable": (
        "error",
        "Queued work has no fresh workspace worker heartbeat. Start or recover the worker.",
    ),
    "queue_delayed": (
        "warning",
        "Queued work exceeded the configured wait threshold. Inspect worker capacity and earlier jobs.",
    ),
}
TRANSIENT = {
    "job_lease_expired",
    "worker_unavailable",
    "queue_delayed",
    "backup_unhealthy",
}


class OperationsStorage:
    @staticmethod
    def setup_operations(db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS worker_heartbeats (id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('idle','busy','stopped')), job_id TEXT REFERENCES jobs(id), updated REAL NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS worker_presence ON worker_heartbeats(state,updated)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS operational_incidents (id TEXT PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','resolved')), occurrences INTEGER NOT NULL, revision INTEGER NOT NULL, opened REAL NOT NULL, updated REAL NOT NULL, resolved REAL, acknowledged_by TEXT, acknowledged_at REAL, note TEXT NOT NULL, UNIQUE(tenant,project,kind,target))"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS incident_scope ON operational_incidents(tenant,project,status,updated)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS incident_history (id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES operational_incidents(id), revision INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, created REAL NOT NULL, UNIQUE(incident_id,revision))"
        )

    @staticmethod
    def _incident_history(db, row, action, actor, note, now):
        db.execute(
            "INSERT INTO incident_history(incident_id,revision,action,actor,note,created) VALUES (?,?,?,?,?,?)",
            (row["id"], row["revision"], action, actor, note, now),
        )
        OperationsStorage.audit(
            db, row["tenant"], row["project"], actor, "incident." + action, row["id"]
        )

    @staticmethod
    def audit(db, tenant, project, actor, action, target):
        db.execute(
            "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
            (tenant, project, actor, action, target, time.time()),
        )

    def _condition(self, db, tenant, project, kind, target, active, now):
        row = db.execute(
            "SELECT * FROM operational_incidents WHERE tenant=? AND project=? AND kind=? AND target=?",
            (tenant, project, kind, target),
        ).fetchone()
        if not row and not active:
            return
        if not row:
            identifier = uuid.uuid4().hex
            db.execute(
                "INSERT INTO operational_incidents VALUES (?,?,?,?,?,?,'open',1,1,?,?,NULL,NULL,NULL,'')",
                (identifier, tenant, project, kind, target, KINDS[kind][0], now, now),
            )
            current = dict(
                db.execute(
                    "SELECT * FROM operational_incidents WHERE id=?", (identifier,)
                ).fetchone()
            )
            self._incident_history(
                db, current, "opened", "monitor", KINDS[kind][1], now
            )
        elif kind not in TRANSIENT:
            # Terminal attempts are observed once; operator resolution never replays them.
            return
        elif active and row["status"] == "resolved":
            db.execute(
                "UPDATE operational_incidents SET status='open',occurrences=occurrences+1,revision=revision+1,opened=?,updated=?,resolved=NULL,acknowledged_by=NULL,acknowledged_at=NULL,note='' WHERE id=?",
                (now, now, row["id"]),
            )
            current = {**dict(row), "revision": row["revision"] + 1}
            self._incident_history(
                db, current, "reopened", "monitor", KINDS[kind][1], now
            )
        elif not active and row["status"] == "open":
            db.execute(
                "UPDATE operational_incidents SET status='resolved',revision=revision+1,updated=?,resolved=? WHERE id=?",
                (now, now, row["id"]),
            )
            current = {**dict(row), "revision": row["revision"] + 1}
            self._incident_history(
                db,
                current,
                "resolved",
                "monitor",
                "Observed condition recovered; acknowledgement is not a repair.",
                now,
            )

    def worker_presence(self, identifier, state, job_id=None):
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-f0-9]{32}", identifier)
            or state not in ("idle", "busy", "stopped")
            or (state != "busy" and job_id is not None)
        ):
            raise ValueError("Invalid worker presence")
        with self.transaction() as db:
            if (
                job_id is not None
                and not db.execute(
                    "SELECT 1 FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
            ):
                raise ValueError("Worker job does not exist")
            db.execute(
                "INSERT INTO worker_heartbeats VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,job_id=excluded.job_id,updated=excluded.updated",
                (identifier, state, job_id, time.time()),
            )

    def collect_operations(
        self, worker_max_age=15, queue_grace=60, queue_delay=300, cursor=None
    ):
        now = time.time()
        with self.transaction() as db:
            live = bool(
                db.execute(
                    "SELECT 1 FROM worker_heartbeats WHERE state IN ('idle','busy') AND updated>=? LIMIT 1",
                    (now - worker_max_age,),
                ).fetchone()
            )
            # Bound historical catch-up; already observed terminal attempts are not reloaded.
            terminal = db.execute(
                "SELECT id,tenant,project,status FROM jobs j WHERE status IN ('failed','interrupted') AND NOT EXISTS (SELECT 1 FROM operational_incidents i WHERE i.tenant=j.tenant AND i.project=j.project AND i.kind='job_'||j.status AND i.target=j.id) ORDER BY updated,id LIMIT 500"
            ).fetchall()
            for job in terminal:
                self._condition(
                    db,
                    job["tenant"],
                    job["project"],
                    "job_" + job["status"],
                    job["id"],
                    True,
                    now,
                )
            query = "SELECT tenant,project FROM (SELECT tenant,project FROM jobs WHERE status IN ('queued','running','cancel_requested') UNION SELECT tenant,project FROM operational_incidents WHERE status='open' AND kind IN ('worker_unavailable','queue_delayed','job_lease_expired'))"
            parameters = ()
            if cursor:
                query += " WHERE (tenant,project)>(?,?)"
                parameters = tuple(cursor)
            projects = db.execute(
                query + " ORDER BY tenant,project LIMIT 500", parameters
            ).fetchall()
            if not projects and cursor:
                projects = db.execute(
                    query.split(" WHERE (tenant,project)>")[0]
                    + " ORDER BY tenant,project LIMIT 500"
                ).fetchall()
            for project in projects:
                tenant, identifier = project["tenant"], project["project"]
                queued = db.execute(
                    "SELECT min(created) FROM jobs WHERE tenant=? AND project=? AND status='queued'",
                    (tenant, identifier),
                ).fetchone()[0]
                wait = now - queued if queued is not None else 0
                self._condition(
                    db,
                    tenant,
                    identifier,
                    "worker_unavailable",
                    "worker",
                    queued is not None and not live and wait >= queue_grace,
                    now,
                )
                self._condition(
                    db,
                    tenant,
                    identifier,
                    "queue_delayed",
                    "queue",
                    queued is not None and wait >= queue_delay,
                    now,
                )
                expired = db.execute(
                    "SELECT id FROM jobs WHERE tenant=? AND project=? AND status IN ('running','cancel_requested') AND (lease IS NULL OR lease<?)",
                    (tenant, identifier, now),
                ).fetchall()
                for job in expired:
                    self._condition(
                        db,
                        tenant,
                        identifier,
                        "job_lease_expired",
                        job["id"],
                        True,
                        now,
                    )
                recovered = db.execute(
                    "SELECT i.target FROM operational_incidents i JOIN jobs j ON j.id=i.target AND j.tenant=i.tenant AND j.project=i.project WHERE i.tenant=? AND i.project=? AND i.kind='job_lease_expired' AND i.status='open' AND NOT (j.status IN ('running','cancel_requested') AND (j.lease IS NULL OR j.lease<?))",
                    (tenant, identifier, now),
                ).fetchall()
                for job in recovered:
                    self._condition(
                        db,
                        tenant,
                        identifier,
                        "job_lease_expired",
                        job["target"],
                        False,
                        now,
                    )
        return (projects[-1]["tenant"], projects[-1]["project"]) if projects else None

    def collect_backup_operations(self, health, cursor=None):
        if not health["configured"]:
            return cursor  # Removing a profile is not evidence that existing backup faults recovered.
        with self.transaction() as db:
            query = "SELECT tenant,id FROM entities WHERE kind='project'"
            parameters = ()
            if cursor:
                query += " AND (tenant,id)>(?,?)"
                parameters = tuple(cursor)
            projects = db.execute(
                query + " ORDER BY tenant,id LIMIT 500", parameters
            ).fetchall()
            if not projects and cursor:
                projects = db.execute(
                    "SELECT tenant,id FROM entities WHERE kind='project' ORDER BY tenant,id LIMIT 500"
                ).fetchall()
            for project in projects:
                self._condition(
                    db,
                    project["tenant"],
                    project["id"],
                    "backup_unhealthy",
                    "workspace-backups",
                    not health["healthy"],
                    time.time(),
                )
            return (projects[-1]["tenant"], projects[-1]["id"]) if projects else None

    def operations(self, tenant, project, worker_max_age=15, limit=50, offset=0):
        from .paging import validate_page

        validate_page(limit, offset, 200)
        now = time.time()
        with self.transaction() as db:
            worker_live = bool(
                db.execute(
                    "SELECT 1 FROM worker_heartbeats WHERE state IN ('idle','busy') AND updated>=? LIMIT 1",
                    (now - worker_max_age,),
                ).fetchone()
            )
            counts = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status,count(*) AS count FROM jobs WHERE tenant=? AND project=? GROUP BY status",
                    (tenant, project),
                )
            }
            oldest = db.execute(
                "SELECT min(created) FROM jobs WHERE tenant=? AND project=? AND status='queued'",
                (tenant, project),
            ).fetchone()[0]
            incidents = [
                {**dict(row), "message": KINDS[row["kind"]][1]}
                for row in db.execute(
                    "SELECT * FROM operational_incidents WHERE tenant=? AND project=? ORDER BY (status='open') DESC,updated DESC,id LIMIT ? OFFSET ?",
                    (tenant, project, limit, offset),
                )
            ]
            summary = db.execute(
                "SELECT count(*) AS total,sum(status='open') AS open,sum(status='open' AND acknowledged_at IS NULL) AS unacknowledged FROM operational_incidents WHERE tenant=? AND project=?",
                (tenant, project),
            ).fetchone()
        return {
            "checked_at": now,
            "worker_alive": worker_live,
            "worker_scope": "workspace-presence; not GPU capability or free capacity",
            "job_counts": counts,
            "oldest_queue_seconds": max(0, now - oldest) if oldest is not None else 0,
            "incident_counts": {
                key: summary[key] or 0 for key in ("total", "open", "unacknowledged")
            },
            "incidents": incidents,
            "limit": limit,
            "offset": offset,
        }

    def incident_action(
        self, tenant, project, identifier, actor, revision, action, note
    ):
        from .store import Conflict

        if (
            action not in ("acknowledge", "resolve")
            or not isinstance(note, str)
            or not 1 <= len(note.strip()) <= 1000
            or type(revision) is not int
            or revision < 1
        ):
            raise ValueError("Choose a valid incident action, revision and note")
        now = time.time()
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM operational_incidents WHERE id=? AND tenant=? AND project=?",
                (identifier, tenant, project),
            ).fetchone()
            if not row:
                raise LookupError("Incident not found")
            if (
                row["revision"] != revision
                or row["status"] != "open"
                or (action == "acknowledge" and row["acknowledged_at"] is not None)
            ):
                raise Conflict(
                    "Incident changed; refresh before submitting another action"
                )
            if action == "resolve" and row["kind"] in TRANSIENT:
                raise ValueError(
                    "This condition resolves automatically when observed healthy; acknowledgement does not repair it"
                )
            if action == "acknowledge":
                db.execute(
                    "UPDATE operational_incidents SET acknowledged_by=?,acknowledged_at=?,note=?,revision=revision+1,updated=? WHERE id=?",
                    (actor, now, note.strip(), now, identifier),
                )
            else:
                db.execute(
                    "UPDATE operational_incidents SET status='resolved',resolved=?,note=?,revision=revision+1,updated=? WHERE id=?",
                    (now, note.strip(), now, identifier),
                )
            current = dict(
                db.execute(
                    "SELECT * FROM operational_incidents WHERE id=?", (identifier,)
                ).fetchone()
            )
            self._incident_history(
                db,
                current,
                "acknowledged" if action == "acknowledge" else "resolved",
                actor,
                note.strip(),
                now,
            )
        return current

    def incident_history(self, tenant, project, identifier, limit=50, offset=0):
        from .paging import validate_page

        validate_page(limit, offset, 200)
        with self.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM operational_incidents WHERE id=? AND tenant=? AND project=?",
                (identifier, tenant, project),
            ).fetchone():
                raise LookupError("Incident not found")
            return [
                dict(row)
                for row in db.execute(
                    "SELECT revision,action,actor,note,created FROM incident_history WHERE incident_id=? ORDER BY revision DESC LIMIT ? OFFSET ?",
                    (identifier, limit, offset),
                )
            ]


def bound(name, default, minimum, maximum):
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Invalid operational setting: " + name)
    return value


class OperationsMonitor:
    def __init__(self, store):
        self.store = store
        self.interval = bound("DMT_MONITOR_INTERVAL_SECONDS", 5, 1, 300)
        self.worker_max_age = bound("DMT_WORKER_MAX_AGE_SECONDS", 15, 5, 300)
        self.queue_grace = bound("DMT_WORKER_ALERT_AFTER_SECONDS", 60, 1, 3600)
        self.queue_delay = bound("DMT_QUEUE_ALERT_AFTER_SECONDS", 300, 1, 86400)
        self.last_checked = None
        self.last_error = None
        self.cursor = None
        from .backup_monitor import Reader

        self.backup_reader = Reader(store.folder)
        self.backup_state = self.backup_reader.read()
        self.backup_cursor = None
        self.started = False
        self.stop = threading.Event()
        self.thread = None

    def check(self):
        try:
            self.cursor = self.store.collect_operations(
                self.worker_max_age, self.queue_grace, self.queue_delay, self.cursor
            )
            self.backup_state = self.backup_reader.read()
            self.backup_cursor = self.store.collect_backup_operations(
                self.backup_state, self.backup_cursor
            )
            self.last_checked = time.time()
            self.last_error = None
        except Exception as exc:
            self.last_error = type(exc).__name__
            logging.getLogger(__name__).error(
                "Operational collector failed: %s", self.last_error
            )

    def start(self):
        self.started = True
        self.check()

        def run():
            while not self.stop.wait(self.interval):
                self.check()

        self.thread = threading.Thread(
            target=run, daemon=True, name="domain-operations-monitor"
        )
        self.thread.start()

    def state(self):
        fresh = (
            self.last_checked is not None
            and time.time() - self.last_checked <= max(30, self.interval * 3)
        )
        return {
            "status": (
                "healthy"
                if self.started and fresh and self.last_error is None
                else "unavailable"
            ),
            "last_checked": self.last_checked,
            "last_error": self.last_error,
            "interval_seconds": self.interval,
            "worker_max_age_seconds": self.worker_max_age,
            "worker_alert_after_seconds": self.queue_grace,
            "queue_alert_after_seconds": self.queue_delay,
            "backup": self.backup_state,
        }

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                raise RuntimeError("Operational collector did not stop")
