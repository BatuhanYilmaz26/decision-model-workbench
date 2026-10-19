"""Native CPU profiles use container capacity before model/tensor allocation."""

import contextlib
import importlib.metadata
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision_workbench.training import (
    memory_limits,
    liquid_runtime,
    d3_runtime,
    liquid_omni_runtime,
)
from decision_workbench.platform.capabilities import PROBE
from decision_workbench.training import liquid_omni_runner


class NativeMemoryPreflightTests(unittest.TestCase):
    def test_linux_uses_inherited_capacity_and_never_falls_back_to_host_on_error(self):
        with (
            patch.object(memory_limits.sys, "platform", "linux"),
            patch.object(
                memory_limits, "available_linux_memory", return_value=2 * 1024**3
            ),
        ):
            self.assertEqual(memory_limits.available_cpu_memory(), 2 * 1024**3)
        with (
            patch.object(memory_limits.sys, "platform", "linux"),
            patch.object(
                memory_limits,
                "available_linux_memory",
                side_effect=ValueError("unreadable limit"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "unreadable"):
                memory_limits.available_cpu_memory()

    def test_other_hosts_validate_physical_memory_readback(self):
        for available in (True, -1, None, 1.5):
            fake = SimpleNamespace(
                virtual_memory=lambda: SimpleNamespace(available=available)
            )
            with (
                patch.object(memory_limits.sys, "platform", "win32"),
                patch.dict(sys.modules, {"psutil": fake}),
                self.assertRaises(ValueError),
            ):
                memory_limits.available_cpu_memory()
        with (
            patch.object(memory_limits.sys, "platform", "win32"),
            patch.dict(
                sys.modules,
                {
                    "psutil": SimpleNamespace(
                        virtual_memory=lambda: SimpleNamespace(available=4 * 1024**3)
                    )
                },
            ),
        ):
            self.assertEqual(memory_limits.available_cpu_memory(), 4 * 1024**3)

    def test_liquid_training_and_inference_keep_their_existing_floors(self):
        with patch.object(
            liquid_runtime, "available_cpu_memory", return_value=18 * 1024**3
        ):
            liquid_runtime.require_cpu_memory()
            with self.assertRaisesRegex(ValueError, "20 GiB"):
                liquid_runtime.require_cpu_memory(training=True)

    def test_d3_cpu_rejects_small_container_and_cuda_keeps_vram_path(self):
        torch = SimpleNamespace(
            cuda=SimpleNamespace(
                mem_get_info=Mock(return_value=(32 * 1024**3, 32 * 1024**3))
            )
        )
        profile = SimpleNamespace(cpu_gib=4, cuda_gib=4, heads=2, layers=2, hidden=16)
        with (
            patch.dict(sys.modules, {"torch": torch}),
            patch.object(
                memory_limits, "available_cpu_memory", return_value=2 * 1024**3
            ) as available,
        ):
            with self.assertRaisesRegex(ValueError, "more available memory"):
                d3_runtime.memory_guard(
                    SimpleNamespace(type="cpu"), startup=True, profile=profile
                )
            torch.cuda.mem_get_info.assert_not_called()
            available.reset_mock()
            d3_runtime.memory_guard(
                SimpleNamespace(type="cuda"), startup=True, profile=profile
            )
            available.assert_not_called()
            torch.cuda.mem_get_info.assert_called_once()

    def test_omni_batch_refuses_before_tensor_allocation(self):
        torch = SimpleNamespace(
            tensor=Mock(side_effect=AssertionError("Allocated before capacity check"))
        )
        model = SimpleNamespace(
            device=SimpleNamespace(type="cpu"), head=SimpleNamespace(training=False)
        )
        with (
            patch.dict(sys.modules, {"torch": torch}),
            patch.object(memory_limits, "available_cpu_memory", return_value=1),
        ):
            with self.assertRaisesRegex(ValueError, "exceeds available memory"):
                liquid_omni_runtime.features(model, [{"input_ids": [1, 2, 3]}])
        torch.tensor.assert_not_called()

    def test_omni_load_refuses_before_reading_weights(self):
        loader = Mock(
            side_effect=AssertionError("Loaded weights before capacity check")
        )
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(liquid_omni_runtime, "runtime_identity"),
            patch.object(liquid_omni_runtime, "checked_runtime"),
            patch.dict(
                sys.modules,
                {
                    "torch": torch,
                    "transformers": SimpleNamespace(
                        AutoModel=SimpleNamespace(from_pretrained=loader)
                    ),
                },
            ),
            patch.object(
                memory_limits, "available_cpu_memory", return_value=2 * 1024**3
            ),
        ):
            with self.assertRaisesRegex(ValueError, "6 GiB"):
                liquid_omni_runtime.OmniEngine(Path(folder), device="cpu", base=True)
        loader.assert_not_called()

    def test_capability_probe_reports_the_same_budget_or_unknown(self):
        for error in (False, True):
            output = io.StringIO()
            with (
                patch.object(
                    importlib.metadata,
                    "version",
                    side_effect=importlib.metadata.PackageNotFoundError,
                ),
                patch.object(
                    memory_limits,
                    "available_cpu_memory",
                    side_effect=ValueError("unreadable") if error else None,
                    return_value=2 * 1024**3,
                ),
                contextlib.redirect_stdout(output),
            ):
                exec(PROBE, {})
            value = json.loads(output.getvalue())
            self.assertEqual(value["ram_available_gib"], None if error else 2.0)

    def test_omni_training_refuses_before_checkpoint_download(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(
                **{**liquid_omni_runner.DEFAULTS, "device": "cpu"},
                data="unused",
                out=str(Path(folder) / "out"),
                cache=str(Path(folder) / "cache"),
                local_study_request=None,
            )
            rows = {
                "train": [{"questions": {}}],
                "validation": [{}],
                "calibration": [{}] * 8,
                "test": [{}],
            }
            with (
                patch.object(liquid_omni_runner, "runtime_identity", return_value={}),
                patch.object(
                    liquid_omni_runner,
                    "load_inputs",
                    return_value=(rows, {name: b"fixture" for name in rows}),
                ),
                patch.object(
                    memory_limits, "available_cpu_memory", return_value=2 * 1024**3
                ),
                patch.object(
                    liquid_omni_runner,
                    "resolve_checkpoint",
                    side_effect=AssertionError("Downloaded before preflight"),
                ) as download,
            ):
                with self.assertRaisesRegex(ValueError, "6 GiB"):
                    liquid_omni_runner.train_omni(args)
            download.assert_not_called()
            self.assertFalse(Path(args.out).exists())

    def test_omni_cuda_checks_do_not_use_cpu_capacity(self):
        torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                mem_get_info=lambda: (8 * 1024**3, 8 * 1024**3),
            )
        )
        with (
            patch.dict(sys.modules, {"torch": torch}),
            patch.object(
                memory_limits,
                "available_cpu_memory",
                side_effect=AssertionError("CUDA route used CPU floor"),
            ),
        ):
            self.assertEqual(liquid_omni_runtime.checked_device("auto"), "cuda")
