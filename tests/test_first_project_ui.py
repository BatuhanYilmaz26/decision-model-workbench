"""A new personal user builds, confirms and reopens a domain in the browser."""

import os
import unittest
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

import test_local_onboarding as local
from decision_workbench.ui.onboarding import personal_workspace


class FirstProjectBrowserTests(unittest.TestCase):
    setUp = local.PersonalWorkspaceTests.setUp
    tearDown = local.PersonalWorkspaceTests.tearDown

    def field(self, widgets, label):
        return next(w for w in widgets if w.label.startswith(label))

    def healthy(self, app):
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [w.value for w in app.error])

    def start(self):
        app = AppTest.from_file(str(local.ROOT / "app.py"), default_timeout=40).run()
        self.field(app.button, "Start local workspace").click().run()
        self.healthy(app)
        self.workspace = personal_workspace(str(self.home), str(local.ROOT))
        return app

    def test_new_personal_user_confirms_three_question_types_and_reopens_project(self):
        settings = {
            "DMT_LOCAL_HOME": str(self.home),
            "DMT_LOCAL_TOKEN": "",
            "DMT_LOCAL_API_URL": "",
            "DMT_TEACHER_BASE_URL": "",
            "DMT_TEACHER_MODEL": "",
        }
        with patch.dict(os.environ, settings):
            app = self.start()
            original_token = self.workspace.token
            for name, kind, rule, outcomes in (
                (
                    "route",
                    "choice",
                    "Choose the queue from observed evidence",
                    "routine | Clear evidence\nreview | Missing evidence",
                ),
                ("needs_review", "noul", "Is required evidence missing?", ""),
                (
                    "priority",
                    "score",
                    "Rate the observed urgency",
                    "Routine\nSoon\nImmediate",
                ),
            ):
                self.field(app.text_input, "Question identifier").set_value(name)
                self.field(app.selectbox, "Answer format").set_value(kind)
                self.field(app.text_area, "What should the model decide?").set_value(
                    rule
                )
                self.field(app.text_area, "Outcomes:").set_value(outcomes)
                self.field(app.button, "Add or update question").click().run()
                self.healthy(app)
            self.field(app.text_input, "Project name").set_value("My first domain")
            description = (
                "Use observed evidence only. Missing required evidence goes to review."
            )
            needs = (
                "Independent real examples, rare cases and source-family identifiers."
            )
            self.field(app.text_area, "Describe your domain, required data").set_value(
                description
            )
            self.field(app.text_area, "Describe the data you need:").set_value(needs)
            self.field(app.checkbox, "I confirm this schema and policy").check()
            self.field(app.button, "Create project").click().run()
            self.healthy(app)
            self.field(
                app.button, "Check available hardware and model packages"
            ).click().run()
            self.healthy(app)
            self.assertTrue(app.session_state["runtime-capabilities"]["cpu_baseline"])
            with httpx.Client(
                base_url=self.workspace.url,
                headers={"Authorization": "Bearer " + original_token},
                trust_env=False,
            ) as client:
                projects = client.get("/v1/projects").json()
                self.assertEqual(len(projects), 1)
                saved = projects[0]
                self.assertEqual(saved["payload"]["description"], description)
                self.assertEqual(saved["payload"]["data_requirements"], needs)
                self.assertCountEqual(
                    saved["payload"]["questions"], ["route", "needs_review", "priority"]
                )
            app.run()
            self.healthy(app)
            self.assertEqual(app.session_state["active_project"], saved["id"])
            self.workspace.close()
            personal_workspace.clear()
            restarted = self.start()
            self.assertNotEqual(self.workspace.token, original_token)
            self.assertEqual(restarted.session_state["active_project"], saved["id"])
            self.assertTrue(any(w.value == "My first domain" for w in restarted.header))
            self.assertEqual(restarted.session_state["connection_mode"], "personal")


if __name__ == "__main__":
    unittest.main()
