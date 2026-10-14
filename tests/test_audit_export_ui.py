"""Live HTTP audit capture and verified browser-download preparation."""

import io
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from decision_workbench.platform import audit_exports as exports
import test_platform_http as fixtures
from test_platform import QUESTIONS


class AuditExportBrowserTests(unittest.TestCase):
    setUp = fixtures.LocalProcessTests.setUp
    tearDown = fixtures.LocalProcessTests.tearDown

    def browser(self, token="a-secret"):
        project = self.http.post(
            "/v1/projects",
            json={
                "name": "Fictional audit source",
                "description": "Use decision-time ticket evidence",
                "questions": QUESTIONS,
            },
        ).json()["id"]
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value(token)
        next(w for w in app.button if w.label == "Connect").click().run()
        self.assertFalse(app.exception)
        return app, project

    def capture(self, app):
        next(w for w in app.checkbox if w.label == "Open audit exports").check().run()
        next(w for w in app.text_area if w.label == "Audit export purpose").set_value(
            "Owner-approved fictional audit study"
        )
        next(
            w
            for w in app.checkbox
            if w.label
            == "I approve downloading this project's audit identities and notes"
        ).check()
        next(w for w in app.button if w.label == "Capture audit window").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [e.value for e in app.error])

    def test_owner_captures_inspects_and_prepares_verified_download(self):
        app, project = self.browser()
        self.capture(app)
        entity = self.app.state.store.list("A", project, "audit-export")[0]
        manifest = entity["payload"]["manifest"]
        self.assertTrue(
            any("Audit window captured" in value.value for value in app.success)
        )
        self.assertTrue(
            any(
                entity["payload"]["manifest_sha256"] == value.value
                for value in app.code
            )
        )
        next(
            w for w in app.button if w.label == "Prepare verified audit download"
        ).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [e.value for e in app.error])
        key = f'audit-download-{project}-{entity["id"]}-{entity["payload"]["manifest_sha256"]}'
        self.assertEqual(
            exports.verify_stream(
                io.BytesIO(app.session_state[key]), entity["payload"]["manifest_sha256"]
            )["count"],
            manifest["count"],
        )
        self.assertTrue(
            any(
                w.proto.label == "Download verified audit window"
                for w in app.get("download_button")
            )
        )

    def test_download_delivery_corruption_never_creates_verified_button_data(self):
        app, project = self.browser()
        self.capture(app)
        entity = self.app.state.store.list("A", project, "audit-export")[0]
        body = self.http.get(
            f'/v1/projects/{project}/audit-exports/{entity["id"]}/download'
        ).content
        with patch.object(exports, "stream", return_value=iter([body[:-5]])):
            next(
                w for w in app.button if w.label == "Prepare verified audit download"
            ).click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("truncated" in e.value for e in app.error))
        key = f'audit-download-{project}-{entity["id"]}-{entity["payload"]["manifest_sha256"]}'
        self.assertNotIn(key, app.session_state)

    def test_reviewer_has_no_audit_export_controls(self):
        app, _ = self.browser("review-secret")
        self.assertFalse(any(w.label == "Open audit exports" for w in app.checkbox))
