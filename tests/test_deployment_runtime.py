"""Managed API/worker wiring and browser process replacement without cloud calls."""

from contextlib import redirect_stdout
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform import deployment
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    DirectoryObjects,
)
from decision_workbench.platform.shared_authentication import publish
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker
import test_platform as fixtures
import test_postgres as pg


@unittest.skipUnless(
    importlib.util.find_spec("boto3"),
    "Install requirements/aws-sdk.txt for managed browser startup",
)
class BrowserProcessTests(unittest.TestCase):
    def test_web_replaces_parent_and_receives_only_browser_configuration(self):
        class Replaced(BaseException):
            pass

        seen = []

        def replace(executable, command, environment):
            seen.append((command, environment))
            raise Replaced()

        variables = {
            "AWS_REGION": "us-east-1",
            "DMT_API_URL": "https://workbench.example",
            "DMT_UI_AUTH_MODE": "workspace",
            "DMT_DATABASE_SECRET_ID": "private-database",
            "DMT_PROVIDER_LIQUID_API_KEY": "private-provider",
            "DMT_APP_PATH": "/app/app.py",
        }
        with patch.dict(os.environ, variables, clear=True), patch.object(
            sys, "argv", ["deployment", "web"]
        ), patch("boto3.client", return_value=SimpleNamespace()), patch.object(
            os, "chdir"
        ), patch.object(
            os, "execve", side_effect=replace
        ), self.assertRaises(
            Replaced
        ):
            deployment.main()
        self.assertIn("streamlit", seen[0][0])
        self.assertIn("0.0.0.0", seen[0][0])
        self.assertNotIn("DMT_DATABASE_SECRET_ID", seen[0][1])
        self.assertNotIn("DMT_PROVIDER_LIQUID_API_KEY", seen[0][1])
        self.assertEqual(seen[0][1]["DMT_API_URL"], "https://workbench.example")


@unittest.skipUnless(
    pg.URL and importlib.util.find_spec("boto3"),
    "Set DMT_TEST_POSTGRES_URL and install AWS clients for managed service wiring",
)
class ManagedRuntimeTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = ArtifactRepository(DirectoryObjects(self.root / "objects"))
        self.store = Store(
            self.root / "authority",
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )
        publish(self.store, {"operator_keys": fixtures.keys()}, 0)

    def test_managed_api_and_worker_share_postgres_and_preserve_authority(self):
        submitted = []
        environment = {
            "DMT_DATABASE_URL": pg.URL,
            "DMT_DATABASE_SCHEMA": self.schema,
            "DMT_HOME": str(self.root / "api"),
            "DMT_KEYS_FILE": str(self.root / "deliberately-absent.json"),
            "DMT_TRUSTED_PROXY_IPS": "10.0.0.0/16",
        }

        def serve(app, **options):
            self.assertEqual(options["forwarded_allow_ips"], "10.0.0.0/16")
            self.assertEqual(app.state.store.backend, "postgresql")
            with TestClient(app) as client:
                self.assertEqual(client.get("/v1/me").status_code, 401)
                headers = {"Authorization": "Bearer a-secret"}
                project = client.post(
                    "/v1/projects",
                    json={
                        "name": "Fictional managed service",
                        "description": "Fictional policy",
                        "questions": fixtures.QUESTIONS,
                    },
                    headers=headers,
                )
                self.assertEqual(project.status_code, 201, project.text)
                identifier = project.json()["id"]
                rows = fixtures.fixture_rows()
                for row in rows:
                    row["metadata"].update(
                        review_status="approved",
                        reviewer="owner",
                        review_note="Fictional labels checked",
                    )
                dataset = app.state.service.save_dataset(
                    {"tenant": "A", "actor": "owner", "role": "admin"},
                    identifier,
                    rows,
                    {"fixture_review": True},
                )
                response = client.post(
                    "/v1/projects/" + identifier + "/jobs",
                    json={
                        "kind": "prepare",
                        "config": {"datasets": [dataset["id"]]},
                        "request_key": "managed-preparation",
                    },
                    headers=headers,
                )
                self.assertEqual(response.status_code, 202, response.text)
                submitted.append((identifier, response.json()["id"]))
                self.assertEqual(client.get("/ready").json()["storage"], "postgresql")

        with patch.dict(os.environ, environment), patch(
            "decision_workbench.platform.deployment.repository_from_environment",
            return_value=self.repo,
        ), patch("uvicorn.run", side_effect=serve):
            deployment.run_backend("api", os.environ)
        stop = threading.Event()
        original = Worker.once

        def once(worker):
            try:
                return original(worker)
            finally:
                stop.set()

        environment["DMT_HOME"] = str(self.root / "worker")
        with patch.dict(os.environ, environment), patch(
            "decision_workbench.platform.deployment.repository_from_environment",
            return_value=self.repo,
        ), patch.object(Worker, "once", once):
            deployment.run_backend("worker", os.environ, stop)
        result = self.store.job(submitted[0][1], "A", submitted[0][0])
        self.assertEqual(result["status"], "succeeded", result["error"])

    def test_publish_auth_command_uses_expected_revision_and_prints_no_keys(self):
        secrets = {
            "db": {"username": "fixture", "password": "fictional-private-password"},
            "backend": {"operator_keys": fixtures.keys()[1:]},
        }
        client = SimpleNamespace(
            get_secret_value=lambda **args: {
                "SecretString": json.dumps(secrets[args["SecretId"]])
            }
        )
        environment = {
            "DMT_DATABASE_SECRET_ID": "db",
            "DMT_CONFIG_SECRET_ID": "backend",
            "DMT_DATABASE_SCHEMA": self.schema,
            "DMT_HOME": str(self.root / "publisher"),
            "AWS_REGION": "us-east-1",
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment), patch.object(
            sys, "argv", ["deployment", "publish-auth", "--expected-auth-revision", "1"]
        ), patch("boto3.client", return_value=client), patch.object(
            deployment, "database_url", return_value=pg.URL
        ), patch.object(
            deployment, "repository_from_environment", return_value=self.repo
        ), redirect_stdout(
            output
        ):
            deployment.main()
        value = json.loads(output.getvalue())
        self.assertEqual(value["revision"], 2)
        self.assertNotIn("fictional-private-password", output.getvalue())
        self.assertNotIn("key_sha256", output.getvalue())


if __name__ == "__main__":
    unittest.main()
