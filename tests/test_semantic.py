"""Semantic arithmetic and reviewed reservation controls; declared-vector fixtures."""

import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import uvicorn
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
import test_platform as fixtures

from decision_workbench.data import prepare
from decision_workbench.semantic import enforce_edges
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store, digest
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.semantic import (
    validate_profile,
    validate_report,
    verify_snapshot,
)
from decision_workbench.training.contracts import validate_splits
from decision_workbench.training.semantic_runner import (
    MODEL_FILES,
    cosine_pairs,
    verify_model,
)

REAL_POPEN = subprocess.Popen


class SemanticArithmeticTests(unittest.TestCase):
    def test_exhaustive_pairs_limit_and_invalid_vectors(self):
        vectors = [[1, 0], [1, 0.01], [1, 0.02], [0, 1]]
        pairs, sha = cosine_pairs(vectors, ["a", "b", "c", "d"], 0.99, 10)
        self.assertEqual(
            [(p["left"], p["right"]) for p in pairs],
            [("a", "b"), ("a", "c"), ("b", "c")],
        )
        self.assertEqual(len(sha), 64)
        with self.assertRaisesRegex(ValueError, "no partial audit"):
            cosine_pairs(vectors, ["a", "b", "c", "d"], 0.99, 1)
        for bad in ([[0, 0]], [[float("nan"), 0]], [1]):
            with self.assertRaises(ValueError):
                cosine_pairs(bad, ["a"], 0.9, 10)
        with self.assertRaisesRegex(ValueError, "cancelled"):
            cosine_pairs(vectors, ["a", "b", "c", "d"], 0.99, 10, lambda: True)

    def test_declared_relations_join_transitively_and_manual_overlap_is_refused(self):
        rows = fixtures.fixture_rows(40)
        rows[0]["metadata"]["semantic_links"] = [rows[1]["id"]]
        rows[1]["metadata"]["semantic_links"] = [rows[2]["id"]]
        splits = prepare(rows)
        validate_splits(splits)
        roles = {r["id"]: name for name, part in splits.items() for r in part}
        self.assertEqual(roles["case-0"], roles["case-2"])
        validation = splits["validation"][0]
        splits["train"][0]["metadata"]["semantic_links"] = [validation["id"]]
        with self.assertRaisesRegex(ValueError, "semantic relatives"):
            validate_splits(splits)
        for value in (["absent"], ["case-0"], [[], "case-1"], ["case-1", "case-1"]):
            rows = fixtures.fixture_rows(40)
            rows[0]["metadata"]["semantic_links"] = value
            with self.assertRaises(ValueError):
                prepare(rows)


class SemanticWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        keys = fixtures.keys() + [
            {
                "key_sha256": hashlib.sha256(b"edit-secret").hexdigest(),
                "tenant": "A",
                "actor": "editor",
                "role": "editor",
            }
        ]
        self.app = create_app(self.home / "workspace", keys)
        self.client = TestClient(self.app, headers={"Authorization": "Bearer a-secret"})
        self.owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        self.project = self.app.state.service.create_project(
            self.owner, "Semantic fixture", "Fictional policy", fixtures.QUESTIONS
        )["id"]
        self.prefix = "/v1/projects/" + self.project
        model = self.home / "model"
        model.mkdir()
        for filename in MODEL_FILES:
            (model / filename).write_bytes(b"{}")
        (model / "config.json").write_text('{"model_type":"bert"}')
        self.profile = {
            "id": "fixture",
            "tenants": ["A"],
            "projects": [self.project],
            "required": True,
            "model": "fixture/declared-vectors",
            "revision": "a" * 40,
            "model_dir": str(model),
            "files_sha256": {
                name: hashlib.sha256((model / name).read_bytes()).hexdigest()
                for name in MODEL_FILES
            },
            "packages": {
                name: "fixture"
                for name in (
                    "torch",
                    "transformers",
                    "numpy",
                    "safetensors",
                    "tokenizers",
                )
            },
            "max_tokens": 256,
            "max_rows": 5000,
            "max_pairs": 10000,
            "timeout_seconds": 10,
        }
        self.profile_path = self.home / "profile.json"
        self.profile_path.write_text(json.dumps(self.profile))
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_SEMANTIC_PROFILE_FILE": str(self.profile_path),
                "DMT_SEMANTIC_PYTHON": sys.executable,
                "DMT_PROVIDER_TYPESAFE_API_KEY": "must-not-enter-child",
                "TORCHINDUCTOR_CACHE_DIR": "must-not-inherit-cache",
            },
        )
        self.environment.start()
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
        self.environment.stop()
        self.client.close()
        self.app.state.service.runtime.close()
        self.temp.cleanup()

    def start_fixture(self, argv, **kwargs):
        self.assertNotIn("DMT_PROVIDER_TYPESAFE_API_KEY", kwargs["env"])
        self.assertEqual(
            kwargs["env"]["TORCHINDUCTOR_CACHE_DIR"],
            str(Path(argv[4]).parent / "torch-cache"),
        )
        child = REAL_POPEN(
            [
                sys.executable,
                str(Path("tests/semantic_fixture_runner.py").resolve()),
                *argv[3:],
            ],
            **kwargs,
        )
        self.children.append(child)
        return child

    def worker_once(self):
        worker = Worker(self.app.state.store)
        try:
            with patch(
                "decision_workbench.platform.semantic.subprocess.Popen",
                self.start_fixture,
            ):
                worker.once()
        finally:
            worker.service.runtime.close()

    def dataset(self, rows=None):
        rows = rows or fixtures.fixture_rows(180)
        rows[0]["state"] = "RELATED invoice policy case"
        rows[1]["state"] = "RELATED different counterfactual case"
        return self.app.state.service.save_dataset(
            self.owner,
            self.project,
            rows,
            {"fixture": "declared vectors, not model quality"},
        )["id"]

    def submit(self, kind, config, key):
        response = self.client.post(
            self.prefix + "/jobs",
            json={"kind": kind, "config": config, "request_key": "semantic-" + key},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.worker_once()
        return self.app.state.store.job(response.json()["id"], "A", self.project)

    def audit(self, dataset, base=None, key="audit"):
        job = self.submit(
            "semantic-audit",
            {
                "datasets": [dataset],
                "threshold": 0.9,
                **({"base_snapshot": base} if base else {}),
            },
            key,
        )
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]["audit_id"]

    def page(self, audit):
        result = self.client.get(self.prefix + "/semantic-audits/" + audit + "/pairs")
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def review(self, audit, pair, decision="related", revision=0):
        page = self.page(audit)
        return self.client.post(
            self.prefix + "/semantic-audits/" + audit + "/reviews",
            json={
                "pair_id": pair,
                "decision": decision,
                "expected_revision": revision,
                "expected_hash": page["sha256"],
                "note": "Fictional family relationship inspected",
            },
        )

    def reservation(self, dataset, audit, base=None, key="prepare"):
        job = self.submit(
            "prepare",
            {
                "datasets": [dataset],
                "semantic_audit": audit,
                **({"base_snapshot": base} if base else {}),
            },
            key,
        )
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]["snapshot"]

    def test_profile_binding_scope_and_file_mutation(self):
        public = self.client.get(self.prefix + "/semantic-profile").json()["profile"]
        self.assertNotIn("model_dir", public)
        self.assertNotIn("tenants", public)
        self.assertEqual(public["revision"], "a" * 40)
        for field, value in (
            ("required", 1),
            ("max_tokens", True),
            ("revision", None),
            ("files_sha256", {}),
        ):
            bad = {**self.profile, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_profile(bad)
        verify_model(self.profile)
        Path(self.profile["model_dir"], "vocab.txt").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            verify_model(self.profile)
        for token, expected in (("b-secret", 404), ("infer-secret", 403)):
            self.assertEqual(
                self.client.get(
                    self.prefix + "/semantic-profile",
                    headers={"Authorization": "Bearer " + token},
                ).status_code,
                expected,
            )

    def test_required_reservations_and_pending_candidates_are_refused(self):
        dataset = self.dataset()
        denied = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset]},
                "request_key": "missing-audit",
            },
        )
        self.assertEqual(denied.status_code, 400, denied.text)
        audit = self.audit(dataset)
        page = self.page(audit)
        self.assertEqual(page["compared_pairs"], 180 * 179 // 2)
        self.assertEqual(page["pending_pairs"], 1)
        denied = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset], "semantic_audit": audit},
                "request_key": "pending",
            },
        )
        self.assertEqual(denied.status_code, 400, denied.text)
        self.assertIn("Review every", denied.text)
        self.assertEqual(self.app.state.store.list("A", self.project, "snapshot"), [])
        for token, expected in (("b-secret", 404), ("infer-secret", 403)):
            self.assertEqual(
                self.client.get(
                    self.prefix + "/semantic-audits/" + audit + "/pairs",
                    headers={"Authorization": "Bearer " + token},
                ).status_code,
                expected,
            )

    def test_review_cas_race_role_restriction_and_immutable_snapshot(self):
        dataset = self.dataset()
        audit = self.audit(dataset)
        pair = self.page(audit)["pairs"][0]["id"]
        for token in ("edit-secret", "infer-secret"):
            denied = self.client.post(
                self.prefix + "/semantic-audits/" + audit + "/reviews",
                headers={"Authorization": "Bearer " + token},
                json={
                    "pair_id": pair,
                    "decision": "related",
                    "expected_revision": 0,
                    "expected_hash": self.page(audit)["sha256"],
                    "note": "Role refusal check",
                },
            )
            self.assertEqual(denied.status_code, 403, denied.text)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.review(audit, pair), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [201, 409])
        snapshot_id = self.reservation(dataset, audit)
        original = self.app.state.store.get(snapshot_id, "A", self.project, "snapshot")[
            "payload"
        ]
        roles = {
            row["id"]: role for role, rows in original["splits"].items() for row in rows
        }
        self.assertEqual(roles["case-0"], roles["case-1"])
        self.assertEqual(self.review(audit, pair, "independent", 1).status_code, 201)
        verify_snapshot(self.app.state.store, "A", self.project, original)
        self.assertEqual(
            self.app.state.store.get(snapshot_id, "A", self.project, "snapshot")[
                "payload"
            ],
            original,
        )
        old_review = original["semantic_contract"]["review_ids"][0]
        with self.app.state.store.transaction() as db:
            row = db.execute(
                "SELECT payload FROM entities WHERE id=?", (old_review,)
            ).fetchone()
            payload = json.loads(row["payload"])
            payload["decision"] = "independent"
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(payload), old_review),
            )
        with self.assertRaisesRegex(ValueError, "review content hash"):
            verify_snapshot(self.app.state.store, "A", self.project, original)

    def test_input_version_profile_drift_and_tampered_reports_refused(self):
        dataset = self.dataset()
        audit = self.audit(dataset)
        page = self.page(audit)
        self.assertEqual(self.review(audit, page["pairs"][0]["id"]).status_code, 201)
        other = self.dataset()
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [other], "semantic_audit": audit},
                "request_key": "wrong-version",
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = self.app.state.store.get(audit, "A", self.project, "semantic-audit")[
            "payload"
        ]
        for mutate in (
            lambda r: r["report"].update(compared_pairs=1),
            lambda r: r["report"]["pairs"][0].update(similarity=0.1),
            lambda r: r["report"].update(row_count=True),
            lambda r: r["report"].update(device="cuda"),
            lambda r: r["report"].update(packages={}),
            lambda r: r["report"].update(pairs=None),
            lambda r: r["profile"].update(model_dir="unexpected-private-field"),
            lambda r: r["report"].update(scope=None),
        ):
            bad = copy.deepcopy(payload)
            mutate(bad)
            bad.pop("sha256")
            bad["sha256"] = digest(bad)
            with self.assertRaises(ValueError):
                validate_report(bad)
        self.profile["max_pairs"] = 9999
        self.profile_path.write_text(json.dumps(self.profile))
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset], "semantic_audit": audit},
                "request_key": "profile-drift",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_successful_training_restore_and_reserved_content_tamper_refusal(self):
        from decision_workbench.platform.recovery import (
            backup_workspace,
            restore_workspace,
        )

        dataset = self.dataset()
        audit = self.audit(dataset)
        self.review(audit, self.page(audit)["pairs"][0]["id"])
        snapshot = self.reservation(dataset, audit)
        self.app.state.service.configure_release_policy(
            self.owner, self.project, 0, 0, 1, 10, None
        )
        trained = self.submit(
            "train", {"snapshot": snapshot, "backend": "cpu-baseline"}, "train"
        )
        self.assertEqual(trained["status"], "succeeded", trained["error"])
        backup = backup_workspace(self.app.state.store.folder, self.home / "backup")
        restore_workspace(
            backup["destination"], self.home / "restored", backup["manifest_sha256"]
        )
        restored = Store(self.home / "restored").get(
            snapshot, "A", self.project, "snapshot"
        )["payload"]
        verify_snapshot(Store(self.home / "restored"), "A", self.project, restored)
        bad = copy.deepcopy(restored)
        bad["splits"]["train"][0]["state"] += " changed"
        bad["sha256"] = digest(bad["splits"])
        with self.assertRaisesRegex(ValueError, "reserved content differs"):
            verify_snapshot(self.app.state.store, "A", self.project, bad)

    def test_synthetic_extension_never_moves_a_related_heldout(self):
        dataset = self.dataset()
        audit = self.audit(dataset)
        self.review(audit, self.page(audit)["pairs"][0]["id"])
        snapshot = self.reservation(dataset, audit)
        base = self.app.state.store.get(snapshot, "A", self.project, "snapshot")[
            "payload"
        ]
        candidate = copy.deepcopy(base["splits"]["train"][0])
        candidate.update(
            id="new-synthetic",
            group_id="new-family",
            state="NEAR:" + base["splits"]["test"][0]["id"],
        )
        candidate["metadata"] = {
            "origin": "synthetic",
            "reviewed": True,
            "seed_ids": [base["splits"]["train"][0]["id"]],
        }
        added = self.app.state.service.save_dataset(
            self.owner, self.project, [candidate], {"fixture": True}
        )["id"]
        audit2 = self.audit(added, snapshot, "extension-audit")
        page = self.page(audit2)
        for pair in page["pairs"]:
            self.review(audit2, pair["id"])
        result = self.submit(
            "prepare",
            {"datasets": [added], "base_snapshot": snapshot, "semantic_audit": audit2},
            "extension-refused",
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("semantic relatives", result["error"])
        self.assertEqual(
            self.app.state.store.get(snapshot, "A", self.project, "snapshot")[
                "payload"
            ],
            base,
        )
        for pair in page["pairs"]:
            if "new-synthetic" in (pair["left"], pair["right"]):
                self.review(audit2, pair["id"], "independent", 1)
        extended = self.reservation(added, audit2, snapshot, "extension-approved")
        result = self.app.state.store.get(extended, "A", self.project, "snapshot")[
            "payload"
        ]
        for role in ("validation", "calibration", "test"):
            self.assertEqual(result["splits"][role], base["splits"][role])
        verify_snapshot(self.app.state.store, "A", self.project, result)

    def test_child_cancel_and_wrong_result_cannot_publish_audit(self):
        rows = fixtures.fixture_rows(40)
        rows[-1]["state"] = "STALL fixture"
        dataset = self.dataset(rows)
        queued = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "semantic-audit",
                "config": {"datasets": [dataset], "threshold": 0.9},
                "request_key": "semantic-cancel",
            },
        ).json()
        thread = threading.Thread(target=self.worker_once)
        thread.start()
        deadline = time.monotonic() + 10
        while not self.children:
            if time.monotonic() > deadline:
                self.fail("Semantic child did not start")
            time.sleep(0.02)
        response = self.client.post(self.prefix + "/jobs/" + queued["id"] + "/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(
            self.app.state.store.job(queued["id"], "A", self.project)["status"],
            "cancelled",
        )
        self.assertIsNotNone(self.children[-1].poll())
        self.assertEqual(
            self.app.state.store.list("A", self.project, "semantic-audit"), []
        )
        rows = fixtures.fixture_rows(40)
        rows[-1]["state"] = "CORRUPT result identity fixture"
        job = self.submit(
            "semantic-audit",
            {"datasets": [self.dataset(rows)], "threshold": 0.9},
            "corrupt",
        )
        self.assertEqual(job["status"], "failed")
        self.assertEqual(
            self.app.state.store.list("A", self.project, "semantic-audit"), []
        )

    def test_deadline_and_candidate_overflow_never_accept_partial_report(self):
        rows = fixtures.fixture_rows(40)
        rows[-1]["state"] = "STALL deadline fixture"
        job = self.submit(
            "semantic-audit",
            {"datasets": [self.dataset(rows)], "threshold": 0.9},
            "deadline",
        )
        self.assertEqual(job["status"], "failed")
        self.assertIn("deadline", job["error"])
        self.assertIsNotNone(self.children[-1].poll())
        self.profile["max_pairs"] = 1
        self.profile_path.write_text(json.dumps(self.profile))
        rows = fixtures.fixture_rows(40)
        rows[2]["state"] = "RELATED third fixture"
        job = self.submit(
            "semantic-audit",
            {"datasets": [self.dataset(rows)], "threshold": 0.9},
            "overflow",
        )
        self.assertEqual(job["status"], "failed")
        self.assertEqual(
            self.app.state.store.list("A", self.project, "semantic-audit"), []
        )

    def test_browser_audit_review_and_reservation_over_actual_http(self):
        dataset = self.dataset()
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
            self.assertFalse(at.exception)

            def widget(kind, label):
                return next(w for w in getattr(at, kind) if w.label == label)

            widget(
                "multiselect",
                "Dataset versions to prepare (approved rows; rejected rows excluded)",
            ).select(dataset).run(timeout=30)
            self.assertTrue(
                widget(
                    "button", "Reserve train / validation / calibration / test"
                ).disabled
            )
            widget("button", "Queue local semantic audit").click().run(timeout=30)
            self.assertFalse(at.exception)
            self.worker_once()
            job = self.app.state.store.jobs("A", self.project)[0]
            self.assertEqual(job["status"], "succeeded", job["error"])
            audit = job["result"]["audit_id"]
            at.run(timeout=30)
            widget("selectbox", "Reviewed semantic audit for this reservation").select(
                audit
            ).run(timeout=30)
            self.assertTrue(
                widget(
                    "button", "Reserve train / validation / calibration / test"
                ).disabled
            )
            widget("text_input", "Semantic review reason").set_value(
                "Same fictional source family"
            ).run(timeout=30)
            widget("button", "Save semantic relationship review").click().run(
                timeout=30
            )
            self.assertFalse(at.exception)
            self.assertFalse(
                widget(
                    "button", "Reserve train / validation / calibration / test"
                ).disabled
            )
            widget(
                "button", "Reserve train / validation / calibration / test"
            ).click().run(timeout=30)
            self.assertFalse(at.exception)
            self.worker_once()
            job = self.app.state.store.jobs("A", self.project)[0]
            self.assertEqual(job["status"], "succeeded", job["error"])
            snapshot = self.app.state.store.get(
                job["result"]["snapshot"], "A", self.project, "snapshot"
            )["payload"]
            verify_snapshot(self.app.state.store, "A", self.project, snapshot)
            self.assertEqual(self.page(audit)["pending_pairs"], 0)
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())
