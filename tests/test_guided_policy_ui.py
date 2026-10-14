"""Actual app rendering, guided table state and owner HTTP persistence."""

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest
import test_platform_http as http_fixtures
from test_platform import QUESTIONS
from test_error_policy_backends import selected_policy


class GuidedPolicyBrowserTests(unittest.TestCase):
    setUp = http_fixtures.LocalProcessTests.setUp
    tearDown = http_fixtures.LocalProcessTests.tearDown

    def browser(self):
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value("a-secret")
        next(b for b in app.button if b.label == "Connect").click().run()
        self.assertEqual(len(app.exception), 0)
        return app

    def create(self):
        return self.http.post(
            "/v1/projects",
            json={
                "name": "Fictional guided risk",
                "description": "Review invoice refunds from decision-time evidence",
                "questions": QUESTIONS,
            },
        ).json()["id"]

    def test_guided_table_submission_persists_asymmetric_costs_and_critical_support(
        self,
    ):
        project = self.create()
        app = self.browser()
        self.assertFalse(
            any(
                w.label == "Error-cost and critical-label policy (JSON)"
                for w in app.text_area
            )
        )
        next(
            w
            for w in app.checkbox
            if w.label == "Add domain error-cost and critical-label gates"
        ).check()
        prefix = "acceptance-" + project + "-new-domain"
        cost_table = next(
            w.value for w in app.dataframe if "include_costs" in w.value.columns
        )
        refund_row = int(cost_table.index[cost_table["question"] == "refund"][0])
        # Supply the documented frontend editor state; AppTest has no cell-edit API.
        app.session_state[prefix + "-costs"] = {
            "edited_rows": {
                refund_row: {
                    "include_costs": True,
                    "default_error_cost": 5.0,
                    "max_mean_cost": 2.0,
                }
            },
            "added_rows": [],
            "deleted_rows": [],
        }
        app.session_state[prefix + "-overrides"] = {
            "edited_rows": {
                0: {
                    "question": "refund",
                    "actual": "false",
                    "predicted": "true",
                    "cost": 100.0,
                }
            },
            "added_rows": [],
            "deleted_rows": [],
        }
        app.session_state[prefix + "-critical"] = {
            "edited_rows": {
                0: {
                    "question": "refund",
                    "outcome": "true",
                    "min_positive_rows": 100,
                    "min_negative_rows": 200,
                }
            },
            "added_rows": [],
            "deleted_rows": [],
        }
        next(
            b for b in app.button if b.label == "Save acceptance criteria"
        ).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0, [e.value for e in app.error])
        policy = self.app.state.store.release_policy("A", project)["policy"]["payload"][
            "criteria"
        ]["error_policy"]
        refund = policy["questions"]["refund"]
        self.assertEqual(refund["costs"]["false"]["true"], 100.0)
        self.assertEqual(refund["costs"]["true"]["false"], 5.0)
        self.assertEqual(refund["critical_labels"]["true"]["min_negative_rows"], 200)
        app.run()
        next(
            w for w in app.radio if w.label == "Configure domain error criteria"
        ).set_value("Advanced JSON").run()
        import json

        displayed = next(
            w
            for w in app.text_area
            if w.label == "Error-cost and critical-label policy (JSON)"
        ).value
        self.assertEqual(json.loads(displayed), policy)

    def test_incomplete_guided_policy_does_not_create_or_change_server_criteria(self):
        project = self.create()
        app = self.browser()
        next(
            w
            for w in app.checkbox
            if w.label == "Add domain error-cost and critical-label gates"
        ).check()
        next(
            b for b in app.button if b.label == "Save acceptance criteria"
        ).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Select at least" in e.value for e in app.error))
        self.assertIsNone(self.app.state.store.release_policy("A", project)["policy"])

    def test_other_owner_update_does_not_accept_retired_form_submission(self):
        project = self.create()
        prefix = "/v1/projects/" + project + "/release-policy"
        original = self.http.put(
            prefix,
            json={
                "min_accuracy": 0.8,
                "min_macro_f1": 0.8,
                "max_ece": 0.1,
                "min_test_rows": 100,
            },
        ).json()["policy"]
        app = self.browser()
        changed = self.http.put(
            prefix,
            json={
                "min_accuracy": 0.82,
                "min_macro_f1": 0.8,
                "max_ece": 0.1,
                "min_test_rows": 100,
                "expected_id": original["id"],
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        current = changed.json()["policy"]
        next(
            b for b in app.button if b.label == "Save acceptance criteria"
        ).click().run()
        self.assertEqual(len(app.exception), 0)
        actual = self.app.state.store.release_policy("A", project)["policy"]
        self.assertEqual(actual["id"], current["id"])
        self.assertEqual(actual["payload"]["criteria"]["min_accuracy"], 0.82)

    def test_natural_language_intake_and_all_typed_question_builders(self):
        app = self.browser()
        configurations = [
            (
                "refund",
                "noul",
                "Approve a refund only when the policy evidence permits it",
                "",
            ),
            (
                "urgency",
                "score",
                "Rate the urgency using available evidence",
                "Routine\nSoon\nImmediate",
            ),
            (
                "team",
                "choice",
                "Select the support team from the request",
                "billing | Invoice questions\nengineering | Product faults",
            ),
        ]
        for name, kind, instruction, outcomes in configurations:
            next(
                w for w in app.text_input if w.label == "Question identifier"
            ).set_value(name)
            next(w for w in app.selectbox if w.label == "Answer format").set_value(kind)
            next(
                w
                for w in app.text_area
                if w.label == "What should the model decide? Include the decision rule."
            ).set_value(instruction)
            next(
                w for w in app.text_area if w.label.startswith("Outcomes: category_key")
            ).set_value(outcomes)
            next(
                b for b in app.button if b.label == "Add or update question"
            ).click().run()
            self.assertEqual(len(app.error), 0)
        next(w for w in app.text_input if w.label == "Project name").set_value(
            "Fictional service intake"
        )
        description = "Route observed requests; use only decision-time evidence. Escalate ambiguous policy exceptions for human review."
        requirements = "Include real billing and crash cases, rare refunds and multilingual evidence. Group each customer family; exclude future-resolution fields."
        next(
            w
            for w in app.text_area
            if w.label.startswith("Describe your domain, required data")
        ).set_value(description)
        next(
            w
            for w in app.text_area
            if w.label.startswith("Describe the data you need:")
        ).set_value(requirements)
        next(
            w
            for w in app.checkbox
            if w.label == "I confirm this schema and policy for the project"
        ).check()
        next(b for b in app.button if b.label == "Create project").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        projects = self.app.state.store.list("A", kind="project")
        self.assertEqual(len(projects), 1)
        payload = projects[0]["payload"]
        self.assertEqual(payload["description"], description)
        self.assertEqual(payload["data_requirements"], requirements)
        self.assertEqual(
            payload["questions"]["urgency"]["criteria"],
            ["Routine", "Soon", "Immediate"],
        )
        self.assertEqual(payload["questions"]["refund"]["type"], "noul")
        self.assertEqual(
            set(payload["questions"]["team"]["criteria"]), {"billing", "engineering"}
        )

    def test_unchanged_guided_resave_preserves_complete_owner_criteria(self):
        project = self.create()
        saved = self.http.put(
            "/v1/projects/" + project + "/release-policy",
            json={
                "min_accuracy": 0.83,
                "min_macro_f1": 0.79,
                "max_ece": 0.08,
                "min_test_rows": 123,
                "error_policy": selected_policy(),
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        original = saved.json()["policy"]["payload"]["criteria"]
        app = self.browser()
        next(
            b for b in app.button if b.label == "Save acceptance criteria"
        ).click().run()
        self.assertEqual(len(app.error), 0, [e.value for e in app.error])
        actual = self.app.state.store.release_policy("A", project)["policy"]["payload"][
            "criteria"
        ]
        self.assertEqual(actual, original)

    def test_guided_editor_does_not_transfer_settings_between_projects(self):
        project = self.create()
        self.http.put(
            "/v1/projects/" + project + "/release-policy",
            json={
                "min_accuracy": 0.8,
                "min_macro_f1": 0.8,
                "max_ece": 0.1,
                "min_test_rows": 100,
                "error_policy": selected_policy(),
            },
        ).raise_for_status()
        app = self.browser()
        self.assertTrue(
            next(
                w
                for w in app.checkbox
                if w.label == "Add domain error-cost and critical-label gates"
            ).value
        )
        other = self.create()
        app.run()
        next(w for w in app.selectbox if w.label == "Project").set_value(other).run()
        self.assertEqual(len(app.error), 0)
        self.assertFalse(
            next(
                w
                for w in app.checkbox
                if w.label == "Add domain error-cost and critical-label gates"
            ).value
        )
        costs = next(
            w.value for w in app.dataframe if "include_costs" in w.value.columns
        )
        self.assertFalse(costs["include_costs"].any())
        self.assertIsNone(self.app.state.store.release_policy("A", other)["policy"])
