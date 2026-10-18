"""Variant identity, shard, geometry, storage and portable-study contracts."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.training.d3_profiles import BACKENDS, get_profile
from decision_workbench.training.d3_local import settings
from decision_workbench.training.d3_runtime import checked_runtime
from decision_workbench.training.d3_storage import training_storage
from decision_workbench.training.d3_data import export_dataset
from decision_workbench.training.native_remote import check_inputs
from decision_workbench.training.contracts import load_splits
import test_d3 as contracts
import test_d3_remote as remote


class D3FamilyContracts(unittest.TestCase):
    def test_large_transfer_budgets_are_bounded_and_modal_downloads_use_them(self):
        from decision_workbench.training.transfer_limits import (
            MAX_ARTIFACT_BYTES,
            transfer_seconds,
        )
        from decision_workbench.platform.modal_transport import operation_seconds

        self.assertGreater(MAX_ARTIFACT_BYTES, get_profile("d3").weight_bytes)
        self.assertEqual(transfer_seconds(1024), 600)
        budget = transfer_seconds(52 * 1024**3)
        self.assertGreater(budget, 600)
        self.assertLessEqual(budget, 4 * 60 * 60)
        self.assertEqual(
            operation_seconds(
                {"max_artifact_bytes": MAX_ARTIFACT_BYTES},
                "download",
                {"marker": {"artifact_bytes": 52 * 1024**3}},
            ),
            budget,
        )
        self.assertEqual(operation_seconds({}, "create", {}), 90)

    def test_variant_settings_cannot_substitute_other_model_revision_or_method(self):
        for backend in BACKENDS:
            profile = get_profile(backend)
            self.assertEqual(settings({"backend": backend})["model"], profile.model)
            for change in (
                {"revision": "0" * 40},
                {"model": "unapproved/model"},
                {"training_method": "chat-sft"},
            ):
                with self.subTest(backend=backend, change=change), self.assertRaises(
                    ValueError
                ):
                    settings({"backend": backend, **change})
        with self.assertRaises(ValueError):
            get_profile("other-organization/d3")

    def test_every_shard_is_verified_and_unsharded_nano_is_supported(self):
        for backend in ("d3", "d3-nano"):
            profile = get_profile(backend)
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                expected = {}
                for index, filename in enumerate(profile.weights):
                    body = ("frozen shard " + str(index)).encode()
                    (root / filename).write_bytes(body)
                    expected[filename] = {
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                readout = b"fictional readout pin fixture"
                (root / "readout.safetensors").write_bytes(readout)
                fixture = replace(
                    profile,
                    assets={},
                    weights=expected,
                    readout_sha256=hashlib.sha256(readout).hexdigest(),
                )
                with patch(
                    "decision_workbench.training.d3_runtime.get_profile",
                    return_value=fixture,
                ):
                    self.assertEqual(
                        checked_runtime(root, base=True, backend=backend), root
                    )
                    filename = next(reversed(expected))
                    (root / filename).write_bytes(b"changed shard")
                    with self.assertRaisesRegex(ValueError, "backbone weights"):
                        checked_runtime(root, base=True, backend=backend)

    def test_native_data_manifest_keeps_selected_variant_and_soft_target_lineage(self):
        profile = get_profile("d3-lite")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = contracts.D3Tests("runTest").inputs(root)
            prompt = contracts.PromptFixture()
            prompt.profile = profile
            manifest = export_dataset(root, root / "native", prompt, 256)
            self.assertEqual(
                (manifest["model"], manifest["revision"]),
                (profile.model, profile.revision),
            )
            original = json.loads((root / "native/canonical/train.jsonl").read_text())
            self.assertEqual(original["gold"], inputs["train"][0]["gold"])
            self.assertEqual(original["metadata"], inputs["train"][0]["metadata"])

    def test_large_variant_storage_refused_before_download_or_export(self):
        profile = get_profile("d3")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with patch(
                "decision_workbench.training.d3_storage.shutil.disk_usage",
                return_value=SimpleNamespace(free=8 * 1024**3),
            ):
                with self.assertRaisesRegex(ValueError, "GiB free"):
                    training_storage(
                        root / "cache", root / "export", profile, download=True
                    )
            self.assertEqual(list(root.iterdir()), [])


class D3FamilyRemoteContracts(unittest.TestCase):
    setUp = remote.D3RemoteTests.setUp
    inputs = remote.D3RemoteTests.inputs
    study = remote.D3RemoteTests.study

    def test_largest_variant_is_accepted_by_each_declared_provider_image_profile(self):
        remote.shared.LiquidRemoteTests.test_all_provider_profiles_refuse_undeclared_models_before_run_creation(
            self, backend="d3"
        )

    def test_all_six_variants_prepare_exact_native_studies_for_five_providers(self):
        for backend in BACKENDS:
            for provider in ("aws", "azure", "gcp", "modal", "runpod"):
                with self.subTest(backend=backend, provider=provider):
                    request, work = self.inputs(
                        self.study(provider, backend), backend + "-" + provider
                    )
                    rows, hashes = load_splits(work / "data")
                    questions = rows["train"][0]["questions"]
                    study = check_inputs(request, rows, hashes, questions)
                    self.assertEqual(study["configuration"]["backend"], backend)
                    self.assertEqual(
                        study["configuration"]["revision"],
                        get_profile(backend).revision,
                    )
                    self.assertFalse(study["configuration"]["download"])
                    self.assertEqual(
                        set(study["data_sha256"]),
                        {"train", "validation", "calibration", "test"},
                    )


if __name__ == "__main__":
    unittest.main()
