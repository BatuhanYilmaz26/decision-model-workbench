"""Approved synthetic requests through real local HTTP, workers and recovery."""

import copy
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import test_platform as fixtures
from test_schema_intake import Teacher
from decision_workbench.platform.generation_plans import record_id
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.store import (
    WORKSPACE_SCHEMA_VERSION,
    Conflict,
    Store,
    digest,
    encode,
)
from decision_workbench.platform.worker import Worker
from decision_workbench.training.resources import ResourceBusy, ResourceLease

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class GenerationLifecycleTests(unittest.TestCase):
    dataset = fixtures.PlatformTests.dataset
    job = fixtures.PlatformTests.job
    snapshot = fixtures.PlatformTests.snapshot

    def setUp(self):
        fixtures.PlatformTests.setUp(self)
        self.teacher = Teacher()
        self.teacher.result = {
            "rows": [
                {
                    "state": "Fictional generated case",
                    "gold": fixtures.fixture_rows(1)[0]["gold"],
                }
            ]
        }
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": self.teacher.url,
                "DMT_TEACHER_MODEL": "fictional-teacher",
                "DMT_TEACHER_API_KEY": "",
                "DMT_GENERATION_TIMEOUT_SECONDS": "10",
                "DMT_GENERATION_CALLS_PER_DAY": "20",
                "DMT_GENERATION_MAX_INFLIGHT": "1",
                "DMT_GENERATION_OUTPUT_TOKENS": "6000",
                "DMT_GENERATION_MAX_ATTEMPTS": "3",
                "DMT_GENERATION_TOKEN_PARAMETER": "max_tokens",
                "DMT_GENERATION_TEMPERATURE": "0.7",
                "DMT_GENERATION_INPUT_USD_PER_MILLION": "",
                "DMT_GENERATION_OUTPUT_USD_PER_MILLION": "",
                "DMT_GENERATION_MAX_ESTIMATED_REQUEST_USD": "",
                "DMT_GENERATION_MAX_ESTIMATED_DAY_USD": "",
            },
        )
        self.environment.start()
        self.snapshot_id = self.snapshot()
        self.selection = {
            "snapshot": self.snapshot_id,
            "count": 1,
            "seed_count": 3,
            "coverage": "Rare policy boundaries",
        }
        self.store = self.app.state.store

    def tearDown(self):
        self.teacher.close()
        self.app.state.service.runtime.close()
        self.environment.stop()
        fixtures.PlatformTests.tearDown(self)

    def preview(self, **changes):
        response = self.client.post(
            self.prefix + "/generation-preview", json={**self.selection, **changes}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, key="generation-attempt-one", **changes):
        selection = {**self.selection, **changes}
        preview = self.preview(**changes)
        config = {
            **selection,
            "data_approved": True,
            "cost_approved": True,
            "expected_preview_sha256": preview["sha256"],
        }
        response = self.client.post(
            self.prefix + "/jobs",
            json={"kind": "generate", "config": config, "request_key": key},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json(), config

    def run_worker(self):
        worker = Worker(Store(self.home))
        try:
            self.assertTrue(worker.once())
        finally:
            worker.service.runtime.close()

    def detail(self, job):
        response = self.client.get(
            self.prefix + "/generation-plans/" + job["config"]["generation_plan"]["id"]
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_approved_request_archives_exact_training_seeds_and_pending_result(self):
        preview = self.preview()
        job, config = self.submit()
        self.assertEqual(set(job["config"]), {"snapshot", "generation_plan"})
        self.run_worker()
        detail = self.detail(job)
        self.assertEqual(detail["job"]["status"], "succeeded", detail["job"]["error"])
        self.assertEqual(detail["state"], "response_received")
        self.assertIsNone(detail["response"]["usage"])
        self.assertIsNone(detail["response"]["reported_cost_usd"])
        self.assertNotIn("raw_base64", detail["response"])
        self.assertEqual(len(self.teacher.calls), 1)
        path, headers, body = self.teacher.calls[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(body, json.loads(preview["request_body"]))
        snapshot = self.store.get(self.snapshot_id, "A", self.project, "snapshot")[
            "payload"
        ]
        prompt = json.loads(body["messages"][1]["content"])
        self.assertEqual(
            prompt["examples"],
            [
                {"state": row["state"], "gold": row["gold"]}
                for row in snapshot["splits"]["train"][:3]
            ],
        )
        candidates = list(
            self.store.verify_dataset(detail["dataset"]["id"], "A", self.project)
        )
        self.assertFalse(candidates[0]["metadata"]["reviewed"])
        self.assertEqual(candidates[0]["metadata"]["generation_plan_id"], detail["id"])
        # Reopening an approval never generates another call, even after disabling the teacher.
        with patch.dict(
            os.environ, {"DMT_TEACHER_BASE_URL": "", "DMT_TEACHER_MODEL": ""}
        ):
            replay = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": config,
                    "request_key": job["request_key"],
                },
            )
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(replay.json()["id"], job["id"])
            self.assertEqual(self.detail(job)["dataset"], detail["dataset"])
        self.assertFalse(Worker(Store(self.home)).once())
        self.assertEqual(len(self.teacher.calls), 1)

    def test_server_approvals_roles_scope_stale_preview_and_paging(self):
        preview = self.preview()
        config = {
            **self.selection,
            "data_approved": True,
            "cost_approved": True,
            "expected_preview_sha256": preview["sha256"],
        }
        for missing in ("data_approved", "cost_approved"):
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": {**config, missing: False},
                    "request_key": "missing-approval-" + missing,
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
        for role, code in (
            ("review-secret", 403),
            ("infer-secret", 403),
            ("b-secret", 404),
        ):
            response = self.client.post(
                self.prefix + "/generation-preview",
                json=self.selection,
                headers={"Authorization": "Bearer " + role},
            )
            self.assertEqual(response.status_code, code, response.text)
        with patch.dict(os.environ, {"DMT_GENERATION_OUTPUT_TOKENS": "7000"}):
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": config,
                    "request_key": "retired-preview",
                },
            )
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            self.client.get(self.prefix + "/generation-plans?limit=21").status_code, 422
        )
        self.assertEqual(self.teacher.calls, [])

    def test_parallel_same_approval_has_one_plan_and_job(self):
        preview = self.preview()
        config = {
            **self.selection,
            "data_approved": True,
            "cost_approved": True,
            "expected_preview_sha256": preview["sha256"],
        }
        service = self.app.state.service

        def submit(_):
            return service.submit(
                OWNER, self.project, "generate", config, "parallel-generation"
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(4)))
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(
            len(self.client.get(self.prefix + "/generation-plans").json()), 1
        )
        self.run_worker()
        self.assertEqual(len(self.teacher.calls), 1)

    def test_cancelled_queued_attempt_is_unsent_and_explicit_retry_is_linked(self):
        job, _ = self.submit()
        self.store.cancel(job["id"], "A", self.project, "owner")
        original = self.detail(job)
        self.assertEqual(original["state"], "not_dispatched")
        retry, _ = self.submit("generation-attempt-two", retry_of=original["id"])
        self.assertEqual(self.detail(retry)["attempt"], 2)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "generate",
                "config": {
                    **self.selection,
                    "retry_of": original["id"],
                    "data_approved": True,
                    "cost_approved": True,
                    "expected_preview_sha256": self.preview(retry_of=original["id"])[
                        "sha256"
                    ],
                },
                "request_key": "competing-retry",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.run_worker()
        self.assertEqual(self.detail(retry)["job"]["status"], "succeeded")
        self.assertEqual(len(self.teacher.calls), 1)

    def test_competing_retry_keys_cannot_fork_one_approved_attempt(self):
        with patch.dict(os.environ, {"DMT_GENERATION_MAX_INFLIGHT": "4"}):
            job, _ = self.submit()
            self.store.cancel(job["id"], "A", self.project, "owner")
            identifier = self.detail(job)["id"]
            preview = self.preview(retry_of=identifier)
            config = {
                **self.selection,
                "retry_of": identifier,
                "data_approved": True,
                "cost_approved": True,
                "expected_preview_sha256": preview["sha256"],
            }

            def submit(index):
                try:
                    return self.app.state.service.submit(
                        OWNER,
                        self.project,
                        "generate",
                        config,
                        "competing-retry-" + str(index),
                    )["id"]
                except Conflict:
                    return None

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(submit, range(4)))
            self.assertEqual(len([value for value in results if value]), 1)
            self.assertEqual(
                len(self.client.get(self.prefix + "/generation-plans").json()), 2
            )
        self.assertEqual(self.teacher.calls, [])

    def test_submission_replay_rechecks_authority_inside_transaction(self):
        job, _ = self.submit()
        calls = []

        def authorize():
            calls.append(True)
            if len(calls) == 2:
                raise PermissionError("Fictional concurrent owner revocation")

        with self.assertRaises(PermissionError):
            self.store.submit(
                "A",
                self.project,
                "owner",
                "generate",
                job["config"],
                job["request_key"],
                authorize=authorize,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            len(self.client.get(self.prefix + "/generation-plans").json()), 1
        )

    def test_request_planning_cap_refuses_before_plan_or_dispatch(self):
        with patch.dict(
            os.environ,
            {
                "DMT_GENERATION_INPUT_USD_PER_MILLION": "1",
                "DMT_GENERATION_OUTPUT_USD_PER_MILLION": "2",
                "DMT_GENERATION_MAX_ESTIMATED_REQUEST_USD": "0.00000001",
            },
        ):
            preview = self.preview()
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": {
                        **self.selection,
                        "data_approved": True,
                        "cost_approved": True,
                        "expected_preview_sha256": preview["sha256"],
                    },
                    "request_key": "over-request-estimate",
                },
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("per-request", response.text)
        self.assertEqual(self.client.get(self.prefix + "/generation-plans").json(), [])
        self.assertEqual(self.teacher.calls, [])

    def test_invalid_complete_response_is_archived_and_retry_needs_new_approval(self):
        self.teacher.finish_reason = "length"
        job, _ = self.submit()
        self.run_worker()
        detail = self.detail(job)
        self.assertEqual(detail["job"]["status"], "failed")
        self.assertEqual(detail["state"], "response_received")
        self.assertIsNotNone(detail["response"])
        self.assertIsNone(detail["dataset"])
        self.assertEqual(len(self.teacher.calls), 1)
        self.teacher.finish_reason = "stop"
        retry, _ = self.submit("reviewed-manual-retry", retry_of=detail["id"])
        self.run_worker()
        self.assertEqual(self.detail(retry)["attempt"], 2)
        self.assertEqual(self.detail(retry)["job"]["status"], "succeeded")
        self.assertEqual(len(self.teacher.calls), 2)

    def test_estimated_budget_unknown_history_and_call_limit_are_not_zero_cost(self):
        job, _ = self.submit()
        self.store.cancel(job["id"], "A", self.project, "owner")
        with patch.dict(
            os.environ,
            {
                "DMT_GENERATION_INPUT_USD_PER_MILLION": "1",
                "DMT_GENERATION_OUTPUT_USD_PER_MILLION": "2",
                "DMT_GENERATION_MAX_ESTIMATED_DAY_USD": "100",
            },
        ):
            preview = self.preview()
            self.assertIsNotNone(preview["estimate"]["usd"])
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": {
                        **self.selection,
                        "data_approved": True,
                        "cost_approved": True,
                        "expected_preview_sha256": preview["sha256"],
                    },
                    "request_key": "priced-after-unknown",
                },
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("unpriced", response.text)
        with patch.dict(os.environ, {"DMT_GENERATION_CALLS_PER_DAY": "1"}):
            preview = self.preview()
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": {
                        **self.selection,
                        "data_approved": True,
                        "cost_approved": True,
                        "expected_preview_sha256": preview["sha256"],
                    },
                    "request_key": "daily-limit-refusal",
                },
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("daily", response.text)
        self.assertEqual(self.teacher.calls, [])

    def test_drift_before_dispatch_refuses_without_sending_approved_data(self):
        job, _ = self.submit()
        with patch.dict(os.environ, {"DMT_TEACHER_MODEL": "changed-alias"}):
            self.run_worker()
        detail = self.detail(job)
        self.assertEqual(detail["job"]["status"], "failed")
        self.assertEqual(detail["state"], "not_dispatched")
        self.assertEqual(self.teacher.calls, [])

    def test_process_receipt_failure_prevents_private_body_delivery(self):
        job, _ = self.submit()
        original = Store.add

        def add(store, kind, *args, **kwargs):
            if kind == "generation-process":
                raise ValueError("Fictional receipt persistence failure")
            return original(store, kind, *args, **kwargs)

        with patch.object(Store, "add", add):
            self.run_worker()
        detail = self.detail(job)
        self.assertEqual(detail["job"]["status"], "failed")
        self.assertEqual(detail["state"], "not_dispatched")
        self.assertEqual(self.teacher.calls, [])

    def test_response_survives_atomic_candidate_publication_failure(self):
        job, _ = self.submit()
        original = Store.audit

        def audit(store, db, tenant, project, actor, action, target):
            if action == "publish.generation-result":
                raise ValueError("Fictional atomic candidate failure")
            return original(db, tenant, project, actor, action, target)

        with patch.object(Store, "audit", audit):
            self.run_worker()
        detail = self.detail(job)
        self.assertEqual(detail["job"]["status"], "failed")
        self.assertEqual(detail["state"], "response_received")
        self.assertIsNone(detail["dataset"])
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM entities WHERE kind='generation-result'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM entities WHERE kind='dataset' AND json_extract(payload,'$.provenance.kind')='generated-v2'"
                ).fetchone()[0],
                0,
            )

    def test_deadline_keeps_unknown_capacity_until_explicit_owner_attestation(self):
        self.teacher.delay = True
        with patch.dict(os.environ, {"DMT_GENERATION_TIMEOUT_SECONDS": "5"}):
            job, _ = self.submit()
            started = time.monotonic()
            self.run_worker()
            self.assertLess(time.monotonic() - started, 12)
            detail = self.detail(job)
            self.assertEqual(detail["job"]["status"], "failed")
            self.assertEqual(detail["state"], "unknown")
            self.assertEqual(len(self.teacher.calls), 1)
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE entities SET created=? WHERE id=?",
                    (time.time() - 90000, detail["id"]),
                )
            preview = self.preview()
            response = self.client.post(
                self.prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": {
                        **self.selection,
                        "data_approved": True,
                        "cost_approved": True,
                        "expected_preview_sha256": preview["sha256"],
                    },
                    "request_key": "unknown-capacity",
                },
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("unresolved", response.text)
            self.teacher.release.set()
            settle = {
                "expected_status_sha256": detail["status_sha256"],
                "local_exit_confirmed": True,
                "upstream_complete_confirmed": True,
                "evidence_ref": "Fictional localhost endpoint and transport checked",
                "note": "Test endpoint finished; no claim about provider billing",
            }
            path = self.prefix + "/generation-plans/" + detail["id"] + "/settle"
            denied = self.client.post(
                path, json=settle, headers={"Authorization": "Bearer review-secret"}
            )
            self.assertEqual(denied.status_code, 403)
            stale = self.client.post(
                path, json={**settle, "expected_status_sha256": "0" * 64}
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            from decision_workbench.platform.generation import settle as confirm_outcome

            checks = []

            def authorize():
                checks.append(True)
                if len(checks) >= 3:
                    raise PermissionError("Fictional settlement authority revoked")

            with self.assertRaises(PermissionError):
                confirm_outcome(
                    self.app.state.service,
                    OWNER,
                    self.project,
                    detail["id"],
                    **settle,
                    authorize=authorize,
                )
            self.assertIsNone(self.detail(job)["settlement"])
            confirmed = self.client.post(path, json=settle)
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(confirmed.json()["state"], "owner_attested_complete")
            self.assertEqual(self.client.post(path, json=settle).status_code, 200)
            self.assertEqual(
                self.client.post(
                    path, json={**settle, "note": "Changed attestation"}
                ).status_code,
                409,
            )
            retry, _ = self.submit("confirmed-outcome-retry", retry_of=detail["id"])
            self.assertEqual(self.detail(retry)["attempt"], 2)
        self.assertEqual(len(self.teacher.calls), 1)

    def test_running_cancellation_keeps_history_without_candidates(self):
        self.teacher.delay = True
        job, _ = self.submit()
        errors = []

        def work():
            try:
                self.run_worker()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(self.teacher.started.wait(8))
            self.store.cancel(job["id"], "A", self.project, "owner")
            thread.join(8)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            detail = self.detail(job)
            self.assertEqual(detail["job"]["status"], "cancelled")
            self.assertEqual(detail["state"], "unknown")
            self.assertIsNone(detail["dataset"])
        finally:
            self.teacher.release.set()
            thread.join(12)

    def test_history_restore_ignores_current_prompt_and_refuses_orphan_records(self):
        job, _ = self.submit()
        self.run_worker()
        detail = self.detail(job)
        with patch(
            "decision_workbench.platform.generation_worker.request_body",
            side_effect=AssertionError("Do not regenerate history"),
        ):
            self.assertEqual(self.detail(job)["request_body"], detail["request_body"])
            backup = backup_workspace(
                self.home, self.home.parent / (self.home.name + "-generation-backup")
            )
        restored_home = self.home.parent / (self.home.name + "-generation-restored")
        try:
            restored = restore_workspace(
                backup["destination"], restored_home, backup["manifest_sha256"]
            )
            self.assertEqual(restored["restored_schema"], WORKSPACE_SCHEMA_VERSION)
            saved = Store(restored_home).get(
                detail["dataset"]["id"], "A", self.project, "dataset"
            )
            self.assertEqual(
                saved["payload"]["rows"][0]["metadata"]["generation_plan_id"],
                detail["id"],
            )
            with self.store.transaction() as db:
                identifier = record_id("generation-process", detail["id"])
                db.execute(
                    "UPDATE entities SET id=? WHERE id=?", ("f" * 32, identifier)
                )
            with self.assertRaises(ValueError):
                backup_workspace(
                    self.home, self.home.parent / (self.home.name + "-tampered-backup")
                )
        finally:
            # All test destinations are owned siblings of the temporary workspace.
            import shutil

            for path in (Path(backup["destination"]), restored_home):
                self.assertEqual(path.resolve().parent, self.home.resolve().parent)
                self.assertTrue(path.name.startswith(self.home.name + "-"))
                if path.exists():
                    shutil.rmtree(path)

    def test_reported_usage_is_retained_even_when_candidates_are_refused(self):
        self.teacher.usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        self.teacher.finish_reason = "length"
        with patch.dict(
            os.environ,
            {
                "DMT_GENERATION_INPUT_USD_PER_MILLION": "1",
                "DMT_GENERATION_OUTPUT_USD_PER_MILLION": "2",
            },
        ):
            job, _ = self.submit()
            self.run_worker()
        detail = self.detail(job)
        self.assertEqual(detail["job"]["status"], "failed")
        self.assertEqual(detail["response"]["usage"], self.teacher.usage)
        self.assertEqual(detail["response"]["reported_cost_usd"], "0.00020000")
        identifier = record_id("generation-response", detail["id"])
        entity = self.store.get(identifier, "A", self.project, "generation-response")
        payload = copy.deepcopy(entity["payload"])
        payload["usage"]["completion_tokens"] = 51
        payload["usage"]["total_tokens"] = 151
        payload["sha256"] = digest({k: v for k, v in payload.items() if k != "sha256"})
        with self.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (encode(payload), identifier),
            )
        response = self.client.get(self.prefix + "/generation-plans/" + detail["id"])
        self.assertEqual(response.status_code, 400, response.text)

    def test_reviewed_candidates_extend_only_training_then_train_a_real_cpu_model(self):
        job, _ = self.submit()
        self.run_worker()
        detail = self.detail(job)
        dataset = self.store.get(detail["dataset"]["id"], "A", self.project, "dataset")
        row = dataset["payload"]["rows"][0]
        reviewed = self.app.state.service.review(
            OWNER,
            self.project,
            dataset["id"],
            row["id"],
            row["gold"],
            True,
            dataset["payload"]["sha256"],
            "Checked fictional generated label against owner policy",
        )
        original = self.store.get(self.snapshot_id, "A", self.project, "snapshot")[
            "payload"
        ]
        prepared = self.app.state.service.submit(
            OWNER,
            self.project,
            "prepare",
            {"datasets": [reviewed["id"]], "base_snapshot": self.snapshot_id},
            "reviewed-generation-extension",
        )
        self.run_worker()
        current = self.store.job(prepared["id"], "A", self.project)
        self.assertEqual(current["status"], "succeeded", current["error"])
        snapshot_id = current["result"]["snapshot"]
        extended = self.store.get(snapshot_id, "A", self.project, "snapshot")["payload"]
        for role in ("validation", "calibration", "test"):
            self.assertEqual(extended["splits"][role], original["splits"][role])
        self.assertEqual(
            len(extended["splits"]["train"]), len(original["splits"]["train"]) + 1
        )
        self.app.state.service.configure_release_policy(
            OWNER, self.project, 0, 0, 1, 10, None
        )
        trained = self.app.state.service.submit(
            OWNER,
            self.project,
            "train",
            {"snapshot": snapshot_id, "backend": "cpu-baseline"},
            "reviewed-generation-model",
        )
        self.run_worker()
        result = self.store.job(trained["id"], "A", self.project)
        self.assertEqual(result["status"], "succeeded", result["error"])

    def test_retry_limit_and_changed_teacher_settings_refuse(self):
        job, _ = self.submit()
        for attempt in (1, 2, 3):
            self.store.cancel(job["id"], "A", self.project, "owner")
            detail = self.detail(job)
            self.assertEqual(detail["attempt"], attempt)
            if attempt < 3:
                job, _ = self.submit(
                    "bounded-retry-" + str(attempt + 1), retry_of=detail["id"]
                )
        response = self.client.post(
            self.prefix + "/generation-preview",
            json={**self.selection, "retry_of": detail["id"]},
        )
        self.assertEqual(response.status_code, 409, response.text)
        with patch.dict(os.environ, {"DMT_GENERATION_TIMEOUT_SECONDS": "11"}):
            response = self.client.post(
                self.prefix + "/generation-preview",
                json={**self.selection, "retry_of": detail["id"]},
            )
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.teacher.calls, [])

    @unittest.skipUnless(
        sys.platform in ("win32", "linux"), "Native child ownership contract"
    )
    def test_actual_worker_parent_crash_ends_transport_and_never_replays(self):
        self.teacher.delay = True
        job, _ = self.submit()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.worker",
                "--home",
                str(self.home),
                "--once",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lock = self.home / "synthetic-teacher.lock"
        try:
            self.assertTrue(self.teacher.started.wait(10))
            with self.assertRaises(ResourceBusy):
                ResourceLease(lock).acquire(timeout=0.05)
            process.kill()
            process.wait(timeout=10)
            deadline = time.monotonic() + 8
            while True:
                try:
                    with ResourceLease(lock).hold(timeout=0.05):
                        break
                except ResourceBusy:
                    if time.monotonic() >= deadline:
                        self.fail(
                            "Orphan generation transport retained its resource lease"
                        )
            with self.store.transaction() as db:
                db.execute("UPDATE jobs SET lease=0 WHERE id=?", (job["id"],))
            self.assertFalse(Worker(Store(self.home)).once())
            detail = self.detail(job)
            self.assertEqual(detail["job"]["status"], "interrupted")
            self.assertEqual(detail["state"], "unknown")
            self.assertIsNotNone(detail["process"])
            self.assertIsNone(detail["dataset"])
            self.assertEqual(len(self.teacher.calls), 1)
        finally:
            self.teacher.release.set()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

    def test_schema8_upgrade_preserves_existing_entities(self):
        with self.store.transaction() as db:
            db.execute("DELETE FROM schema_version WHERE version>8")
            before = [
                tuple(row) for row in db.execute("SELECT * FROM entities ORDER BY id")
            ]
        upgraded = Store(self.home)
        with upgraded.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0],
                WORKSPACE_SCHEMA_VERSION,
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in db.execute("SELECT * FROM entities ORDER BY id")
                ],
                before,
            )

    def test_revoked_approval_rolls_back_plan_job_and_audit(self):
        preview = self.preview()
        config = {
            **self.selection,
            "data_approved": True,
            "cost_approved": True,
            "expected_preview_sha256": preview["sha256"],
        }
        calls = []

        def authorize():
            calls.append(True)
            if len(calls) >= 4:
                raise PermissionError("Fictional owner credential revoked")

        with self.assertRaises(PermissionError):
            self.app.state.service.submit(
                OWNER, self.project, "generate", config, "revoked-generation", authorize
            )
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM entities WHERE kind='generation-plan'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM jobs WHERE kind='generate'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM events WHERE action='confirm.generation-plan'"
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
