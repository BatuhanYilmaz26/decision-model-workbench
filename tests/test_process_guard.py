"""Real Linux parent/thread death, inherited flock handoff and refusal contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.training.process_guard import leased_process_options
from decision_workbench.training.resources import ResourceLease


class ProcessGuardTests(unittest.TestCase):
    def test_non_linux_launch_clears_stale_guard_metadata_and_keeps_environment(self):
        with patch("decision_workbench.training.process_guard.sys.platform", "darwin"):
            options = leased_process_options(
                ResourceLease("unused"),
                {"DMT_LINUX_PARENT_PID": "123", "KEEP": "value"},
            )
        self.assertEqual(options, {"env": {"KEEP": "value"}})

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_linux_requires_held_lease_before_launch(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                leased_process_options(ResourceLease(Path(name) / "gpu.lock"))

    def run_scenario(self, mode):
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("process_lifecycle_fixture.py")),
                "scenario",
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result["passed"])
        self.assertTrue(result["child_ended"])
        self.assertTrue(result["lease_free_after_exit"])
        return result

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_direct_supervising_parent_including_container_pid_one(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            with ResourceLease(folder / "gpu.lock").hold() as lease:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("process_lifecycle_fixture.py")),
                        "child",
                        name,
                        "normal",
                    ],
                    **leased_process_options(lease),
                )
                try:
                    # The Linux container suite itself runs as PID 1. This
                    # direct child catches rejecting a valid init supervisor.
                    (folder / "finish").touch()
                    self.assertEqual(child.wait(timeout=10), 0)
                    self.assertTrue((folder / "work-started").exists())
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=10)

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_normal_child_arms_without_changing_exit_or_lease_contract(self):
        self.assertTrue(self.run_scenario("normal")["model_work_started"])

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_parent_sigkill_ends_armed_child_and_recovers_capacity(self):
        self.assertTrue(self.run_scenario("armed")["model_work_started"])

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_death_before_arming_retains_lease_until_child_refuses_work(self):
        self.assertFalse(self.run_scenario("before-arm")["model_work_started"])

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_creating_thread_exit_ends_child_while_parent_process_lives(self):
        self.assertTrue(self.run_scenario("thread-exit")["model_work_started"])

    @unittest.skipUnless(
        sys.platform == "linux", "Linux kernel parent-death/FD contract"
    )
    def test_partial_and_invalid_guard_metadata_refuse_before_work(self):
        for metadata in [
            {"DMT_LINUX_PARENT_PID": "123"},
            {
                "DMT_LINUX_PARENT_PID": "x",
                "DMT_LINUX_PARENT_TID": "2",
                "DMT_LINUX_LEASE_FD": "3",
            },
        ]:
            env = dict(os.environ)
            env.update(metadata)
            process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    'from decision_workbench.training.process_guard import arm_parent_guard; arm_parent_guard(); print("WORK")',
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn("WORK", process.stdout)


if __name__ == "__main__":
    unittest.main()
