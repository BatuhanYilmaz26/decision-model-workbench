"""Real owned-process allocation limits; no models or large successful allocations."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from decision_workbench.training.memory_limits import (
    checked_process_limit,
    limit_linux_address_space,
)
from decision_workbench.training.process_guard import leased_process_options
from decision_workbench.training.resources import ResourceLease


class ManagedMemoryTests(unittest.TestCase):
    def test_invalid_budgets_refuse_and_unrelated_launches_clear_stale_budget(self):
        for value in (True, None, "512", 0, 16 * 1024**2 - 1, 1024**4 + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checked_process_limit(value)
        with patch("decision_workbench.training.process_guard.sys.platform", "darwin"):
            self.assertEqual(
                leased_process_options(
                    ResourceLease("unused"),
                    {"DMT_PROCESS_MEMORY_LIMIT_BYTES": "1", "KEEP": "value"},
                ),
                {"env": {"KEEP": "value"}},
            )
            with self.assertRaisesRegex(ValueError, "Windows or Linux"):
                leased_process_options(
                    ResourceLease("unused"), {}, memory_limit_bytes=512 * 1024**2
                )

    def test_inherited_linux_soft_or_hard_limits_are_never_raised(self):
        mb = 1024**2
        for inherited in ((128 * mb, 256 * mb), (-1, 256 * mb), (256 * mb, -1)):
            cap = 128 * mb if inherited[0] == 128 * mb else 256 * mb
            resource = types.SimpleNamespace(
                RLIMIT_AS=9,
                RLIM_INFINITY=-1,
                getrlimit=Mock(side_effect=[inherited, (cap, cap)]),
                setrlimit=Mock(),
            )
            with patch.dict(sys.modules, {"resource": resource}):
                self.assertEqual(limit_linux_address_space(512 * mb), cap)
            resource.setrlimit.assert_called_once_with(9, (cap, cap))

    @unittest.skipUnless(
        sys.platform in ("linux", "win32"), "Managed OS process budget"
    )
    def test_owned_child_cannot_allocate_above_budget_and_capacity_is_reusable(self):
        program = """from decision_workbench.training.process_guard import arm_parent_guard
arm_parent_guard()
import json
try:
    block=bytearray(768*1024**2)
except MemoryError:
    print(json.dumps({"denied":True}))
else:
    print(json.dumps({"denied":False}))
"""
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "semantic.lock"
            with ResourceLease(path).hold() as lease:
                result = subprocess.run(
                    [sys.executable, "-c", program],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    **leased_process_options(lease, memory_limit_bytes=512 * 1024**2),
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"denied": True})
            with ResourceLease(path).hold(timeout=2):
                pass


if __name__ == "__main__":
    unittest.main()
