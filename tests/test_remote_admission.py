"""Remote-study/admission failure contracts; fake engines are not GPU evidence."""

import argparse
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from decision_workbench.data import prepare, options, dumps
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Conflict, digest
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.remote import stage_import, study_entity
from decision_workbench.training.artifacts import write_manifest, file_sha
from decision_workbench.training.admission_runner import qualify
from decision_workbench.training.provenance import training_provenance
import test_platform as fixtures

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class FixtureEngine:
    def predict(self, state, questions):
        return {
            name: {
                "probabilities": {
                    key: 0.9 if i == 0 else 0.1 / (len(options(q)) - 1)
                    for i, key in enumerate(options(q))
                }
            }
            for name, q in questions.items()
        }


class RemoteAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "workspace", fixtures.keys())
        self.client = TestClient(self.app)
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.store = self.app.state.store
        self.service = self.app.state.service
        self.project = self.service.create_project(
            OWNER, "remote support", "Fictional mechanical study", fixtures.QUESTIONS
        )["id"]
        splits = prepare(fixtures.fixture_rows(240))
        self.snapshot = self.store.add(
            "snapshot",
            "A",
            self.project,
            "owner",
            {
                "splits": splits,
                "sha256": digest(splits),
                "schema_sha256": digest(fixtures.QUESTIONS),
                "counts": {k: len(v) for k, v in splits.items()},
            },
        )["id"]
        self.service.configure_release_policy(
            OWNER, self.project, 0.8, 0.8, 0.2, 10, None
        )
        self.config = {
            "snapshot": self.snapshot,
            "backend": "laya-native",
            "model": "convaiinnovations/laya",
            "revision": "7b928d828b7b0e022f929d9bd2e44165aa270148",
            "epochs": 1,
            "batch_size": 2,
            "grad_accum": 4,
            "max_length": 256,
            "provider": "modal",
            "region": "fixture-environment",
            "gpu": "A10G",
            "image_digest": "fixture/trainer@sha256:" + "a" * 64,
        }
        self.worker = Worker(self.store)

    def tearDown(self):
        self.client.close()
        self.worker.service.runtime.close()
        self.service.runtime.close()
        self.temp.cleanup()

    def study(self):
        job = self.service.submit(
            OWNER, self.project, "remote-study", self.config, "prepare-remote-study"
        )
        self.worker.once()
        job = self.store.job(job["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        return study_entity(self.store, "A", self.project, job["id"])

    def export(self, study):
        folder = self.root / "returned"
        (folder / "merged").mkdir(parents=True)
        (folder / "merged/config.json").write_text("{}")
        (folder / "merged/model.safetensors").write_bytes(
            b"fixture-only-not-model-weights"
        )
        request = study["payload"]["request"]
        report = {
            "backend": "laya-native",
            "model": self.config["model"],
            "revision": self.config["revision"],
            "data_sha256": request["data_sha256"],
            "source_sha256": request["source_sha256"],
            "remote_study_sha256": study["payload"]["request_sha256"],
            "reload_equivalence": True,
            "export_qualification": {"passed": True},
            "validation": {"untrusted": "claim"},
            "test": {"untrusted": "claim"},
        }
        (folder / "report.json").write_text(dumps(report), encoding="utf-8")
        checksum = write_manifest(folder, "laya-native", fixtures.QUESTIONS)
        receipt = {
            k: self.config[k] for k in ("provider", "region", "gpu", "image_digest")
        }
        receipt.update(
            provider_job_id="fixture-call-001",
            status="succeeded",
            manifest_sha256=checksum,
        )
        return folder, checksum, receipt

    def stage(self, study, folder, checksum, receipt, key="import-study-once"):
        return stage_import(
            self.service,
            OWNER,
            self.project,
            study["id"],
            folder,
            checksum,
            receipt,
            key,
        )

    def test_preparation_freezes_policy_download_and_scope(self):
        study = self.study()
        self.assertTrue(self.store.release_policy("A", self.project)["locked"])
        with self.assertRaises(Conflict):
            self.service.configure_release_policy(
                OWNER,
                self.project,
                0.0,
                0.0,
                1.0,
                10,
                self.store.release_policy("A", self.project)["policy"]["id"],
            )
        url = f'/v1/projects/{self.project}/remote-studies/{study["id"]}/download'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "request.json",
                    "data/manifest.json",
                    *[
                        f"data/{n}.jsonl"
                        for n in ("train", "validation", "calibration", "test")
                    ],
                },
            )
            import hashlib

            self.assertEqual(
                hashlib.sha256(archive.read("request.json")).hexdigest(),
                study["payload"]["request_sha256"],
            )
            for name, sha in study["payload"]["request"]["data_sha256"].items():
                body = archive.read("data/" + name + ".jsonl")
                self.assertEqual(hashlib.sha256(body).hexdigest(), sha)
                self.assertNotIn(b"\r\n", body)
        for token, code in [
            ("infer-secret", 403),
            ("review-secret", 403),
            ("b-secret", 404),
        ]:
            self.assertEqual(
                self.client.get(
                    url, headers={"Authorization": "Bearer " + token}
                ).status_code,
                code,
            )
        retry = self.service.submit(
            OWNER, self.project, "remote-study", self.config, "prepare-remote-study"
        )
        self.assertEqual(retry["id"], study["id"])

    def test_preparation_requires_owner_approved_policy_and_image(self):
        editor = {**OWNER, "role": "editor"}
        with self.assertRaises(PermissionError):
            self.service.submit(
                editor, self.project, "remote-study", self.config, "not-owner-key"
            )
        with self.assertRaises(ValueError):
            self.service.submit(
                OWNER,
                self.project,
                "remote-study",
                {**self.config, "image_digest": "latest"},
                "invalid-image-key",
            )
        other = self.service.create_project(
            OWNER, "no policy", "fixture", fixtures.QUESTIONS
        )["id"]
        split = self.store.add(
            "snapshot",
            "A",
            other,
            "owner",
            {"splits": prepare(fixtures.fixture_rows()), "sha256": "fixture"},
        )["id"]
        with self.assertRaises(Conflict):
            self.service.submit(
                OWNER,
                other,
                "remote-study",
                {**self.config, "snapshot": split},
                "missing-policy-key",
            )

    def test_training_provenance_checks_request_before_model_work(self):
        study = self.study()
        request = study["payload"]["request"]
        path = self.store.folder / "jobs" / study["id"] / "remote-study/request.json"
        args = argparse.Namespace(
            model=self.config["model"],
            revision=self.config["revision"],
            epochs=1,
            batch_size=2,
            grad_accum=4,
            max_length=256,
            train_encoder=False,
            study_request=str(path),
        )
        self.assertEqual(
            training_provenance(args, request["data_sha256"])["remote_study_sha256"],
            study["payload"]["request_sha256"],
        )
        for name, value in [("epochs", 3), ("revision", "b" * 40)]:
            changed = copy.copy(args)
            setattr(changed, name, value)
            with self.assertRaises(ValueError):
                training_provenance(changed, request["data_sha256"])
        with self.assertRaises(ValueError):
            training_provenance(args, {**request["data_sha256"], "test": "b" * 64})

    def test_staging_is_idempotent_and_receipt_is_attestation(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        job = self.stage(study, folder, checksum, receipt)
        self.assertEqual(self.stage(study, folder, checksum, receipt)["id"], job["id"])
        self.assertIn(
            "operator-attested", job["config"]["provider_receipt"]["verification"]
        )
        self.assertEqual(len(list((self.store.folder / "incoming").iterdir())), 1)
        with self.assertRaises(Conflict):
            self.stage(
                study, folder, checksum, {**receipt, "provider_job_id": "changed"}
            )
        self.assertIsNone(self.store.active("A", self.project)["model_id"])

    def test_wrong_provenance_and_receipt_refused_without_queued_job(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        with self.assertRaises(ValueError):
            self.stage(study, folder, checksum, {**receipt, "gpu": "different"})
        report = json.loads((folder / "report.json").read_text())
        report["data_sha256"]["test"] = "b" * 64
        (folder / "report.json").write_text(dumps(report))
        checksum = write_manifest(folder, "laya-native", fixtures.QUESTIONS)
        receipt["manifest_sha256"] = checksum
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.stage(study, folder, checksum, receipt)
        self.assertFalse(
            any(
                j["kind"] == "remote-import" for j in self.store.jobs("A", self.project)
            )
        )
        self.assertEqual(list((self.store.folder / "incoming").iterdir()), [])

    def test_executable_extra_and_quota_refusal(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        (folder / "evil.py").write_text('raise RuntimeError("not executed")')
        checksum = write_manifest(folder, "laya-native", fixtures.QUESTIONS)
        receipt["manifest_sha256"] = checksum
        with self.assertRaisesRegex(ValueError, "executable"):
            self.stage(study, folder, checksum, receipt)
        (folder / "evil.py").unlink()
        checksum = write_manifest(folder, "laya-native", fixtures.QUESTIONS)
        receipt["manifest_sha256"] = checksum
        with patch("decision_workbench.platform.remote.MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "byte/time"):
                self.stage(study, folder, checksum, receipt)
        (folder / "not-in-manifest.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "inventory"):
            self.stage(study, folder, checksum, receipt)

    def test_operator_permissions_and_live_reauthorization(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        for role in ("editor", "reviewer", "inference"):
            with self.assertRaises(PermissionError):
                stage_import(
                    self.service,
                    {**OWNER, "role": role},
                    self.project,
                    study["id"],
                    folder,
                    checksum,
                    receipt,
                    "permission-test",
                )
        with self.assertRaises(LookupError):
            stage_import(
                self.service,
                {**OWNER, "tenant": "B"},
                self.project,
                study["id"],
                folder,
                checksum,
                receipt,
                "permission-test",
            )

        def denied():
            raise PermissionError("revoked during copy")

        with self.assertRaises(PermissionError):
            stage_import(
                self.service,
                OWNER,
                self.project,
                study["id"],
                folder,
                checksum,
                receipt,
                "permission-test",
                authorize=denied,
            )
        self.assertEqual(list((self.store.folder / "incoming").iterdir()), [])

    def test_independent_metrics_replace_forged_claims_and_reload_changes_fail(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        request = {
            **study["payload"]["request"],
            "request_sha256": study["payload"]["request_sha256"],
        }
        data = self.store.folder / "jobs" / study["id"] / "remote-study/data"
        result = qualify(
            folder, data, checksum, request, engine_factory=lambda *_: FixtureEngine()
        )
        self.assertLess(result["report"]["test"]["team"]["accuracy"], 0.8)
        self.assertTrue(result["report"]["export_qualification"]["passed"])
        self.assertEqual(
            result["report"]["admission"]["source_manifest_sha256"], checksum
        )
        self.assertNotIn("untrusted", result["report"]["test"])
        self.assertTrue((folder / "training_report.json").exists())
        self.assertNotEqual(result["manifest_sha256"], checksum)

    def test_worker_registration_uses_fenced_job_and_original_policy(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        job = self.stage(study, folder, checksum, receipt)
        request = {
            **study["payload"]["request"],
            "request_sha256": study["payload"]["request_sha256"],
        }

        class LocalQualificationProcess:
            def __init__(self, argv, **kwargs):
                def arg(key):
                    return argv[argv.index(key) + 1]

                qualify(
                    arg("--export"),
                    arg("--data"),
                    arg("--manifest-sha256"),
                    request,
                    engine_factory=lambda *_: FixtureEngine(),
                )
                self.returncode = 0

            def poll(self):
                return self.returncode

        with patch(
            "decision_workbench.platform.remote_admission.subprocess.Popen",
            LocalQualificationProcess,
        ):
            self.worker.once()
        done = self.store.job(job["id"], "A", self.project)
        self.assertEqual(done["status"], "succeeded", done["error"])
        model = self.store.get(done["result"]["model"], "A", self.project, "model")[
            "payload"
        ]
        self.assertEqual(model["configuration"], done["config"])
        self.assertEqual(
            model["configuration"]["release_policy_id"],
            study["payload"]["request"]["configuration"]["release_policy_id"],
        )
        self.service.checked_export(model, fixtures.QUESTIONS)
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.service.activate(
                OWNER,
                self.project,
                done["result"]["model"],
                0,
                "do not release invalid study",
            )
        self.assertIsNone(self.store.active("A", self.project)["model_id"])

    def test_actual_operator_cli_authenticates_and_queues_only(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        keys = self.root / "keys.json"
        keys.write_text(json.dumps(fixtures.keys()))
        receiptfile = self.root / "receipt.json"
        receiptfile.write_text(json.dumps(receipt))
        command = [
            sys.executable,
            "-m",
            "decision_workbench.platform.cli",
            "import-remote",
            "--home",
            str(self.store.folder),
            "--keys-file",
            str(keys),
            "--project",
            self.project,
            "--study",
            study["id"],
            "--export",
            str(folder),
            "--manifest-sha256",
            checksum,
            "--receipt",
            str(receiptfile),
            "--request-key",
            "cli-admission-001",
        ]
        env = {**os.environ, "DMT_OPERATOR_TOKEN": "infer-secret"}
        refused = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        env["DMT_OPERATOR_TOKEN"] = "a-secret"
        accepted = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        body = json.loads(accepted.stdout)
        self.assertEqual(body["status"], "queued")
        self.assertNotIn("a-secret", accepted.stdout + accepted.stderr)

    def test_reload_refuses_changed_probabilities(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        request = {
            **study["payload"]["request"],
            "request_sha256": study["payload"]["request_sha256"],
        }
        data = self.store.folder / "jobs" / study["id"] / "remote-study/data"

        class ChangedEngine(FixtureEngine):
            def predict(self, state, questions):
                result = super().predict(state, questions)
                result["team"]["probabilities"] = {"billing": 0.8, "engineering": 0.2}
                return result

        count = 0

        def factory(*_):
            nonlocal count
            count += 1
            return FixtureEngine() if count == 1 else ChangedEngine()

        with self.assertRaisesRegex(ValueError, "tolerance"):
            qualify(folder, data, checksum, request, engine_factory=factory)
        self.assertEqual(file_sha(folder / "manifest.json"), checksum)

    def test_worker_cancellation_fences_registration_and_cleans_artifact(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        job = self.stage(study, folder, checksum, receipt)
        store = self.store
        project = self.project

        class CancelledQualification:
            def __init__(self, argv, **kwargs):
                store.cancel(job["id"], "A", project, "owner")
                self.returncode = 0

            def poll(self):
                return self.returncode

        with patch(
            "decision_workbench.platform.remote_admission.subprocess.Popen",
            CancelledQualification,
        ):
            self.worker.once()
        done = self.store.job(job["id"], "A", self.project)
        self.assertEqual(done["status"], "cancelled")
        self.assertEqual(self.store.list("A", self.project, "model"), [])
        self.assertEqual(list((self.store.folder / "artifacts").iterdir()), [])

    def test_staged_export_tampering_refused_before_runtime_launch(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        job = self.stage(study, folder, checksum, receipt)
        incoming = self.store.folder / "incoming" / job["config"]["incoming_id"]
        (incoming / "merged/model.safetensors").write_bytes(b"tampered")
        with patch(
            "decision_workbench.platform.remote_admission.subprocess.Popen"
        ) as launch:
            self.worker.once()
        launch.assert_not_called()
        self.assertEqual(
            self.store.job(job["id"], "A", self.project)["status"], "failed"
        )
        self.assertEqual(self.store.list("A", self.project, "model"), [])

    def test_actual_backup_restore_reconstructs_identical_frozen_archive(self):
        from decision_workbench.platform.recovery import (
            backup_workspace,
            restore_workspace,
        )
        from decision_workbench.platform.remote import study_archive
        from decision_workbench.platform.store import Store

        study = self.study()
        expected = study_archive(
            self.store, OWNER, self.project, study["id"]
        ).read_bytes()
        backup = backup_workspace(self.store.folder, self.root / "backup")
        restore_workspace(
            self.root / "backup", self.root / "restored", backup["manifest_sha256"]
        )
        restored = Store(self.root / "restored")
        self.assertEqual(
            study_archive(restored, OWNER, self.project, study["id"]).read_bytes(),
            expected,
        )

    def test_oversized_metadata_refused_before_json_parsing(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        with (folder / "report.json").open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        checksum = write_manifest(folder, "laya-native", fixtures.QUESTIONS)
        receipt["manifest_sha256"] = checksum
        with self.assertRaisesRegex(ValueError, "metadata size"):
            self.stage(study, folder, checksum, receipt)
        (folder / "report.json").write_text("{}")
        with (folder / "manifest.json").open("wb") as stream:
            stream.truncate(1024 * 1024 + 1)
        checksum = file_sha(folder / "manifest.json")
        receipt["manifest_sha256"] = checksum
        with self.assertRaisesRegex(ValueError, "metadata size"):
            self.stage(study, folder, checksum, receipt)
