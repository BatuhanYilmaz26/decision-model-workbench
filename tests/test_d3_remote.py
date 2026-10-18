"""D3 frozen cloud studies, native dispatch and offline browser preparation."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from decision_workbench.data import dumps
from decision_workbench.platform.remote import study_entity
from decision_workbench.training import d3_local
from decision_workbench.training.d3_profiles import get_profile
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.native_remote import (
    check_inputs,
    export_assets,
    export_files,
)
from decision_workbench.training.cloud_runner import trainer_command
from decision_workbench.training.artifacts import write_manifest
import test_liquid_remote as shared


class D3RemoteTests(unittest.TestCase):
    setUp = shared.LiquidRemoteTests.setUp
    inputs = shared.LiquidRemoteTests.inputs

    def study(self, provider="modal", backend="d3-edge"):
        config = {
            "snapshot": self.f.snapshot,
            **get_profile(backend).fields,
            "device": "cuda",
            "download": False,
            "max_length": 256,
            "max_steps": 1,
            **{k: self.f.config[k] for k in ("region", "gpu", "image_digest")},
            "provider": provider,
        }
        job = self.f.service.submit(
            shared.fixtures.OWNER,
            self.f.project,
            "remote-study",
            config,
            "d3-cloud-" + backend + "-" + provider,
        )
        self.f.worker.once()
        done = self.f.store.job(job["id"], "A", self.f.project)
        self.assertEqual(done["status"], "succeeded", done["error"])
        return study_entity(self.f.store, "A", self.f.project, job["id"])["payload"]

    def test_all_five_providers_bind_native_settings_source_and_four_roles(self):
        for provider in ("aws", "azure", "gcp", "modal", "runpod"):
            with self.subTest(provider=provider):
                request, work = self.inputs(self.study(provider), provider)
                rows, hashes = load_splits(work / "data")
                native = check_inputs(
                    request, rows, hashes, shared.fixtures.fixtures.QUESTIONS
                )
                self.assertEqual(native, request["configuration"]["d3_study"])
                self.assertEqual(
                    set(native["counts"]),
                    {"train", "validation", "calibration", "test"},
                )
                argv = trainer_command(request, work, work / "output")
                self.assertEqual(argv[0], sys.executable)
                self.assertIn("decision_workbench.training.native_remote_runner", argv)
                self.assertNotIn("--rank", argv)
                for changes in (
                    {"device": "cpu"},
                    {"download": True},
                    {"python": "unapproved"},
                    {"d3_study": {}},
                ):
                    altered = copy.deepcopy(request)
                    altered["configuration"].update(changes)
                    with self.assertRaises(ValueError):
                        check_inputs(
                            altered, rows, hashes, shared.fixtures.fixtures.QUESTIONS
                        )

    def test_cloud_browser_requires_no_installed_local_model(self):
        with patch.object(shared, "BACKENDS", ("d3-edge",)):
            shared.LiquidRemoteTests.test_browser_prepares_all_liquid_cloud_profiles_without_a_local_gpu(
                self
            )

    def test_native_readout_trainer_runs_before_remote_provenance(self):
        from decision_workbench.training.native_remote_runner import execute

        request, work = self.inputs(self.study(), "dispatch")
        destination = self.f.root / "provider-output"
        destination.mkdir()
        calls = []

        def train(args):
            calls.append(args)
            self.assertEqual(
                json.loads(Path(args.local_study_request).read_text()),
                request["configuration"]["d3_study"],
            )
            self.assertEqual(args.device, "cuda")
            self.assertFalse(args.download)
            folder = Path(args.out)
            (folder / "checkpoint").mkdir(parents=True)
            (folder / "checkpoint/readout.safetensors").write_bytes(
                b"fictional transport fixture"
            )
            report = {"backend": "d3-edge"}
            (folder / "report.json").write_text(dumps(report))
            write_manifest(
                folder, "d3-edge", shared.fixtures.fixtures.QUESTIONS, "checkpoint"
            )
            return report

        with patch(
            "decision_workbench.training.d3_runner.train_d3", side_effect=train
        ), patch("decision_workbench.platform.remote.validate_export") as validate:
            result = execute(work / "request.json", work / "data", destination)
        self.assertEqual(len(calls), 1)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(
            result["remote_study_sha256"],
            hashlib.sha256(dumps(request).encode()).hexdigest(),
        )
        self.assertEqual(
            json.loads((destination / "report.json").read_text())[
                "remote_study_sha256"
            ],
            result["remote_study_sha256"],
        )
        self.assertTrue((destination / "manifest.json").is_file())
        self.assertFalse((destination / ".native-incomplete").exists())

    def test_d3_export_pins_original_config_source_and_license(self):
        from decision_workbench.training.d3_assets import ASSETS

        values = export_assets("d3-edge", "checkpoint")
        self.assertEqual(values, {"checkpoint/" + k: v for k, v in ASSETS.items()})
        files = export_files("d3-edge", "checkpoint")
        self.assertIn("checkpoint/NOTICE", files)
        self.assertNotIn("checkpoint/__pycache__/d3_runtime.pyc", files)

    def test_all_provider_profiles_require_declared_d3_image_before_allocation(self):
        shared.LiquidRemoteTests.test_all_provider_profiles_refuse_undeclared_models_before_run_creation(
            self, backend="d3-edge"
        )

    def test_managed_extraction_refuses_changed_source_and_cached_bytecode(self):
        import io
        import tarfile
        from decision_workbench.platform.cloud_archive import extract_model

        for name in (
            "checkpoint/d3_runtime.py",
            "checkpoint/__pycache__/d3_runtime.pyc",
            "checkpoint/config.json",
        ):
            archive = self.f.root / (Path(name).name + ".tar.gz")
            with tarfile.open(archive, "w:gz", format=tarfile.USTAR_FORMAT) as bundle:
                body = b"unreviewed native content"
                entry = tarfile.TarInfo(name)
                entry.size = len(body)
                bundle.addfile(entry, io.BytesIO(body))
            destination = self.f.root / (Path(name).name + "-extracted")
            with self.subTest(name=name), self.assertRaises(ValueError):
                extract_model(archive, destination, 4096, backend="d3-edge")
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
