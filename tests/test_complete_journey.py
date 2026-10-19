"""One personal browser project from intake through reviewed generation and reuse.

The upload is injected at Streamlit's unsupported file-picker boundary. Bulk
fictional annotations use the authenticated API; individual reviews, preparation,
generation, training, release and prediction use the actual browser controls.
No pretrained model, external teacher or paid provider is used.
"""

import io
import json
import os
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from decision_workbench.data import jsonl
from decision_workbench.ui.onboarding import personal_workspace
import test_local_onboarding as local
import test_platform as data
from test_schema_intake import Teacher


class CompleteJourneyTests(unittest.TestCase):
    setUp = local.PersonalWorkspaceTests.setUp
    tearDown = local.PersonalWorkspaceTests.tearDown
    wait_job = local.PersonalWorkspaceTests.wait_job

    def field(self, widgets, label):
        return next(w for w in widgets if w.label == label)

    def healthy(self, browser):
        self.assertFalse(browser.exception, [w.value for w in browser.exception])
        self.assertFalse(browser.error, [w.value for w in browser.error])

    def click(self, browser, label):
        self.field(browser.button, label).click().run()
        self.healthy(browser)

    def start(self):
        browser = AppTest.from_file(str(local.ROOT / "app.py"), default_timeout=40)
        browser.run()
        self.click(browser, "Start local workspace")
        self.workspace = personal_workspace(str(self.home), str(local.ROOT))
        self.assertEqual(browser.session_state["connection_mode"], "personal")
        return browser

    def job(self, client, prefix, kind, action):
        previous = {job["id"] for job in client.get(prefix + "/jobs").json()}
        action()
        fresh = [
            job
            for job in client.get(prefix + "/jobs").json()
            if job["id"] not in previous and job["kind"] == kind
        ]
        self.assertEqual(len(fresh), 1, fresh)
        return self.wait_job(client, prefix, fresh[0]["id"])

    def snapshot(self, client, prefix, identifier):
        response = client.get(prefix + "/snapshots/" + identifier + "/download")
        self.assertEqual(
            response.status_code, 200, response.text if response.is_error else ""
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return {
                "splits": {
                    role: [
                        json.loads(line)
                        for line in archive.read(role + ".jsonl").splitlines()
                    ]
                    for role in ("train", "validation", "calibration", "test")
                }
            }

    def test_one_project_import_review_generate_train_release_predict_and_reopen(self):
        teacher = Teacher()
        self.addCleanup(teacher.close)
        teacher.result = {
            "rows": [
                {
                    "state": "Fictional invoice payment refund requested routine newcase",
                    "gold": data.fixture_rows(1)[0]["gold"],
                }
            ]
        }
        settings = {
            "DMT_LOCAL_HOME": str(self.home),
            "DMT_LOCAL_TOKEN": "",
            "DMT_LOCAL_API_URL": "",
            "DMT_TEACHER_BASE_URL": teacher.url,
            "DMT_TEACHER_MODEL": "fictional-teacher",
            "DMT_TEACHER_API_KEY": "",
            "DMT_GENERATION_TIMEOUT_SECONDS": "10",
        }
        rows = data.fixture_rows()
        upload = None

        def file_widget(label, *args, **kwargs):
            if label == "Upload examples or operational data; labels may be missing":
                return upload
            return None

        with patch.dict(os.environ, settings), patch(
            "streamlit.file_uploader", side_effect=file_widget
        ):
            browser = self.start()
            self.field(browser.text_area, "Existing schema JSON").set_value(
                json.dumps(data.QUESTIONS)
            )
            self.click(browser, "Import schema")
            self.field(browser.text_input, "Project name").set_value("Complete journey")
            next(
                w
                for w in browser.text_area
                if w.label.startswith("Describe your domain,")
            ).set_value(
                "Fictional routing, refund and urgency policy for lifecycle checks"
            )
            self.field(
                browser.checkbox, "I confirm this schema and policy for the project"
            ).check()
            self.click(browser, "Create project")
            project = browser.session_state["active_project"]
            prefix = "/v1/projects/" + project
            with httpx.Client(
                base_url=self.workspace.url,
                headers={"Authorization": "Bearer " + self.workspace.token},
                trust_env=False,
                timeout=30,
            ) as client:
                self.assertEqual(len(client.get("/v1/projects").json()), 1)
                upload = SimpleNamespace(
                    name="fictional.jsonl", getvalue=lambda: jsonl(rows).encode()
                )
                browser.run()
                self.click(browser, "Validate and preview import")
                self.click(browser, "Save new collection for review")
                upload = None
                browser.run()
                self.healthy(browser)
                # The first annotation is saved in the actual browser form.
                self.field(browser.selectbox, "Review decision").set_value("approved")
                self.field(browser.text_input, "Review note").set_value(
                    "Checked fictional label"
                )
                self.click(browser, "Save reviewed version")
                head = self.field(browser.selectbox, "Dataset version").value
                dataset = client.get(prefix + "/datasets/" + head + "/rows").json()[
                    "dataset"
                ]
                for row in rows[1:]:
                    result = client.post(
                        prefix + "/datasets/" + dataset["id"] + "/reviews",
                        json={
                            "record_id": row["id"],
                            "gold": row["gold"],
                            "approved": True,
                            "expected_hash": dataset["payload"]["sha256"],
                            "note": "Checked fictional lifecycle annotation",
                        },
                    )
                    self.assertEqual(result.status_code, 201, result.text)
                    dataset = result.json()
                browser.run()
                self.field(
                    browser.multiselect,
                    "Dataset versions to prepare (approved rows; rejected rows excluded)",
                ).set_value([dataset["id"]]).run()
                reserved = self.job(
                    client,
                    prefix,
                    "prepare",
                    lambda: self.click(
                        browser, "Reserve train / validation / calibration / test"
                    ),
                )["snapshot"]
                original = self.snapshot(client, prefix, reserved)
                browser.run()
                self.field(browser.number_input, "Candidates").set_value(1)
                next(
                    w
                    for w in browser.text_area
                    if w.label.startswith("Describe the synthetic data you need")
                ).set_value("One fictional training case")
                self.click(browser, "Preview synthetic request")
                self.field(
                    browser.checkbox,
                    "I approve sending this displayed policy and training seeds to the configured teacher",
                ).check()
                self.field(
                    browser.checkbox,
                    "I approve any charges for this generation attempt",
                ).check().run()
                generated = self.job(
                    client,
                    prefix,
                    "generate",
                    lambda: self.click(
                        browser, "Approve and queue synthetic generation"
                    ),
                )["dataset"]
                self.assertEqual(len(teacher.calls), 1)
                prompt = json.loads(teacher.calls[0][2]["messages"][1]["content"])
                self.assertTrue(prompt["examples"])
                training_states = {
                    json.dumps(row["state"], sort_keys=True)
                    for row in original["splits"]["train"]
                }
                self.assertTrue(
                    all(
                        json.dumps(row["state"], sort_keys=True) in training_states
                        for row in prompt["examples"]
                    )
                )
                candidate = client.get(
                    prefix + "/datasets/" + generated + "/rows"
                ).json()["rows"][0]
                self.assertFalse(candidate["metadata"]["reviewed"])
                browser.run()
                self.field(browser.selectbox, "Dataset version").set_value(
                    generated
                ).run()
                self.field(browser.selectbox, "Review decision").set_value("approved")
                self.field(browser.text_input, "Review note").set_value(
                    "Checked generated evidence and labels"
                )
                self.click(browser, "Save reviewed version")
                reviewed = self.field(browser.selectbox, "Dataset version").value
                self.field(
                    browser.multiselect,
                    "Dataset versions to prepare (approved rows; rejected rows excluded)",
                ).set_value([reviewed])
                self.field(
                    browser.selectbox,
                    "Preserve holdouts from an existing snapshot when adding synthetic data",
                ).set_value(reserved).run()
                extended = self.job(
                    client,
                    prefix,
                    "prepare",
                    lambda: self.click(
                        browser, "Reserve train / validation / calibration / test"
                    ),
                )["snapshot"]
                updated = self.snapshot(client, prefix, extended)
                for role in ("validation", "calibration", "test"):
                    self.assertEqual(updated["splits"][role], original["splits"][role])
                self.assertEqual(
                    len(updated["splits"]["train"]),
                    len(original["splits"]["train"]) + 1,
                )
                browser.run()
                # Permissive fixture gates check mechanics, not domain accuracy.
                for label, value in (
                    ("Minimum accuracy for every question", 0.0),
                    ("Minimum macro-F1 for every question", 0.0),
                    ("Maximum ECE (10 equal-width bins)", 1.0),
                    ("Minimum real test rows for every question", 10),
                ):
                    self.field(browser.number_input, label).set_value(value)
                self.click(browser, "Save acceptance criteria")
                self.field(browser.selectbox, "Training snapshot").set_value(extended)
                self.field(browser.selectbox, "Training backend").set_value(
                    "cpu-baseline"
                )
                model = self.job(
                    client,
                    prefix,
                    "train",
                    lambda: self.click(browser, "Queue training"),
                )["model"]
                trained = next(
                    item["payload"]
                    for item in client.get(prefix + "/entities/model").json()
                    if item["id"] == model
                )
                self.assertEqual(trained["snapshot"], extended)
                self.assertTrue(trained["report"]["reload_equivalence"])
                self.assertEqual(
                    trained["report"]["counts"]["train"],
                    len(updated["splits"]["train"]),
                )
                browser.run()
                self.field(browser.selectbox, "Evaluated model version").set_value(
                    model
                )
                self.field(browser.text_input, "Release or rollback reason").set_value(
                    "Fictional continuous lifecycle check"
                )
                self.click(browser, "Activate selected version")
                browser.run()
                self.field(
                    browser.text_area, "Evidence for the released model"
                ).set_value("invoice payment refund requested urgent")
                self.click(browser, "Make decision")
                decisions = [json.loads(w.value) for w in browser.json]
                decision = next(
                    value
                    for value in decisions
                    if isinstance(value, dict)
                    and value.get("model_id") == model
                    and "answers" in value
                )
                self.assertEqual(decision["release_revision"], 1)
                self.assertEqual(decision["answers"]["team"]["answer"], "billing")
                self.assertEqual(set(decision["answers"]), set(data.QUESTIONS))
                self.assertEqual(client.get("/ready").status_code, 200)
            self.workspace.close()
            personal_workspace.clear()
            reopened = self.start()
            self.assertEqual(reopened.session_state["active_project"], project)
            self.field(reopened.text_area, "Evidence for the released model").set_value(
                "invoice payment refund requested urgent"
            )
            self.click(reopened, "Make decision")
            self.assertEqual(len(teacher.calls), 1)
            outputs = [json.loads(w.value) for w in reopened.json]
            self.assertIn(decision, outputs)


if __name__ == "__main__":
    unittest.main()
