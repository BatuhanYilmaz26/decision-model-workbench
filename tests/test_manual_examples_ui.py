"""Manual batches preserve draft rows and publish one atomic collection revision."""

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

import test_platform as data
import test_platform_http as http


class ManualExampleBrowserTests(unittest.TestCase):
    setUp = http.LocalProcessTests.setUp

    def tearDown(self):
        self.app.state.service.runtime.close()
        http.LocalProcessTests.tearDown(self)

    def browser(self):
        project = self.http.post(
            "/v1/projects",
            json={
                "name": "Fictional manual examples",
                "description": "Prepare reviewed domain examples",
                "questions": data.QUESTIONS,
            },
        ).json()["id"]
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value("a-secret")
        self.button(app, "Connect").click().run()
        self.assertFalse(app.exception)
        return app, project, "/v1/projects/" + project

    @staticmethod
    def button(app, label):
        return next(w for w in app.button if w.label == label)

    @staticmethod
    def draft(app, project, rows):
        try:
            generation = app.session_state["example-generation-" + project]
        except KeyError:
            generation = 0
        key = "batch-examples-" + project + "-" + str(generation)
        app.session_state[key] = {
            "edited_rows": {},
            "added_rows": rows,
            "deleted_rows": [0],
        }

    def test_fifty_rows_append_with_unknown_and_known_labels_reset_only_on_success(
        self,
    ):
        app, project, prefix = self.browser()
        self.http.post(
            prefix + "/examples",
            json={
                "examples": [{"id": "original", "state": "Original fictional evidence"}]
            },
        ).raise_for_status()
        app.run()
        root = self.http.get(prefix + "/entities/dataset").json()[0]
        next(
            w for w in app.selectbox if w.label == "Collection for new examples"
        ).set_value(root["payload"]["root"])
        next(w for w in app.radio if w.label == "Example entry").set_value(
            "A batch of examples"
        ).run()
        rows = [
            {
                "Input evidence": f"Fictional batch evidence {i}",
                "Example ID": f"manual-{i}",
                "Source family": "family-" + str(i // 2),
                "Source": "synthetic",
                "Outcome: team": "billing" if i % 2 == 0 else None,
            }
            for i in range(50)
        ]
        self.draft(app, project, rows)
        self.button(app, "Save batch for review").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state["example-generation-" + project], 1)
        historical = self.http.get(prefix + "/datasets/" + root["id"] + "/rows").json()
        self.assertEqual(historical["total"], 1)
        self.assertNotEqual(historical["head"], root["id"])
        page = self.http.get(
            prefix + "/datasets/" + historical["head"] + "/rows?limit=100"
        ).json()
        self.assertEqual(page["total"], 51)
        self.assertEqual(
            [r["id"] for r in page["rows"][1:]], [f"manual-{i}" for i in range(50)]
        )
        self.assertTrue(
            all(
                not r["metadata"]["reviewed"] and r["metadata"]["origin"] == "synthetic"
                for r in page["rows"]
            )
        )
        self.assertEqual(page["rows"][1]["group_id"], page["rows"][2]["group_id"])
        self.assertEqual(page["rows"][1]["gold"]["team"]["probabilities"]["billing"], 1)
        self.assertEqual(page["rows"][2]["gold"], {})

    def test_incomplete_overlimit_and_duplicate_batches_keep_the_draft_without_partial_save(
        self,
    ):
        app, project, prefix = self.browser()
        next(w for w in app.radio if w.label == "Example entry").set_value(
            "A batch of examples"
        ).run()
        cases = [
            (
                [
                    {"Input evidence": "Valid fictional row"},
                    {"Input evidence": "", "Example ID": "unfinished"},
                ],
                "Example row 2",
            ),
            (
                [{"Input evidence": "Valid fictional row"}, {"Outcome: urgency": "0"}],
                "Example row 2",
            ),
            (
                [{"Input evidence": "Valid fictional row"}, {"Outcome: refund": False}],
                "Example row 2",
            ),
            ([], "1–50"),
            (
                [{"Input evidence": "Valid fictional row"}, {"Source": "real"}],
                "Example row 2",
            ),
            ([{"Input evidence": f"Evidence {i}"} for i in range(51)], "1–50"),
            (
                [
                    {"Input evidence": "A", "Example ID": "duplicate"},
                    {"Input evidence": "B", "Example ID": "duplicate"},
                ],
                "unique",
            ),
        ]
        for rows, message in cases:
            with self.subTest(message=message):
                self.draft(app, project, rows)
                self.button(app, "Save batch for review").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(
                    any(message.lower() in w.value.lower() for w in app.error),
                    [w.value for w in app.error],
                )
                with self.assertRaises(KeyError):
                    app.session_state["example-generation-" + project]
                self.assertEqual(self.http.get(prefix + "/entities/dataset").json(), [])
                key = "batch-examples-" + project + "-0"
                self.assertEqual(app.session_state[key]["added_rows"], rows)
        self.draft(
            app,
            project,
            [
                {"Input evidence": "", "Source": "synthetic"},
                {"Input evidence": "Complete fictional row", "Example ID": "saved"},
            ],
        )
        self.button(app, "Save batch for review").click().run()
        self.assertFalse(app.error)
        self.assertEqual(
            self.http.get(prefix + "/entities/dataset").json()[0]["payload"][
                "row_count"
            ],
            1,
        )

    def test_manual_json_rejects_duplicate_keys_and_retains_input(self):
        app, project, prefix = self.browser()
        next(w for w in app.checkbox if w.label == "This evidence is JSON").check()
        source = '{"evidence":"original","evidence":"silently replaced"}'
        next(w for w in app.text_area if w.label == "Example input evidence").set_value(
            source
        )
        self.button(app, "Save example for review").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("duplicate" in w.value for w in app.error))
        self.assertEqual(
            next(w for w in app.text_area if w.label == "Example input evidence").value,
            source,
        )
        self.assertEqual(self.http.get(prefix + "/entities/dataset").json(), [])


if __name__ == "__main__":
    unittest.main()
