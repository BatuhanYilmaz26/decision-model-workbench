"""Owner sets frozen cost/critical criteria through the actual browser and worker."""

import json
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest
import test_platform_http as http_fixtures
from decision_workbench.platform.store import Store, digest
from decision_workbench.platform.worker import Worker
from decision_workbench.data import prepare
from test_platform import QUESTIONS, fixture_rows
from test_error_policy_backends import selected_policy


class ErrorPolicyBrowserTests(unittest.TestCase):
    setUp = http_fixtures.LocalProcessTests.setUp
    tearDown = http_fixtures.LocalProcessTests.tearDown

    def test_owner_cost_criteria_freeze_with_real_cpu_model_and_report_panel(self):
        store = self.app.state.store
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        project = self.app.state.service.create_project(
            owner,
            "Fictional critical review",
            "Reviewed fictional routing/refund policy",
            QUESTIONS,
        )["id"]
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value("a-secret")
        next(b for b in app.button if b.label == "Connect").click().run()
        next(
            w for w in app.radio if w.label == "Configure domain error criteria"
        ).set_value("Advanced JSON").run()
        next(
            w
            for w in app.checkbox
            if w.label == "Add domain error-cost and critical-label gates"
        ).check()
        next(
            w
            for w in app.text_area
            if w.label == "Error-cost and critical-label policy (JSON)"
        ).set_value(json.dumps(selected_policy()))
        next(
            w
            for w in app.number_input
            if w.label == "Minimum real test rows for every question"
        ).set_value(10)
        next(
            b for b in app.button if b.label == "Save acceptance criteria"
        ).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        saved = store.release_policy("A", project)["policy"]["payload"]["criteria"][
            "error_policy"
        ]
        self.assertIn("refund", saved["questions"])
        splits = prepare(fixture_rows(180))
        snapshot = store.add(
            "snapshot",
            "A",
            project,
            "owner",
            {
                "splits": splits,
                "sha256": digest(splits),
                "schema_sha256": digest(QUESTIONS),
                "counts": {k: len(v) for k, v in splits.items()},
            },
        )["id"]
        job = self.app.state.service.submit(
            owner,
            project,
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "ui-cost-evaluation-study",
        )
        self.assertTrue(Worker(Store(self.home)).once())
        self.assertEqual(store.job(job["id"], "A", project)["status"], "succeeded")
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertTrue(
            next(
                b for b in app.button if b.label == "Save acceptance criteria"
            ).disabled
        )
        self.assertTrue(
            any(
                e.label == "Domain error costs and critical outcomes"
                for e in app.expander
            )
        )
