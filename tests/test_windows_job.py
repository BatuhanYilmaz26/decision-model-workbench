"""Actual Windows job trees/parent death/startup refusal and capacity fencing."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from decision_workbench.training.resources import ResourceLease, ResourceBusy
from decision_workbench.training.process_guard import leased_process_options


@unittest.skipUnless(sys.platform == "win32", "Windows kernel process-job contract")
class WindowsJobTests(unittest.TestCase):
    def run_scenario(self, mode):
        with tempfile.TemporaryDirectory() as name:
            process = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("windows_process_fixture.py")),
                    "scenario",
                    name,
                    mode,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertTrue(result["passed"])
            self.assertTrue(result["owned_tree_ended"])
            self.assertTrue(result["lease_free"])
            return result

    def test_normal_direct_exit_removes_remaining_owned_descendant(self):
        result = self.run_scenario("normal")
        self.assertEqual(result["process_count"], 2)
        self.assertTrue(result["complete_marker"])

    def test_parent_crash_ends_child_and_grandchild_without_completion_marker(self):
        result = self.run_scenario("crash")
        self.assertEqual(result["process_count"], 2)
        self.assertFalse(result["complete_marker"])

    def test_capacity_remains_refused_while_last_observer_handle_delays_job_close(self):
        result = self.run_scenario("held-reference")
        self.assertEqual(result["process_count"], 2)

    def test_death_before_child_joins_refuses_model_work(self):
        result = self.run_scenario("before-arm")
        self.assertEqual(result["process_count"], 1)
        self.assertFalse(result["complete_marker"])

    def test_destroyed_job_and_invalid_metadata_refuse_before_work(self):
        with tempfile.TemporaryDirectory() as name:
            lock = Path(name) / "gpu.lock"
            with ResourceLease(lock).hold() as lease:
                options = leased_process_options(lease)
                lease.stop_children()
                process = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        'from decision_workbench.training.process_guard import arm_parent_guard;arm_parent_guard();print("WORK")',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **options,
                )
                self.assertNotEqual(process.returncode, 0)
                self.assertNotIn("WORK", process.stdout)
            marker = lock.with_name(lock.name + ".windows-job.json")
            for content in [
                "broken",
                "{}",
                '{"version":true,"job_name":"Local\\\\DMT-bad"}',
            ]:
                marker.write_text(content, encoding="utf-8")
                with self.assertRaises(ResourceBusy):
                    ResourceLease(lock).acquire(timeout=0.02)
            marker.unlink()
            with ResourceLease(lock).hold():
                pass

    def test_job_requires_held_lease_and_refuses_second_launch_on_same_lease(self):
        with tempfile.TemporaryDirectory() as name:
            lease = ResourceLease(Path(name) / "gpu.lock")
            with self.assertRaises(ValueError):
                leased_process_options(lease)
            with lease.hold():
                options = leased_process_options(lease)
                self.assertIn("DMT_WINDOWS_MODEL_JOB", options["env"])
                with self.assertRaises(ValueError):
                    leased_process_options(lease)


if __name__ == "__main__":
    unittest.main()
