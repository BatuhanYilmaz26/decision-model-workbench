"""Owner assignment and reviewer completion through the actual browser and HTTP API."""

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

from decision_workbench.data import jsonl
from test_platform import fixture_rows, QUESTIONS
import test_platform_http as http_fixtures


class AssignmentBrowserTests(unittest.TestCase):
    setUp = http_fixtures.LocalProcessTests.setUp
    tearDown = http_fixtures.LocalProcessTests.tearDown

    def browser(self, token):
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value(token)
        next(b for b in app.button if b.label == "Connect").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        return app

    def test_assign_open_off_page_example_approve_and_inspect_history(self):
        project = self.http.post(
            "/v1/projects",
            json={
                "name": "Fictional review queue",
                "description": "Owner-defined routing policy",
                "questions": QUESTIONS,
            },
        ).json()["id"]
        prefix = "/v1/projects/" + project
        rows = fixture_rows(60)
        uploaded = self.http.post(
            prefix + "/datasets",
            files={"file": ("fictional.jsonl", jsonl(rows).encode())},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        dataset = uploaded.json()
        owner = self.browser("a-secret")
        next(w for w in owner.text_input if w.label == "Find example by ID").set_value(
            rows[-1]["id"]
        ).run()
        self.assertEqual(
            next(w for w in owner.selectbox if w.label == "Example to review").value,
            rows[-1]["id"],
        )
        next(w for w in owner.text_input if w.label == "Reviewer actor ID").set_value(
            "reviewer"
        )
        next(w for w in owner.text_input if w.label == "Assignment reason").set_value(
            "Fictional policy annotation"
        )
        next(w for w in owner.button if w.label == "Save assignment").click().run()
        self.assertEqual(len(owner.exception), 0)
        self.assertEqual(len(owner.error), 0)
        assigned = self.http.get(prefix + "/review-assignments").json()["items"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["payload"]["assignee"], "reviewer")
        self.assertEqual(
            self.http.get(
                prefix + "/datasets/" + dataset["id"] + "/rows?limit=1"
            ).json()["dataset"]["payload"]["sha256"],
            dataset["payload"]["sha256"],
        )
        reviewer = self.browser("review-secret")
        next(
            w for w in reviewer.button if w.label == "Open assigned example"
        ).click().run()
        self.assertEqual(len(reviewer.exception), 0)
        self.assertEqual(
            next(w for w in reviewer.selectbox if w.label == "Example to review").value,
            rows[-1]["id"],
        )
        next(w for w in reviewer.selectbox if w.label == "Review decision").set_value(
            "approved"
        )
        next(w for w in reviewer.text_input if w.label == "Review note").set_value(
            "Fictional labels approved by assigned reviewer"
        )
        next(
            w for w in reviewer.button if w.label == "Save reviewed version"
        ).click().run()
        self.assertEqual(len(reviewer.exception), 0)
        self.assertEqual(len(reviewer.error), 0)
        self.assertEqual(
            self.http.get(prefix + "/review-assignments?status=assigned").json()[
                "items"
            ],
            [],
        )
        completed = self.http.get(
            prefix + "/review-assignments?status=completed"
        ).json()["items"][0]
        self.assertEqual(completed["payload"]["event"], "complete")
        self.assertEqual(completed["payload"]["updated_by"], "reviewer")
        reviewed = self.http.get(
            prefix + "/datasets/" + completed["dataset_head"] + "/rows",
            params={"record_id": rows[-1]["id"]},
        ).json()["rows"][0]
        self.assertTrue(reviewed["metadata"]["reviewed"])
        self.assertEqual(reviewed["metadata"]["reviewer"], "reviewer")
        next(
            w for w in reviewer.checkbox if w.label == "Show assignment history"
        ).check().run()
        self.assertEqual(len(reviewer.exception), 0)
        self.assertEqual(len(reviewer.error), 0)
        history = self.http.get(
            prefix
            + "/datasets/"
            + completed["dataset_head"]
            + "/review-assignments/history",
            params={"record_id": rows[-1]["id"]},
        ).json()["items"]
        self.assertEqual([e["payload"]["revision"] for e in history], [2, 1])
