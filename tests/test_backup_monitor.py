"""Signed backup health, public-key-only reads, incident scope and readiness controls."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform import backup_monitor as monitor
from decision_workbench.platform import backup_schedule as schedule
from decision_workbench.platform import encrypted_recovery as encrypted
from decision_workbench.platform.store import encode
import test_backup_schedule as fixtures
from test_platform import keys, QUESTIONS


class BackupMonitorTests(unittest.TestCase):
    setUp = fixtures.BackupScheduleTests.setUp
    tearDown = fixtures.BackupScheduleTests.tearDown
    configure = fixtures.BackupScheduleTests.configure

    def setup_monitor(self, required=False):
        self.configure()
        self.signing = self.folder / "monitor.dmtkey"
        self.public = self.folder / "monitor.dmtpub"
        self.health = self.folder / ".dmt-backup-health.json"
        self.profile = self.folder / ".dmt-backup-monitor.json"
        monitor.create_keys(self.signing, self.public)
        monitor.configure_monitor(
            self.config,
            self.key,
            self.public,
            self.health,
            self.profile,
            max_snapshot_age=30,
            max_backup_age=60,
            required=required,
        )
        self.publisher = monitor.Publisher(
            self.config, self.key, self.signing, self.health
        )
        self.reader = monitor.Reader(self.home, self.profile)

    def healthy(self):
        schedule.run_due(self.config, self.key)
        self.publisher.publish()
        self.assertEqual(self.reader.read()["state"], "healthy")

    def resign(self, payload):
        signature = self.publisher.key.sign(
            monitor.CONTEXT + encode(payload).encode()
        ).hex()
        self.health.write_text(encode({"payload": payload, "signature": signature}))

    def test_public_key_reader_never_loads_decryption_or_signing_secrets(self):
        self.setup_monitor()
        self.healthy()
        with patch.object(
            encrypted, "_key", side_effect=AssertionError("Reader accessed backup key")
        ):
            report = self.reader.read()
        self.assertTrue(report["healthy"])
        body = encode(report)
        for private in (
            str(self.home),
            str(self.catalog),
            str(self.key),
            self.publisher.policy["id"],
        ):
            self.assertNotIn(private, body)
        self.assertNotIn("receipt", body)

    def test_no_success_stale_snapshot_and_stale_backup_are_distinct(self):
        self.setup_monitor()
        self.publisher.publish()
        self.assertEqual(self.reader.read()["state"], "no_success")
        self.healthy()
        payload = json.loads(self.health.read_text())["payload"]
        payload["generated_at"] -= 40
        payload["last_success_at"] -= 40
        payload["last_attempt"]["started"] -= 40
        payload["last_attempt"]["finished"] -= 40
        self.resign(payload)
        self.assertEqual(self.reader.read()["state"], "snapshot_stale")
        payload["generated_at"] = time.time()
        payload["last_success_at"] = time.time() - 100
        payload["last_attempt"] = None
        self.resign(payload)
        self.assertEqual(self.reader.read()["state"], "backup_stale")

    def test_capture_failure_overdue_and_retention_attention(self):
        self.setup_monitor()
        with patch.object(
            schedule.encrypted,
            "backup_encrypted",
            side_effect=OSError("Fictional capture failure"),
        ):
            with self.assertRaises(OSError):
                schedule.run_due(self.config, self.key)
        self.publisher.publish()
        self.assertEqual(self.reader.read()["state"], "capture_failed")
        payload = json.loads(self.health.read_text())["payload"]
        payload["last_success_at"] = time.time() - 1
        payload["latest_bundle_available"] = True
        payload["last_attempt"] = {
            "status": "running",
            "started": time.time() - 20,
            "finished": None,
        }
        payload["capture_timeout_seconds"] = 10
        self.resign(payload)
        self.assertEqual(self.reader.read()["state"], "capture_overdue")
        payload["last_attempt"] = None
        payload["retention_attention"] = True
        self.resign(payload)
        self.assertEqual(self.reader.read()["state"], "retention_attention")

    def test_catalog_failure_publishes_signed_unavailability_without_exception_details(
        self,
    ):
        self.setup_monitor()
        self.healthy()
        with patch.object(
            schedule,
            "status",
            side_effect=ValueError("Private enterprise marker and key details"),
        ):
            self.publisher.publish()
        self.assertEqual(self.reader.read()["state"], "unavailable")
        self.assertNotIn("Private enterprise", self.health.read_text())

    def test_tamper_wrong_policy_wrong_workspace_and_future_dates_refuse(self):
        self.setup_monitor()
        self.healthy()
        original = json.loads(self.health.read_text())
        changed = copy.deepcopy(original)
        changed["payload"]["last_success_at"] = 0
        self.health.write_text(encode(changed))
        self.assertEqual(self.reader.read()["state"], "unavailable")
        for field, value in (
            ("policy_id", "0" * 32),
            ("workspace_sha256", "0" * 64),
            ("generated_at", time.time() + 60),
        ):
            payload = copy.deepcopy(original["payload"])
            payload[field] = value
            self.resign(payload)
            self.assertEqual(self.reader.read()["state"], "unavailable", field)
        self.health.write_text(encode(original))
        self.assertEqual(
            monitor.Reader(self.folder / "other-home", self.profile).read()["state"],
            "unavailable",
        )

    def test_bounded_malformed_monitor_inputs_and_required_profile_disappearance(self):
        self.setup_monitor(required=True)
        self.healthy()
        self.health.write_bytes(b"x" * 4097)
        self.assertEqual(self.reader.read()["state"], "unavailable")
        self.profile.unlink()
        report = self.reader.read()
        self.assertTrue(report["required"])
        self.assertFalse(report["healthy"])
        self.assertFalse(monitor.Reader(self.home, "").read()["configured"])

    def test_http_incidents_scope_recovery_readiness_and_no_catalog_exposure(self):
        self.setup_monitor(required=True)
        other = self.service.create_project(
            {"tenant": "B", "actor": "other", "role": "admin"},
            "Other tenant",
            "Independent fictional policy",
            QUESTIONS,
        )["id"]
        self.publisher.publish()
        with patch.dict(os.environ, {"DMT_BACKUP_MONITOR_PROFILE": str(self.profile)}):
            app = create_app(self.home, keys())
            with TestClient(app) as client:
                client.headers.update({"Authorization": "Bearer a-secret"})
                self.assertEqual(client.get("/ready").status_code, 503)
                route = f"/v1/projects/{self.project}/operations"
                response = client.get(route)
                self.assertEqual(response.status_code, 200, response.text)
                health = response.json()
                incidents = [
                    i for i in health["incidents"] if i["kind"] == "backup_unhealthy"
                ]
                self.assertEqual(len(incidents), 1)
                self.assertEqual(incidents[0]["status"], "open")
                self.assertEqual(
                    client.get(f"/v1/projects/{other}/operations").status_code, 404
                )
                for private in (
                    str(self.key),
                    str(self.catalog),
                    str(self.config),
                    self.publisher.policy["id"],
                ):
                    self.assertNotIn(private, response.text)
                self.healthy()
                app.state.monitor.check()
                self.assertEqual(client.get("/ready").status_code, 200)
                result = client.get(route).json()
                incident = next(
                    i for i in result["incidents"] if i["kind"] == "backup_unhealthy"
                )
                self.assertEqual(incident["status"], "resolved")
                history = client.get(
                    route + f"/incidents/{incident['id']}/history"
                ).json()
                self.assertTrue(any(h["action"] == "resolved" for h in history))

    def test_optional_monitor_does_not_remove_serving_readiness_on_missing_snapshot(
        self,
    ):
        self.setup_monitor(required=False)
        with patch.dict(os.environ, {"DMT_BACKUP_MONITOR_PROFILE": str(self.profile)}):
            app = create_app(self.home, keys())
            with TestClient(app) as client:
                self.assertEqual(client.get("/ready").status_code, 200)
                self.assertFalse(app.state.monitor.state()["backup"]["healthy"])

    def test_cli_keys_profile_publisher_and_no_raw_signing_key_output(self):
        self.configure()
        self.signing, self.public = (
            self.folder / "monitor.dmtkey",
            self.folder / "monitor.dmtpub",
        )
        self.profile, self.health = (
            self.folder / ".dmt-profile.json",
            self.folder / ".dmt-health.json",
        )
        prefix = [sys.executable, "-m", "decision_workbench.platform.cli"]
        result = subprocess.run(
            prefix
            + [
                "create-backup-monitor-key",
                "--private-file",
                str(self.signing),
                "--public-file",
                str(self.public),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.signing.read_bytes()[8:].hex(), result.stdout)
        profile = subprocess.run(
            prefix
            + [
                "configure-backup-monitor",
                "--config",
                str(self.config),
                "--key-file",
                str(self.key),
                "--public-key-file",
                str(self.public),
                "--health-file",
                str(self.health),
                "--output",
                str(self.profile),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(profile.returncode, 0, profile.stderr)
        run = subprocess.run(
            prefix
            + [
                "backup-run",
                "--config",
                str(self.config),
                "--key-file",
                str(self.key),
                "--monitor-key-file",
                str(self.signing),
                "--health-file",
                str(self.health),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            monitor.Reader(self.home, self.profile).read()["state"], "healthy"
        )

    def test_missing_latest_bundle_and_profile_disable_do_not_claim_recovery(self):
        self.setup_monitor()
        self.healthy()
        latest = schedule.status(self.config, self.key)["runs"][0]
        folder = self.vault / ("run-" + latest["id"])
        (folder / encrypted.PAYLOAD).unlink()
        self.publisher.publish()
        report = self.reader.read()
        self.assertEqual(report["state"], "backup_missing")
        self.store.collect_backup_operations(report)
        existing = self.store.operations("A", self.project)["incidents"]
        incident = next(i for i in existing if i["kind"] == "backup_unhealthy")
        self.store.collect_backup_operations(monitor.Reader(self.home, "").read())
        self.assertEqual(
            next(
                i
                for i in self.store.operations("A", self.project)["incidents"]
                if i["id"] == incident["id"]
            )["status"],
            "open",
        )

    def test_snapshot_targets_cannot_overwrite_private_control_or_workspace_files(self):
        self.setup_monitor()
        for output in (
            self.key,
            self.signing,
            self.config,
            self.catalog,
            self.home / ".dmt-data.json",
            self.vault / ".dmt-data.json",
        ):
            with self.subTest(output=output.name), self.assertRaises(ValueError):
                monitor.Publisher(self.config, self.key, self.signing, output)

    def test_backup_incidents_survive_backup_restore_and_schema7_guard(self):
        self.setup_monitor()
        self.publisher.publish()
        self.store.collect_backup_operations(self.reader.read())
        captured = encrypted.backup_encrypted(
            self.home, self.folder / "captured", self.key
        )
        restored = encrypted.restore_encrypted(
            captured["destination"],
            self.folder / "restored",
            self.key,
            captured["encrypted_manifest_sha256"],
        )
        self.assertEqual(restored["restored_schema"], 7)
        from decision_workbench.platform.store import Store

        incidents = Store(restored["destination"]).operations("A", self.project)[
            "incidents"
        ]
        self.assertTrue(
            any(
                i["kind"] == "backup_unhealthy" and i["target"] == "workspace-backups"
                for i in incidents
            )
        )

    def test_independent_health_thread_refreshes_while_catalog_has_running_capture(
        self,
    ):
        self.setup_monitor()
        self.healthy()
        policy, signed, key = schedule.load(self.config, self.key)
        state = schedule._state(policy, signed, key)
        import uuid

        schedule._append(policy, key, state, uuid.uuid4().hex, "capture.begin")
        self.publisher.start(interval=1)
        try:
            deadline = time.monotonic() + 5
            initial = None
            while True:
                try:
                    payload = json.loads(self.health.read_text())["payload"]
                except PermissionError:
                    # Windows can briefly deny a read during atomic replacement.
                    if time.monotonic() > deadline:
                        self.fail("Backup health snapshot remained unreadable")
                    time.sleep(0.05)
                    continue
                if initial is None:
                    initial = payload["generated_at"]
                elif payload["generated_at"] > initial:
                    break
                if time.monotonic() > deadline:
                    self.fail("Independent backup health thread did not refresh")
                time.sleep(0.05)
            self.assertEqual(
                payload["last_attempt"]["status"],
                "running",
            )
            self.assertTrue(self.reader.read()["healthy"])
        finally:
            self.publisher.close()
        self.assertFalse(self.publisher.thread.is_alive())

    def test_actual_backup_service_publishes_signed_idle_heartbeats(self):
        self.setup_monitor()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.cli",
                "backup-service",
                "--config",
                str(self.config),
                "--key-file",
                str(self.key),
                "--monitor-key-file",
                str(self.signing),
                "--health-file",
                str(self.health),
                "--health-interval-seconds",
                "1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        try:
            initial = None
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    self.fail(process.communicate()[1].decode())
                if self.health.exists() and self.reader.read()["healthy"]:
                    generated = json.loads(self.health.read_text())["payload"][
                        "generated_at"
                    ]
                    if initial is None:
                        initial = generated
                    elif generated > initial + 0.5:
                        break
                if time.monotonic() > deadline:
                    self.fail("Backup service did not publish fresh idle heartbeats")
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    def test_browser_backup_warning_and_healthy_summary_over_live_http(self):
        self.setup_monitor()
        self.publisher.publish()
        from streamlit.testing.v1 import AppTest
        from http_server_fixture import LiveServer

        with patch.dict(os.environ, {"DMT_BACKUP_MONITOR_PROFILE": str(self.profile)}):
            with LiveServer(self.home, keys()) as server:
                app = AppTest.from_file(
                    str(Path(__file__).resolve().parents[1] / "app.py"),
                    default_timeout=30,
                ).run()
                app.text_input[0].set_value(server.url)
                app.text_input[1].set_value("a-secret")
                next(b for b in app.button if b.label == "Connect").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(
                    any(w.value == monitor.STATES["no_success"] for w in app.warning)
                )
                self.healthy()
                server.app.state.monitor.check()
                app.run()
                self.assertEqual(len(app.error), 0)
                self.assertTrue(
                    any(w.value == monitor.STATES["healthy"] for w in app.caption)
                )
