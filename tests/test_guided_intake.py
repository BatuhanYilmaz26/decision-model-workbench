"""Real HTTP import/review contracts for worksheets, drafts and diagnostics."""

import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from fastapi.testclient import TestClient
from openpyxl import Workbook
from decision_workbench.data import hard_gold, read_records, validate
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker
from test_platform import QUESTIONS, keys


class GuidedIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.app = create_app(self.home, keys())
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        response = self.client.post(
            "/v1/projects",
            json={
                "name": "Mapped intake",
                "description": "Fictional routing test policy",
                "questions": QUESTIONS,
                "data_requirements": "Operational tickets, rare urgent crashes and refund requests; avoid future-resolution fields",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.project = response.json()["id"]
        self.prefix = "/v1/projects/" + self.project

    def tearDown(self):
        self.app.state.service.runtime.close()
        self.client.close()
        self.temp.cleanup()

    def rows(self, dataset):
        return self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/rows"
        ).json()["rows"]

    def test_cover_worksheet_value_mapping_partial_labels_and_review(self):
        book = Workbook()
        book.active.title = "Cover"
        sheet = book.create_sheet("Tickets")
        sheet.append(["Ticket", "Account", "Text", "Queue", "Refund", "Level"])
        sheet.append(
            [
                "t-1",
                "account-1",
                "invoice payment routine",
                "Billing dept",
                "Yes",
                "Low",
            ]
        )
        sheet.append(["t-2", "account-1", "crash urgent", "Tech desk", None, "High"])
        body = io.BytesIO()
        book.save(body)
        book.close()
        initial = self.client.post(
            self.prefix + "/imports/inspect",
            files={"file": ("tickets.xlsx", body.getvalue())},
        )
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["worksheets"], ["Cover", "Tickets"])
        self.assertIn("worksheet_error", initial.json())
        inspected = self.client.post(
            self.prefix + "/imports/inspect",
            files={"file": ("tickets.xlsx", body.getvalue())},
            data={"worksheet": "Tickets"},
        )
        self.assertEqual(inspected.json()["row_count"], 2)
        self.assertEqual(inspected.json()["column_summary"]["Refund"]["missing"], 1)
        mapping = {
            "worksheet": "Tickets",
            "state_columns": ["Text"],
            "id_column": "Ticket",
            "group_column": "Account",
            "labels": {"team": "Queue", "refund": "Refund", "urgency": "Level"},
            "value_maps": {
                "team": {"Billing dept": "billing", "Tech desk": "engineering"},
                "refund": {"Yes": "true"},
                "urgency": {"Low": "0", "High": "2"},
            },
        }
        response = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("tickets.xlsx", body.getvalue())},
            data={"mapping": json.dumps(mapping)},
        )
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()
        rows = self.rows(dataset)
        self.assertEqual(rows[0]["id"], "t-1")
        self.assertEqual(rows[0]["group_id"], rows[1]["group_id"])
        self.assertEqual(rows[0]["metadata"]["source_row"], 2)
        self.assertEqual(set(rows[1]["gold"]), {"team", "urgency"})
        self.assertFalse(rows[0]["metadata"]["reviewed"])
        quality = self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/quality"
        ).json()["report"]
        self.assertEqual(quality["rows_with_missing_labels"], 1)
        self.assertEqual(quality["per_question"]["refund"]["missing_rows"], 1)
        note = {
            "record_id": "t-2",
            "gold": rows[1]["gold"],
            "approved": True,
            "expected_hash": dataset["payload"]["sha256"],
            "note": "Incomplete targets cannot be approved",
        }
        refused = self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/reviews", json=note
        )
        self.assertEqual(refused.status_code, 400)
        partial = self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/reviews",
            json={**note, "approved": False},
        )
        self.assertEqual(partial.status_code, 201, partial.text)
        note.update(
            gold=hard_gold(
                QUESTIONS, {"team": "engineering", "refund": False, "urgency": 2}
            ),
            expected_hash=partial.json()["payload"]["sha256"],
        )
        reviewed = self.client.post(
            self.prefix + "/datasets/" + partial.json()["id"] + "/reviews",
            json=note,
            headers={"Authorization": "Bearer review-secret"},
        )
        self.assertEqual(reviewed.status_code, 201, reviewed.text)
        self.assertNotIn("refund", self.rows(dataset)[1]["gold"])
        self.assertEqual(reviewed.json()["payload"]["reviewed_count"], 1)
        self.assertFalse(
            Store(self.home).verify_dataset(dataset["id"], "A", self.project)[1][
                "metadata"
            ]["reviewed"]
        )

    def test_draft_examples_survive_restart_and_cannot_prepare(self):
        example = {
            "state": "Unlabeled handwritten example",
            "labels": {"team": "billing"},
        }
        response = self.client.post(
            self.prefix + "/examples", json={"examples": [example]}
        )
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()
        row = self.rows(dataset)[0]
        self.assertEqual(row["metadata"]["origin"], "synthetic")
        validate(row, allow_missing_labels=True)
        with self.assertRaises(ValueError):
            validate(row, require_review=True)
        with self.assertRaises(ValueError):
            read_records(json.dumps([row]).encode(), "draft.json")
        self.assertEqual(
            read_records(
                json.dumps([row]).encode(), "draft.json", allow_missing_labels=True
            )[0]["gold"],
            row["gold"],
        )
        reopened = Store(self.home)
        self.assertEqual(
            reopened.verify_dataset(dataset["id"], "A", self.project)[0]["gold"],
            row["gold"],
        )
        queued = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset["id"]]},
                "request_key": "draft-must-fail",
            },
        )
        self.assertEqual(queued.status_code, 202)
        Worker(reopened).once()
        job = reopened.job(queued.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(reopened.list("A", self.project, "snapshot"), [])

    def test_inspection_mapping_errors_and_roles_are_atomic(self):
        content = b"Input,Outcome\ninvoice,billing\n"
        other = {"Authorization": "Bearer b-secret"}
        infer = {"Authorization": "Bearer infer-secret"}
        for headers, expected in ((other, 404), (infer, 403)):
            result = self.client.post(
                self.prefix + "/imports/inspect",
                files={"file": ("data.csv", content)},
                headers=headers,
            )
            self.assertEqual(result.status_code, expected)
            result = self.client.post(
                self.prefix + "/examples",
                json={"examples": [{"state": "input"}]},
                headers=headers,
            )
            self.assertEqual(result.status_code, expected)
        self.assertEqual(
            self.client.post(
                self.prefix + "/datasets",
                files={"file": ("data.csv", content)},
                data={"mapping": json.dumps({"state_columns": ["NoSuchColumn"]})},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.prefix + "/datasets",
                files={"file": ("data.csv", content)},
                data={"mapping": "{bad json"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.prefix + "/datasets",
                files={"file": ("data.csv", b"Input,Input\nx,y\n")},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.prefix + "/datasets",
                files={"file": ("data.csv", b"state,team\ninput,wrong-outcome\n")},
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get(self.prefix + "/entities/dataset").json(), [])
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            for i in range(4097):
                archive.writestr(str(i), "")
        rejected = self.client.post(
            self.prefix + "/imports/inspect",
            files={"file": ("oversized.xlsx", bomb.getvalue())},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("archive entries", rejected.text)

    def test_non_excel_zip_has_clear_rejection(self):
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w") as archive:
            archive.writestr("unrelated.txt", "not a workbook")
        for route in ("/datasets", "/imports/inspect"):
            response = self.client.post(
                self.prefix + route, files={"file": ("wrong.xlsx", body.getvalue())}
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("workbook structure", response.text)
        self.assertEqual(self.client.get(self.prefix + "/entities/dataset").json(), [])

    def test_multi_column_evidence_quality_conflicts_and_tenant_boundaries(self):
        source = b"ID,Customer,Subject,Details,Queue\n1,c1,Invoice,refund,billing\n2,c2,Invoice,refund,engineering\n3,c3,Crash,urgent,\n"
        mapping = {
            "state_columns": ["Subject", "Details"],
            "state_mode": "json",
            "id_column": "ID",
            "group_column": "Customer",
            "labels": {"team": "Queue"},
        }
        response = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("real.csv", source)},
            data={"mapping": json.dumps(mapping)},
        )
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()
        self.assertEqual(
            self.rows(dataset)[0]["state"], {"Subject": "Invoice", "Details": "refund"}
        )
        report = self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/quality"
        ).json()["report"]
        self.assertEqual(report["duplicate_state_families"], 1)
        self.assertEqual(report["rows_with_missing_labels"], 3)
        self.assertEqual(
            report["per_question"]["team"]["class_counts"],
            {"billing": 1, "engineering": 1},
        )
        for token, expected in (("b-secret", 404), ("infer-secret", 403)):
            self.assertEqual(
                self.client.get(
                    self.prefix + "/datasets/" + dataset["id"] + "/quality",
                    headers={"Authorization": "Bearer " + token},
                ).status_code,
                expected,
            )
        # Conflicting complete gold is reported, rather than silently relabeled.
        from decision_workbench.quality import quality_report

        rows = self.rows(dataset)[:2]
        for i, row in enumerate(rows):
            row["gold"] = hard_gold(
                QUESTIONS,
                {"team": ["billing", "engineering"][i], "refund": True, "urgency": 0},
            )
        self.assertEqual(
            quality_report(rows, QUESTIONS)["conflicting_state_families"], 1
        )
        self.assertIn(
            "rare urgent",
            self.client.get(self.prefix).json()["payload"]["data_requirements"],
        )

    def prepare_reviewed_fixture(self):
        from test_platform import fixture_rows

        rows = fixture_rows(180)
        for row in rows:
            row["metadata"].update(
                reviewer="fixture-owner",
                review_note="Fictional mechanical fixture, no domain efficacy claim",
            )
        dataset = self.app.state.service.save_dataset(
            {"tenant": "A", "actor": "fixture-owner", "role": "admin"},
            self.project,
            rows,
            {"mechanical_fixture": True},
        )
        queued = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset["id"]]},
                "request_key": "prepare-class-gate-fixture",
            },
        )
        Worker(Store(self.home)).once()
        return self.app.state.store.job(queued.json()["id"], "A", self.project)[
            "result"
        ]["snapshot"]

    def test_frozen_training_class_gate_fails_before_artifact_or_gpu_work(self):
        snapshot = self.prepare_reviewed_fixture()
        policy = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_training_examples_per_class": 100,
                "min_calibration_examples_per_class": 1,
                "min_test_rows": 10,
            },
        )
        self.assertEqual(policy.status_code, 200, policy.text)
        queued = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": {"backend": "cpu-baseline", "snapshot": snapshot},
                "request_key": "fail-class-minimum",
            },
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        relaxation = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_training_examples_per_class": 0,
                "min_calibration_examples_per_class": 0,
                "expected_id": policy.json()["policy"]["id"],
            },
        )
        self.assertEqual(relaxation.status_code, 409)
        Worker(Store(self.home)).once()
        job = self.app.state.store.job(queued.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job["error"].startswith("train/"), job["error"])
        self.assertFalse((self.home / "artifacts").exists())
        self.assertEqual(self.client.get(self.prefix + "/entities/model").json(), [])

    def test_calibration_class_gate_is_separate_from_training(self):
        snapshot = self.prepare_reviewed_fixture()
        policy = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_training_examples_per_class": 1,
                "min_calibration_examples_per_class": 100,
                "min_test_rows": 10,
            },
        )
        self.assertEqual(policy.status_code, 200, policy.text)
        queued = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": {"backend": "cpu-baseline", "snapshot": snapshot},
                "request_key": "fail-calibration-minimum",
            },
        )
        Worker(Store(self.home)).once()
        job = self.app.state.store.job(queued.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job["error"].startswith("calibration/"), job["error"])

    def test_offline_training_jsonl_can_exceed_per_upload_limit(self):
        question = {
            "route": {
                "type": "choice",
                "instructions": "Select a route",
                "criteria": {"a": "first", "b": "second"},
            }
        }
        row = {
            "id": "0",
            "group_id": "0",
            "state": "evidence",
            "questions": question,
            "gold": hard_gold(question, {"route": "a"}),
            "metadata": {"origin": "real", "reviewed": True},
        }
        lines = [
            json.dumps({**row, "id": str(i), "group_id": str(i)}) for i in range(20001)
        ]
        body = ("\n".join(lines) + "\n").encode()
        self.assertEqual(len(read_records(body, "prepared-train.jsonl")), 20001)
        with self.assertRaises(ValueError):
            read_records(body, "upload.jsonl", max_rows=20000)

    def test_duplicate_states_do_not_count_as_independent_holdouts(self):
        from decision_workbench.quality import quality_report

        rows = []
        for i in range(4):
            rows.append(
                {
                    "id": str(i),
                    "group_id": "claimed-independent-" + str(i),
                    "state": "the same evidence",
                    "questions": QUESTIONS,
                    "gold": {},
                    "metadata": {"origin": "real", "reviewed": False},
                }
            )
        report = quality_report(rows, QUESTIONS)
        self.assertEqual(report["real_source_groups"], 4)
        self.assertEqual(report["real_families_after_known_links"], 1)
        self.assertTrue(
            any(
                "four real families" in message
                for message in report["preparation_blockers"]
            )
        )

    def test_known_semantic_links_reduce_real_family_readiness_count(self):
        from decision_workbench.quality import quality_report

        rows = [
            {
                "id": str(i),
                "group_id": "family-" + str(i),
                "state": "Different evidence " + str(i),
                "questions": QUESTIONS,
                "gold": {},
                "metadata": {
                    "origin": "real",
                    "reviewed": False,
                    "semantic_links": [str(i - 1)] if i else [],
                },
            }
            for i in range(4)
        ]
        report = quality_report(rows, QUESTIONS)
        self.assertEqual(report["real_source_groups"], 4)
        self.assertEqual(report["real_families_after_known_links"], 1)
        self.assertTrue(
            any("four real families" in text for text in report["preparation_blockers"])
        )
        # A quality view may contain only a subset of an existing reservation.
        rows[0]["metadata"]["semantic_links"] = ["outside-this-view"]
        self.assertEqual(
            quality_report(rows, QUESTIONS)["real_families_after_known_links"], 1
        )


if __name__ == "__main__":
    unittest.main()
