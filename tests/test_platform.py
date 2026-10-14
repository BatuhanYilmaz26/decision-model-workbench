"""Integration tests of the actual API, durable worker and learned baseline."""

import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from decision_workbench.data import hard_gold, jsonl
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store, Conflict, digest
from decision_workbench.platform.worker import Worker

QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Route invoice to billing and crashes to engineering",
        "criteria": {
            "billing": "invoice and payment",
            "engineering": "software crash and bug",
        },
    },
    "refund": {"type": "noul", "instructions": "Was a refund requested?"},
    "urgency": {
        "type": "score",
        "instructions": "none, normal or urgent",
        "criteria": ["routine", "normal", "urgent"],
    },
}


def fixture_rows(count=180):
    rows = []
    for i in range(count):
        labels = {
            "team": "billing" if i % 2 == 0 else "engineering",
            "refund": i % 2 == 0,
            "urgency": i % 3,
        }
        message = (
            (
                "invoice payment refund requested"
                if i % 2 == 0
                else "software crash bug no refund needed"
            )
            + " "
            + ["routine", "normal", "urgent"][i % 3]
            + f" sourcecase{i:05d}"
        )
        rows.append(
            {
                "id": f"case-{i}",
                "group_id": f"group-{i}",
                "state": message,
                "questions": QUESTIONS,
                "gold": hard_gold(QUESTIONS, labels),
                "metadata": {"origin": "real", "reviewed": True, "fixture": True},
            }
        )
    return rows


def keys():
    return [
        {
            "key_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "tenant": tenant,
            "actor": actor,
            "role": role,
        }
        for token, tenant, actor, role in [
            ("a-secret", "A", "owner", "admin"),
            ("b-secret", "B", "owner", "admin"),
            ("review-secret", "A", "reviewer", "reviewer"),
            ("infer-secret", "A", "app", "inference"),
        ]
    ]


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.app = create_app(self.home, keys())
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        self.project = self.client.post(
            "/v1/projects",
            json={
                "name": "support",
                "description": "Use invoice/crash routing, refund-request and urgency policy.",
                "questions": QUESTIONS,
            },
        ).json()["id"]
        self.prefix = "/v1/projects/" + self.project

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def dataset(self, count=180):
        response = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("data.jsonl", jsonl(fixture_rows(count)).encode())},
        )
        self.assertEqual(response.status_code, 201, response.text)
        dataset = response.json()
        dataset["payload"]["rows"] = self.app.state.store.dataset_rows(
            dataset["id"], "A", self.project
        )
        self.assertTrue(
            all(not r["metadata"]["reviewed"] for r in dataset["payload"]["rows"])
        )
        # Model lifecycle tests use service-reviewed revisions. HTTP review is separately exercised below.
        rows = copy.deepcopy(dataset["payload"]["rows"])
        for row in rows:
            row["metadata"].update(
                reviewed=True,
                review_status="approved",
                reviewer="owner",
                review_note="Handwritten test fixture label checked",
            )
        p = {"tenant": "A", "actor": "owner", "role": "admin"}
        return self.app.state.service.save_dataset(
            p, self.project, rows, {"parent": dataset["id"], "fixture_review": True}
        )

    def job(self, kind, config, key="idempotent-test-1"):
        if (
            kind == "train"
            and not self.app.state.store.release_policy("A", self.project)["policy"]
        ):
            self.client.put(
                self.prefix + "/release-policy",
                json={
                    "min_accuracy": 0.0,
                    "min_macro_f1": 0.0,
                    "max_ece": 1.0,
                    "min_test_rows": 10,
                },
            )
        response = self.client.post(
            self.prefix + "/jobs",
            json={"kind": kind, "config": config, "request_key": key},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def snapshot(self):
        dataset = self.dataset()
        job = self.job("prepare", {"datasets": [dataset["id"]]})
        self.assertTrue(Worker(Store(self.home)).once())
        current = self.app.state.store.job(job["id"], "A", self.project)
        self.assertEqual(current["status"], "succeeded", current["error"])
        return current["result"]["snapshot"]

    def test_authenticated_isolation_roles_and_immutable_reviews(self):
        dataset = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("data.jsonl", jsonl(fixture_rows(6)).encode())},
        ).json()
        self.assertEqual(
            self.client.get(
                "/v1/projects", headers={"Authorization": "Bearer wrong"}
            ).status_code,
            401,
        )
        other = {"Authorization": "Bearer b-secret"}
        for path in (
            self.prefix,
            self.prefix + "/entities/dataset",
            self.prefix + "/datasets/" + dataset["id"] + "/download",
            self.prefix + "/jobs",
            self.prefix + "/active",
            self.prefix + "/audit",
        ):
            self.assertEqual(self.client.get(path, headers=other).status_code, 404)
        row = self.app.state.store.dataset_rows(dataset["id"], "A", self.project)[0]
        body = {
            "record_id": row["id"],
            "gold": row["gold"],
            "approved": True,
            "expected_hash": dataset["payload"]["sha256"],
            "note": "Reviewed fixture",
        }
        result = self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/reviews",
            json=body,
            headers={"Authorization": "Bearer review-secret"},
        )
        self.assertEqual(result.status_code, 201, result.text)
        original = self.app.state.store.get(dataset["id"], "A", self.project)[
            "payload"
        ]["rows"][0]
        self.assertFalse(original["metadata"]["reviewed"])
        reviewed_row = self.app.state.store.dataset_rows(
            result.json()["id"], "A", self.project
        )[0]
        self.assertTrue(reviewed_row["metadata"]["reviewed"])
        self.assertEqual(reviewed_row["metadata"]["reviewer"], "reviewer")
        self.assertEqual(
            self.client.get(
                self.prefix + "/entities/dataset",
                headers={"Authorization": "Bearer infer-secret"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "prepare",
                    "config": {"datasets": [dataset["id"]]},
                    "request_key": "role-rejection",
                },
                headers={"Authorization": "Bearer review-secret"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.prefix + "/datasets/" + dataset["id"] + "/reviews",
                json={**body, "expected_hash": "wrong"},
            ).status_code,
            409,
        )

    def test_real_learned_model_release_reload_restart_rollback_and_tamper(self):
        snapshot = self.snapshot()
        job = self.job(
            "train", {"snapshot": snapshot, "backend": "cpu-baseline"}, "train-first"
        )
        Worker(Store(self.home)).once()
        current = self.app.state.store.job(job["id"], "A", self.project)
        self.assertEqual(current["status"], "succeeded", current["error"])
        model_id = current["result"]["model"]
        model = self.app.state.store.get(model_id, "A", self.project, "model")[
            "payload"
        ]
        self.assertTrue(model["report"]["reload_equivalence"])
        self.assertEqual(model["report"]["test"]["team"]["count"], 18)
        bad = {
            "model_id": model_id,
            "expected_revision": 0,
            "note": "Initial release fixture",
            "min_accuracy": 0.0,
            "min_test_rows": 100,
        }
        self.assertEqual(
            self.client.post(self.prefix + "/releases", json=bad).status_code, 422
        )
        approved = {
            k: v for k, v in bad.items() if k not in ("min_accuracy", "min_test_rows")
        }
        released = self.client.post(self.prefix + "/releases", json=approved)
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(
            self.client.post(self.prefix + "/releases", json=approved).status_code, 409
        )
        request = {"state": "invoice payment refund requested urgent", "threshold": 0.5}
        result = self.client.post(
            self.prefix + "/decisions",
            json=request,
            headers={"Authorization": "Bearer infer-secret"},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["answers"]["team"]["answer"], "billing")
        self.assertAlmostEqual(
            sum(result.json()["answers"]["urgency"]["probabilities"].values()), 1
        )
        with TestClient(create_app(self.home, keys())) as restarted:
            restarted.headers.update({"Authorization": "Bearer infer-secret"})
            again = restarted.post(self.prefix + "/decisions", json=request)
            self.assertEqual(again.json(), result.json())
        rollback = self.client.post(
            self.prefix + "/releases",
            json={
                **approved,
                "expected_revision": 1,
                "note": "Reactivation preserves version history",
            },
        )
        self.assertEqual(rollback.json()["revision"], 2)
        path = self.home / "artifacts" / model["artifact_id"] / "model.json"
        path.write_text("{}")
        tampered = self.client.post(self.prefix + "/decisions", json=request)
        self.assertEqual(tampered.status_code, 400)
        self.assertIn("integrity", tampered.text.lower())

    def test_job_claim_fencing_idempotency_and_cancel(self):
        dataset = self.dataset(12)
        config = {"datasets": [dataset["id"]]}
        first = self.job("prepare", config)
        self.assertEqual(self.job("prepare", config)["id"], first["id"])
        different_dataset = self.dataset(6)
        different = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [different_dataset["id"]]},
                "request_key": "idempotent-test-1",
            },
        )
        self.assertEqual(different.status_code, 409)
        store = self.app.state.store
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: Store(self.home).claim(), range(2)))
        self.assertEqual(sum(r is not None for r in claimed), 1)
        job = next(r for r in claimed if r)
        self.client.post(self.prefix + "/jobs/" + job["id"] + "/cancel")
        with self.assertRaises(Conflict):
            store.finish(job, result={"should": "not publish"})
        store.acknowledge_cancel(job)
        self.assertEqual(store.job(job["id"], "A", self.project)["status"], "cancelled")
        second = self.job("prepare", config, "expired-claim-test")
        expired = store.claim(lease_seconds=-1)
        self.assertIsNone(store.claim())
        self.assertEqual(
            store.job(second["id"], "A", self.project)["status"], "interrupted"
        )
        with self.assertRaises(Conflict):
            store.add("snapshot", "A", self.project, "owner", {}, job=expired)

    def test_extend_snapshot_preserves_test_and_rejects_seed_leak(self):
        snapshot_id = self.snapshot()
        store = self.app.state.store
        base = store.get(snapshot_id, "A", self.project, "snapshot")["payload"]
        seed = base["splits"]["train"][0]
        candidate = copy.deepcopy(seed)
        candidate.update(
            id="synthetic-1",
            group_id="synthetic-family-1",
            state=seed["state"] + " reviewed paraphrase",
        )
        candidate["metadata"].update(
            origin="synthetic", reviewed=True, seed_ids=[seed["id"]]
        )
        p = {"tenant": "A", "actor": "owner", "role": "admin"}
        dataset = self.app.state.service.save_dataset(
            p, self.project, [candidate], {"fixture": True}
        )
        job = self.job(
            "prepare",
            {"datasets": [dataset["id"]], "base_snapshot": snapshot_id},
            "extend-reservation",
        )
        Worker(Store(self.home)).once()
        complete = store.job(job["id"], "A", self.project)
        self.assertEqual(complete["status"], "succeeded", complete["error"])
        new = store.get(complete["result"]["snapshot"], "A", self.project, "snapshot")[
            "payload"
        ]
        for name in ("validation", "calibration", "test"):
            self.assertEqual(new["splits"][name], base["splits"][name])
        candidate["metadata"]["seed_ids"] = [base["splits"]["test"][0]["id"]]
        leaked = self.app.state.service.save_dataset(
            p, self.project, [candidate], {"fixture": True}
        )
        failed = self.job(
            "prepare",
            {"datasets": [leaked["id"]], "base_snapshot": snapshot_id},
            "deny-heldout-lineage",
        )
        Worker(Store(self.home)).once()
        current = store.job(failed["id"], "A", self.project)
        self.assertEqual(current["status"], "failed")
        self.assertIn("lineage", current["error"])

    def test_generation_worker_uses_only_reserved_training_seeds(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from unittest.mock import patch

        snapshot = self.snapshot()
        base = self.app.state.store.get(snapshot, "A", self.project, "snapshot")[
            "payload"
        ]
        calls = []

        class Teacher(BaseHTTPRequestHandler):
            def do_POST(handler):
                request = json.loads(
                    handler.rfile.read(int(handler.headers["Content-Length"]))
                )
                prompt = json.loads(request["messages"][1]["content"])
                calls.append(prompt)
                row = fixture_rows(1)[0]
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "rows": [
                                                {
                                                    "state": "fictional new invoice refund routine",
                                                    "gold": row["gold"],
                                                }
                                            ]
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ).encode()
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)

            def log_message(handler, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Teacher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            job = self.job(
                "generate",
                {
                    "snapshot": snapshot,
                    "count": 1,
                    "seed_count": 3,
                    "coverage": "rare invoice exceptions",
                },
                "generation-worker",
            )
            with patch.dict(
                "os.environ",
                {
                    "DMT_TEACHER_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                    "DMT_TEACHER_MODEL": "fixture-chat-teacher",
                },
            ):
                Worker(Store(self.home)).once()
            current = self.app.state.store.job(job["id"], "A", self.project)
            self.assertEqual(current["status"], "succeeded", current["error"])
            generated = self.app.state.store.get(
                current["result"]["dataset"], "A", self.project, "dataset"
            )["payload"]["rows"][0]
            self.assertFalse(generated["metadata"]["reviewed"])
            self.assertEqual(
                generated["metadata"]["seed_ids"],
                [r["id"] for r in base["splits"]["train"][:3]],
            )
            self.assertEqual(
                [r["state"] for r in calls[0]["examples"]],
                [r["state"] for r in base["splits"]["train"][:3]],
            )
            self.assertIn("rare invoice exceptions", calls[0]["domain_description"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_unreviewed_failure_unknown_config_and_no_implicit_model(self):
        dataset = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("data.jsonl", jsonl(fixture_rows(6)).encode())},
        ).json()
        job = self.job("prepare", {"datasets": [dataset["id"]]})
        Worker(Store(self.home)).once()
        self.assertEqual(
            self.app.state.store.job(job["id"], "A", self.project)["status"], "failed"
        )
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset["id"]], "api_key": "must-not-persist"},
                "request_key": "unknown-fields-test",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.post(
                self.prefix + "/decisions", json={"state": "ticket", "threshold": 0.8}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                self.prefix + "/decisions",
                content='{"state":"ticket","threshold":1e100}',
                headers={"Content-Type": "application/json"},
            ).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
