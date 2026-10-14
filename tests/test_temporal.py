"""Point-in-time contracts and actual authenticated preparation/worker wiring."""

import copy
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from decision_workbench.data import jsonl, prepare, read_records, validate, write_splits
from decision_workbench.intake import map_table
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store, digest
from decision_workbench.platform.worker import Worker
from decision_workbench.quality import quality_report
from decision_workbench.temporal import FIELDS, audit_splits, instant
from decision_workbench.training.contracts import load_splits, validate_splits
import test_platform as fixtures


def dated_rows(count=60):
    rows = fixtures.fixture_rows(count)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, row in enumerate(rows):
        decision = start + timedelta(days=i)
        row["metadata"]["temporal"] = {
            "decision_at": decision.isoformat(),
            "evidence_available_at": (decision - timedelta(hours=1)).isoformat(),
            "label_available_at": (decision + timedelta(hours=2)).isoformat(),
        }
    return rows


class TemporalTests(unittest.TestCase):
    def test_timezone_and_availability_validation(self):
        self.assertEqual(
            instant("2026-01-01T02:00:00+02:00"), instant("2026-01-01T00:00:00Z")
        )
        for value in (
            "2026-01-01",
            "2026-01-01T00:00:00",
            "2026-02-30T00:00:00Z",
            "2026-01-01T00:00:00+25:00",
            "2026-01-01T00:00:00+00:99",
            0,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                instant(value)
        for declaration in (None, {}, {"decision_at": "2026-01-01T00:00:00Z"}):
            row = dated_rows(1)[0]
            row["metadata"]["temporal"] = declaration
            with self.assertRaises(ValueError):
                validate(row)
        row = dated_rows(1)[0]
        row["metadata"]["temporal"]["evidence_available_at"] = "2026-01-02T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "after the decision"):
            validate(row)

    def test_chronological_reservation_is_seed_independent_and_preserves_families(self):
        rows = dated_rows()
        rows[3]["group_id"] = rows[0]["group_id"]
        splits = prepare(list(reversed(rows)))
        self.assertEqual(splits, prepare(list(reversed(rows)), seed=7))
        validate_splits(splits)
        report = audit_splits(splits)
        self.assertEqual(report["strategy"], "chronological")
        self.assertEqual(report["timestamped_real_rows"], 60)
        self.assertEqual(sum(map(len, splits.values())), 60)
        locations = {row["id"]: name for name, part in splits.items() for row in part}
        self.assertEqual(locations["case-0"], locations["case-3"])
        # Interval joins pull the intervening independent cases into one family.
        self.assertEqual(locations["case-0"], locations["case-2"])

    def test_missing_dates_random_override_and_overlapping_windows_refused(self):
        rows = dated_rows()
        del rows[0]["metadata"]["temporal"]
        with self.assertRaisesRegex(ValueError, "every real record"):
            prepare(rows)
        report = quality_report(rows, fixtures.QUESTIONS)
        self.assertEqual(report["temporal_coverage"]["strategy"], "incomplete")
        self.assertTrue(
            any(
                "every real record" in issue for issue in report["preparation_blockers"]
            )
        )
        with self.assertRaisesRegex(ValueError, "chronological reservations"):
            prepare(dated_rows(), split_strategy="group-random")
        with self.assertRaises(ValueError):
            prepare(fixtures.fixture_rows(), split_strategy="chronological")
        rows = dated_rows(6)
        rows[0]["metadata"]["temporal"]["label_available_at"] = rows[-1]["metadata"][
            "temporal"
        ]["decision_at"]
        with self.assertRaisesRegex(ValueError, "nonoverlapping"):
            prepare(rows)

    def test_manual_files_refuse_future_labels_and_equal_boundary(self):
        splits = prepare(dated_rows())
        for field in ("decision_at", "label_available_at"):
            bad = copy.deepcopy(splits)
            bad["train"][0]["metadata"]["temporal"][field] = bad["validation"][0][
                "metadata"
            ]["temporal"]["decision_at"]
            with self.assertRaisesRegex(ValueError, "Temporal leakage"):
                validate_splits(bad)
            with tempfile.TemporaryDirectory() as folder:
                write_splits(bad, folder)
                with self.assertRaisesRegex(ValueError, "Temporal leakage"):
                    load_splits(folder)

    def test_synthetic_seed_from_future_refused_and_undated_extension_supported(self):
        rows = dated_rows()
        synthetic = copy.deepcopy(rows[-1])
        synthetic.update(
            id="synth", group_id="synthetic-family", state="A new hypothetical request"
        )
        synthetic["metadata"] = {
            "origin": "synthetic",
            "reviewed": True,
            "seed_ids": [rows[-1]["id"]],
        }
        with self.assertRaisesRegex(ValueError, "Temporal leakage"):
            prepare(rows + [synthetic])
        splits = prepare(rows)
        synthetic["metadata"]["seed_ids"] = [splits["train"][0]["id"]]
        splits["train"].append(synthetic)
        validate_splits(splits)
        self.assertEqual(
            audit_splits(splits)["ranges"]["train"]["undated_synthetic_rows"], 1
        )

    def test_legacy_undated_random_behavior_and_explicit_warning(self):
        rows = fixtures.fixture_rows()
        self.assertEqual(prepare(rows), prepare(rows, split_strategy="group-random"))
        self.assertEqual(audit_splits(prepare(rows))["ranges"], {})
        self.assertTrue(
            any(
                "has not been checked" in w
                for w in quality_report(rows, fixtures.QUESTIONS)["warnings"]
            )
        )

    def test_csv_xlsx_mapping_preserves_dates_and_rejects_partial_mapping(self):
        from openpyxl import Workbook

        row = dated_rows(1)[0]
        stamps = row["metadata"]["temporal"]
        mapping = {
            "state_columns": ["text"],
            "id_column": "id",
            "temporal_columns": {f: f for f in FIELDS},
        }
        columns = ["id", "text", *FIELDS]
        values = ["mapped", "Fictional evidence", *(stamps[f] for f in FIELDS)]
        csv = (",".join(columns) + "\n" + ",".join(values) + "\n").encode()
        wb = Workbook()
        wb.active.append(columns)
        wb.active.append(values)
        body = io.BytesIO()
        wb.save(body)
        wb.close()
        for content, filename in ((csv, "input.csv"), (body.getvalue(), "input.xlsx")):
            with self.subTest(filename=filename):
                imported = map_table(content, filename, fixtures.QUESTIONS, mapping)[0]
                self.assertEqual(imported["metadata"]["temporal"], stamps)
                self.assertFalse(imported["metadata"]["reviewed"])
        with self.assertRaises(ValueError):
            map_table(
                csv,
                "input.csv",
                fixtures.QUESTIONS,
                {**mapping, "temporal_columns": {"decision_at": "decision_at"}},
            )
        # Maintenance simple-table import also preserves the canonical date columns.
        q = {"refund": fixtures.QUESTIONS["refund"]}
        simple = (
            "id,state,refund,reviewed,"
            + ",".join(FIELDS)
            + "\nx,evidence,true,true,"
            + ",".join(stamps[f] for f in FIELDS)
            + "\n"
        ).encode()
        self.assertEqual(
            read_records(simple, "simple.csv", {"questions": q})[0]["metadata"][
                "temporal"
            ],
            stamps,
        )


class TemporalPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.app = create_app(self.home, fixtures.keys())
        self.client = TestClient(self.app, headers={"Authorization": "Bearer a-secret"})
        self.owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        self.project = self.app.state.service.create_project(
            self.owner, "Temporal fixture", "Fictional policy", fixtures.QUESTIONS
        )["id"]
        self.prefix = "/v1/projects/" + self.project

    def tearDown(self):
        self.client.close()
        self.app.state.service.runtime.close()
        self.temp.cleanup()

    def dataset(self):
        return self.app.state.service.save_dataset(
            self.owner, self.project, dated_rows(120), {"fixture": True}
        )["id"]

    def submit(self, kind, config, key):
        response = self.client.post(
            self.prefix + "/jobs",
            json={"kind": kind, "config": config, "request_key": key},
        )
        self.assertEqual(response.status_code, 202, response.text)
        worker = Worker(Store(self.home))
        try:
            worker.once()
        finally:
            worker.service.runtime.close()
        return self.app.state.store.job(response.json()["id"], "A", self.project)

    def test_authenticated_worker_snapshot_policy_and_pretraining_refusal(self):
        dataset = self.dataset()
        for strategy in ("unexpected", None, []):
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "prepare",
                    "config": {"datasets": [dataset], "split_strategy": strategy},
                    "request_key": "invalid",
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
        failed = self.submit(
            "prepare",
            {"datasets": [dataset], "split_strategy": "group-random"},
            "random-refused",
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.app.state.store.list("A", self.project, "snapshot"), [])
        job = self.submit(
            "prepare",
            {"datasets": [dataset], "split_strategy": "chronological"},
            "temporal",
        )
        self.assertEqual(job["status"], "succeeded", job["error"])
        snapshot = self.app.state.store.get(
            job["result"]["snapshot"], "A", self.project, "snapshot"
        )
        self.assertEqual(
            snapshot["payload"]["temporal_audit"]["strategy"], "chronological"
        )
        bad = copy.deepcopy(snapshot["payload"])
        bad["splits"]["train"][0]["metadata"]["temporal"]["label_available_at"] = bad[
            "splits"
        ]["test"][0]["metadata"]["temporal"]["decision_at"]
        bad["sha256"] = digest(bad["splits"])
        corrupt = self.app.state.store.add("snapshot", "A", self.project, "owner", bad)
        self.app.state.service.configure_release_policy(
            self.owner, self.project, 0.0, 0.0, 1.0, 10, None
        )
        trained = self.submit(
            "train",
            {"snapshot": corrupt["id"], "backend": "cpu-baseline"},
            "future-refused",
        )
        self.assertEqual(trained["status"], "failed")
        self.assertIn("Temporal leakage", trained["error"])
        self.assertEqual(self.app.state.store.list("A", self.project, "model"), [])
        self.assertFalse((self.home / "artifacts").exists())

    def test_synthetic_extension_preserves_roles_and_refuses_strategy_change(self):
        job = self.submit("prepare", {"datasets": [self.dataset()]}, "reserve-base")
        base_id = job["result"]["snapshot"]
        base = self.app.state.store.get(base_id, "A", self.project, "snapshot")[
            "payload"
        ]
        candidate = copy.deepcopy(base["splits"]["train"][0])
        candidate.update(
            id="synthetic-extension",
            group_id="candidate-family",
            state="Hypothetical billing question",
        )
        candidate["metadata"] = {
            "origin": "synthetic",
            "reviewed": True,
            "seed_ids": [base["splits"]["train"][0]["id"]],
        }
        dataset = self.app.state.service.save_dataset(
            self.owner, self.project, [candidate], {"fixture": True}
        )["id"]
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {
                    "datasets": [dataset],
                    "base_snapshot": base_id,
                    "split_strategy": "group-random",
                },
                "request_key": "bypass",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        job = self.submit(
            "prepare", {"datasets": [dataset], "base_snapshot": base_id}, "extend-base"
        )
        self.assertEqual(job["status"], "succeeded", job["error"])
        extended = self.app.state.store.get(
            job["result"]["snapshot"], "A", self.project, "snapshot"
        )["payload"]
        for role in ("validation", "calibration", "test"):
            self.assertEqual(extended["splits"][role], base["splits"][role])
        self.assertEqual(
            extended["temporal_audit"]["ranges"]["train"]["undated_synthetic_rows"], 1
        )

    def test_actual_chronological_baseline_training_and_backup_restore(self):
        from decision_workbench.platform.recovery import (
            backup_workspace,
            restore_workspace,
        )

        job = self.submit("prepare", {"datasets": [self.dataset()]}, "prepare-training")
        snapshot_id = job["result"]["snapshot"]
        self.app.state.service.configure_release_policy(
            self.owner, self.project, 0.0, 0.0, 1.0, 10, None
        )
        trained = self.submit(
            "train",
            {"snapshot": snapshot_id, "backend": "cpu-baseline"},
            "chronological-training",
        )
        self.assertEqual(trained["status"], "succeeded", trained["error"])
        self.assertEqual(len(self.app.state.store.list("A", self.project, "model")), 1)
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            backup = backup_workspace(self.home, folder / "backup")
            restore_workspace(
                backup["destination"], folder / "restored", backup["manifest_sha256"]
            )
            restored = Store(folder / "restored").get(
                snapshot_id, "A", self.project, "snapshot"
            )["payload"]
            self.assertEqual(
                restored["temporal_audit"], audit_splits(restored["splits"])
            )
        # A valid data hash does not make contaminated chronology restorable.
        bad = copy.deepcopy(restored)
        bad["splits"]["train"][0]["metadata"]["temporal"]["label_available_at"] = bad[
            "splits"
        ]["test"][0]["metadata"]["temporal"]["decision_at"]
        bad["sha256"] = digest(bad["splits"])
        self.app.state.store.add("snapshot", "A", self.project, "owner", bad)
        with (
            tempfile.TemporaryDirectory() as name,
            self.assertRaisesRegex(ValueError, "Temporal leakage"),
        ):
            backup_workspace(self.home, Path(name) / "refused")

    def test_browser_reserves_chronological_data_over_actual_http(self):
        dataset = self.dataset()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("HTTP startup timed out")
                time.sleep(0.02)
            at = AppTest.from_file(str(Path("app.py").resolve()))
            at.session_state["credential"] = "a-secret"
            at.session_state["api_url"] = (
                f"http://127.0.0.1:{listener.getsockname()[1]}"
            )
            at.session_state["principal"] = self.owner
            at.run(timeout=30)
            self.assertFalse(at.exception)
            next(
                w
                for w in at.multiselect
                if w.label.startswith("Dataset versions to prepare")
            ).select(dataset)
            next(w for w in at.selectbox if w.label == "Reservation strategy").select(
                "chronological"
            ).run(timeout=30)
            next(
                w
                for w in at.button
                if w.label == "Reserve train / validation / calibration / test"
            ).click().run(timeout=30)
            self.assertFalse(at.exception)
            queued = self.app.state.store.jobs("A", self.project)[0]
            self.assertEqual(queued["config"]["split_strategy"], "chronological")
            worker = Worker(self.app.state.store)
            try:
                worker.once()
            finally:
                worker.service.runtime.close()
            current = self.app.state.store.job(queued["id"], "A", self.project)
            self.assertEqual(current["status"], "succeeded", current["error"])
            at.run(timeout=30)
            self.assertFalse(at.exception)
            self.assertTrue(
                any(
                    e.label == "Declared temporal reservation audit"
                    for e in at.expander
                )
            )
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())
