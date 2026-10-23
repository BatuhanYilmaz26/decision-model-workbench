"""Real PostgreSQL/CPU workflow with fictional versioned Azure object transport."""

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform.artifact_azure import AzureBlobObjects
from decision_workbench.platform.artifact_storage import ArtifactRepository
from decision_workbench.platform import shared_recovery as recovery
from decision_workbench.platform.worker import Worker
import test_platform as fixtures
import test_postgres as pg
import test_shared_recovery as recovery_fixtures
from azure_blob_fixture import MemoryAccount

try:
    HAVE_SDK = importlib.util.find_spec("azure.storage.blob") is not None
except ModuleNotFoundError:
    HAVE_SDK = False


@unittest.skipUnless(
    pg.URL and HAVE_SDK, "Set DMT_TEST_POSTGRES_URL and install Azure artifact clients"
)
class AzureSharedWorkflowTests(pg.DatabaseFixture, unittest.TestCase):
    dataset = fixtures.PlatformTests.dataset
    job = fixtures.PlatformTests.job
    fresh_schema = recovery_fixtures.SharedRecoveryTests.fresh_schema

    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.account = MemoryAccount()
        self.credential = SimpleNamespace(
            get_token=lambda *args: SimpleNamespace(token="fictional-copy-token")
        )
        self.repo, self.vault, self.target = [
            ArtifactRepository(
                AzureBlobObjects(
                    self.account, name, "workspace", credential=self.credential
                )
            )
            for name in ["artifacts", "backups", "restored"]
        ]
        self.app = create_app(
            self.root / "api",
            fixtures.keys(),
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )
        self.addCleanup(self.app.state.service.runtime.close)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        response = self.client.post(
            "/v1/projects",
            json={
                "name": "Fictional Azure workflow",
                "description": "Fictional decision policy",
                "questions": fixtures.QUESTIONS,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.project = response.json()["id"]
        self.prefix = "/v1/projects/" + self.project
        self.store = self.app.state.store

    def test_train_release_cold_restore_and_prediction_preserve_tenant_boundary(self):
        dataset = self.dataset()
        prepare = self.job("prepare", {"datasets": [dataset["id"]]}, "azure-prepare")
        worker = Worker(self.store)
        self.addCleanup(worker.service.runtime.close)
        self.assertTrue(worker.once())
        prepared = self.store.job(prepare["id"], "A", self.project)
        self.assertEqual(prepared["status"], "succeeded", prepared["error"])
        train = self.job(
            "train",
            {"snapshot": prepared["result"]["snapshot"], "backend": "cpu-baseline"},
            "azure-train",
        )
        self.assertTrue(worker.once())
        trained = self.store.job(train["id"], "A", self.project)
        self.assertEqual(trained["status"], "succeeded", trained["error"])
        release = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": trained["result"]["model"],
                "expected_revision": 0,
                "note": "Fictional Azure release",
            },
        )
        self.assertEqual(release.status_code, 200, release.text)
        request = {"state": "invoice payment refund requested urgent", "threshold": 0.5}
        original = self.client.post(self.prefix + "/decisions", json=request)
        self.assertEqual(original.status_code, 200, original.text)
        backup = self.root / "backup"
        result = recovery.backup_shared(self.store, backup, self.vault)
        schema = self.fresh_schema()
        # Only this in-memory account's live container is lost; independent backups remain.
        self.account.current = {
            k: v for k, v in self.account.current.items() if k[0] != "artifacts"
        }
        self.account.versions = {
            k: v for k, v in self.account.versions.items() if k[0] != "artifacts"
        }
        receipt = recovery.restore_shared(
            backup,
            self.root / "recovered",
            result["manifest_sha256"],
            self.vault,
            self.target,
            database_url=pg.URL,
            database_schema=schema,
        )
        restored_repo = ArtifactRepository(
            AzureBlobObjects(
                self.account,
                "restored",
                receipt["artifact_storage"]["prefix"],
                credential=self.credential,
            )
        )
        with TestClient(
            create_app(
                self.root / "reopened",
                fixtures.keys(),
                database_url=pg.URL,
                database_schema=schema,
                artifact_store=restored_repo,
            )
        ) as client:
            self.addCleanup(client.app.state.service.runtime.close)
            restored = client.post(
                self.prefix + "/decisions",
                json=request,
                headers={"Authorization": "Bearer a-secret"},
            )
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()["answers"], original.json()["answers"])
            denied = client.get(
                self.prefix, headers={"Authorization": "Bearer b-secret"}
            )
            self.assertEqual(denied.status_code, 404)
            self.assertEqual(client.get("/ready").status_code, 200)
        self.assertTrue(any(c[0] == "server-copy" for c in self.account.calls))


if __name__ == "__main__":
    unittest.main()
