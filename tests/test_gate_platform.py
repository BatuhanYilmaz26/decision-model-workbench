"""Real authenticated API/storage/worker applicability workflow and counterexamples."""

import copy
import csv
import io
import json
import tempfile
import socket
import threading
import time
import zipfile
import hashlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from decision_workbench.data import jsonl
from decision_workbench.gate_data import reserve
from decision_workbench.platform.api import create_app
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.store import Store, digest
from decision_workbench.platform.worker import Worker
from test_platform import QUESTIONS, fixture_rows, keys


def gate_rows(count=40):
    return [
        {
            "id": f"weather-{index}",
            "group_id": f"weather-source-{index}",
            "state": f"Fictional weather bulletin {index}: clear sky.",
            "negative_for": list(QUESTIONS),
            "reason": "No support, refund or service evidence",
            "metadata": {
                "origin": "real",
                "reviewed": True,
                "review_status": "approved",
                "reviewer": "forged",
                "review_note": "forged",
                "fictional_fixture": True,
            },
        }
        for index in range(count)
    ]


class GatePlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.app = create_app(
            self.home,
            keys()
            + [
                {
                    "key_sha256": hashlib.sha256(b"editor-secret").hexdigest(),
                    "tenant": "A",
                    "actor": "editor",
                    "role": "editor",
                }
            ],
        )
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        response = self.client.post(
            "/v1/projects",
            json={
                "name": "Support applicability",
                "description": "Route support requests and refuse questions unrelated to support.",
                "questions": QUESTIONS,
            },
        )
        self.project = response.json()["id"]
        self.prefix = "/v1/projects/" + self.project

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def upload(self, rows=None):
        response = self.client.post(
            self.prefix + "/gate-datasets",
            files={"file": ("weather.jsonl", jsonl(rows or gate_rows()).encode())},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def review(self, dataset, row, status="approved", token="review-secret"):
        return self.client.post(
            self.prefix + "/gate-datasets/" + dataset["id"] + "/reviews",
            headers={"Authorization": "Bearer " + token},
            json={
                "record_id": row["id"],
                "negative_for": row["negative_for"],
                "reason": row["reason"],
                "status": status,
                "expected_hash": dataset["payload"]["sha256"],
                "note": "Fictional example reviewed for mechanics; no actual domain quality claim",
            },
        )

    def approve_all(self, dataset):
        for row in self.app.state.store.dataset_rows(
            dataset["id"], "A", self.project, kind="gate-dataset"
        ):
            response = self.review(dataset, row)
            self.assertEqual(response.status_code, 201, response.text)
            dataset = response.json()
        return dataset

    def positive_snapshot(self):
        principal = {"tenant": "A", "role": "admin", "actor": "owner"}
        dataset = self.app.state.service.save_dataset(
            principal,
            self.project,
            fixture_rows(),
            {"scope": "fictional positive mechanics fixture"},
        )
        job = self.app.state.service.submit(
            principal,
            self.project,
            "prepare",
            {"datasets": [dataset["id"]]},
            "positive-reservation",
        )
        Worker(self.app.state.store).once()
        finished = self.app.state.store.job(job["id"], "A", self.project)
        self.assertEqual(finished["status"], "succeeded", finished)
        return self.app.state.store.get(
            finished["result"]["snapshot"], "A", self.project, "snapshot"
        )

    def prepare(self, positive, dataset, key="gate-reservation", independent=False):
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {"snapshot": positive["id"], "datasets": [dataset["id"]]},
                "request_key": key,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        if independent:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "decision_workbench.platform.worker",
                    "--home",
                    str(self.home),
                    "--once",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
        else:
            Worker(self.app.state.store).once()
        return self.app.state.store.job(response.json()["id"], "A", self.project)

    def test_upload_cannot_forge_approval_and_positive_routes_cannot_mix(self):
        dataset = self.upload(gate_rows(2))
        rows = self.client.get(
            self.prefix + "/gate-datasets/" + dataset["id"] + "/rows"
        ).json()["rows"]
        self.assertEqual(dataset["kind"], "gate-dataset")
        self.assertTrue(
            all(
                not row["metadata"]["reviewed"]
                and row["metadata"]["review_status"] == "pending"
                and "reviewer" not in row["metadata"]
                and "review_note" not in row["metadata"]
                for row in rows
            )
        )
        self.assertEqual(
            self.client.get(
                self.prefix + "/datasets/" + dataset["id"] + "/rows"
            ).status_code,
            404,
        )
        invalid = gate_rows(1)
        invalid[0]["gold"] = {}
        response = self.client.post(
            self.prefix + "/gate-examples", json={"examples": invalid}
        )
        self.assertEqual(response.status_code, 400, response.text)
        with self.assertRaises(LookupError):
            self.app.state.store.verify_dataset(dataset["id"], "A", self.project)

    def test_review_append_history_cas_permissions_and_restart(self):
        dataset = self.upload(gate_rows(1))
        row = gate_rows(1)[0]
        rejected = self.review(dataset, row, "rejected")
        self.assertEqual(rejected.status_code, 201, rejected.text)
        approved = self.review(rejected.json(), row)
        self.assertEqual(approved.status_code, 201, approved.text)
        self.assertEqual(self.review(dataset, row).status_code, 409)
        for token, expected in (("infer-secret", 403), ("b-secret", 404)):
            response = self.client.get(
                self.prefix + "/gate-datasets/" + dataset["id"] + "/rows",
                headers={"Authorization": "Bearer " + token},
            )
            self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(
            self.client.post(
                self.prefix + "/gate-examples",
                headers={"Authorization": "Bearer review-secret"},
                json={"examples": gate_rows(1)},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.review(approved.json(), row, token="editor-secret").status_code, 403
        )
        current = approved.json()
        new_row = gate_rows(2)[1]
        appended = self.client.post(
            self.prefix + "/gate-datasets/" + current["id"] + "/examples",
            json={"examples": [new_row], "expected_hash": current["payload"]["sha256"]},
        )
        self.assertEqual(appended.status_code, 201, appended.text)
        reopened = Store(self.home)
        history = reopened.verify_dataset(
            dataset["id"], "A", self.project, kind="gate-dataset"
        )
        self.assertFalse(history[0]["metadata"]["reviewed"])
        latest = reopened.verify_dataset(
            appended.json()["id"], "A", self.project, kind="gate-dataset"
        )
        self.assertEqual(len(latest), 2)
        self.assertEqual(latest[0]["metadata"]["reviewer"], "reviewer")
        self.assertFalse(latest[1]["metadata"]["reviewed"])
        self.assertEqual(
            reopened.dataset_head(
                dataset["id"], "A", self.project, kind="gate-dataset"
            ),
            appended.json()["id"],
        )

    def test_all_five_upload_formats_mapping_and_atomic_refusal(self):
        from openpyxl import Workbook

        row = gate_rows(1)[0]
        columns = ["source", "family", "text", "questions", "explanation"]
        values = [
            row["id"],
            row["group_id"],
            row["state"],
            json.dumps(row["negative_for"]),
            row["reason"],
        ]
        mapping = {
            "id_column": "source",
            "group_column": "family",
            "state_column": "text",
            "questions_column": "questions",
            "reason_column": "explanation",
        }
        csv_text = io.StringIO()
        csv.writer(csv_text).writerows([columns, values])
        tsv = io.StringIO()
        csv.writer(tsv, delimiter="\t").writerows([columns, values])
        workbook = Workbook()
        workbook.active.title = "Cover"
        sheet = workbook.create_sheet("Evidence")
        sheet.append(columns)
        sheet.append(values)
        xlsx = io.BytesIO()
        workbook.save(xlsx)
        workbook.close()
        for filename, content, chosen in (
            ("data.csv", csv_text.getvalue().encode(), mapping),
            ("data.tsv", tsv.getvalue().encode(), mapping),
            ("data.xlsx", xlsx.getvalue(), mapping | {"worksheet": "Evidence"}),
            ("data.json", json.dumps([row]).encode(), None),
            ("data.jsonl", jsonl([row]).encode(), None),
        ):
            with self.subTest(filename=filename):
                response = self.client.post(
                    self.prefix + "/gate-datasets",
                    files={"file": (filename, content)},
                    data={"mapping": json.dumps(chosen)} if chosen else {},
                )
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()["payload"]["reviewed_count"], 0)
        before = len(self.app.state.store.list("A", self.project, "gate-dataset"))
        bad = gate_rows(2)
        bad[1]["negative_for"] = ["unknown"]
        response = self.client.post(
            self.prefix + "/gate-datasets",
            files={"file": ("invalid.json", json.dumps(bad).encode())},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            len(self.app.state.store.list("A", self.project, "gate-dataset")), before
        )

    def test_pending_reservation_fails_then_reviewed_worker_and_backup_restore(self):
        positive = self.positive_snapshot()
        dataset = self.upload()
        failed = self.prepare(positive, dataset, "pending-reservation")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("approval", failed["error"])
        approved = self.approve_all(dataset)
        finished = self.prepare(positive, approved, independent=True)
        self.assertEqual(finished["status"], "succeeded", finished)
        identifier = finished["result"]["gate_snapshot"]
        value = self.app.state.service.gates.verify_snapshot(
            "A", self.project, identifier
        )
        self.assertEqual(value["snapshot_sha256"], positive["payload"]["sha256"])
        self.assertEqual(
            digest(
                self.app.state.store.get(positive["id"], "A", self.project, "snapshot")[
                    "payload"
                ]["splits"]
            ),
            positive["payload"]["sha256"],
        )
        self.assertTrue(all(value["counts"][role] > 0 for role in value["counts"]))
        download = self.client.get(
            self.prefix + "/gate-snapshots/" + identifier + "/download"
        )
        self.assertEqual(
            download.status_code,
            200,
            download.text if download.status_code != 200 else "",
        )
        self.assertEqual(
            download.headers["X-DMT-Archive-SHA256"],
            hashlib.sha256(download.content).hexdigest(),
        )
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            names = {role + ".jsonl" for role in value["splits"]} | {
                "gate_" + role + ".jsonl" for role in value["splits"]
            }
            self.assertEqual(set(archive.namelist()), names | {"manifest.json"})
            manifest = json.loads(archive.read("manifest.json"))
            for name in names:
                self.assertEqual(
                    hashlib.sha256(archive.read(name)).hexdigest(),
                    manifest["files"][name]["sha256"],
                )
        listed = self.client.get(self.prefix + "/entities/gate-snapshot").json()
        self.assertNotIn("splits", listed[0]["payload"])
        backup = backup_workspace(self.home, Path(self.temp.name) / "backup")
        restored = Path(self.temp.name) / "restored"
        restore_workspace(
            Path(self.temp.name) / "backup", restored, backup["manifest_sha256"]
        )
        recovered_app = create_app(restored, keys())
        actual = recovered_app.state.service.gates.verify_snapshot(
            "A", self.project, identifier
        )
        self.assertEqual(actual["sha256"], value["sha256"])

    def test_snapshot_cannot_substitute_unreviewed_content_even_with_recomputed_digest(
        self,
    ):
        positive = self.positive_snapshot()
        dataset = self.approve_all(self.upload())
        finished = self.prepare(positive, dataset)
        identifier = finished["result"]["gate_snapshot"]
        value = self.app.state.service.gates.verify_snapshot(
            "A", self.project, identifier
        )
        value["splits"]["test"][0]["reason"] = "Substituted after reviewer approval"
        value["sha256"] = digest(value["splits"])
        with self.app.state.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(value), identifier),
            )
        with self.assertRaisesRegex(ValueError, "reviewed source reservation"):
            self.app.state.service.gates.verify_snapshot("A", self.project, identifier)

    def test_browser_example_authenticated_review_and_reservation_over_live_http(self):
        import uvicorn
        from streamlit.testing.v1 import AppTest

        positive = self.positive_snapshot()
        dataset = self.approve_all(self.upload())
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
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
                    raise RuntimeError("Local fixture HTTP server did not start")
                time.sleep(0.02)
            app = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py")
            ).run(timeout=30)
            app.text_input[0].set_value(url)
            app.text_input[1].set_value("a-secret")
            next(
                button for button in app.button if button.label == "Connect"
            ).click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            next(
                widget
                for widget in app.text_area
                if widget.label == "Inapplicable source evidence"
            ).set_value("Fictional browser weather report without support evidence")
            next(
                widget
                for widget in app.text_input
                if widget.label == "Applicability source family"
            ).set_value("browser-weather-family")
            next(
                widget
                for widget in app.text_area
                if widget.label == "Why these questions are inapplicable"
            ).set_value("No support or service facts")
            next(
                widget
                for widget in app.checkbox
                if widget.key == "gate-real-" + self.project
            ).set_value(True)
            next(
                button
                for button in app.button
                if button.label == "Save applicability example"
            ).click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            created = self.app.state.store.list("A", self.project, "gate-dataset")[0]
            self.assertEqual(created["payload"]["reviewed_count"], 0)
            next(
                widget
                for widget in app.selectbox
                if widget.label == "Applicability review decision"
            ).set_value("approved")
            next(
                widget
                for widget in app.text_area
                if widget.label == "Applicability review note"
            ).set_value("Browser fixture reviewed; not operational gold")
            next(
                button
                for button in app.button
                if button.label == "Record applicability review"
            ).click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            latest = self.app.state.store.list("A", self.project, "gate-dataset")[0]
            self.assertEqual(latest["payload"]["reviewed_count"], 1)
            next(
                widget
                for widget in app.selectbox
                if widget.label == "Frozen positive snapshot for applicability"
            ).set_value(positive["id"])
            next(
                widget
                for widget in app.multiselect
                if widget.label == "Reviewed applicability versions to reserve"
            ).set_value([dataset["id"], latest["id"]]).run(timeout=30)
            next(
                button
                for button in app.button
                if button.label
                == "Reserve applicability train / validation / calibration / test"
            ).click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            job = self.client.get(self.prefix + "/jobs").json()[0]
            self.assertEqual(job["kind"], "gate-prepare")
            Worker(self.app.state.store).once()
            finished = self.app.state.store.job(job["id"], "A", self.project)
            self.assertEqual(finished["status"], "succeeded", finished)
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            self.assertTrue(
                any(
                    button.label == "Prepare eight-file Vega study download"
                    for button in app.button
                )
            )
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()

    def test_known_ancestors_anchor_negatives_and_bridge_cannot_join_frozen_roles(self):
        positive = self.positive_snapshot()["payload"]["splits"]
        rows = gate_rows()
        for row in rows:
            row["metadata"].update(
                reviewer="reviewer", review_note="checked fictional evidence"
            )
        anchor = positive["train"][0]
        rows[0]["metadata"]["seed_ids"] = [anchor["id"]]
        splits = reserve(positive, rows)
        self.assertIn(rows[0]["id"], {row["id"] for row in splits["train"]})
        rows[0]["metadata"]["semantic_links"] = [positive["test"][0]["id"]]
        with self.assertRaisesRegex(ValueError, "different frozen"):
            reserve(positive, rows)

    def test_chronological_applicability_preserves_positive_time_boundaries(self):
        positive = self.positive_snapshot()["payload"]["splits"]
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def stamp(row, offset):
            moment = start + timedelta(minutes=offset)
            row["metadata"]["temporal"] = {
                "decision_at": moment.isoformat(),
                "evidence_available_at": (moment - timedelta(seconds=1)).isoformat(),
                "label_available_at": (moment + timedelta(seconds=1)).isoformat(),
            }

        roles = ("train", "validation", "calibration", "test")
        for role_index, role in enumerate(roles):
            for index, row in enumerate(positive[role]):
                stamp(row, role_index * 200 + index)
        rows = gate_rows(4)
        for index, row in enumerate(rows):
            stamp(row, index * 200 + 5)
        splits = reserve(positive, rows)
        self.assertEqual(
            {role: part[0]["id"] for role, part in splits.items()},
            dict(zip(roles, [row["id"] for row in rows])),
        )
        bad = copy.deepcopy(rows)
        del bad[0]["metadata"]["temporal"]
        with self.assertRaisesRegex(ValueError, "every real record"):
            reserve(positive, bad)
        bad = copy.deepcopy(rows)
        bad[0]["metadata"]["temporal"]["label_available_at"] = (
            start + timedelta(minutes=201)
        ).isoformat()
        with self.assertRaisesRegex(ValueError, "Temporal leakage"):
            reserve(positive, bad)

    def test_two_reviewers_race_without_branching_the_immutable_collection(self):
        dataset = self.upload(gate_rows(1))
        row = gate_rows(1)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda token: self.review(dataset, row, token=token),
                    ("a-secret", "review-secret"),
                )
            )
        self.assertEqual(
            sorted(response.status_code for response in responses), [201, 409]
        )
        head = self.app.state.store.dataset_head(
            dataset["id"], "A", self.project, kind="gate-dataset"
        )
        current = self.app.state.store.get(
            head, "A", self.project, "gate-dataset", include_rows=False
        )
        self.assertEqual(current["payload"]["sequence"], 1)
        self.assertEqual(current["payload"]["reviewed_count"], 1)
        self.app.state.store.verify_dataset(
            head, "A", self.project, kind="gate-dataset"
        )

    def test_related_records_across_collections_keep_the_original_reservation_order(
        self,
    ):
        positive = self.positive_snapshot()
        left, right = gate_rows(20), gate_rows(20)
        for row in right:
            row["id"] = "right-" + row["id"]
            row["state"] += " Alternate source detail"
        datasets = [
            self.approve_all(self.upload(left)),
            self.approve_all(self.upload(right)),
        ]
        datasets.sort(key=lambda dataset: dataset["id"], reverse=True)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {
                    "snapshot": positive["id"],
                    "datasets": [dataset["id"] for dataset in datasets],
                },
                "request_key": "related-collection-order",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        Worker(self.app.state.store).once()
        finished = self.app.state.store.job(response.json()["id"], "A", self.project)
        self.assertEqual(finished["status"], "succeeded", finished)
        self.app.state.service.gates.verify_snapshot(
            "A", self.project, finished["result"]["gate_snapshot"]
        )
