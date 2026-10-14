"""Owner-scoped frozen windows, canonical streams, recovery and failure contracts."""

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform import audit_exports as exports
from decision_workbench.platform.api import create_app
from decision_workbench.platform.auth import AuthenticationError
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
import test_platform as fixtures


class AuditExportTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown

    def add_events(self, count=620):
        store = self.app.state.store
        with store.transaction() as db:
            for index in range(count):
                store.audit(
                    db, "A", self.project, "owner", "fixture-review", f"fixture-{index}"
                )
        with store.transaction() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT id FROM events WHERE tenant='A' AND project=? ORDER BY id",
                    (self.project,),
                )
            ]

    def capture(self, **kwargs):
        response = self.client.post(
            self.prefix + "/audit-exports",
            json={
                "note": "Owner needs a fictional audit archive",
                "request_key": "first-audit-window",
                **kwargs,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def data(self, entity):
        response = self.client.get(
            self.prefix + "/audit-exports/" + entity["id"] + "/download"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.headers["x-audit-manifest-sha256"],
            entity["payload"]["manifest_sha256"],
        )
        return response.content

    def test_export_exceeds_recent_view_and_remains_fixed_after_later_writes(self):
        identifiers = self.add_events()
        self.assertEqual(len(self.client.get(self.prefix + "/audit").json()), 500)
        entity = self.capture()
        manifest = entity["payload"]["manifest"]
        self.assertEqual(manifest["count"], 621)
        self.assertFalse(manifest["has_more"])
        self.add_events(3)
        rows = []
        for index in range(len(manifest["segments"])):
            result = self.client.get(
                self.prefix + f'/audit-exports/{entity["id"]}/pages/{index}'
            )
            self.assertEqual(result.status_code, 200, result.text)
            self.assertLessEqual(len(result.json()["rows"]), 200)
            rows.extend(result.json()["rows"])
        self.assertEqual([row["id"] for row in rows], identifiers)
        body = self.data(entity)
        verified = exports.verify_stream(
            io.BytesIO(body), entity["payload"]["manifest_sha256"]
        )
        self.assertEqual(verified["count"], 621)
        self.assertNotIn(b"a-secret", body)

    def test_bounded_windows_continue_with_original_highwater_without_later_events(
        self,
    ):
        expected = self.add_events()
        with patch.object(exports, "MAX_ROWS", 201):
            first = self.capture()
        self.assertTrue(first["payload"]["manifest"]["has_more"])
        self.assertEqual(first["payload"]["manifest"]["count"], 201)
        end = first["payload"]["manifest"]["until_id"]
        self.add_events(10)
        captured, current = [], first
        for index in range(4):
            manifest = current["payload"]["manifest"]
            body = self.data(current)
            verified = exports.verify_stream(
                io.BytesIO(body), current["payload"]["manifest_sha256"]
            )
            captured.extend(
                json.loads(line)["event"]["id"] for line in body.splitlines()[1:-1]
            )
            if not verified["has_more"]:
                break
            with patch.object(exports, "MAX_ROWS", 201):
                current = self.capture(
                    after_id=manifest["last_id"],
                    until_id=end,
                    request_key=f"continuation-{index}",
                )
        self.assertEqual(captured, expected)

    def test_byte_window_reports_remaining_history_and_empty_window_verifies(self):
        self.add_events(10)
        with patch.object(exports, "MAX_BYTES", 800):
            entity = self.capture()
        manifest = entity["payload"]["manifest"]
        self.assertLessEqual(manifest["byte_count"], 800)
        self.assertTrue(manifest["has_more"])
        self.assertTrue(
            exports.verify_stream(
                io.BytesIO(self.data(entity)), entity["payload"]["manifest_sha256"]
            )["verified"]
        )
        end = manifest["until_id"]
        empty = self.capture(after_id=end, until_id=end, request_key="empty-window")
        self.assertEqual(empty["payload"]["manifest"]["count"], 0)
        self.assertTrue(
            exports.verify_stream(
                io.BytesIO(self.data(empty)), empty["payload"]["manifest_sha256"]
            )["verified"]
        )

    def test_roles_tenants_pages_and_offset_bounds(self):
        entity = self.capture()
        paths = (
            self.prefix + "/audit-exports",
            self.prefix + "/audit-exports/" + entity["id"],
            self.prefix + "/audit-exports/" + entity["id"] + "/pages/0",
            self.prefix + "/audit-exports/" + entity["id"] + "/download",
        )
        for token, status in (
            ("review-secret", 403),
            ("infer-secret", 403),
            ("b-secret", 404),
        ):
            for path in paths:
                response = self.client.get(
                    path, headers={"Authorization": "Bearer " + token}
                )
                self.assertEqual(response.status_code, status, response.text)
            response = self.client.post(
                self.prefix + "/audit-exports",
                json={"note": "Attempt", "request_key": "forbidden"},
                headers={"Authorization": "Bearer " + token},
            )
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(
            self.client.get(paths[0] + "?offset=9223372036854775808").status_code, 422
        )
        self.assertEqual(
            self.client.get(paths[2].replace("/0", "/-1")).status_code, 404
        )
        self.assertEqual(
            self.client.get(paths[2].replace("/0", "/999999")).status_code, 404
        )

    def test_concurrent_request_replay_one_export_and_changed_content_conflicts(self):
        self.add_events(10)
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}

        def capture(_):
            service = Service(Store(self.home), runtime=object(), hosted=object())
            return exports.create(
                service, owner, self.project, "Concurrent archive", "race"
            )["id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            identifiers = list(pool.map(capture, range(4)))
        self.assertEqual(len(set(identifiers)), 1)
        self.assertEqual(
            len(self.app.state.store.list("A", self.project, "audit-export")), 1
        )
        self.add_events(2)
        replay = exports.create(
            self.app.state.service, owner, self.project, "Concurrent archive", "race"
        )
        self.assertEqual(replay["id"], identifiers[0])
        response = self.client.post(
            self.prefix + "/audit-exports",
            json={"note": "Changed purpose", "request_key": "race"},
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_deadline_and_storage_failure_publish_neither_export_nor_capture_event(
        self,
    ):
        before = self.add_events(10)
        with patch.object(exports, "CAPTURE_SECONDS", -1):
            response = self.client.post(
                self.prefix + "/audit-exports",
                json={"note": "Deadline", "request_key": "deadline"},
            )
            self.assertEqual(response.status_code, 400, response.text)
        store = self.app.state.store
        audit = store.audit

        def fail(db, tenant, project, actor, action, target):
            if action == "create.audit-export":
                raise OSError("Fictional storage failure")
            return audit(db, tenant, project, actor, action, target)

        with patch.object(store, "audit", side_effect=fail):
            with self.assertRaises(OSError):
                self.capture()
        self.assertEqual(store.list("A", self.project, "audit-export"), [])
        with store.transaction() as db:
            after = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM events WHERE project=? ORDER BY id", (self.project,)
                )
            ]
        self.assertEqual(before, after)
        self.capture()

    def test_reauthorization_after_capture_writes_rolls_back(self):
        self.add_events(3)
        auth = self.app.state.authentication
        original = auth.authenticate
        calls = []

        def authenticate(header):
            calls.append(header)
            if len(calls) == 3:
                raise AuthenticationError("Fixture revoked during capture")
            return original(header)

        with patch.object(auth, "authenticate", side_effect=authenticate):
            response = self.client.post(
                self.prefix + "/audit-exports",
                json={"note": "Revocation", "request_key": "revoked"},
            )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(
            self.app.state.store.list("A", self.project, "audit-export"), []
        )
        self.assertFalse(
            any(
                e["action"] == "create.audit-export"
                for e in self.app.state.store.events("A", self.project)
            )
        )

    def test_midstream_revocation_cannot_produce_verified_complete_file(self):
        self.add_events()
        entity = self.capture()
        auth = self.app.state.authentication
        original = auth.authenticate
        calls = []

        def authenticate(header):
            calls.append(header)
            if len(calls) >= 6:
                raise AuthenticationError("Fixture revoked while streaming")
            return original(header)

        with patch.object(auth, "authenticate", side_effect=authenticate):
            body = self.data(entity)
        self.assertIn(b'"error"', body)
        self.assertNotIn(b"Fixture revoked", body)
        with self.assertRaises(ValueError):
            exports.verify_stream(
                io.BytesIO(body), entity["payload"]["manifest_sha256"]
            )

    def test_source_tamper_refuses_page_stream_and_new_backup(self):
        identifiers = self.add_events(3)
        entity = self.capture()
        good = self.data(entity)
        with self.app.state.store.transaction() as db:
            db.execute(
                "UPDATE events SET action=? WHERE id=?",
                ("Different history", identifiers[0]),
            )
        response = self.client.get(
            self.prefix + f'/audit-exports/{entity["id"]}/pages/0'
        )
        self.assertEqual(response.status_code, 400, response.text)
        with self.assertRaises(ValueError):
            exports.verify_stream(
                io.BytesIO(self.data(entity)), entity["payload"]["manifest_sha256"]
            )
        self.assertTrue(
            exports.verify_stream(
                io.BytesIO(good), entity["payload"]["manifest_sha256"]
            )["verified"]
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                backup_workspace(self.home, Path(temporary) / "backup")

    def test_truncated_reordered_changed_extra_and_malformed_files_refuse(self):
        self.add_events(3)
        entity = self.capture()
        body = self.data(entity)
        lines = body.splitlines(keepends=True)
        footer = json.loads(lines[-1])
        fake_footer = {**footer, "complete": 1}
        candidates = [
            b"".join(lines[:-1]),
            body[:-1],
            body + b"{}\n",
            b"".join([lines[0], lines[2], lines[1], *lines[3:]]),
            b"".join([lines[0], exports.line({"event": None}), *lines[2:]]),
            b"".join([*lines[:-1], exports.line(fake_footer)]),
            b"".join([lines[0], b"x" * (exports.MAX_LINE + 1) + b"\n", *lines[2:]]),
        ]
        for bad in candidates:
            with self.assertRaises(ValueError):
                exports.verify_stream(
                    io.BytesIO(bad), entity["payload"]["manifest_sha256"]
                )
        with self.assertRaises(ValueError):
            exports.verify_stream(io.BytesIO(body), "0" * 64)

    def test_cli_verifies_receipt_without_echoing_audit_records(self):
        entity = self.capture()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.dmtlog"
            path.write_bytes(self.data(entity))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "decision_workbench.platform.cli",
                    "verify-audit-export",
                    "--file",
                    str(path),
                    "--manifest-sha256",
                    entity["payload"]["manifest_sha256"],
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["verified"])
        self.assertNotIn('"actor"', result.stdout)

    def test_restore_preserves_exact_captured_stream_after_recovery_events(self):
        self.add_events(3)
        entity = self.capture()
        body = self.data(entity)
        with tempfile.TemporaryDirectory() as temporary:
            backup, recovered = Path(temporary) / "backup", Path(temporary) / "restored"
            receipt = backup_workspace(self.home, backup)
            restore_workspace(backup, recovered, receipt["manifest_sha256"])
            with TestClient(create_app(recovered, fixtures.keys())) as client:
                client.headers.update({"Authorization": "Bearer a-secret"})
                response = client.get(
                    self.prefix + f'/audit-exports/{entity["id"]}/download'
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, body)

    def test_authorization_delay_cannot_publish_past_capture_deadline(self):
        self.add_events(3)
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        with patch.object(exports, "CAPTURE_SECONDS", 0.02):
            with self.assertRaisesRegex(ValueError, "operation window"):
                exports.create(
                    self.app.state.service,
                    owner,
                    self.project,
                    "Bounded authorization",
                    "auth-delay",
                    authorize=lambda: time.sleep(0.04),
                )
        self.assertEqual(
            self.app.state.store.list("A", self.project, "audit-export"), []
        )

    def test_real_credential_file_revocation_after_page_read_stops_event_delivery(self):
        self.add_events()
        entity = self.capture()
        auth = self.app.state.authentication
        with tempfile.TemporaryDirectory() as temporary:
            keys_file = Path(temporary) / "operator-keys.json"
            keys_file.write_text(json.dumps(fixtures.keys()), encoding="utf-8")
            auth.keys = None
            auth.keys_file = keys_file
            original = exports.page

            def revoke_after_read(*args, **kwargs):
                result = original(*args, **kwargs)
                keys_file.write_text(
                    json.dumps(
                        [
                            k
                            for k in fixtures.keys()
                            if not (k["tenant"] == "A" and k["actor"] == "owner")
                        ]
                    ),
                    encoding="utf-8",
                )
                return result

            with patch.object(exports, "page", side_effect=revoke_after_read):
                body = self.data(entity)
            self.assertIn(b'"error"', body)
            self.assertNotIn(b'"event"', body)
            self.assertEqual(
                self.client.get(self.prefix + "/audit-exports").status_code, 401
            )
            with self.assertRaises(ValueError):
                exports.verify_stream(
                    io.BytesIO(body), entity["payload"]["manifest_sha256"]
                )

    def test_bad_cursor_boolean_and_whitespace_purpose_refuse(self):
        for fields, status in (
            ({"after_id": True}, 422),
            ({"after_id": -1}, 422),
            ({"until_id": 9223372036854775808}, 422),
            ({"after_id": 2, "until_id": 1}, 400),
            ({"note": " "}, 400),
        ):
            response = self.client.post(
                self.prefix + "/audit-exports",
                json={"note": "Archive", "request_key": "invalid", **fields},
            )
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(
            self.app.state.store.list("A", self.project, "audit-export"), []
        )
