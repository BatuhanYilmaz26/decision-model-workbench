"""Streamlit generation approvals and history against a live workspace API."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import test_platform as data
import test_platform_http as http
from test_schema_intake import Teacher
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class GenerationBrowserTests(unittest.TestCase):
    def setUp(self):
        self.teacher = Teacher()
        self.teacher.result = {
            "rows": [
                {
                    "state": "Fictional generated evidence",
                    "gold": data.fixture_rows(1)[0]["gold"],
                }
            ]
        }
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": self.teacher.url,
                "DMT_TEACHER_MODEL": "fictional-teacher",
                "DMT_TEACHER_API_KEY": "",
                "DMT_GENERATION_TIMEOUT_SECONDS": "10",
                "DMT_GENERATION_INPUT_USD_PER_MILLION": "",
                "DMT_GENERATION_OUTPUT_USD_PER_MILLION": "",
                "DMT_GENERATION_MAX_ESTIMATED_REQUEST_USD": "",
                "DMT_GENERATION_MAX_ESTIMATED_DAY_USD": "",
            },
        )
        self.environment.start()
        http.LocalProcessTests.setUp(self)
        self.service = self.app.state.service
        self.project = self.service.create_project(
            OWNER, "Fictional generation", "Owner-defined domain policy", data.QUESTIONS
        )["id"]
        dataset = self.service.save_dataset(
            OWNER, self.project, data.fixture_rows(), {"fictional": True}
        )
        self.service.submit(
            OWNER,
            self.project,
            "prepare",
            {"datasets": [dataset["id"]]},
            "generation-browser-reservation",
        )
        self.worker = Worker(Store(self.home))
        self.assertTrue(self.worker.once())

    def tearDown(self):
        self.worker.service.runtime.close()
        http.LocalProcessTests.tearDown(self)
        self.environment.stop()
        self.teacher.close()

    def browser(self, token="a-secret"):
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value(token)
        next(w for w in app.button if w.label == "Connect").click().run()
        self.assertFalse(app.exception)
        return app

    def preview(self, app):
        next(w for w in app.number_input if w.label == "Candidates").set_value(1)
        next(
            w
            for w in app.text_area
            if w.label.startswith("Describe the synthetic data you need")
        ).set_value("Rare cases with missing evidence")
        next(
            w for w in app.button if w.label == "Preview synthetic request"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [e.value for e in app.error])

    def approve(self, app, *, previous_failure=False):
        next(
            w
            for w in app.checkbox
            if w.label
            == "I approve sending this displayed policy and training seeds to the configured teacher"
        ).check()
        next(
            w
            for w in app.checkbox
            if w.label == "I approve any charges for this generation attempt"
        ).check().run()
        next(
            w for w in app.button if w.label == "Approve and queue synthetic generation"
        ).click().run()
        self.assertFalse(app.exception)
        errors = [e.value for e in app.error]
        if previous_failure:
            self.assertEqual(
                errors,
                [
                    "Job failed. Inspect the scoped job and private worker log before submitting a new attempt."
                ],
            )
        else:
            self.assertEqual(errors, [])

    def test_preview_approvals_saved_result_and_separate_new_batch(self):
        app = self.browser()
        self.preview(app)
        button = next(
            w for w in app.button if w.label == "Approve and queue synthetic generation"
        )
        self.assertTrue(button.disabled)
        self.approve(app)
        self.assertTrue(
            next(
                w
                for w in app.button
                if w.label == "Approve and queue synthetic generation"
            ).disabled
        )
        self.assertTrue(self.worker.once())
        next(
            w for w in app.button if w.label == "Refresh generation request"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("pending review" in w.value for w in app.success))
        self.assertEqual(len(self.teacher.calls), 1)
        # History remains readable with teacher configuration disabled.
        with patch.dict(os.environ, {"DMT_TEACHER_MODEL": ""}):
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("pending review" in w.value for w in app.success))
        next(
            w
            for w in app.button
            if w.label == "Prepare a separate new generation batch"
        ).click().run()
        self.assertFalse(
            any(w.label == "Approve and queue synthetic generation" for w in app.button)
        )
        self.preview(app)
        self.assertFalse(
            next(
                w
                for w in app.checkbox
                if w.label == "I approve any charges for this generation attempt"
            ).value
        )
        self.assertEqual(len(self.teacher.calls), 1)

    def test_failed_response_retry_requires_fresh_preview_and_both_approvals(self):
        self.teacher.finish_reason = "length"
        app = self.browser()
        self.preview(app)
        self.approve(app)
        self.assertTrue(self.worker.once())
        next(
            w for w in app.button if w.label == "Refresh generation request"
        ).click().run()
        next(
            w for w in app.button if w.label == "Prepare an explicit retry"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(
            any(w.label == "Approve and queue synthetic generation" for w in app.button)
        )
        next(
            w for w in app.button if w.label == "Preview synthetic request"
        ).click().run()
        self.assertTrue(
            next(
                w
                for w in app.button
                if w.label == "Approve and queue synthetic generation"
            ).disabled
        )
        self.teacher.finish_reason = "stop"
        self.approve(app, previous_failure=True)
        self.assertTrue(self.worker.once())
        next(
            w for w in app.button if w.label == "Refresh generation request"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self.teacher.calls), 2)

    def test_reviewer_has_no_generation_approval_or_teacher_configuration(self):
        app = self.browser("review-secret")
        self.assertFalse(
            any(w.label == "Preview synthetic request" for w in app.button)
        )
        self.assertFalse(
            any(w.label == "Approve and queue synthetic generation" for w in app.button)
        )
        self.assertTrue(any("workspace owner" in w.value for w in app.info))
        self.assertEqual(self.teacher.calls, [])

    def test_unknown_terminal_outcome_requires_both_owner_confirmations(self):
        self.teacher.delay = True
        snapshot = self.app.state.store.list("A", self.project, "snapshot")[0]["id"]
        prefix = f"/v1/projects/{self.project}"
        selection = {
            "snapshot": snapshot,
            "count": 1,
            "seed_count": 1,
            "coverage": "Fictional deadline reconciliation",
        }
        with patch.dict(os.environ, {"DMT_GENERATION_TIMEOUT_SECONDS": "5"}):
            preview = self.http.post(prefix + "/generation-preview", json=selection)
            self.assertEqual(preview.status_code, 200, preview.text)
            response = self.http.post(
                prefix + "/jobs",
                json={
                    "kind": "generate",
                    "request_key": "browser-unknown-outcome",
                    "config": {
                        **selection,
                        "data_approved": True,
                        "cost_approved": True,
                        "expected_preview_sha256": preview.json()["sha256"],
                    },
                },
            )
            self.assertEqual(response.status_code, 202, response.text)
            job = response.json()
            self.assertTrue(self.worker.once())
        self.teacher.release.set()
        identifier = job["config"]["generation_plan"]["id"]
        detail = self.http.get(prefix + "/generation-plans/" + identifier).json()
        self.assertEqual(detail["state"], "unknown")
        app = self.browser()
        next(
            w for w in app.text_input if w.label == "Outcome evidence reference"
        ).set_value("Fictional endpoint and exited child checked")
        next(
            w for w in app.text_area if w.label == "Outcome confirmation note"
        ).set_value("Local transport exited; fictional teacher was released")
        next(
            w for w in app.button if w.label == "Confirm the checked generation outcome"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertEqual(
            self.http.get(prefix + "/generation-plans/" + identifier).json()["state"],
            "unknown",
        )
        next(
            w
            for w in app.checkbox
            if w.label == "I verified that the local transport has exited"
        ).check()
        next(
            w
            for w in app.checkbox
            if w.label
            == "I verified that the teacher is no longer processing this request"
        ).check()
        next(
            w for w in app.button if w.label == "Confirm the checked generation outcome"
        ).click().run()
        self.assertFalse(app.exception)
        confirmed = self.http.get(prefix + "/generation-plans/" + identifier).json()
        self.assertEqual(confirmed["state"], "owner_attested_complete")
        self.assertTrue(any(w.label == "Prepare an explicit retry" for w in app.button))
        self.assertEqual(len(self.teacher.calls), 1)
