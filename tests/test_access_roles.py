"""Exercise the documented workspace roles at the real HTTP boundary."""

import hashlib
import os
import unittest
from unittest.mock import patch

from decision_workbench.data import jsonl
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker
import test_platform as fixtures


ROLES = {
    "admin": "a-secret",
    "editor": "editor-secret",
    "reviewer": "review-secret",
    "inference": "infer-secret",
}
ALL = set(ROLES)
READ = {"admin", "editor", "reviewer"}
EDIT = {"admin", "editor"}
OWNER = {"admin"}


class AccessRoleTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_DECISION_PROFILES_FILE": "",
                "DMT_CLOUD_PROFILES_FILE": "",
                "DMT_TAXONOMY_PROFILES_FILE": "",
                "DMT_SEMANTIC_PROFILE_FILE": "",
                "DMT_TEACHER_MODEL": "",
                "DMT_TEACHER_BASE_URL": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        keys = fixtures.keys() + [
            {
                "key_sha256": hashlib.sha256(b"editor-secret").hexdigest(),
                "tenant": "A",
                "actor": "editor",
                "role": "editor",
            }
        ]
        self.f = fixtures.PlatformTests("runTest")
        with patch.object(fixtures, "keys", return_value=keys):
            self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.addCleanup(self.f.app.state.service.runtime.close)
        self.prefix = self.f.prefix
        response = self.f.client.post(
            self.prefix + "/datasets",
            files={
                "file": ("fictional.jsonl", jsonl(fixtures.fixture_rows(6)).encode())
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.dataset = response.json()

    def headers(self, role):
        return {"Authorization": "Bearer " + ROLES[role]}

    def state(self):
        with self.f.app.state.store.transaction() as db:
            return {
                table: [
                    tuple(row)
                    for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")
                ]
                for table in (
                    "entities",
                    "events",
                    "jobs",
                    "active_models",
                    "release_policy_heads",
                )
            }

    def test_read_access_matches_all_four_documented_roles(self):
        routes = [
            ("/v1/me", ALL),
            ("/v1/projects", ALL),
            (self.prefix, ALL),
            (self.prefix + "/active", ALL),
            (self.prefix + "/entities/dataset", READ),
            (self.prefix + "/entities/model", READ),
            (self.prefix + "/datasets/" + self.dataset["id"] + "/rows", READ),
            (self.prefix + "/datasets/" + self.dataset["id"] + "/download", READ),
            (self.prefix + "/datasets/" + self.dataset["id"] + "/quality", READ),
            (self.prefix + "/release-policy", READ),
            (self.prefix + "/jobs", READ),
            (self.prefix + "/operations", EDIT),
            (self.prefix + "/decision-profiles", EDIT),
            (self.prefix + "/taxonomy-profiles", READ),
            (self.prefix + "/semantic-profile", READ),
            (self.prefix + "/audit", OWNER),
            (self.prefix + "/provider-calls", OWNER),
            (self.prefix + "/cloud-runs", OWNER),
            ("/v1/cloud-profiles", OWNER),
            ("/v1/schema-intakes", OWNER),
            ("/v1/schema-teacher", OWNER),
            ("/v1/generation-teacher", OWNER),
            ("/v1/runtime-capabilities", OWNER),
        ]
        with patch(
            "decision_workbench.platform.capabilities.runtime_capabilities",
            return_value={"fixture": True},
        ):
            for path, allowed in routes:
                for role in ROLES:
                    with self.subTest(path=path, role=role):
                        response = self.f.client.get(path, headers=self.headers(role))
                        self.assertEqual(
                            response.status_code,
                            200 if role in allowed else 403,
                            response.text,
                        )

    def test_denied_mutations_leave_domain_jobs_and_release_state_unchanged(self):
        row = fixtures.fixture_rows(1)[0]
        fake = "f" * 32
        review = {
            "record_id": row["id"],
            "gold": row["gold"],
            "approved": True,
            "expected_hash": self.dataset["payload"]["sha256"],
            "note": "Fictional review",
        }
        project = {
            "name": "Fictional domain",
            "description": "Owner-defined decision policy",
            "questions": fixtures.QUESTIONS,
        }
        cases = [
            ("POST", "/v1/projects", project, EDIT),
            (
                "PUT",
                self.prefix + "/release-policy",
                {
                    "min_accuracy": 0.0,
                    "min_macro_f1": 0.0,
                    "max_ece": 1.0,
                    "min_test_rows": 10,
                },
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/datasets/" + self.dataset["id"] + "/reviews",
                review,
                {"admin", "reviewer"},
            ),
            (
                "POST",
                self.prefix + "/releases",
                {"model_id": fake, "expected_revision": 0, "note": "Fictional release"},
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/generation-preview",
                {"snapshot": fake, "count": 1, "seed_count": 1},
                OWNER,
            ),
            (
                "POST",
                "/v1/schema-intakes",
                {
                    "name": project["name"],
                    "description": project["description"],
                    "request_key": "role-schema-intake",
                    "data_approved": True,
                    "cost_approved": True,
                },
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/cloud-runs",
                {
                    "study": fake,
                    "profile_id": "fictional",
                    "data_approved": True,
                    "compute_approved": True,
                    "request_key": "role-cloud-run",
                },
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/cloud-runs/" + fake + "/actions",
                {"action": "submit", "revision": 1, "request_key": "role-cloud-action"},
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/provider-calls/" + fake + "/reconcile",
                {
                    "expected_outcome_sha256": "f" * 64,
                    "local_transport_stopped": True,
                    "provider_finished": True,
                    "reference": "fictional",
                    "note": "Fictional settlement",
                },
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/audit-exports",
                {
                    "after_id": 0,
                    "note": "Fictional role acceptance",
                    "request_key": "role-audit-export",
                },
                OWNER,
            ),
            (
                "POST",
                self.prefix + "/datasets/" + self.dataset["id"] + "/review-assignments",
                {
                    "targets": [{"record_id": row["id"], "expected_revision": 0}],
                    "assignee": "reviewer",
                    "expected_hash": self.dataset["payload"]["sha256"],
                    "note": "Fictional assignment",
                },
                OWNER,
            ),
        ]
        for kind, config, allowed in (
            ("prepare", {"datasets": [self.dataset["id"]]}, EDIT),
            ("train", {"snapshot": fake, "backend": "cpu-baseline"}, EDIT),
            (
                "hosted-evaluate",
                {"snapshot": fake, "profile_id": "fictional", "data_approved": True},
                OWNER,
            ),
            (
                "generate",
                {
                    "snapshot": fake,
                    "count": 1,
                    "seed_count": 1,
                    "data_approved": True,
                    "cost_approved": True,
                    "expected_preview_sha256": "f" * 64,
                },
                OWNER,
            ),
            (
                "remote-study",
                {
                    "snapshot": fake,
                    "backend": "laya-native",
                    "provider": "aws",
                    "region": "us-east-1",
                    "gpu": "ml.g4dn.xlarge",
                    "image_digest": "sha256:" + "f" * 64,
                },
                OWNER,
            ),
        ):
            cases.append(
                (
                    "POST",
                    self.prefix + "/jobs",
                    {
                        "kind": kind,
                        "config": config,
                        "request_key": "role-denial-" + kind,
                    },
                    allowed,
                )
            )
        before = self.state()
        for method, path, body, allowed in cases:
            for role in ALL - allowed:
                with self.subTest(
                    method=method, path=path, kind=body.get("kind"), role=role
                ):
                    response = self.f.client.request(
                        method, path, json=body, headers=self.headers(role)
                    )
                    self.assertEqual(response.status_code, 403, response.text)
        for role in ("reviewer", "inference"):
            response = self.f.client.post(
                self.prefix + "/datasets",
                files={"file": ("examples.jsonl", jsonl([row]).encode())},
                headers=self.headers(role),
            )
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.state(), before)

    def test_editors_import_but_only_owners_and_reviewers_approve_labels(self):
        for role in EDIT:
            response = self.f.client.post(
                self.prefix + "/datasets",
                files={
                    "file": ("examples.jsonl", jsonl(fixtures.fixture_rows(1)).encode())
                },
                headers=self.headers(role),
            )
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["payload"]["reviewed_count"], 0)
        dataset = self.dataset
        for index, role in enumerate(("admin", "reviewer")):
            row = fixtures.fixture_rows(6)[index]
            response = self.f.client.post(
                self.prefix + "/datasets/" + dataset["id"] + "/reviews",
                json={
                    "record_id": row["id"],
                    "gold": row["gold"],
                    "approved": True,
                    "expected_hash": dataset["payload"]["sha256"],
                    "note": "Checked fictional annotation",
                },
                headers=self.headers(role),
            )
            self.assertEqual(response.status_code, 201, response.text)
            dataset = response.json()
            saved = self.f.client.get(
                self.prefix + "/datasets/" + dataset["id"] + "/rows",
                params={"record_id": row["id"]},
            ).json()["rows"][0]
            self.assertEqual(
                saved["metadata"]["reviewer"],
                "owner" if role == "admin" else "reviewer",
            )

    def test_editor_trains_owner_releases_and_all_roles_use_the_released_model(self):
        snapshot = self.f.snapshot()
        self.f.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0.0,
                "min_macro_f1": 0.0,
                "max_ece": 1.0,
                "min_test_rows": 10,
            },
        )
        queued = self.f.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": {"snapshot": snapshot, "backend": "cpu-baseline"},
                "request_key": "editor-cpu-training",
            },
            headers=self.headers("editor"),
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        worker = Worker(Store(self.f.home))
        self.addCleanup(worker.service.runtime.close)
        self.assertTrue(worker.once())
        job = self.f.app.state.store.job(queued.json()["id"], "A", self.f.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        body = {
            "model_id": job["result"]["model"],
            "expected_revision": 0,
            "note": "Fictional role acceptance",
        }
        for role in ("editor", "reviewer", "inference"):
            self.assertEqual(
                self.f.client.post(
                    self.prefix + "/releases", json=body, headers=self.headers(role)
                ).status_code,
                403,
            )
        released = self.f.client.post(
            self.prefix + "/releases", json=body, headers=self.headers("admin")
        )
        self.assertEqual(released.status_code, 200, released.text)
        for role in ROLES:
            response = self.f.client.post(
                self.prefix + "/decisions",
                json={
                    "state": "invoice payment refund requested urgent",
                    "threshold": 0.5,
                },
                headers=self.headers(role),
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["model_id"], body["model_id"])
            self.assertEqual(response.json()["answers"]["team"]["answer"], "billing")

    def test_identity_headers_do_not_override_trusted_roles_or_tenants(self):
        headers = {
            **self.headers("inference"),
            "X-Tenant": "B",
            "X-Role": "admin",
            "X-Actor": "owner",
        }
        identity = self.f.client.get("/v1/me", headers=headers)
        self.assertEqual(
            identity.json(), {"tenant": "A", "role": "inference", "actor": "app"}
        )
        self.assertEqual(
            self.f.client.get(self.prefix + "/audit", headers=headers).status_code, 403
        )
        other = {"Authorization": "Bearer b-secret"}
        for path in (
            self.prefix,
            self.prefix + "/active",
            self.prefix + "/datasets/" + self.dataset["id"] + "/download",
        ):
            self.assertEqual(self.f.client.get(path, headers=other).status_code, 404)


if __name__ == "__main__":
    unittest.main()
