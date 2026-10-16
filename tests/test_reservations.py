"""Explicit group/time policies, real worker publication and immutable recovery."""

from decision_workbench.platform.store import WORKSPACE_SCHEMA_VERSION

import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import test_platform as fixtures
import test_temporal as temporal
import test_platform_http as http_fixture
from decision_workbench.reservations import (
    inventory,
    split,
    group_csv,
    read_group_csv,
    read_group_json,
)
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store, encode
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform import reservations

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


def group_policy(rows):
    return {
        "mode": "groups",
        "embargo_seconds": 0,
        "group_roles": {
            row["group_id"]: (
                "train"
                if i < len(rows) - 9
                else (
                    "validation"
                    if i < len(rows) - 6
                    else ("calibration" if i < len(rows) - 3 else "test")
                )
            )
            for i, row in enumerate(rows)
            if row["metadata"]["origin"] == "real"
        },
    }


class ReservationPolicyTests(unittest.TestCase):
    def test_manual_groups_preserve_all_rows_and_reject_linked_family_splits(self):
        rows = fixtures.fixture_rows(24)
        policy = group_policy(rows)
        result = split(rows, policy)
        self.assertEqual(
            {k: len(v) for k, v in result.items()},
            {"train": 15, "validation": 3, "calibration": 3, "test": 3},
        )
        self.assertEqual(
            {r["id"] for part in result.values() for r in part}, {r["id"] for r in rows}
        )
        for transform in ("group", "identical", "semantic", "seed"):
            changed = copy.deepcopy(rows)
            if transform == "group":
                changed[-1]["group_id"] = changed[0]["group_id"]
                adjusted = copy.deepcopy(policy)
                adjusted["group_roles"].pop(rows[-1]["group_id"])
                # Duplicate source identifiers are one family and cannot be assigned twice.
                result = split(changed, adjusted)
                self.assertIn(changed[-1]["id"], {r["id"] for r in result["train"]})
                continue
            if transform == "identical":
                changed[-1]["state"] = changed[0]["state"]
                changed[-1]["gold"] = copy.deepcopy(changed[0]["gold"])
            else:
                changed[0]["metadata"][
                    "semantic_links" if transform == "semantic" else "seed_ids"
                ] = [changed[-1]["id"]]
            with self.assertRaisesRegex(ValueError, "Related records"):
                split(changed, policy)

    def test_time_cutoffs_and_embargo_use_label_availability_without_purging(self):
        rows = temporal.dated_rows(120)
        dates = [rows[i]["metadata"]["temporal"]["decision_at"] for i in (60, 80, 100)]
        policy = {"mode": "time", "cutoffs": dates, "embargo_seconds": 3600}
        result = split(rows, policy)
        self.assertEqual([len(v) for v in result.values()], [60, 20, 20, 20])
        for altered in (
            {**policy, "embargo_seconds": 22 * 3600},
            {**policy, "cutoffs": list(reversed(dates))},
        ):
            with self.assertRaises(ValueError):
                split(rows, altered)
        changed = copy.deepcopy(rows)
        changed[59]["metadata"]["temporal"]["label_available_at"] = dates[0]
        with self.assertRaises(ValueError):
            split(changed, policy)
        changed = copy.deepcopy(rows)
        changed[-1]["group_id"] = changed[0]["group_id"]
        with self.assertRaisesRegex(ValueError, "Related records"):
            split(changed, policy)
        with self.assertRaisesRegex(ValueError, "complete operational timestamps"):
            split(fixtures.fixture_rows(120), policy)

    def test_synthetic_exposed_missing_and_unknown_groups_cannot_become_holdouts(self):
        rows = fixtures.fixture_rows(24)
        for forced in ([rows[-1]["id"]],):
            with self.assertRaisesRegex(ValueError, "training-only"):
                split(rows, group_policy(rows), training_only_ids=forced)
        synthetic = copy.deepcopy(rows[-1])
        synthetic.update(
            id="generated",
            group_id="hypothetical",
            state="Fictional generated variation",
        )
        synthetic["metadata"].update(origin="synthetic", seed_ids=[rows[-1]["id"]])
        with self.assertRaisesRegex(ValueError, "training-only"):
            split(rows + [synthetic], group_policy(rows))
        for changed in ({}, {**group_policy(rows)["group_roles"], "extra": "train"}):
            with self.assertRaises(ValueError):
                split(
                    rows,
                    {"mode": "groups", "embargo_seconds": 0, "group_roles": changed},
                )

    def test_assignment_files_refuse_duplicates_invalid_roles_and_spreadsheet_formulas(
        self,
    ):
        groups = inventory(fixtures.fixture_rows(24))["groups"]
        csv = group_csv(groups)
        self.assertIn(b"group_id,role", csv)
        with self.assertRaises(ValueError):
            read_group_csv(csv)
        self.assertEqual(
            read_group_csv(b"group_id,role\na,train\nb,test\n"),
            {"a": "train", "b": "test"},
        )
        for raw in (
            b"group_id,role\na,train\na,test\n",
            b"role,group_id\ntrain,a\n",
            b"group_id,role\na,unknown\n",
            b"x" * (1024 * 1024 + 1),
        ):
            with self.assertRaises(ValueError):
                read_group_csv(raw)
        with self.assertRaises(ValueError):
            read_group_json(b'{"a":"train","a":"test"}')
        unsafe = [{**groups[0], "group_id": '=HYPERLINK("https://example.com")'}]
        with self.assertRaises(ValueError):
            group_csv(unsafe)
        self.assertEqual(
            read_group_json(b'{"=example":"train"}'), {"=example": "train"}
        )


class ReservationPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = (Path(self.temp.name) / "workspace").resolve()
        self.app = create_app(self.home, fixtures.keys())
        self.client = TestClient(self.app, headers={"Authorization": "Bearer a-secret"})
        self.service = self.app.state.service
        self.project = self.service.create_project(
            OWNER, "Reservation fixture", "Fictional review policy", fixtures.QUESTIONS
        )["id"]
        self.prefix = f"/v1/projects/{self.project}"
        self.rows = fixtures.fixture_rows(120)
        self.dataset = self.service.save_dataset(
            OWNER, self.project, self.rows, {"fictional_fixture": True}
        )["id"]

    def tearDown(self):
        self.service.runtime.close()
        self.client.close()
        self.temp.cleanup()

    def preview(self, policy=None, **changes):
        return self.client.post(
            self.prefix + "/reservation-preview",
            json={"datasets": [self.dataset], "policy": policy, **changes},
        )

    def queue(self, policy=None, key="explicit-study-1", **changes):
        policy = policy or group_policy(self.rows)
        p = self.preview(policy)
        self.assertEqual(p.status_code, 200, p.text)
        config = {
            "datasets": [self.dataset],
            "reservation_policy": policy,
            "expected_reservation_sha256": p.json()["sha256"],
            "reservation_approved": True,
            **changes,
        }
        return self.client.post(
            self.prefix + "/jobs",
            json={"kind": "prepare", "config": config, "request_key": key},
        )

    def run_worker(self):
        worker = Worker(Store(self.home))
        try:
            return worker.once()
        finally:
            worker.service.runtime.close()

    def test_owner_preview_atomic_idempotent_plan_and_independent_worker(self):
        p = self.preview()
        self.assertEqual(p.status_code, 200, p.text)
        self.assertEqual(len(p.json()["groups"]), 120)
        self.assertEqual(
            self.client.get(self.prefix + "/entities/reservation-plan").json(), []
        )
        queued = self.queue()
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(self.queue().json()["id"], queued.json()["id"])
        plans = self.client.get(self.prefix + "/entities/reservation-plan").json()
        self.assertEqual(len(plans), 1)
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.worker",
                "--home",
                str(self.home),
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        job = self.app.state.store.job(queued.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        snapshot = self.app.state.store.get(
            job["result"]["snapshot"], "A", self.project, "snapshot"
        )["payload"]
        self.assertEqual(
            snapshot["counts"],
            {"train": 111, "validation": 3, "calibration": 3, "test": 3},
        )
        reservations.verify_snapshot(self.app.state.store, "A", self.project, snapshot)

    def test_owner_role_scope_approval_and_stale_preview_are_enforced(self):
        for token, expected in (
            ("review-secret", 403),
            ("infer-secret", 403),
            ("b-secret", 404),
        ):
            response = self.client.post(
                self.prefix + "/reservation-preview",
                json={"datasets": [self.dataset]},
                headers={"Authorization": "Bearer " + token},
            )
            self.assertEqual(response.status_code, expected, response.text)
        for change in (
            {"reservation_approved": False},
            {"expected_reservation_sha256": "0" * 64},
            {"base_snapshot": "missing"},
        ):
            result = self.queue(**change)
            self.assertIn(result.status_code, (400, 404, 409), result.text)
        self.assertEqual(
            self.client.get(self.prefix + "/entities/reservation-plan").json(), []
        )

    def test_revoked_publication_rolls_back_plan_and_job(self):
        policy = group_policy(self.rows)
        prepared = self.preview(policy).json()
        calls = []

        def revoked():
            calls.append(True)
            if len(calls) == 2:
                raise PermissionError("Fixture owner was revoked")

        with self.assertRaises(PermissionError):
            self.service.submit(
                OWNER,
                self.project,
                "prepare",
                {
                    "datasets": [self.dataset],
                    "reservation_policy": policy,
                    "reservation_approved": True,
                    "expected_reservation_sha256": prepared["sha256"],
                },
                "revoked-study-1",
                authorize=revoked,
            )
        self.assertEqual(
            self.client.get(self.prefix + "/entities/reservation-plan").json(), []
        )
        self.assertEqual(self.client.get(self.prefix + "/jobs").json(), [])

    def test_plan_backup_restore_and_erased_binding_refusal(self):
        queued = self.queue().json()
        self.run_worker()
        job = self.app.state.store.job(queued["id"], "A", self.project)
        sid = job["result"]["snapshot"]
        captured = backup_workspace(self.home, self.home.parent / "captured")
        restored = self.home.parent / "restored"
        restore_workspace(
            self.home.parent / "captured", restored, captured["manifest_sha256"]
        )
        other = Store(restored)
        snapshot = other.get(sid, "A", self.project, "snapshot")["payload"]
        reservations.verify_snapshot(other, "A", self.project, snapshot)
        snapshot["reservation_plan"] = None
        with other.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?", (encode(snapshot), sid)
            )
        with self.assertRaisesRegex(ValueError, "preparation job"):
            reservations.verify_snapshot(other, "A", self.project, snapshot)
        with self.assertRaises(ValueError):
            backup_workspace(restored, self.home.parent / "tampered")

    def test_later_preparations_cannot_reassign_existing_real_roles(self):
        first = self.queue().json()
        self.run_worker()
        self.assertEqual(
            self.app.state.store.job(first["id"], "A", self.project)["status"],
            "succeeded",
        )
        changed = group_policy(self.rows)
        changed["group_roles"][self.rows[-1]["group_id"]] = "validation"
        self.assertEqual(self.preview(changed).status_code, 400)
        queued = self.service.submit(
            OWNER,
            self.project,
            "prepare",
            {"datasets": [self.dataset]},
            "automatic-reassign-1",
        )
        self.run_worker()
        refused = self.app.state.store.job(queued["id"], "A", self.project)
        self.assertEqual(refused["status"], "failed", refused)
        self.assertIn("original roles", refused["error"])
        self.assertEqual(
            len(self.client.get(self.prefix + "/entities/snapshot").json()), 1
        )

    def test_conflicting_concurrent_plans_are_fenced_without_breaking_recovery(self):
        original = group_policy(self.rows)
        changed = copy.deepcopy(original)
        changed["group_roles"][self.rows[-1]["group_id"]] = "validation"
        first = self.queue(original, key="first-planned-study").json()
        second = self.queue(changed, key="second-planned-study").json()
        self.run_worker()
        self.run_worker()
        states = [
            self.app.state.store.job(value["id"], "A", self.project)["status"]
            for value in (first, second)
        ]
        self.assertEqual(states, ["succeeded", "failed"])
        # Unpublished rejected plans remain auditable history and recoverable.
        backup_workspace(self.home, self.home.parent / "conflicting-plan-backup")

    def test_reviewed_synthetic_extension_preserves_the_manual_holdouts(self):
        queued = self.queue().json()
        self.run_worker()
        first = self.app.state.store.job(queued["id"], "A", self.project)["result"][
            "snapshot"
        ]
        original = self.app.state.store.get(first, "A", self.project, "snapshot")[
            "payload"
        ]
        row = copy.deepcopy(original["splits"]["train"][0])
        row.update(
            id="candidate",
            group_id="hypothetical",
            state="Hypothetical independently reviewed variation",
        )
        row["metadata"].update(
            origin="synthetic", seed_ids=[original["splits"]["train"][0]["id"]]
        )
        additional = self.service.save_dataset(
            OWNER, self.project, [row], {"fictional_fixture": True}
        )["id"]
        job = self.service.submit(
            OWNER,
            self.project,
            "prepare",
            {"datasets": [additional], "base_snapshot": first},
            "extend-reviewed-1",
        )
        self.run_worker()
        complete = self.app.state.store.job(job["id"], "A", self.project)
        self.assertEqual(complete["status"], "succeeded", complete["error"])
        updated = self.app.state.store.get(
            complete["result"]["snapshot"], "A", self.project, "snapshot"
        )["payload"]
        for role in ("validation", "calibration", "test"):
            self.assertEqual(updated["splits"][role], original["splits"][role])
        reservations.verify_snapshot(self.app.state.store, "A", self.project, updated)

    def test_cutoff_reservation_trains_a_real_cpu_model(self):
        self.rows = temporal.dated_rows(120)
        self.dataset = self.service.save_dataset(
            OWNER, self.project, self.rows, {"fictional_fixture": True}
        )["id"]
        policy = {
            "mode": "time",
            "cutoffs": [
                self.rows[i]["metadata"]["temporal"]["decision_at"]
                for i in (90, 100, 110)
            ],
            "embargo_seconds": 3600,
        }
        queued = self.queue(policy).json()
        self.run_worker()
        reserved = self.app.state.store.job(queued["id"], "A", self.project)
        self.assertEqual(reserved["status"], "succeeded", reserved["error"])
        criteria = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0.0,
                "min_macro_f1": 0.0,
                "max_ece": 1.0,
                "min_test_rows": 10,
            },
        )
        self.assertEqual(criteria.status_code, 200, criteria.text)
        trained = self.service.submit(
            OWNER,
            self.project,
            "train",
            {"snapshot": reserved["result"]["snapshot"], "backend": "cpu-baseline"},
            "time-model-1",
        )
        self.run_worker()
        result = self.app.state.store.job(trained["id"], "A", self.project)
        self.assertEqual(result["status"], "succeeded", result["error"])

    def test_schema7_upgrade_preserves_entities_and_future_schema_refuses(self):
        with self.app.state.store.transaction() as db:
            db.execute("DELETE FROM schema_version WHERE version>7")
            before = [
                tuple(r) for r in db.execute("SELECT * FROM entities ORDER BY id")
            ]
        current = Store(self.home)
        with current.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0],
                WORKSPACE_SCHEMA_VERSION,
            )
            self.assertEqual(
                [tuple(r) for r in db.execute("SELECT * FROM entities ORDER BY id")],
                before,
            )
            db.execute(
                "INSERT INTO schema_version VALUES (?)", (WORKSPACE_SCHEMA_VERSION + 1,)
            )
        checksum = hashlib.sha256(current.database.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, "Unsupported workspace"):
            Store(self.home)
        self.assertEqual(
            hashlib.sha256(current.database.read_bytes()).hexdigest(), checksum
        )


class ReservationBrowserTests(unittest.TestCase):
    def setUp(self):
        http_fixture.LocalProcessTests.setUp(self)
        self.project = self.app.state.service.create_project(
            OWNER, "Browser reservation", "Fictional policy", fixtures.QUESTIONS
        )["id"]
        self.rows = temporal.dated_rows(40)
        self.dataset = self.app.state.service.save_dataset(
            OWNER, self.project, self.rows, {"fictional_fixture": True}
        )["id"]

    def tearDown(self):
        self.app.state.service.runtime.close()
        http_fixture.LocalProcessTests.tearDown(self)

    def editor(self):
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        ).run()
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value("a-secret")
        next(w for w in app.button if w.label == "Connect").click().run()
        next(
            w
            for w in app.multiselect
            if w.label.startswith("Dataset versions to prepare")
        ).select(self.dataset).run()
        next(
            w
            for w in app.checkbox
            if w.label == "Choose holdout groups or date cutoffs yourself"
        ).check().run()
        next(
            w for w in app.button if w.label == "Inspect reservation inputs"
        ).click().run()
        self.assertFalse(app.exception)
        return app

    def test_live_http_cutoff_preview_confirmation_and_worker(self):
        app = self.editor()
        next(
            w for w in app.radio if w.label == "Assign reservation roles using"
        ).set_value("Date cutoffs").run()
        for label, index in (
            ("Validation starts at", 20),
            ("Calibration starts at", 27),
            ("Test starts at", 34),
        ):
            next(w for w in app.text_input if w.label == label).set_value(
                self.rows[index]["metadata"]["temporal"]["decision_at"]
            )
        next(
            w for w in app.button if w.label == "Preview explicit reservation"
        ).click().run()
        self.assertFalse(app.error, [e.value for e in app.error])
        reserve = next(
            w
            for w in app.button
            if w.label == "Reserve train / validation / calibration / test"
        )
        self.assertTrue(reserve.disabled)
        next(
            w
            for w in app.checkbox
            if w.label
            == "I reviewed and approve these source groups, time boundaries and heldout roles"
        ).check().run()
        next(
            w
            for w in app.button
            if w.label == "Reserve train / validation / calibration / test"
        ).click().run()
        self.assertFalse(app.exception)
        worker = Worker(Store(self.home))
        try:
            worker.once()
        finally:
            worker.service.runtime.close()
        snapshots = self.http.get(
            f"/v1/projects/{self.project}/entities/snapshot"
        ).json()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            snapshots[0]["payload"]["counts"],
            {"train": 20, "validation": 7, "calibration": 7, "test": 6},
        )

    def test_group_editor_saves_pages_and_invalidates_approved_preview(self):
        from decision_workbench.ui.reservations import identity

        self.rows = temporal.dated_rows(80)
        self.dataset = self.app.state.service.save_dataset(
            OWNER, self.project, self.rows, {"fictional_fixture": True}
        )["id"]
        app = self.editor()
        selected = {"datasets": [self.dataset], "semantic_audit": None}
        preview = self.http.post(
            f"/v1/projects/{self.project}/reservation-preview", json=selected
        ).json()
        scope = self.project + "-" + identity(selected)
        key = scope + "-" + preview["selection_sha256"]
        policy = group_policy(self.rows)
        for page in (0, 1):
            next(
                w
                for w in app.number_input
                if w.label == "Group assignment page (50 groups)"
            ).set_value(page).run()
            table = next(
                w.value for w in app.dataframe if "group_id" in w.value.columns
            )
            app.session_state[f"reservation-grid-{key}-{page}-0"] = {
                "edited_rows": {
                    int(i): {"role": policy["group_roles"][row["group_id"]]}
                    for i, row in table.iterrows()
                },
                "added_rows": [],
                "deleted_rows": [],
            }
            next(
                w for w in app.button if w.label == "Save assignments for this page"
            ).click().run()
            self.assertFalse(app.exception)
        self.assertEqual(
            app.session_state["reservation-group-roles-" + key], policy["group_roles"]
        )
        next(
            w for w in app.button if w.label == "Preview explicit reservation"
        ).click().run()
        self.assertFalse(app.error, [e.value for e in app.error])
        next(
            w
            for w in app.checkbox
            if w.label
            == "I reviewed and approve these source groups, time boundaries and heldout roles"
        ).check().run()
        self.assertFalse(
            next(
                w
                for w in app.button
                if w.label == "Reserve train / validation / calibration / test"
            ).disabled
        )
        next(
            w
            for w in app.number_input
            if w.label == "Minimum gap before each later role (seconds)"
        ).set_value(3600).run()
        self.assertTrue(
            next(
                w
                for w in app.button
                if w.label == "Reserve train / validation / calibration / test"
            ).disabled
        )
        self.assertFalse(
            any(
                w.label
                == "I reviewed and approve these source groups, time boundaries and heldout roles"
                for w in app.checkbox
            )
        )
