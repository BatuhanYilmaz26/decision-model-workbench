"""Live HTTP browser successor creation, paging, role and stale-form contracts."""

import json
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest
import test_platform_http as fixtures
from test_platform import QUESTIONS


class SuccessorBrowserTests(unittest.TestCase):
    setUp = fixtures.LocalProcessTests.setUp
    tearDown = fixtures.LocalProcessTests.tearDown

    def create(self):
        response = self.http.post(
            "/v1/projects",
            json={
                "name": "Fictional successor source",
                "description": "Route tickets using decision-time evidence",
                "questions": QUESTIONS,
                "data_requirements": "Real operational examples and rare boundaries",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def browser(self, token="a-secret"):
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value(token)
        next(b for b in app.button if b.label == "Connect").click().run()
        self.assertEqual(len(app.exception), 0)
        return app

    def plan(self, app):
        next(
            w for w in app.checkbox if w.label == "Plan a policy successor"
        ).check().run()
        self.assertEqual(len(app.exception), 0)

    def approve(self, app):
        next(
            w for w in app.text_area if w.label == "Reason for the policy successor"
        ).set_value("Owner reviewed changed rules and evidence requirements")
        next(
            w
            for w in app.checkbox
            if w.label
            == "I approve this successor policy, schema and selected criteria"
        ).check()
        next(
            w for w in app.button if w.label == "Create reviewed successor"
        ).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0, [e.value for e in app.error])

    def test_browser_carries_domain_schema_criteria_and_opens_empty_child(self):
        parent = self.create()
        policy = {
            "min_accuracy": 0.7,
            "min_macro_f1": 0.6,
            "max_ece": 0.2,
            "min_test_rows": 30,
        }
        self.http.put(f"/v1/projects/{parent}/release-policy", json=policy)
        app = self.browser()
        self.plan(app)
        self.assertEqual(
            next(
                w.value
                for w in app.text_area
                if w.label == "Successor data requirements"
            ),
            "Real operational examples and rare boundaries",
        )
        schema = json.loads(
            next(
                w.value
                for w in app.text_area
                if w.label == "Successor question schema (JSON)"
            )
        )
        schema["refund"]["instructions"] = "Require an explicit customer refund request"
        next(
            w for w in app.text_area if w.label == "Successor question schema (JSON)"
        ).set_value(json.dumps(schema))
        next(
            w for w in app.text_input if w.label == "Successor project name"
        ).set_value("Reviewed support v2")
        self.approve(app)
        child = app.session_state["active_project"]
        self.assertNotEqual(child, parent)
        spec = self.http.get(f"/v1/projects/{child}").json()["payload"]
        self.assertEqual(spec["questions"], schema)
        self.assertEqual(spec["parent_project"], parent)
        saved = self.http.get(f"/v1/projects/{child}/release-policy").json()
        self.assertEqual(saved["policy"]["payload"]["criteria"]["min_accuracy"], 0.7)
        self.assertFalse(saved["locked"])
        self.assertTrue(any("Policy successor created" in e.value for e in app.success))
        self.assertEqual(
            self.http.get(f"/v1/projects/{child}/entities/dataset").json(), []
        )

    def test_approval_required_and_parent_policy_replacement_discards_retired_form(
        self,
    ):
        parent = self.create()
        app = self.browser()
        self.plan(app)
        next(
            w for w in app.button if w.label == "Create reviewed successor"
        ).click().run()
        self.assertTrue(any("Approve the successor" in e.value for e in app.error))
        app.run()
        self.plan(app)
        next(
            w for w in app.text_area if w.label == "Reason for the policy successor"
        ).set_value("Old draft")
        next(
            w
            for w in app.checkbox
            if w.label
            == "I approve this successor policy, schema and selected criteria"
        ).check()
        self.http.put(
            f"/v1/projects/{parent}/release-policy", json={"min_accuracy": 0.9}
        )
        next(
            w for w in app.button if w.label == "Create reviewed successor"
        ).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(self.http.get("/v1/projects").json()), 1)
        self.assertFalse(
            next(
                w.value
                for w in app.checkbox
                if w.label
                == "I approve this successor policy, schema and selected criteria"
            )
        )
        self.approve(app)
        self.assertEqual(len(self.http.get("/v1/projects").json()), 2)

    def test_successor_from_older_project_page_opens_on_first_page(self):
        parent = self.create()
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        for index in range(102):
            self.app.state.service.create_project(
                owner,
                f"Other project {index}",
                "Independent fictional domain",
                QUESTIONS,
            )
        app = self.browser()
        next(
            w for w in app.number_input if w.label == "Project history page"
        ).set_value(1).run()
        next(w for w in app.selectbox if w.label == "Project").set_value(parent).run()
        self.plan(app)
        self.approve(app)
        self.assertEqual(app.session_state["project_history_page"], 0)
        child = app.session_state["active_project"]
        self.assertEqual(
            self.http.get(f"/v1/projects/{child}").json()["payload"]["parent_project"],
            parent,
        )

    def test_reviewer_does_not_receive_owner_successor_controls(self):
        self.create()
        app = self.browser("review-secret")
        self.assertFalse(
            any(w.label == "Plan a policy successor" for w in app.checkbox)
        )

    def test_ordinary_project_creation_still_opens_new_project_from_existing_selection(
        self,
    ):
        parent = self.create()
        app = self.browser()
        app.session_state["schema_draft"] = QUESTIONS
        app.run()
        next(w for w in app.text_input if w.label == "Project name").set_value(
            "New independent domain"
        )
        next(
            w
            for w in app.text_area
            if w.label
            == "Describe your domain, required data, decision policy and boundary cases"
        ).set_value("Independent owner-confirmed decision rules")
        next(
            w
            for w in app.checkbox
            if w.label == "I confirm this schema and policy for the project"
        ).check()
        next(w for w in app.button if w.label == "Create project").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0, [e.value for e in app.error])
        child = app.session_state["active_project"]
        self.assertNotEqual(child, parent)
        spec = self.http.get(f"/v1/projects/{child}").json()["payload"]
        self.assertIsNone(spec["parent_project"])
        self.assertEqual(spec["name"], "New independent domain")
