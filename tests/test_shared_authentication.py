"""Cross-node revocation, concurrent publication and restored authorization state."""

from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    DirectoryObjects,
)
from decision_workbench.platform.auth import (
    AuthenticationError,
    AuthenticationUnavailable,
)
from decision_workbench.platform.shared_authentication import (
    SharedAuthentication,
    publish,
)
from decision_workbench.platform.shared_recovery import backup_shared, restore_shared
from decision_workbench.platform.store import Store, Conflict
import test_platform as fixtures
import test_postgres as pg


@unittest.skipUnless(
    pg.URL, "Set DMT_TEST_POSTGRES_URL for shared authorization checks"
)
class SharedAuthenticationTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repository = ArtifactRepository(DirectoryObjects(self.root / "objects"))
        self.store = self.node("first")

    def node(self, name):
        return Store(
            self.root / name,
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repository,
        )

    def test_two_live_api_instances_observe_role_changes_and_revocation_without_restart(
        self,
    ):
        publish(self.store, {"operator_keys": fixtures.keys()}, 0)
        second = self.node("second")
        with TestClient(
            create_app(
                self.root / "api-a",
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=self.repository,
                authentication=SharedAuthentication(self.store),
            )
        ) as first, TestClient(
            create_app(
                self.root / "api-b",
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=self.repository,
                authentication=SharedAuthentication(second),
            )
        ) as other:
            header = {"Authorization": "Bearer a-secret"}
            for client in (first, other):
                self.assertEqual(
                    client.get("/v1/me", headers=header).json()["role"], "admin"
                )
            updated = fixtures.keys()
            updated[0]["role"] = "reviewer"
            publish(second, {"operator_keys": updated}, 1)
            for client in (first, other):
                self.assertEqual(
                    client.get("/v1/me", headers=header).json()["role"], "reviewer"
                )
            publish(self.store, {"operator_keys": updated[1:]}, 2)
            for client in (first, other):
                self.assertEqual(client.get("/v1/me", headers=header).status_code, 401)
                self.assertEqual(
                    client.get(
                        "/v1/me", headers={"Authorization": "Bearer b-secret"}
                    ).status_code,
                    200,
                )

    def test_nested_reauthorization_and_racing_revisions_use_one_committed_authority(
        self,
    ):
        result = publish(self.store, {"operator_keys": fixtures.keys()}, 0)
        auth = SharedAuthentication(self.store)
        with self.store.transaction():
            self.assertEqual(auth.authenticate("Bearer a-secret")["role"], "admin")
        self.assertFalse(
            publish(self.store, {"operator_keys": fixtures.keys()}, 0)["changed"]
        )

        def change(role):
            value = fixtures.keys()
            value[0]["role"] = role
            try:
                return publish(self.node("node-" + role), {"operator_keys": value}, 1)
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(change, ("reviewer", "editor")))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(auth.read()["revision"], 2)
        self.assertEqual(auth.read()["previous_sha256"], result["sha256"])

    def test_missing_corrupt_or_unavailable_authority_has_no_file_fallback(self):
        with self.assertRaises(AuthenticationUnavailable):
            SharedAuthentication(self.store)
        publish(self.store, {"operator_keys": fixtures.keys()}, 0)
        auth = SharedAuthentication(self.store)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload='{}' WHERE kind='workspace-authentication'"
            )
        with self.assertRaises(AuthenticationUnavailable):
            auth.authenticate("Bearer a-secret")

    def test_all_tenant_authentication_history_survives_shared_recovery(self):
        import psycopg
        from psycopg import sql

        publish(self.store, {"operator_keys": fixtures.keys()}, 0)
        updated = fixtures.keys()[1:]
        publish(self.store, {"operator_keys": updated}, 1)
        vault = ArtifactRepository(DirectoryObjects(self.root / "vault"))
        backup = backup_shared(self.store, self.root / "backup", vault)
        schema = "dmt_test_" + uuid.uuid4().hex
        with psycopg.connect(pg.URL, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def cleanup():
            assert schema.startswith("dmt_test_") and len(schema) == 41
            with psycopg.connect(pg.URL, autocommit=True) as db:
                db.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )

        self.addCleanup(cleanup)
        target = ArtifactRepository(DirectoryObjects(self.root / "recovered-objects"))
        receipt = restore_shared(
            self.root / "backup",
            self.root / "recovered",
            backup["manifest_sha256"],
            vault,
            target,
            database_url=pg.URL,
            database_schema=schema,
        )
        target = ArtifactRepository(
            DirectoryObjects(receipt["artifact_storage"]["root"])
        )
        store = Store(
            self.root / "recovered",
            database_url=pg.URL,
            database_schema=schema,
            artifact_store=target,
        )
        auth = SharedAuthentication(store)
        self.assertEqual(auth.read()["revision"], 2)
        with self.assertRaises(AuthenticationError):
            auth.authenticate("Bearer a-secret")
        self.assertEqual(auth.authenticate("Bearer b-secret")["tenant"], "B")


if __name__ == "__main__":
    unittest.main()
