"""Release snapshot preconditions with real CPU training, HTTP and browser paths."""

from pathlib import Path
import unittest
from unittest.mock import patch

from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker
from decision_workbench.platform import baseline
import test_platform as fixtures

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class ReleasePinTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PlatformTests("runTest")
        self.f.setUp()
        self.snapshot = self.f.snapshot()
        job = self.f.job(
            "train",
            {"snapshot": self.snapshot, "backend": "cpu-baseline"},
            "release-pin-train",
        )
        Worker(Store(self.f.home)).once()
        trained = self.f.app.state.store.job(job["id"], "A", self.f.project)
        self.assertEqual(trained["status"], "succeeded", trained["error"])
        self.model = trained["result"]["model"]
        self.release(self.model, 0)
        model = self.f.app.state.store.get(self.model, "A", self.f.project, "model")[
            "payload"
        ]
        self.pin = {
            "model_id": self.model,
            "release_revision": 1,
            "schema_sha256": model["schema_sha256"],
        }
        self.body = {
            "state": "invoice payment refund requested urgent",
            "threshold": 0.5,
        }

    def tearDown(self):
        self.f.app.state.service.runtime.close()
        self.f.tearDown()

    def release(self, model, revision):
        response = self.f.client.post(
            self.f.prefix + "/releases",
            json={
                "model_id": model,
                "expected_revision": revision,
                "note": "Fictional release precondition acceptance",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def predict(self, pin):
        return self.f.client.post(
            self.f.prefix + "/decisions",
            json={**self.body, "expected_release": pin},
            headers={"Authorization": "Bearer infer-secret"},
        )

    def test_matching_precondition_preserves_legacy_prediction_and_metadata(self):
        legacy = self.f.client.post(self.f.prefix + "/decisions", json=self.body)
        pinned = self.predict(self.pin)
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(pinned.status_code, 200, pinned.text)
        self.assertEqual(pinned.json(), legacy.json())
        self.assertEqual(pinned.json()["answers"]["team"]["answer"], "billing")

    def test_each_mismatch_refuses_before_model_or_provider_execution(self):
        service = self.f.app.state.service
        with (
            patch.object(
                service,
                "checked_bundle",
                side_effect=AssertionError("Loaded before pin check"),
            ) as load,
            patch.object(service.runtime, "predict") as native,
            patch.object(service.hosted, "call") as hosted,
        ):
            for values in (
                {"model_id": "f" * 32},
                {"release_revision": 2},
                {"schema_sha256": "f" * 64},
            ):
                with self.subTest(values=values):
                    result = self.predict({**self.pin, **values})
                    self.assertEqual(result.status_code, 409, result.text)
            load.assert_not_called()
            native.assert_not_called()
            hosted.assert_not_called()

    def test_rollback_to_the_same_model_still_requires_the_new_revision(self):
        job = self.f.job(
            "train",
            {"snapshot": self.snapshot, "backend": "cpu-baseline"},
            "release-pin-second-train",
        )
        Worker(Store(self.f.home)).once()
        other = self.f.app.state.store.job(job["id"], "A", self.f.project)["result"][
            "model"
        ]
        self.release(other, 1)
        self.release(self.model, 2)
        self.assertEqual(self.predict(self.pin).status_code, 409)
        refreshed = {**self.pin, "release_revision": 3}
        result = self.predict(refreshed)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["model_id"], self.model)
        self.assertEqual(result.json()["release_revision"], 3)

    def test_inflight_prediction_keeps_the_snapshot_it_already_selected(self):
        original = baseline.predict

        def activate_during_compute(bundle, state, threshold):
            self.f.app.state.service.activate(
                OWNER, self.f.project, self.model, 1, "Concurrent fictional activation"
            )
            return original(bundle, state, threshold)

        with patch.object(baseline, "predict", side_effect=activate_during_compute):
            result = self.predict(self.pin)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["release_revision"], 1)
        self.assertEqual(
            self.f.app.state.store.active("A", self.f.project)["revision"], 2
        )
        self.assertEqual(self.predict(self.pin).status_code, 409)

    def test_strict_api_and_direct_service_validation_refuse_partial_or_boolean_pins(
        self,
    ):
        invalid = [
            {},
            [],
            {**self.pin, "release_revision": True},
            {**self.pin, "release_revision": 0},
            {**self.pin, "schema_sha256": "invalid"},
            {**self.pin, "model_id": "other-project"},
            {**self.pin, "extra": "no"},
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.predict(value).status_code, 422)
                with self.assertRaises(ValueError):
                    self.f.app.state.service.predict(
                        OWNER, self.f.project, **self.body, expected_release=value
                    )
        with self.assertRaises(ValueError):
            self.f.app.state.service.predict(
                OWNER, self.f.project, self.body["state"], True
            )
        self.assertEqual(self.predict(None).status_code, 200)

    def test_owner_and_inference_views_send_the_snapshot_and_allow_explicit_unpinning(
        self,
    ):
        from http_server_fixture import LiveServer
        from streamlit.testing.v1 import AppTest

        with LiveServer(self.f.home, fixtures.keys()) as live:
            original = live.app.state.service.predict
            with patch.object(
                live.app.state.service, "predict", wraps=original
            ) as calls:
                for role, token, button, label in (
                    (
                        "admin",
                        "a-secret",
                        "Make decision",
                        "Evidence for the released model",
                    ),
                    ("inference", "infer-secret", "Decide", "Input evidence"),
                ):
                    with self.subTest(role=role):
                        browser = AppTest.from_file(str(Path("app.py").resolve()))
                        browser.session_state["credential"] = token
                        browser.session_state["api_url"] = live.url
                        browser.session_state["principal"] = {
                            "tenant": "A",
                            "actor": "owner" if role == "admin" else "app",
                            "role": role,
                        }
                        browser.session_state["connection_mode"] = "shared"
                        browser.run(timeout=30)
                        next(
                            w for w in browser.text_area if w.label == label
                        ).set_value(self.body["state"])
                        next(
                            w for w in browser.button if w.label == button
                        ).click().run(timeout=30)
                        self.assertFalse(browser.exception)
                        self.assertFalse(browser.error)
                        self.assertEqual(
                            calls.call_args.kwargs["expected_release"], self.pin
                        )
                        next(
                            w
                            for w in browser.checkbox
                            if w.label == "Require the active release version"
                        ).uncheck().run(timeout=30)
                        next(
                            w for w in browser.button if w.label == button
                        ).click().run(timeout=30)
                        self.assertFalse(browser.exception)
                        self.assertNotIn("expected_release", calls.call_args.kwargs)
