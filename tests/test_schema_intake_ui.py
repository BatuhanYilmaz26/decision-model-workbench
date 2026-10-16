"""Live API and Streamlit form flow for the optional schema assistant."""

import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import test_platform_http as fixtures
from test_platform import QUESTIONS
from test_schema_intake import Teacher, DOMAIN
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.store import Store


class SchemaAssistantBrowserTests(unittest.TestCase):
    def setUp(self):
        self.teacher = Teacher()
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": self.teacher.url,
                "DMT_TEACHER_MODEL": "fictional-teacher",
                "DMT_TEACHER_API_KEY": "private-fixture-key",
                "DMT_SCHEMA_TIMEOUT_SECONDS": "10",
                "DMT_SCHEMA_CALLS_PER_DAY": "20",
            },
        )
        self.environment.start()
        fixtures.LocalProcessTests.setUp(self)
        self.worker = Worker(Store(self.home))

    def tearDown(self):
        self.worker.service.runtime.close()
        fixtures.LocalProcessTests.tearDown(self)
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

    def assistant(self, app):
        next(
            w
            for w in app.checkbox
            if w.label == "Suggest decision questions with a teacher"
        ).check().run()
        self.assertFalse(app.exception)

    def request(self, app):
        next(
            w for w in app.text_input if w.label == "Suggested project name"
        ).set_value(DOMAIN["name"])
        next(
            w for w in app.text_area if w.label == "Describe the decision and its rules"
        ).set_value(DOMAIN["description"])
        next(
            w for w in app.text_area if w.label == "Data requirements for this decision"
        ).set_value(DOMAIN["data_requirements"])
        next(
            w
            for w in app.checkbox
            if w.label
            == "I approve sending this domain brief to the configured teacher"
        ).check()
        next(
            w
            for w in app.checkbox
            if w.label == "I approve any charges for this schema suggestion request"
        ).check()
        next(
            w for w in app.button if w.label == "Request schema suggestions"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [e.value for e in app.error])

    def test_review_edits_and_confirmation_open_the_new_empty_project(self):
        app = self.browser()
        self.assistant(app)
        self.request(app)
        self.assertEqual(self.http.get("/v1/projects").json(), [])
        self.worker.once()
        next(
            w for w in app.button if w.label == "Refresh schema suggestion"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("urgency policy" in w.value for w in app.warning))
        edited = copy.deepcopy(QUESTIONS)
        edited["refund"][
            "instructions"
        ] = "Owner edited the question before project creation"
        next(
            w
            for w in app.text_area
            if w.label == "Review or edit suggested questions (JSON)"
        ).set_value(json.dumps(edited))
        next(
            w
            for w in app.checkbox
            if w.label
            == "I reviewed and approve this policy and every decision outcome"
        ).check()
        next(
            w
            for w in app.button
            if w.label == "Create project from reviewed suggestions"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [e.value for e in app.error])
        projects = self.http.get("/v1/projects").json()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["payload"]["questions"], edited)
        self.assertEqual(app.session_state["active_project"], projects[0]["id"])
        datasets = self.http.get(
            f"/v1/projects/{projects[0]['id']}/entities/dataset"
        ).json()
        self.assertEqual(datasets, [])
        self.assertEqual(len(self.teacher.calls), 1)
        self.assertNotIn("private-fixture-key", str([w.value for w in app.caption]))
        # Reopening history must lead to the reviewed project rather than an
        # editable copy of the original teacher draft that cannot be confirmed.
        next(
            w for w in app.button if w.label == "Refresh schema suggestion"
        ).click().run()
        self.assertTrue(any("already created" in w.value for w in app.success))
        self.assertFalse(
            any(
                w.label == "Create project from reviewed suggestions"
                for w in app.button
            )
        )
        next(w for w in app.button if w.label == "Open reviewed project").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["active_project"], projects[0]["id"])
        self.assertEqual(len(self.http.get("/v1/projects").json()), 1)

    def test_reviewer_has_no_assistant_or_provider_configuration(self):
        app = self.browser("review-secret")
        self.assertFalse(
            any(
                w.label == "Suggest decision questions with a teacher"
                for w in app.checkbox
            )
        )
        self.assertFalse(
            any(w.label == "Request schema suggestions" for w in app.button)
        )

    def test_duplicate_reviewed_question_names_are_refused_without_creating_project(
        self,
    ):
        app = self.browser()
        self.assistant(app)
        self.request(app)
        self.worker.once()
        next(
            w for w in app.button if w.label == "Refresh schema suggestion"
        ).click().run()
        question = json.dumps(QUESTIONS["refund"])
        next(
            w
            for w in app.text_area
            if w.label == "Review or edit suggested questions (JSON)"
        ).set_value('{"refund":' + question + ',"refund":' + question + "}")
        next(
            w
            for w in app.checkbox
            if w.label
            == "I reviewed and approve this policy and every decision outcome"
        ).check()
        next(
            w
            for w in app.button
            if w.label == "Create project from reviewed suggestions"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertEqual(self.http.get("/v1/projects").json(), [])
        self.assertEqual(len(self.teacher.calls), 1)

    def test_failed_request_requires_a_new_explicitly_approved_form(self):
        self.teacher.finish_reason = "length"
        app = self.browser()
        self.assistant(app)
        self.request(app)
        self.worker.once()
        next(
            w for w in app.button if w.label == "Refresh schema suggestion"
        ).click().run()
        self.assertTrue(app.warning)
        next(
            w for w in app.button if w.label == "Prepare a new schema request"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(
            next(
                w
                for w in app.checkbox
                if w.label
                == "I approve sending this domain brief to the configured teacher"
            ).value
        )
        self.assertFalse(
            next(
                w
                for w in app.checkbox
                if w.label == "I approve any charges for this schema suggestion request"
            ).value
        )
        self.assertEqual(len(self.teacher.calls), 1)
