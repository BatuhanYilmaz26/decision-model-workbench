"""Shared host model capacity across independently rooted workspaces."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from decision_workbench.platform.runtime_broker import RuntimeBroker, RuntimeUnavailable
from decision_workbench.platform.store import Store
from decision_workbench.training.process_guard import leased_process_options
from decision_workbench.training.resources import (
    ModelResourceLease,
    ResourceBusy,
    ResourceLease,
    model_resource_path,
)


class SharedResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.shared = self.root / "host/gpu.lock"
        self.env = patch.dict(os.environ, {"DMT_MODEL_RESOURCE_LOCK": str(self.shared)})
        self.env.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)

    def test_default_path_is_independent_of_workspace_and_relative_paths_refuse(self):
        with patch.dict(
            os.environ, {"DMT_MODEL_RESOURCE_LOCK": "", "DMT_HOME": "first"}
        ):
            first = ModelResourceLease(self.root / "a").path
            with patch.dict(os.environ, {"DMT_HOME": "second"}):
                self.assertEqual(ModelResourceLease(self.root / "b").path, first)
            self.assertEqual(
                first,
                (Path.home() / ".cache/decision-workbench/locks/gpu.lock").resolve(),
            )
        for value in ("relative/gpu.lock", "gpu.lock"):
            with patch.dict(os.environ, {"DMT_MODEL_RESOURCE_LOCK": value}):
                with self.assertRaises(ValueError):
                    model_resource_path()

    def test_distinct_workspace_processes_exclude_then_recover(self):
        code = (
            "from decision_workbench.training.resources import ModelResourceLease,ResourceBusy;import sys\n"
            "try:\n with ModelResourceLease(sys.argv[1]).hold(): pass\n"
            "except ResourceBusy: sys.exit(5)\n"
        )
        with ModelResourceLease(self.root / "a").hold():
            attempt = subprocess.run(
                [sys.executable, "-c", code, str(self.root / "b")],
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(attempt.returncode, 5, attempt.stderr.decode())
            with self.assertRaises(ResourceBusy):
                ResourceLease(self.root / "a/gpu.lock").acquire()
        recovered = subprocess.run(
            [sys.executable, "-c", code, str(self.root / "b")],
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr.decode())

    def test_legacy_contention_releases_shared_capacity_and_deduplicates_alias(self):
        with ResourceLease(self.root / "a/gpu.lock").hold():
            refused = ModelResourceLease(self.root / "a")
            with self.assertRaises(ResourceBusy):
                refused.acquire()
            self.assertIsNone(refused.file)
            with ModelResourceLease(self.root / "b").hold():
                pass
        with patch.dict(
            os.environ, {"DMT_MODEL_RESOURCE_LOCK": str(self.root / "a/gpu.lock")}
        ):
            lease = ModelResourceLease(self.root / "a")
            self.assertIsNone(lease.workspace_lease)
            with lease.hold():
                self.assertIsNotNone(lease.file)

    def test_cancelled_and_invalid_waits_do_not_reserve_capacity(self):
        event = threading.Event()
        event.set()
        lease = ModelResourceLease(self.root / "a")
        with self.assertRaises(ResourceBusy):
            lease.acquire(cancelled=event)
        for timeout in (True, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                lease.acquire(timeout=timeout)
        with ModelResourceLease(self.root / "b").hold():
            pass

    def test_cancellation_interrupts_shared_wait_without_leaking_workspace_lock(self):
        event = threading.Event()
        timer = threading.Timer(0.1, event.set)
        with ModelResourceLease(self.root / "a").hold():
            timer.start()
            try:
                began = time.monotonic()
                with self.assertRaises(ResourceBusy):
                    ModelResourceLease(self.root / "b").acquire(
                        timeout=10, cancelled=event
                    )
                self.assertLess(time.monotonic() - began, 2)
                with ResourceLease(self.root / "b/gpu.lock").hold():
                    pass
            finally:
                timer.cancel()
                timer.join()

    def test_runtime_in_another_workspace_refuses_before_launch(self):
        broker = RuntimeBroker(Store(self.root / "b"))
        self.addCleanup(broker.close)
        with ModelResourceLease(self.root / "a").hold(), patch(
            "decision_workbench.platform.runtime_broker.subprocess.Popen"
        ) as launch:
            with self.assertRaises(RuntimeUnavailable):
                broker.preload("1" * 32, {"artifact_id": "2" * 32}, {})
            launch.assert_not_called()

    @unittest.skipUnless(
        sys.platform in ("win32", "linux"),
        "Owned process guards require Windows or Linux",
    )
    def test_child_inherits_shared_ownership_and_parent_death_frees_capacity(self):
        ready = self.root / "ready"
        fixture = Path(__file__).with_name("shared_resource_fixture.py")
        parent = subprocess.Popen(
            [sys.executable, str(fixture), "parent", str(self.root / "a"), str(ready)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not ready.exists():
                if parent.poll() is not None:
                    self.fail(parent.communicate(timeout=5)[1].decode())
                if time.monotonic() > deadline:
                    self.fail("Owned fixture did not become ready")
                time.sleep(0.05)
            with self.assertRaises(ResourceBusy):
                ModelResourceLease(self.root / "b").acquire()
            parent.kill()
            parent.wait(timeout=10)
            with ModelResourceLease(self.root / "b").hold(timeout=10):
                pass
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)
            parent.communicate(timeout=10)

    def test_supervision_uses_shared_file_and_cpu_training_stays_independent(self):
        import test_platform as fixtures
        from decision_workbench.platform.worker import Worker

        fixture = fixtures.PlatformTests("runTest")
        fixture.setUp()
        try:
            snapshot = fixture.snapshot()
            job = fixture.job(
                "train",
                {"snapshot": snapshot, "backend": "cpu-baseline"},
                "shared-capacity-cpu-training",
            )
            with ModelResourceLease(self.root / "other").hold() as lease:
                self.assertEqual(lease.path, self.shared)
                options = leased_process_options(lease)
                if sys.platform == "linux":
                    self.assertIn(lease.file.fileno(), options["pass_fds"])
                self.assertTrue(Worker(Store(fixture.home)).once())
                self.assertEqual(
                    fixture.app.state.store.job(job["id"], "A", fixture.project)[
                        "status"
                    ],
                    "succeeded",
                )
        finally:
            fixture.app.state.service.runtime.close()
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
