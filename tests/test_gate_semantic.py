"""Combined authenticated reservations; declared-vector subprocesses are fixtures."""

import copy
import hashlib
import json
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import uvicorn
from streamlit.testing.v1 import AppTest

import test_semantic as semantic_fixture
import test_platform as positive_fixture
from test_gate_platform import gate_rows
from decision_workbench.platform.store import digest
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.vega_contracts import load_gate_splits
from decision_workbench.training.vega_remote import write_inputs


class CombinedSemanticTests(unittest.TestCase):
    setUp = semantic_fixture.SemanticWorkflowTests.setUp
    tearDown = semantic_fixture.SemanticWorkflowTests.tearDown
    start_fixture = semantic_fixture.SemanticWorkflowTests.start_fixture
    worker_once = semantic_fixture.SemanticWorkflowTests.worker_once
    submit = semantic_fixture.SemanticWorkflowTests.submit
    dataset = semantic_fixture.SemanticWorkflowTests.dataset
    audit = semantic_fixture.SemanticWorkflowTests.audit
    page = semantic_fixture.SemanticWorkflowTests.page
    review = semantic_fixture.SemanticWorkflowTests.review

    def positive(self, count=800):
        dataset = self.dataset(positive_fixture.fixture_rows(count))
        audit = self.audit(dataset)
        for pair in self.page(audit)["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        job = self.submit(
            "prepare", {"datasets": [dataset], "semantic_audit": audit}, "positive"
        )
        self.assertEqual(job["status"], "succeeded", job["error"])
        snapshot = job["result"]["snapshot"]
        payload = self.app.state.store.get(snapshot, "A", self.project, "snapshot")[
            "payload"
        ]
        return snapshot, payload

    def negatives(self, positive, *, targets=None, bridge=False, count=100):
        rows = gate_rows(count)
        targets = targets or [positive["splits"]["train"][0]["id"]]
        for index, target in enumerate(targets):
            rows[index]["state"] = "NEAR:" + target
        if bridge:
            rows[0]["metadata"]["semantic_links"] = [rows[1]["id"]]
        service = self.app.state.service
        saved = service.gates.save(
            self.owner, self.project, rows, {"fixture": "declared vectors"}
        )
        for row in rows:
            saved = service.gates.review(
                self.owner,
                self.project,
                saved["id"],
                row["id"],
                row["negative_for"],
                row["reason"],
                "approved",
                saved["payload"]["sha256"],
                "Fictional applicability annotation",
            )
        return saved["id"]

    def combined(self, snapshot, negative, key="combined"):
        job = self.submit(
            "gate-semantic-audit",
            {"snapshot": snapshot, "datasets": [negative], "threshold": 0.9},
            key,
        )
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]["audit_id"]

    def reserve(self, snapshot, negative, audit, key="gate-reserve"):
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {
                    "snapshot": snapshot,
                    "datasets": [negative],
                    "semantic_audit": audit,
                },
                "request_key": key,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.worker_once()
        return self.app.state.store.job(response.json()["id"], "A", self.project)

    def test_required_pending_and_positive_only_audits_refuse(self):
        snapshot, positive = self.positive()
        negative = self.negatives(positive)
        config = {"snapshot": snapshot, "datasets": [negative]}
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": config,
                "request_key": "missing-combined",
            },
        )
        self.assertEqual(response.status_code, 400)
        audit = self.combined(snapshot, negative)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {**config, "semantic_audit": audit},
                "request_key": "pending-combined",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Review every", response.text)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {
                    **config,
                    "semantic_audit": positive["semantic_contract"]["audit_id"],
                },
                "request_key": "positive-only-audit",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("combined audit", response.text)

    def test_related_negative_follows_frozen_positive_and_portable_inputs_replay(self):
        snapshot, positive = self.positive()
        before = copy.deepcopy(positive)
        target = positive["splits"]["test"][0]["id"]
        negative = self.negatives(positive, targets=[target])
        audit = self.combined(snapshot, negative)
        page = self.page(audit)
        self.assertEqual(page["row_count"], 900)
        self.assertEqual(page["compared_pairs"], 900 * 899 // 2)
        for pair in page["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        job = self.reserve(snapshot, negative, audit)
        self.assertEqual(job["status"], "succeeded", job["error"])
        gate = self.app.state.service.gates.verify_snapshot(
            "A", self.project, job["result"]["gate_snapshot"]
        )
        self.assertIn("weather-0", [r["id"] for r in gate["splits"]["test"]])
        self.assertIn(
            target,
            next(r for r in gate["splits"]["test"] if r["id"] == "weather-0")[
                "metadata"
            ]["semantic_links"],
        )
        self.assertEqual(
            self.app.state.store.get(snapshot, "A", self.project, "snapshot")[
                "payload"
            ],
            before,
        )
        self.assertNotEqual(gate["positive_splits"], positive["splits"])
        folder = self.home / "portable"
        write_inputs(folder, gate["positive_splits"], gate["splits"])
        portable, hashes = load_splits(folder)
        negatives, negative_hashes = load_gate_splits(folder, portable)
        self.assertEqual(negatives, gate["splits"])
        self.assertEqual(len(hashes) + len(negative_hashes), 8)
        sealed = backup_workspace(self.app.state.store.folder, self.home / "backup")
        restored = self.home / "restored"
        restore_workspace(self.home / "backup", restored, sealed["manifest_sha256"])
        from decision_workbench.platform.service import Service
        from decision_workbench.platform.store import Store

        cold = Service(Store(restored), runtime=object())
        self.assertEqual(
            cold.gates.verify_snapshot(
                "A", self.project, job["result"]["gate_snapshot"]
            ),
            gate,
        )

    def test_frozen_pair_reviews_survive_later_review_revision(self):
        snapshot, positive = self.positive()
        negative = self.negatives(positive)
        audit = self.combined(snapshot, negative)
        page = self.page(audit)
        for pair in page["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {
                    "snapshot": snapshot,
                    "datasets": [negative],
                    "semantic_audit": audit,
                },
                "request_key": "freeze-before-review",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        binding = response.json()["config"]["semantic_contract"]
        self.assertEqual(
            self.review(audit, page["pairs"][0]["id"], "independent", 1).status_code,
            201,
        )
        self.worker_once()
        job = self.app.state.store.job(response.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        gate = self.app.state.service.gates.verify_snapshot(
            "A", self.project, job["result"]["gate_snapshot"]
        )
        self.assertEqual(gate["semantic_contract"], binding)

    def test_current_profile_drift_refuses(self):
        snapshot, positive = self.positive()
        negative = self.negatives(positive)
        audit = self.combined(snapshot, negative)
        for pair in self.page(audit)["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        self.profile["max_pairs"] -= 1
        self.profile_path.write_text(json.dumps(self.profile))
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "gate-prepare",
                "config": {
                    "snapshot": snapshot,
                    "datasets": [negative],
                    "semantic_audit": audit,
                },
                "request_key": "drifted-profile",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_negative_semantic_bridge_between_frozen_positive_roles_refuses(self):
        snapshot, positive = self.positive()
        targets = [positive["splits"][role][0]["id"] for role in ("train", "test")]
        negative = self.negatives(positive, targets=targets, bridge=True, count=120)
        audit = self.combined(snapshot, negative)
        for pair in self.page(audit)["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        job = self.reserve(snapshot, negative, audit)
        self.assertEqual(job["status"], "failed")
        self.assertIn("different frozen positive roles", job["error"])
        self.assertFalse(self.app.state.store.list("A", self.project, "gate-snapshot"))

    def test_calibration_negatives_merge_discovered_positive_families_and_freeze_input_hashes(
        self,
    ):
        from decision_workbench.training.vega_study import calibration_partition

        snapshot, positive = self.positive()
        targets = [r["id"] for r in positive["splits"]["calibration"][:2]]
        negative = self.negatives(positive, targets=targets, bridge=True, count=120)
        audit = self.combined(snapshot, negative)
        for pair in self.page(audit)["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        job = self.reserve(snapshot, negative, audit)
        self.assertEqual(job["status"], "succeeded", job["error"])
        gate_id = job["result"]["gate_snapshot"]
        gate = self.app.state.service.gates.verify_snapshot("A", self.project, gate_id)
        before = calibration_partition(positive["splits"]["calibration"], [])
        after = calibration_partition(
            gate["positive_splits"]["calibration"], gate["splits"]["calibration"]
        )
        self.assertEqual(
            sum(after["family_counts"].values()),
            sum(before["family_counts"].values()) - 1,
        )
        service = self.app.state.service
        service.configure_release_policy(
            self.owner,
            self.project,
            0,
            0,
            1,
            10,
            None,
            min_training_examples_per_class=0,
            min_calibration_examples_per_class=0,
            vega={
                "min_positive_accepted_fraction": 0,
                "max_negative_false_open_rate": 1,
                "min_negative_test_rows": 10,
            },
        )
        trained = service.submit(
            self.owner,
            self.project,
            "train",
            {
                "backend": "vega",
                "snapshot": snapshot,
                "gate_snapshot": gate_id,
                "offline": True,
            },
            "combined-vega-inputs",
        )
        config = trained["config"]
        self.assertEqual(
            config["vega_contract"]["preflight"]["calibration_partition"], after
        )
        folder = self.home / "derived-vega-inputs"
        write_inputs(folder, gate["positive_splits"], gate["splits"])
        _, hashes = load_splits(folder)
        self.assertEqual(config["vega_contract"]["data_sha256"], hashes)
        self.app.state.store.cancel(
            trained["id"], "A", self.project, self.owner["actor"]
        )
        remote = service.submit(
            self.owner,
            self.project,
            "remote-study",
            {
                "backend": "vega",
                "snapshot": snapshot,
                "gate_snapshot": gate_id,
                "offline": True,
                "provider": "modal",
                "region": "fictional-local-fixture",
                "gpu": "fictional-local-fixture",
                "image_digest": "fixture/trainer@sha256:" + "a" * 64,
            },
            "combined-vega-remote-inputs",
        )
        self.worker_once()
        done = self.app.state.store.job(remote["id"], "A", self.project)
        self.assertEqual(done["status"], "succeeded", done["error"])
        from decision_workbench.platform.remote import study_entity

        study = study_entity(self.app.state.store, "A", self.project, done["id"])[
            "payload"
        ]
        self.assertEqual(study["request"]["data_sha256"], hashes)
        prepared = self.home / "workspace/jobs" / done["id"] / "remote-study/data"
        copied, prepared_hashes = load_splits(prepared)
        copied_negative, prepared_negative_hashes = load_gate_splits(prepared, copied)
        self.assertEqual(copied, gate["positive_splits"])
        self.assertEqual(copied_negative, gate["splits"])
        self.assertEqual(prepared_hashes, hashes)
        self.assertEqual(study["request"]["gate_data_sha256"], prepared_negative_hashes)

    def test_owner_only_combined_audit_and_inference_scope(self):
        snapshot, positive = self.positive()
        negative = self.negatives(positive)
        for token, expected in (
            ("edit-secret", 403),
            ("review-secret", 403),
            ("infer-secret", 403),
            ("b-secret", 404),
        ):
            response = self.client.post(
                self.prefix + "/jobs",
                headers={"Authorization": "Bearer " + token},
                json={
                    "kind": "gate-semantic-audit",
                    "config": {
                        "snapshot": snapshot,
                        "datasets": [negative],
                        "threshold": 0.9,
                    },
                    "request_key": "combined-scope-" + token,
                },
            )
            self.assertEqual(response.status_code, expected, response.text)

    def test_combined_review_and_reservation_in_full_app_over_http(self):
        snapshot, positive = self.positive()
        negative = self.negatives(positive)
        ordinary = positive["semantic_contract"]["audit_id"]
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    self.fail("HTTP startup timed out")
                time.sleep(0.02)
            at = AppTest.from_file(str(Path("app.py").resolve()))
            at.session_state["credential"] = "a-secret"
            at.session_state["api_url"] = (
                f"http://127.0.0.1:{listener.getsockname()[1]}"
            )
            at.session_state["principal"] = self.owner
            at.run(timeout=30)

            def widget(kind, label):
                return next(w for w in getattr(at, kind) if w.label == label)

            widget("selectbox", "Reviewed semantic audit for this reservation").select(
                ordinary
            ).run(timeout=30)
            widget("selectbox", "Frozen positive snapshot for applicability").select(
                snapshot
            ).run(timeout=30)
            widget("multiselect", "Reviewed applicability versions to reserve").select(
                negative
            ).run(timeout=30)
            reserve = lambda: widget(
                "button",
                "Reserve applicability train / validation / calibration / test",
            )
            self.assertTrue(reserve().disabled)
            widget("number_input", "Combined Candidate cosine threshold").set_value(
                0.9
            ).run(timeout=30)
            widget("button", "Combined Queue local semantic audit").click().run(
                timeout=30
            )
            self.assertFalse(at.exception)
            self.worker_once()
            job = self.app.state.store.jobs("A", self.project)[0]
            self.assertEqual(job["status"], "succeeded", job["error"])
            self.assertEqual(job["kind"], "gate-semantic-audit")
            audit = job["result"]["audit_id"]
            at.run(timeout=30)
            widget(
                "selectbox", "Combined Reviewed semantic audit for this reservation"
            ).select(audit).run(timeout=30)
            self.assertFalse(at.exception)
            self.assertTrue(reserve().disabled)
            # The same positive pair appears in both displayed audits; review
            # widgets must bind the audit as well as the content-derived pair ID.
            common = set(p["id"] for p in self.page(ordinary)["pairs"]) & set(
                p["id"] for p in self.page(audit)["pairs"]
            )
            self.assertTrue(common)
            last = sorted(common)[0]
            for pair in self.page(audit)["pairs"]:
                if pair["id"] != last:
                    self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
            widget("selectbox", "Semantic candidate pair").select(last).run(timeout=30)
            widget("selectbox", "Combined Semantic candidate pair").select(last).run(
                timeout=30
            )
            self.assertFalse(at.exception)
            next(
                w
                for w in at.text_input
                if w.key == "semantic-reason-" + audit + "-" + last
            ).set_value("Same fictional family; combined review").run(timeout=30)
            widget("button", "Combined Save semantic relationship review").click().run(
                timeout=30
            )
            self.assertFalse(at.exception)
            self.assertFalse(reserve().disabled)
            reserve().click().run(timeout=30)
            self.assertFalse(at.exception)
            self.worker_once()
            job = self.app.state.store.jobs("A", self.project)[0]
            self.assertEqual(job["status"], "succeeded", job["error"])
            gate = self.app.state.service.gates.verify_snapshot(
                "A", self.project, job["result"]["gate_snapshot"]
            )
            self.assertEqual(gate["semantic_contract"]["audit_id"], audit)
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())

    def test_frozen_combined_review_content_tamper_refuses_replay(self):
        snapshot, positive = self.positive()
        negative = self.negatives(positive)
        audit = self.combined(snapshot, negative)
        for pair in self.page(audit)["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        job = self.reserve(snapshot, negative, audit)
        identifier = job["result"]["gate_snapshot"]
        gate = self.app.state.service.gates.verify_snapshot(
            "A", self.project, identifier
        )
        review = gate["semantic_contract"]["review_ids"][0]
        with self.app.state.store.transaction() as db:
            value = json.loads(
                db.execute(
                    "SELECT payload FROM entities WHERE id=?", (review,)
                ).fetchone()["payload"]
            )
            value["note"] += " tampered"
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?", (json.dumps(value), review)
            )
        with self.assertRaisesRegex(ValueError, "review content hash"):
            self.app.state.service.gates.verify_snapshot("A", self.project, identifier)

    def test_committed_combined_job_inventory_and_input_hash_tamper_refuse(self):
        snapshot, positive = self.positive(80)
        negative = self.negatives(positive)
        audit = self.combined(snapshot, negative)
        value = self.app.state.store.get(audit, "A", self.project, "semantic-audit")[
            "payload"
        ]
        for pair in self.page(audit)["pairs"]:
            self.assertEqual(self.review(audit, pair["id"]).status_code, 201)
        job_id = value["job_id"]
        original = self.app.state.store.job(job_id, "A", self.project)["config"]
        for key, replacement in (
            ("datasets", ["0" * 32]),
            ("input_sha256", "0" * 64),
            ("applicability", False),
        ):
            with self.subTest(field=key):
                changed = {**original, key: replacement}
                with self.app.state.store.transaction() as db:
                    db.execute(
                        "UPDATE jobs SET config=? WHERE id=?",
                        (json.dumps(changed), job_id),
                    )
                response = self.client.post(
                    self.prefix + "/jobs",
                    json={
                        "kind": "gate-prepare",
                        "config": {
                            "snapshot": snapshot,
                            "datasets": [negative],
                            "semantic_audit": audit,
                        },
                        "request_key": "committed-combined-tamper-" + key,
                    },
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("matching successfully completed job", response.text)


if __name__ == "__main__":
    unittest.main()
