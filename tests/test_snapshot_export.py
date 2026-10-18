"""Actual reserved-study download, scope, integrity and browser consent checks."""

import copy
import hashlib
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import uvicorn
from streamlit.testing.v1 import AppTest
import test_platform as fixture
from decision_workbench.platform.snapshot_export import study_archive
from decision_workbench.platform.store import encode


class SnapshotExportTests(unittest.TestCase):
    setUp = fixture.PlatformTests.setUp
    dataset = fixture.PlatformTests.dataset
    job = fixture.PlatformTests.job
    snapshot = fixture.PlatformTests.snapshot

    def tearDown(self):
        self.app.state.service.runtime.close()
        fixture.PlatformTests.tearDown(self)

    def endpoint(self, identifier):
        return self.prefix + "/snapshots/" + identifier + "/download"

    def test_exact_roles_manifest_determinism_and_audit(self):
        identifier = self.snapshot()
        first = self.client.get(self.endpoint(identifier))
        self.assertEqual(
            first.status_code, 200, first.text if first.status_code != 200 else ""
        )
        self.assertEqual(first.headers["content-type"], "application/zip")
        self.assertEqual(
            first.content, self.client.get(self.endpoint(identifier)).content
        )
        snapshot = self.app.state.store.get(identifier, "A", self.project, "snapshot")[
            "payload"
        ]
        with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "manifest.json",
                    "train.jsonl",
                    "validation.jsonl",
                    "calibration.jsonl",
                    "test.jsonl",
                },
            )
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["snapshot_sha256"], snapshot["sha256"])
            for role, original in snapshot["splits"].items():
                body = archive.read(role + ".jsonl")
                self.assertEqual(
                    [json.loads(line) for line in body.splitlines()], original
                )
                self.assertEqual(
                    manifest["files"][role + ".jsonl"]["sha256"],
                    hashlib.sha256(body).hexdigest(),
                )
                self.assertEqual(
                    manifest["files"][role + ".jsonl"]["rows"], len(original)
                )
        with self.app.state.store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM events WHERE action='snapshot.export.prepared'"
                ).fetchone()[0],
                2,
            )

    def test_roles_tenants_missing_snapshot_and_revoked_identity(self):
        identifier = self.snapshot()
        for token, status in (
            ("review-secret", 200),
            ("infer-secret", 403),
            ("b-secret", 404),
        ):
            self.client.headers["Authorization"] = "Bearer " + token
            self.assertEqual(
                self.client.get(self.endpoint(identifier)).status_code, status
            )
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.assertEqual(self.client.get(self.endpoint("missing")).status_code, 404)
        real = self.app.state.authentication.authenticate
        calls = []

        def changed(value):
            principal = real(value)
            calls.append(value)
            return {**principal, "role": "inference"} if len(calls) > 1 else principal

        with patch.object(
            self.app.state.authentication, "authenticate", side_effect=changed
        ):
            response = self.client.get(self.endpoint(identifier))
        self.assertEqual(response.status_code, 401)
        self.assertNotEqual(response.headers.get("content-type"), "application/zip")

    def test_corrupt_hash_counts_and_uncompleted_reservation_refused(self):
        identifier = self.snapshot()
        store = self.app.state.store
        original = store.get(identifier, "A", self.project, "snapshot")["payload"]
        for field, value in (
            ("sha256", "0" * 64),
            ("counts", {k: 0 for k in original["counts"]}),
            ("schema_sha256", "f" * 64),
        ):
            changed = copy.deepcopy(original)
            changed[field] = value
            with store.transaction() as db:
                db.execute(
                    "UPDATE entities SET payload=? WHERE id=?",
                    (encode(changed), identifier),
                )
            self.assertEqual(
                self.client.get(self.endpoint(identifier)).status_code, 400
            )
        with store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (encode(original), identifier),
            )
            db.execute("UPDATE jobs SET status='failed' WHERE kind='prepare'")
        self.assertEqual(self.client.get(self.endpoint(identifier)).status_code, 400)

    def test_archive_budget_and_editor_download(self):
        identifier = self.snapshot()
        with patch("decision_workbench.platform.snapshot_export.MAX_BYTES", 10):
            self.assertEqual(
                self.client.get(self.endpoint(identifier)).status_code, 400
            )
        body = study_archive(
            self.app.state.service,
            {"tenant": "A", "actor": "editor", "role": "editor"},
            self.project,
            identifier,
        )
        self.assertTrue(body.startswith(b"PK"))

    def test_browser_requires_consent_and_preserves_all_four_roles(self):
        identifier = self.snapshot()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Local API startup timed out")
                time.sleep(0.02)
            at = AppTest.from_file(str(Path("app.py").resolve()))
            at.session_state["credential"] = "a-secret"
            at.session_state["api_url"] = (
                f"http://127.0.0.1:{listener.getsockname()[1]}"
            )
            at.session_state["principal"] = {
                "tenant": "A",
                "actor": "owner",
                "role": "admin",
            }
            at.run(timeout=30)
            self.assertFalse(at.exception)
            button = next(
                b for b in at.button if b.label == "Prepare reserved study download"
            )
            self.assertTrue(button.disabled)
            next(
                c
                for c in at.checkbox
                if c.label
                == "I approve downloading this snapshot's evidence and reviewed labels"
            ).check().run(timeout=30)
            next(
                b for b in at.button if b.label == "Prepare reserved study download"
            ).click().run(timeout=30)
            self.assertFalse(at.exception)
            body = at.session_state[
                "snapshot-export-bytes-snapshot-export-"
                + self.project
                + "-"
                + identifier
            ]
            self.assertEqual(
                sum(
                    b.proto.label == "Download reserved canonical study"
                    for b in at.get("download_button")
                ),
                1,
            )
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                self.assertEqual(
                    json.loads(archive.read("manifest.json"))["snapshot"], identifier
                )
            next(
                c
                for c in at.checkbox
                if c.label
                == "I approve downloading this snapshot's evidence and reviewed labels"
            ).uncheck().run(timeout=30)
            self.assertFalse(
                any(
                    b.proto.label == "Download reserved canonical study"
                    for b in at.get("download_button")
                )
            )
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
