"""Streamlit upload controls against a live API; files are injected at the widget boundary."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from decision_workbench.data import jsonl
import test_import_preview as uploads
import test_platform as data
import test_platform_http as http


class ImportPreviewBrowserTests(unittest.TestCase):
    def setUp(self):
        http.LocalProcessTests.setUp(self)
        response = self.http.post(
            "/v1/projects",
            json={
                "name": "Fictional file preparation",
                "description": "Review domain examples before training",
                "questions": data.QUESTIONS,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.project = response.json()["id"]
        self.prefix = "/v1/projects/" + self.project
        self.file = None
        self.widget = patch("streamlit.file_uploader", side_effect=self.file_widget)
        self.widget.start()

    def tearDown(self):
        self.widget.stop()
        self.app.state.service.runtime.close()
        http.LocalProcessTests.tearDown(self)

    def file_widget(self, label, *args, **kwargs):
        return (
            self.file
            if label == "Upload examples or operational data; labels may be missing"
            else None
        )

    def choose_file(self, name, content):
        self.file = SimpleNamespace(name=name, getvalue=lambda: content)

    def browser(self, token="a-secret"):
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value(token)
        self.button(app, "Connect").click().run()
        self.assertFalse(app.exception)
        return app

    @staticmethod
    def button(app, label):
        return next(w for w in app.button if w.label == label)

    def test_selected_canonical_xlsx_and_jsonl_replace_preview_before_save(self):
        rows = data.fixture_rows(2)
        values = [
            [json.dumps(v) if isinstance(v, dict) else v for v in row.values()]
            for row in rows
        ]
        self.choose_file(
            "examples.xlsx",
            uploads.workbook_bytes(list(rows[0]), values, sheet="Canonical"),
        )
        app = self.browser()
        self.assertTrue(any("Choose another worksheet" in w.value for w in app.warning))
        next(w for w in app.selectbox if w.label == "Worksheet to import").set_value(
            "Canonical"
        ).run()
        self.assertTrue(self.button(app, "Save new collection for review").disabled)
        self.button(app, "Validate and preview import").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertFalse(self.button(app, "Save new collection for review").disabled)
        self.assertEqual(self.http.get(self.prefix + "/entities/dataset").json(), [])
        self.choose_file("examples.jsonl", jsonl(rows).encode())
        app.run()
        self.assertTrue(self.button(app, "Save new collection for review").disabled)
        self.assertTrue(any("changed" in w.value for w in app.info))
        self.button(app, "Validate and preview import").click().run()
        self.button(app, "Save new collection for review").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        saved = self.http.get(self.prefix + "/entities/dataset").json()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["payload"]["row_count"], 2)
        self.assertEqual(
            saved[0]["payload"]["provenance"]["filename"], "examples.jsonl"
        )
        self.assertTrue(self.button(app, "Save new collection for review").disabled)

    def test_mapped_file_preview_reset_and_failed_mapping_keep_user_choices(self):
        self.choose_file(
            "source.csv",
            b"ID,Family,Evidence,Queue\n1,g1,invoice,Finance\n2,g1,software crash,Engineering\n",
        )
        app = self.browser()
        next(
            w
            for w in app.multiselect
            if w.label == "Columns containing the input evidence"
        ).set_value(["Evidence"])
        next(
            w for w in app.selectbox if w.label == "Unique example ID column"
        ).set_value("ID")
        next(
            w
            for w in app.selectbox
            if w.label == "Source family / customer / document column"
        ).set_value("Family")
        next(w for w in app.selectbox if w.label == "Label column for team").set_value(
            "Queue"
        ).run()
        next(w for w in app.selectbox if w.label.startswith("Finance →")).set_value(
            "billing"
        )
        # One unmapped source value prevents the entire batch from being saved.
        self.button(app, "Validate and preview import").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Source row 3" in w.value for w in app.error))
        self.assertEqual(
            next(
                w
                for w in app.multiselect
                if w.label == "Columns containing the input evidence"
            ).value,
            ["Evidence"],
        )
        self.assertTrue(self.button(app, "Save new collection for review").disabled)
        next(w for w in app.selectbox if w.label.startswith("Engineering →")).set_value(
            "engineering"
        )
        self.button(app, "Validate and preview import").click().run()
        self.assertFalse(app.error)
        next(w for w in app.selectbox if w.label == "File source").set_value(
            "synthetic"
        ).run()
        self.assertTrue(self.button(app, "Save new collection for review").disabled)
        self.button(app, "Validate and preview import").click().run()
        self.button(app, "Save new collection for review").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        saved = self.http.get(self.prefix + "/entities/dataset").json()[0]
        actual = self.http.get(
            self.prefix + "/datasets/" + saved["id"] + "/rows"
        ).json()["rows"]
        self.assertEqual([r["id"] for r in actual], ["1", "2"])
        self.assertTrue(
            all(
                r["metadata"]["origin"] == "synthetic" and not r["metadata"]["reviewed"]
                for r in actual
            )
        )

    def test_append_collection_advance_invalidates_old_preview(self):
        first = self.http.post(
            self.prefix + "/datasets",
            files={"file": ("first.csv", b"id,state,team\n1,original,billing\n")},
        ).json()
        self.choose_file("second.csv", b"id,state,team\n2,new,engineering\n")
        app = self.browser()
        next(
            w for w in app.selectbox if w.label == "Collection for file import"
        ).set_value(first["payload"]["root"]).run()
        self.button(app, "Validate and preview import").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(self.button(app, "Append file to collection").disabled)
        advance = self.http.post(
            self.prefix + "/datasets/" + first["id"] + "/examples",
            json={
                "expected_hash": first["payload"]["sha256"],
                "examples": [{"id": "3", "state": "another owner's example"}],
            },
        )
        self.assertEqual(advance.status_code, 201, advance.text)
        self.button(app, "Append file to collection").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(self.button(app, "Append file to collection").disabled)
        self.assertTrue(
            any("changed" in w.value for w in app.info)
            or any("changed" in w.value for w in app.error)
        )
        self.button(app, "Validate and preview import").click().run()
        self.button(app, "Append file to collection").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        page = self.http.get(self.prefix + "/datasets/" + first["id"] + "/rows").json()
        self.assertEqual(page["total"], 1)
        latest = self.http.get(
            self.prefix + "/datasets/" + page["head"] + "/rows"
        ).json()
        self.assertEqual([r["id"] for r in latest["rows"]], ["1", "3", "2"])


if __name__ == "__main__":
    unittest.main()
