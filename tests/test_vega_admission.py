"""Eight-file Vega admission contracts. Fixture engines are not model/GPU evidence."""

import copy
import hashlib
import io
import json
import shutil
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from decision_workbench.data import dumps, options
from decision_workbench.platform.remote import stage_import, study_entity, study_archive
from decision_workbench.platform.worker import Worker
from decision_workbench.training.admission_runner import qualify
from decision_workbench.training.artifacts import file_sha, write_manifest
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.vega_contracts import load_gate_splits
from decision_workbench.training.vega_remote import validate_request
from decision_workbench.training.metrics import normalize_answers
from decision_workbench.training.vega_local import DEFAULTS
import test_vega_platform as fixtures

OWNER, QUESTIONS, fixture_report = (
    fixtures.OWNER,
    fixtures.QUESTIONS,
    fixtures.fixture_report,
)


class DetailFixture:
    def __init__(self, changed=False):
        self.changed = changed

    def predict_details(self, state, questions):
        answers = normalize_answers(
            {
                name: {
                    "probabilities": {k: 1 / len(options(q)) for k in options(q)},
                    "model_abstained": True,
                }
                for name, q in questions.items()
            },
            questions,
        )
        return {
            "answers": answers,
            "uncertainty": {
                name: {
                    "prediction_set": list(options(q)),
                    "adapter": "domain",
                    "unbound": self.changed,
                    "upstream_abstain": True,
                    "gate": 0.8,
                }
                for name, q in questions.items()
            },
        }

    def predict(self, state, questions):
        return self.predict_details(state, questions)["answers"]


class VegaAdmissionTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.VegaPlatformTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.VegaPlatformTests.tearDownClass.__func__)
    setUp = fixtures.VegaPlatformTests.setUp
    tearDown = fixtures.VegaPlatformTests.tearDown
    policy = fixtures.VegaPlatformTests.policy
    inputs = fixtures.VegaPlatformTests.inputs

    def study(self):
        self.policy(
            {
                "min_positive_accepted_fraction": 0.0,
                "max_negative_false_open_rate": 1.0,
                "min_negative_test_rows": 10,
            }
        )
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "remote-study",
                "request_key": "vega-remote-study",
                "config": {
                    "backend": "vega",
                    "snapshot": self.snapshot,
                    "gate_snapshot": self.gate,
                    "offline": True,
                    "max_steps": 2,
                    "provider": "modal",
                    "region": "fixture",
                    "gpu": "fixture",
                    "image_digest": "fixture/trainer@sha256:" + "a" * 64,
                },
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        worker = Worker(self.store)
        try:
            worker.once()
        finally:
            worker.service.runtime.close()
        job = self.store.job(response.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        return study_entity(self.store, "A", self.project, job["id"])["payload"]

    def export(self, study):
        folder = self.home.parent / "export"
        (folder / "merged/adapters").mkdir(parents=True)
        (folder / "merged/adapters/domain.json").write_text(
            dumps({"gate_threshold": 0.7})
        )
        (folder / "merged/model.safetensors").write_bytes(b"fixture not weights")
        positive, negative = self.inputs()
        report = fixture_report(study["request"]["configuration"], positive, negative)
        report.update(
            remote_study_sha256=study["request_sha256"], local_study_sha256=None
        )
        (folder / "report.json").write_text(dumps(report))
        checksum = write_manifest(folder, "vega", QUESTIONS)
        receipt = {
            k: study["request"]["configuration"][k]
            for k in ("provider", "region", "gpu", "image_digest")
        }
        receipt.update(
            provider_job_id="fictional-only",
            status="succeeded",
            manifest_sha256=checksum,
        )
        return folder, checksum, receipt

    def test_download_has_eight_lf_inputs_and_recovery_preserves_exact_archive(self):
        study = self.study()
        path = study_archive(self.store, OWNER, self.project, study["job_id"])
        body = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "request.json",
                    *[
                        "data/" + prefix + role + ".jsonl"
                        for prefix in ("", "gate_")
                        for role in ("train", "validation", "calibration", "test")
                    ],
                },
            )
            for prefix, field in (("", "data_sha256"), ("gate_", "gate_data_sha256")):
                for role, checksum in study["request"][field].items():
                    data = archive.read("data/" + prefix + role + ".jsonl")
                    self.assertNotIn(b"\r\n", data)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), checksum)
        source = path.parent.resolve()
        self.assertTrue(source.is_relative_to(self.home.resolve()))
        shutil.rmtree(source)
        self.assertEqual(
            study_archive(
                self.store, OWNER, self.project, study["job_id"]
            ).read_bytes(),
            body,
        )

    def test_remote_request_rejects_input_option_and_source_drift_before_model(self):
        study = self.study()
        folder = self.home / "jobs" / study["job_id"] / "remote-study"
        rows, hashes = load_splits(folder / "data")
        negative, nhashes = load_gate_splits(folder / "data", rows)
        config = study["request"]["configuration"]
        args = Namespace(
            **{
                **config,
                "study_request": folder / "request.json",
                "local_study_request": None,
            }
        )
        check = lambda: validate_request(
            args, hashes, nhashes, QUESTIONS, config["vega_contract"]["preflight"]
        )
        self.assertEqual(check(), study["request_sha256"])
        args.rank += 1
        with self.assertRaises(ValueError):
            check()
        args.rank -= 1
        nhashes["test"] = "0" * 64
        with self.assertRaises(ValueError):
            check()
        nhashes["test"] = study["request"]["gate_data_sha256"]["test"]
        with patch(
            "decision_workbench.training.vega_local.source_fingerprint",
            return_value="0" * 64,
        ):
            with self.assertRaises(ValueError):
                check()

    def test_local_measurements_replace_supplied_applicability_and_bind_registration(
        self,
    ):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        job = stage_import(
            self.service,
            OWNER,
            self.project,
            study["job_id"],
            folder,
            checksum,
            receipt,
            "vega-import-once",
        )
        replay = stage_import(
            self.service,
            OWNER,
            self.project,
            study["job_id"],
            folder,
            checksum,
            receipt,
            "vega-import-once",
        )
        self.assertEqual(job["id"], replay["id"])
        request = {
            **study["request"],
            "request_sha256": study["request_sha256"],
            "questions": QUESTIONS,
        }
        data = self.home / "jobs" / study["job_id"] / "remote-study/data"
        result = qualify(
            folder, data, checksum, request, engine_factory=lambda *_: DetailFixture()
        )
        for name in QUESTIONS:
            self.assertEqual(
                result["report"]["applicability"]["test"][name][
                    "positive_accepted_fraction"
                ],
                0.0,
            )
            self.assertEqual(
                result["report"]["applicability"]["test"][name]["negative"][
                    "open_rate"
                ],
                1.0,
            )
        self.assertTrue(result["report"]["export_qualification"]["same_uncertainty"])
        self.assertEqual(
            result["report"]["export_qualification"]["negative_probes"], 10
        )

        def fake_child(argv, **kwargs):
            qualify(
                Path(argv[4]),
                argv[6],
                checksum,
                json.loads(Path(argv[8]).read_text()),
                engine_factory=lambda *_: DetailFixture(),
            )
            child = Mock()
            child.poll.return_value = 0
            child.returncode = 0
            return child

        worker = Worker(self.store)
        try:
            with patch(
                "decision_workbench.platform.remote_admission.subprocess.Popen",
                side_effect=fake_child,
            ):
                worker.once()
            final = self.store.job(job["id"], "A", self.project)
            self.assertEqual(final["status"], "succeeded", final["error"])
            model = self.store.get(
                final["result"]["model"], "A", self.project, "model"
            )["payload"]
            self.service.checked_export(model, QUESTIONS)
            self.assertEqual(
                model["report"]["applicability"]["test"]["urgency"][
                    "positive_accepted_fraction"
                ],
                0.0,
            )
            model["configuration"]["remote_request_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                self.service.checked_export(model, QUESTIONS)
        finally:
            worker.service.runtime.close()

    def test_second_load_uncertainty_drift_refuses_even_when_answers_match(self):
        study = self.study()
        folder, checksum, _ = self.export(study)
        request = {
            **study["request"],
            "request_sha256": study["request_sha256"],
            "questions": QUESTIONS,
        }
        calls = []

        def factory(*_):
            calls.append(1)
            return DetailFixture(changed=len(calls) > 1)

        with self.assertRaisesRegex(ValueError, "uncertainty"):
            qualify(
                folder,
                self.home / "jobs" / study["job_id"] / "remote-study/data",
                checksum,
                request,
                engine_factory=factory,
            )

    def test_managed_launch_requires_image_declaration_before_provider_work(self):
        study = self.study()
        with patch.object(self.service.cloud, "profile", return_value={}) as profile:
            with self.assertRaisesRegex(ValueError, "does not declare"):
                self.service.cloud.create(
                    OWNER,
                    self.project,
                    study["job_id"],
                    "unqualified",
                    True,
                    True,
                    "no-launch-for-vega",
                )
            profile.assert_called_once()

    def test_staging_rejects_tampered_negative_provenance_and_nonowner(self):
        study = self.study()
        folder, checksum, receipt = self.export(study)
        for role in ("editor", "reviewer", "inference"):
            with self.assertRaises(PermissionError):
                stage_import(
                    self.service,
                    {**OWNER, "role": role},
                    self.project,
                    study["job_id"],
                    folder,
                    checksum,
                    receipt,
                    "bad-role-" + role,
                )
        report = json.loads((folder / "report.json").read_text())
        report["gate_data_sha256"]["test"] = "0" * 64
        (folder / "report.json").write_text(dumps(report))
        bad_sha = write_manifest(folder, "vega", QUESTIONS)
        receipt["manifest_sha256"] = bad_sha
        with self.assertRaisesRegex(ValueError, "paired inputs"):
            stage_import(
                self.service,
                OWNER,
                self.project,
                study["job_id"],
                folder,
                bad_sha,
                receipt,
                "bad-gate-provenance",
            )
        self.assertFalse(list((self.home / "incoming").iterdir()))


if __name__ == "__main__":
    unittest.main()
