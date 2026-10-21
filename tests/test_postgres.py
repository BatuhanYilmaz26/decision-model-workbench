"""Workspace metadata contracts against an explicitly selected test database.

Set DMT_TEST_POSTGRES_URL only to a disposable database. Each case creates and
drops its own dmt_test_<random> schema; no existing application schema is used.
"""

from concurrent.futures import ThreadPoolExecutor
import ast
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from decision_workbench.platform.api import create_app
from decision_workbench.platform.postgres import PostgresDatabase, postgres_sql
from decision_workbench.platform.store import Store, Conflict
import test_platform as fixtures


URL = os.environ.get("DMT_TEST_POSTGRES_URL")


class SqlAdapterTests(unittest.TestCase):
    def test_values_quotes_comments_and_dollar_quotes_are_not_placeholders(self):
        source = "SELECT '? 50% it''s json_extract(x,''$.revision'')',? -- ?\n/* outer /* ? */ end */,$body$?$body$"
        target = postgres_sql(source)
        self.assertEqual(target.count("%s"), 1)
        self.assertIn("'? 50%% it''s json_extract(x,''$.revision'')'", target)
        self.assertIn("-- ?", target)
        self.assertIn("$body$?$body$", target)

    def test_numeric_paths_and_conflict_handling_are_explicit(self):
        self.assertIn(
            "AS BIGINT", postgres_sql("SELECT json_extract(payload,'$.revision')")
        )
        self.assertIn(
            "DOUBLE PRECISION",
            postgres_sql("SELECT json_extract(payload,'$.reported_cost_usd')"),
        )
        self.assertTrue(
            postgres_sql("INSERT OR IGNORE INTO record_contents VALUES (?,?)").endswith(
                "ON CONFLICT DO NOTHING"
            )
        )
        with self.assertRaises(ValueError):
            postgres_sql("INSERT OR IGNORE INTO jobs VALUES (?)")

    def test_invalid_configuration_and_sql_are_refused(self):
        for schema in ("public", "pg_catalog", "other; DROP TABLE jobs", "UPPER", ""):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                PostgresDatabase("postgresql://unused", schema)
        for sql in (
            "PRAGMA journal_mode=WAL",
            "SELECT 'unfinished",
            "/* unfinished",
            "SELECT $$unfinished",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                postgres_sql(sql)


class DatabaseFixture:
    def database_setup(self):
        import psycopg
        from psycopg import sql

        self.schema = "dmt_test_" + uuid.uuid4().hex
        with psycopg.connect(URL, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.database_cleanup)

    def database_cleanup(self):
        import psycopg
        from psycopg import sql

        if not self.schema.startswith("dmt_test_") or len(self.schema) != 41:
            raise AssertionError(
                "Refusing cleanup outside the uniquely owned test schema"
            )
        with psycopg.connect(URL, autocommit=True) as db:
            db.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )

    def store(self, folder):
        return Store(folder, database_url=URL, database_schema=self.schema)


@unittest.skipUnless(URL, "Set DMT_TEST_POSTGRES_URL to run real PostgreSQL contracts")
class PostgresStorageTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.first = self.store(self.home / "first")

    def test_independent_folders_share_entities_without_creating_sqlite(self):
        other = self.store(self.home / "second")
        entity = self.first.add(
            "project",
            "tenant",
            "project",
            "actor",
            {"text": "O'Reilly ? % ; DROP TABLE jobs"},
        )
        self.assertEqual(
            other.get(entity["id"], "tenant")["payload"], entity["payload"]
        )
        with self.assertRaises(LookupError):
            other.get(entity["id"], "another-tenant")
        self.assertFalse(list(self.home.rglob("*.sqlite3")))
        self.assertEqual(other.backend, "postgresql")
        local = Store(self.home / "personal")
        self.assertEqual(local.backend, "sqlite")
        self.assertTrue(local.database.is_file())

    def test_rollback_constraint_refusal_and_epoch_precision(self):
        created = 1792500000.123456
        with self.assertRaises(RuntimeError):
            with self.first.transaction() as db:
                db.execute(
                    "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
                    ("t", "p", "a", "discard", "x", created),
                )
                raise RuntimeError("fictional interruption")
        self.assertEqual(self.first.events("t", "p"), [])
        with self.first.transaction() as db:
            db.execute(
                "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
                ("t", "p", "a", "keep", "x", created),
            )
        self.assertEqual(self.first.events("t", "p")[0]["created"], created)
        with self.assertRaises(sqlite3.IntegrityError) as raised:
            with self.first.transaction() as db:
                db.execute("INSERT INTO schema_version VALUES (9)")
        self.assertNotIn(URL, str(raised.exception))
        with self.first.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_json_revisions_sort_numerically_and_preserve_null_and_cost(self):
        for revision in (2, 10, 1):
            self.first.add(
                "cloud-event",
                "t",
                "p",
                "a",
                {"run_id": "run", "revision": revision, "reported_cost_usd": 0.125},
            )
        with self.first.transaction() as db:
            rows = db.execute(
                "SELECT json_extract(payload,'$.revision') AS revision,json_extract(payload,'$.reported_cost_usd') AS cost,json_extract(payload,'$.absent') AS missing FROM entities ORDER BY json_extract(payload,'$.revision') DESC"
            ).fetchall()
            self.assertEqual([r["revision"] for r in rows], [10, 2, 1])
            self.assertEqual(rows[0]["cost"], 0.125)
            self.assertIsNone(rows[0]["missing"])
            self.assertEqual(
                db.execute("SELECT '? 50%',? AS value", ("quoted ? %",)).fetchone()[1],
                "quoted ? %",
            )

    def test_unique_cloud_revision_and_idempotency_are_database_enforced(self):
        self.first.add("cloud-event", "t", "p", "a", {"run_id": "r", "revision": 1})
        with self.assertRaises(sqlite3.IntegrityError):
            self.first.add("cloud-event", "t", "p", "a", {"run_id": "r", "revision": 1})
        first = self.first.submit("t", "p", "a", "prepare", {}, "one-request")
        other = self.store(self.home / "second")
        self.assertEqual(
            first["id"], other.submit("t", "p", "a", "prepare", {}, "one-request")["id"]
        )
        with self.assertRaises(Conflict):
            other.submit("t", "p", "a", "prepare", {"changed": True}, "one-request")

    def test_independent_processes_claim_once_and_stale_tokens_cannot_commit(self):
        for number in range(2):
            self.first.submit("t", "p", "a", "prepare", {}, "request-" + str(number))
        source = "from decision_workbench.platform.store import Store; import json,os; s=Store(os.environ['DMT_TEST_CACHE'],database_url=os.environ['DMT_TEST_POSTGRES_URL'],database_schema=os.environ['DMT_TEST_SCHEMA']); print(json.dumps(s.claim()))"
        env = dict(
            os.environ,
            DMT_TEST_SCHEMA=self.schema,
            DMT_TEST_CACHE=str(self.home / "worker"),
        )
        children = [
            subprocess.Popen(
                [sys.executable, "-c", source],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        jobs = []
        try:
            for child in children:
                out, err = child.communicate(timeout=30)
                self.assertEqual(child.returncode, 0, err)
                jobs.append(json.loads(out))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
        self.assertEqual(len({j["id"] for j in jobs}), 2)
        self.assertIsNone(self.first.claim())
        stale = {**jobs[0], "token": "wrong-owner"}
        with self.assertRaises(Conflict):
            self.first.finish(stale, {"wrong": True})
        self.first.finish(jobs[0], {"ok": True})
        self.assertEqual(
            self.first.job(jobs[0]["id"], "t", "p")["result"], {"ok": True}
        )

    def test_lock_timeout_rolls_back_and_next_connection_recovers(self):
        impatient = PostgresDatabase(URL, self.schema, timeout=1)

        def contender():
            with impatient.transaction():
                return "should not enter"

        with self.first.transaction():
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(sqlite3.DatabaseError):
                    pool.submit(contender).result(timeout=5)
        with impatient.transaction() as db:
            self.assertEqual(db.execute("SELECT 1").fetchone()[0], 1)

    def test_missing_schema_and_future_version_refuse_without_sqlite_fallback(self):
        with self.assertRaises(ValueError):
            Store(
                self.home / "missing",
                database_url=URL,
                database_schema="dmt_missing_" + uuid.uuid4().hex,
            )
        with self.first.transaction() as db:
            db.execute("INSERT INTO schema_version VALUES (999)")
        with self.assertRaises(RuntimeError):
            self.store(self.home / "future")
        self.assertFalse(list(self.home.rglob("*.sqlite3")))

    def test_artifact_folder_binding_prevents_backend_or_database_mixups(self):
        with self.assertRaises(ValueError):
            Store(self.home / "first")
        local = Store(self.home / "personal")
        with self.assertRaises(ValueError):
            self.store(local.folder)
        marker = self.home / "first" / ".dmt-postgres.json"
        body = json.loads(marker.read_text(encoding="utf-8"))
        self.assertNotIn(URL, marker.read_text(encoding="utf-8"))
        body["workspace"] = "0" * 32
        marker.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store(self.home / "first")

    def test_existing_complete_json_selects_plan_on_the_real_database(self):
        planned = set()
        for path in (
            Path(__file__).resolve().parents[1] / "decision_workbench/platform"
        ).glob("*.py"):
            if path.name == "postgres.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Constant) or not isinstance(
                    node.value, str
                ):
                    continue
                query = node.value
                if not query.startswith("SELECT ") or "json_extract(" not in query:
                    continue
                with self.first.transaction() as db:
                    db.execute(
                        "EXPLAIN " + query, (None,) * postgres_sql(query).count("%s")
                    ).fetchall()
                planned.add(query)
        self.assertGreaterEqual(len(planned), 20)


@unittest.skipUnless(URL, "Set DMT_TEST_POSTGRES_URL to run real PostgreSQL workflows")
class PostgresWorkflowTests(DatabaseFixture, fixtures.PlatformTests):
    """Reuse the full real API/worker/CPU release contracts with PostgreSQL."""

    def setUp(self):
        self.database_setup()
        app_patch = patch.object(
            fixtures,
            "create_app",
            side_effect=lambda *a, **kw: create_app(
                *a, **kw, database_url=URL, database_schema=self.schema
            ),
        )
        store_patch = patch.object(fixtures, "Store", side_effect=self.store)
        app_patch.start()
        store_patch.start()
        self.addCleanup(app_patch.stop)
        self.addCleanup(store_patch.stop)
        super().setUp()
        self.addCleanup(self.app.state.service.runtime.close)


if __name__ == "__main__":
    unittest.main()
