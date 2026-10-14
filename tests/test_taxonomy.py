"""Actual proposal/API/worker/schema and recovery contracts; fictional teacher responses."""

import copy, hashlib, json, os, tempfile, threading, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import httpx
import socket
import sys
import time
import uvicorn
from streamlit.testing.v1 import AppTest
import test_platform as fixtures
from decision_workbench.platform.api import create_app
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.store import Store, digest
from decision_workbench.platform import taxonomy, taxonomy_provider
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.hosted_transport import (
    endpoint,
    receive,
    TransportFailure,
    run_process,
)


class TaxonomyTests(unittest.TestCase):
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
        self.service = self.app.state.service
        self.store = self.app.state.store
        self.project = self.service.create_project(
            self.owner,
            "Fictional category study",
            "Inspect fixture categories only",
            fixtures.QUESTIONS,
        )["id"]
        self.prefix = "/v1/projects/" + self.project
        self.rows = fixtures.fixture_rows(180)
        self.dataset = self.service.save_dataset(
            self.owner, self.project, self.rows, {"fixture": "fictional source"}
        )["id"]
        self.profile = {
            "id": "fixture",
            "provider": "typellm",
            "model": "typellm-latest",
            "response_model": "typellm-latest",
            "key_env": "DMT_TAXONOMY_FIXTURE_API_KEY",
            "tenants": ["A"],
            "projects": [self.project],
            "max_calls_day": 20,
            "max_inflight": 1,
            "timeout_seconds": 5,
            "max_request_bytes": 64000,
            "max_rows": 20,
        }
        self.profile_file = self.home / "profile.json"
        self.profile_file.write_text(
            json.dumps(
                {"format": "domain-taxonomy-profiles-v1", "profiles": [self.profile]}
            )
        )
        self.env = patch.dict(
            os.environ,
            {
                "DMT_TAXONOMY_PROFILES_FILE": str(self.profile_file),
                "DMT_TAXONOMY_FIXTURE_API_KEY": "fixture-only-key",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        self.service.runtime.close()
        self.temp.cleanup()

    def proposal(self, names=("Meals", "meal expense"), ids=None):
        ids = ids or ["case-0", "case-1"]
        r = self.client.post(
            self.prefix + "/taxonomy-proposals",
            json={
                "dataset": self.dataset,
                "record_ids": ids,
                "question_id": "team",
                "candidates": [
                    {"name": name, "example_ids": [ids[i % len(ids)]]}
                    for i, name in enumerate(names)
                ],
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def page(self, p):
        r = self.client.get(self.prefix + "/taxonomy-proposals/" + p)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def review(self, p, c, decision="add", canonical="meals", revision=0, token=None):
        r = self.client.post(
            self.prefix + "/taxonomy-proposals/" + p + "/reviews",
            headers={"Authorization": "Bearer " + token} if token else None,
            json={
                "candidate_id": c["id"],
                "decision": decision,
                "canonical_id": canonical,
                "description": (
                    "Fixture category definition" if decision == "add" else ""
                ),
                "expected_revision": revision,
                "expected_hash": self.page(p)["proposal"]["sha256"],
                "note": "Inspect fictional source cases",
            },
        )
        return r

    def confirm(self, p, state=None):
        return self.client.post(
            self.prefix + "/taxonomy-proposals/" + p + "/confirm",
            json={
                "name": "Confirmed successor",
                "note": "Owner confirms fixture policy only",
                "expected_state_sha256": state or self.page(p)["state_sha256"],
            },
        )

    def approve(self, p):
        candidates = self.page(p)["candidates"]
        self.assertEqual(self.review(p, candidates[0]).status_code, 201)
        for c in candidates[1:]:
            self.assertEqual(self.review(p, c, "alias", "meals").status_code, 201)

    def worker(self):
        w = Worker(self.store)
        try:
            return w.once()
        finally:
            w.service.runtime.close()

    def submit(self, kind, config, key):
        r = self.client.post(
            self.prefix + "/jobs",
            json={"kind": kind, "config": config, "request_key": "taxonomy-" + key},
        )
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()["id"]

    def remote_job(self, key="discovery"):
        return self.submit(
            "taxonomy-discover",
            {
                "dataset": self.dataset,
                "question_id": "team",
                "record_ids": ["case-0", "case-1"],
                "profile_id": "fixture",
                "data_approved": True,
                "cost_approved": True,
            },
            key,
        )

    def response(self, profile, body, key, **kwargs):
        req = json.loads(body)
        self.assertEqual(profile["provider"], "typellm")
        self.assertEqual(key, "fixture-only-key")
        self.assertNotIn("gold", json.loads(req["context"])["records"][0])
        values = {}
        for name, q in req["questions"].items():
            values[name] = "__propose__" if name.endswith("_category") else "meals"
        return json.dumps(
            {
                "id": "gen_fixture",
                "model": "typellm-latest",
                "result": values,
                "usage": {"input_tokens": 200, "thinking_tokens": 0},
                "elapsed": 0.1,
            }
        ).encode()

    def test_manual_aliases_atomic_successor_and_parent_unchanged(self):
        parent = copy.deepcopy(
            self.service.project(self.owner, self.project)["payload"]
        )
        p = self.proposal()
        self.approve(p)
        r = self.confirm(p)
        self.assertEqual(r.status_code, 201, r.text)
        child = r.json()["successor"]
        self.assertEqual(
            child["payload"]["questions"]["team"]["criteria"]["meals"],
            "Fixture category definition",
        )
        self.assertEqual(
            child["payload"]["questions"]["refund"], parent["questions"]["refund"]
        )
        self.assertEqual(child["payload"]["policy_version"], 2)
        self.assertFalse(r.json()["labels_transferred"])
        self.assertEqual(
            self.service.project(self.owner, self.project)["payload"], parent
        )
        self.assertEqual(self.store.list("A", child["id"], "dataset"), [])
        self.assertEqual(self.store.list("A", child["id"], "model"), [])
        taxonomy.validate_history(self.store)
        self.assertEqual(self.confirm(p).status_code, 409)
        self.assertEqual(
            self.review(p, self.page(p)["candidates"][0], revision=1).status_code, 409
        )

    def test_pending_stale_review_race_and_owner_confirmation_race(self):
        p = self.proposal(names=("meals",))
        page = self.page(p)
        self.assertEqual(self.confirm(p).status_code, 400)
        c = page["candidates"][0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            rs = list(pool.map(lambda _: self.review(p, c), range(2)))
        self.assertEqual(sorted(r.status_code for r in rs), [201, 409])
        self.assertEqual(self.confirm(p, page["state_sha256"]).status_code, 409)
        state = self.page(p)["state_sha256"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            rs = list(pool.map(lambda _: self.confirm(p, state), range(2)))
        self.assertEqual(sorted(r.status_code for r in rs), [201, 409])
        self.assertEqual(len(self.store.list("A", self.project, "taxonomy-commit")), 1)

    def test_roles_scopes_invalid_names_and_alias_collision(self):
        p = self.proposal()
        c = self.page(p)["candidates"][0]
        for token, code in [
            ("edit-secret", 403),
            ("infer-secret", 403),
            ("b-secret", 404),
        ]:
            self.assertEqual(self.review(p, c, token=token).status_code, code)
        self.approve(p)
        for token in ("review-secret", "edit-secret", "infer-secret"):
            r = self.client.post(
                self.prefix + "/taxonomy-proposals/" + p + "/confirm",
                headers={"Authorization": "Bearer " + token},
                json={
                    "name": "Forbidden",
                    "note": "Role check",
                    "expected_state_sha256": self.page(p)["state_sha256"],
                },
            )
            self.assertEqual(r.status_code, 403)
        for name in ("", "bad\x00name", "a" * 81):
            with self.assertRaises(ValueError):
                taxonomy.name_key(name)
        p2 = self.proposal(names=("billing",))
        self.assertEqual(
            self.review(
                p2, self.page(p2)["candidates"][0], canonical="billing"
            ).status_code,
            201,
        )
        self.assertEqual(self.confirm(p2).status_code, 400)

    def test_source_versions_rejected_synthetic_and_reserved_holdouts(self):
        p = self.proposal(names=("Meals", "meals"))
        self.assertEqual(len(self.page(p)["candidates"]), 1)
        for kind in ("rejected", "synthetic"):
            rows = copy.deepcopy(self.rows)
            (
                rows[0]["metadata"].update(review_status="rejected", reviewed=False)
                if kind == "rejected"
                else rows[0]["metadata"].update(origin="synthetic")
            )
            d = self.service.save_dataset(
                self.owner, self.project, rows, {"fixture": True}
            )["id"]
            with self.assertRaises(ValueError):
                taxonomy.selection(self.store, "A", self.project, d, ["case-0"], "team")
        job = self.submit("prepare", {"datasets": [self.dataset]}, "prepare")
        self.worker()
        snap = self.store.job(job, "A", self.project)
        self.assertEqual(snap["status"], "succeeded", snap["error"])
        s = self.store.get(snap["result"]["snapshot"], "A", self.project, "snapshot")[
            "payload"
        ]
        for i in ("case-0", "case-1"):
            self.assertIn(i, {r["id"] for r in s["splits"]["train"]})
        held = s["splits"]["test"][0]
        with self.assertRaisesRegex(ValueError, "Reserved holdouts"):
            taxonomy.selection(
                self.store, "A", self.project, self.dataset, [held["id"]], "team"
            )
        taxonomy.checked_proposal(self.store, "A", self.project, p)

    def test_discovery_worker_accounting_schema_only_and_recovery(self):
        job = self.remote_job()
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process", self.response
        ):
            self.worker()
        result = self.store.job(job, "A", self.project)
        self.assertEqual(result["status"], "succeeded", result["error"])
        p = result["result"]["proposal_id"]
        self.approve(p)
        self.assertEqual(self.confirm(p).status_code, 201)
        taxonomy_provider.validate_history(self.store)
        taxonomy.validate_history(self.store)
        backup = backup_workspace(self.store.folder, self.home / "backup")
        restore_workspace(
            backup["destination"], self.home / "restored", backup["manifest_sha256"]
        )
        restored = Store(self.home / "restored")
        taxonomy.validate_history(restored)
        taxonomy_provider.validate_history(restored)
        self.assertEqual(
            restored.get(p, "A", self.project, "taxonomy-proposal")["payload"],
            self.store.get(p, "A", self.project, "taxonomy-proposal")["payload"],
        )

    def test_unknown_result_capacity_and_owner_settlement(self):
        job = self.remote_job()
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process",
            side_effect=TransportFailure(),
        ):
            self.worker()
        self.assertEqual(self.store.job(job, "A", self.project)["status"], "failed")
        calls = self.store.list("A", self.project, "taxonomy-discovery")
        self.assertEqual(len(calls), 1)
        call = calls[0]["id"]
        next_job = self.remote_job("second")
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process"
        ) as transport:
            self.worker()
            transport.assert_not_called()
        self.assertEqual(
            self.store.job(next_job, "A", self.project)["status"], "failed"
        )
        self.assertEqual(self.store.list("A", self.project, "taxonomy-proposal"), [])
        outcome = self.client.get(self.prefix + "/taxonomy-discoveries/" + call).json()
        body = {
            "expected_outcome_sha256": outcome["outcome_sha256"],
            "local_transport_stopped": True,
            "provider_finished": True,
            "reference": "fixture external settlement",
            "note": "Fictional fixture only",
        }
        r = self.client.post(
            self.prefix + "/taxonomy-discoveries/" + call + "/settle", json=body
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(
            self.client.post(
                self.prefix + "/taxonomy-discoveries/" + call + "/settle", json=body
            ).status_code,
            409,
        )
        next_job = self.remote_job("third")
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process", self.response
        ):
            self.worker()
        self.assertEqual(
            self.store.job(next_job, "A", self.project)["status"], "succeeded"
        )
        taxonomy_provider.validate_history(self.store)

    def test_contract_model_conditional_and_request_limits(self):
        p = taxonomy_provider.public_profile(self.profile)
        spec = self.service.project(self.owner, self.project)["payload"]
        body = taxonomy_provider.body_for(spec, "team", self.rows[:2], p)
        raw = json.loads(self.response(p, body, "fixture-only-key"))
        for mutate in (
            lambda r: r.update(model="changed"),
            lambda r: r["result"].pop("record_0_proposal"),
            lambda r: r["result"].update(record_0_category="unknown"),
            lambda r: r["usage"].update(thinking_tokens=1),
            lambda r: r.update(skipped=["record_0_proposal"]),
        ):
            changed = copy.deepcopy(raw)
            mutate(changed)
            with self.assertRaises(ValueError):
                taxonomy_provider.decode_response(
                    json.dumps(changed).encode(), spec, "team", self.rows[:2], p
                )
        with self.assertRaises(ValueError):
            taxonomy_provider.body_for(
                spec, "team", self.rows[:2], {**p, "max_request_bytes": 1024}
            )
        self.assertEqual(endpoint(p), "https://api.typellm.ai/v1/generate")
        observed = []

        def handle(req):
            observed.append((str(req.url), json.loads(req.content)))
            return httpx.Response(
                200, stream=httpx.ByteStream(json.dumps(raw).encode())
            )

        received = receive(p, body, "fixture-only-key", httpx.MockTransport(handle))
        self.assertEqual(json.loads(received), raw)
        self.assertEqual(observed[0][1]["model"], "typellm-latest")

    def test_approval_profile_and_source_drift_refused(self):
        cfg = {
            "dataset": self.dataset,
            "question_id": "team",
            "record_ids": ["case-0"],
            "profile_id": "fixture",
            "data_approved": False,
            "cost_approved": True,
        }
        r = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "taxonomy-discover",
                "config": cfg,
                "request_key": "taxonomy-no-approval",
            },
        )
        self.assertEqual(r.status_code, 400)
        job = self.remote_job()
        self.profile["model"] = "changed"
        self.profile_file.write_text(
            json.dumps(
                {"format": "domain-taxonomy-profiles-v1", "profiles": [self.profile]}
            )
        )
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process"
        ) as transport:
            self.worker()
            transport.assert_not_called()
        self.assertEqual(self.store.job(job, "A", self.project)["status"], "failed")
        self.assertEqual(self.store.list("A", self.project, "taxonomy-discovery"), [])

    def test_pinned_review_tamper_prevents_backup(self):
        p = self.proposal()
        self.approve(p)
        r = self.confirm(p)
        self.assertEqual(r.status_code, 201, r.text)
        review = r.json()["confirmation"]["payload"]["review_ids"][0]
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT payload FROM entities WHERE id=?", (review,)
            ).fetchone()
            value = json.loads(row["payload"])
            value["note"] = "Changed private history"
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?", (json.dumps(value), review)
            )
        with self.assertRaises(ValueError):
            backup_workspace(self.store.folder, self.home / "corrupt-backup")

    def test_successor_lineage_reserves_used_examples_and_parent_release(self):
        prepare = self.submit(
            "prepare", {"datasets": [self.dataset]}, "parent-reservation"
        )
        self.worker()
        snapshot_id = self.store.job(prepare, "A", self.project)["result"]["snapshot"]
        snapshot = self.store.get(snapshot_id, "A", self.project, "snapshot")["payload"]
        self.service.configure_release_policy(
            self.owner, self.project, 0.0, 0.0, 1.0, 10, None
        )
        train = self.submit(
            "train",
            {"snapshot": snapshot_id, "backend": "cpu-baseline"},
            "parent-model",
        )
        self.worker()
        job = self.store.job(train, "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        self.service.activate(
            self.owner,
            self.project,
            job["result"]["model"],
            0,
            "Fictional baseline release",
        )
        release = self.store.release_policy("A", self.project)
        before = self.client.post(
            self.prefix + "/decisions",
            json={"state": "invoice refund requested urgent", "threshold": 0.5},
        ).json()
        ids = [r["id"] for r in snapshot["splits"]["train"][:2]]
        p = self.proposal(ids=ids)
        self.approve(p)
        result = self.confirm(p)
        self.assertEqual(result.status_code, 201, result.text)
        child = result.json()["successor"]["id"]
        self.assertEqual(self.store.release_policy("A", self.project), release)
        after = self.client.post(
            self.prefix + "/decisions",
            json={"state": "invoice refund requested urgent", "threshold": 0.5},
        ).json()
        self.assertEqual(before, after)
        rows = copy.deepcopy(self.rows)
        questions = result.json()["successor"]["payload"]["questions"]
        for row in rows:
            row["questions"] = copy.deepcopy(questions)
            row["gold"]["team"]["probabilities"]["meals"] = 0.0
        dataset = self.service.save_dataset(
            self.owner, child, rows, {"fixture": "explicit new schema annotations"}
        )["id"]
        queued = self.service.submit(
            self.owner,
            child,
            "prepare",
            {"datasets": [dataset]},
            "taxonomy-successor-reserve",
        )
        self.worker()
        job = self.store.job(queued["id"], "A", child)
        self.assertEqual(job["status"], "succeeded", job["error"])
        new = self.store.get(job["result"]["snapshot"], "A", child, "snapshot")[
            "payload"
        ]
        self.assertTrue(set(ids) <= {r["id"] for r in new["splits"]["train"]})
        taxonomy.verify_snapshot(self.store, "A", child, new)

    def test_actual_isolated_transport_success_and_cancellation(self):
        program = """import os,time,json,httpx
from decision_workbench.platform.hosted_transport import child_main
assert "DMT_TAXONOMY_FIXTURE_API_KEY" not in os.environ
assert "DMT_PROVIDER_TYPESAFE_API_KEY" not in os.environ
def response(req):
    assert str(req.url)=="https://api.typellm.ai/v1/generate"
    assert req.headers["authorization"]=="Bearer fixture-only-key"
    body=json.loads(req.content)
    if "STALL" in body["context"]: time.sleep(30)
    values={k:("__propose__" if k.endswith("_category") else "meals") for k in body["questions"]}
    data={"id":"gen_fixture","model":"typellm-latest","result":values,"usage":{"input_tokens":200,"thinking_tokens":0},"elapsed":0.1}
    return httpx.Response(200,stream=httpx.ByteStream(json.dumps(data).encode()))
child_main(httpx.MockTransport(response))
"""
        children = []

        def isolated(profile, body, key, **kwargs):
            observe = kwargs.pop("observe")

            def watch(process):
                children.append(process)
                observe(process)

            return run_process(
                profile,
                body,
                key,
                command=[sys.executable, "-c", program],
                observe=watch,
                **kwargs,
            )

        job = self.remote_job("isolated")
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process", isolated
        ):
            self.worker()
        state = self.store.job(job, "A", self.project)
        self.assertEqual(state["status"], "succeeded", state["error"])
        self.assertIsNotNone(children[-1].poll())
        self.assertEqual(
            len(self.store.list("A", self.project, "taxonomy-transport")), 1
        )
        rows = copy.deepcopy(self.rows)
        rows[0]["state"] = "STALL fixture cancellation"
        self.dataset = self.service.save_dataset(
            self.owner, self.project, rows, {"fixture": "stall"}
        )["id"]
        job = self.remote_job("cancelled")
        with patch(
            "decision_workbench.platform.taxonomy_provider.run_process", isolated
        ):
            thread = threading.Thread(target=self.worker)
            thread.start()
            deadline = time.monotonic() + 10
            while len(children) < 2:
                if time.monotonic() > deadline:
                    self.fail("Child did not start")
                time.sleep(0.02)
            r = self.client.post(self.prefix + "/jobs/" + job + "/cancel")
            self.assertEqual(r.status_code, 200, r.text)
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.job(job, "A", self.project)["status"], "cancelled")
        self.assertIsNotNone(children[-1].poll())
        self.assertEqual(
            len(self.store.list("A", self.project, "taxonomy-proposal")), 1
        )
        taxonomy_provider.validate_history(self.store)

    def test_browser_manual_review_and_owner_confirmation_over_http(self):
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

            widget("checkbox", "Open taxonomy workspace").check().run(timeout=30)
            self.assertFalse(at.exception)
            widget("multiselect", "Taxonomy source examples on this page").select(
                "case-0"
            ).run(timeout=30)
            widget("selectbox", "Taxonomy question").select("team").run(timeout=30)
            widget("text_input", "Proposed category name").set_value("Meals").run(
                timeout=30
            )
            widget("button", "Save manual taxonomy proposal").click().run(timeout=30)
            self.assertFalse(at.exception)
            widget("text_input", "Canonical category ID or alias target").set_value(
                "meals"
            )
            widget("text_area", "New category definition").set_value(
                "Fictional meals only"
            )
            widget("text_input", "Taxonomy review reason").set_value(
                "Reviewed fictional case"
            ).run(timeout=30)
            widget("button", "Save taxonomy review").click().run(timeout=30)
            self.assertFalse(at.exception)
            widget("text_input", "Successor project name").set_value(
                "Reviewed meals successor"
            )
            widget("text_input", "Owner schema confirmation reason").set_value(
                "Fixture acceptance only"
            )
            widget(
                "checkbox", "I confirm the reviewed taxonomy as a new project schema"
            ).check().run(timeout=30)
            widget("button", "Confirm taxonomy and create successor").click().run(
                timeout=30
            )
            self.assertFalse(at.exception)
            commits = self.store.list("A", self.project, "taxonomy-commit")
            self.assertEqual(len(commits), 1)
            child = commits[0]["payload"]["successor_project"]
            self.assertEqual(at.session_state["active_project"], child)
            self.assertEqual(
                self.store.get(child, "A", child, "project")["payload"]["questions"][
                    "team"
                ]["criteria"]["meals"],
                "Fictional meals only",
            )
            taxonomy.validate_history(self.store)
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
