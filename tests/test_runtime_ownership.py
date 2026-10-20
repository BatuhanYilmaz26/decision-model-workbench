"""Resident processes outlive request threads and end with their broker owner."""

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
from decision_workbench.training.resources import ModelResourceLease


REAL_POPEN = subprocess.Popen
FIXTURE = Path(__file__).with_name("resident_runtime_fixture.py")


@unittest.skipUnless(
    sys.platform in ("linux", "win32"),
    "Supervised resident ownership requires Windows or Linux",
)
class ResidentOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_MODEL_RESOURCE_LOCK": str(self.root / "host.lock"),
                "DMT_MODEL_LOAD_TIMEOUT_SECONDS": "5",
                "DMT_INFERENCE_TIMEOUT_SECONDS": "5",
                "DMT_INFERENCE_IDLE_SECONDS": "60",
            },
        )
        self.environment.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.environment.stop)
        self.broker = RuntimeBroker(Store(self.root / "workspace"))
        self.addCleanup(self.broker.close)
        self.model = {
            "backend": "laya-native",
            "artifact_id": "b" * 32,
            "model_sha256": "c" * 64,
        }

    def spawn(self, argv, **kwargs):
        return REAL_POPEN([sys.executable, str(FIXTURE), "serve"], **kwargs)

    def request(self, action):
        result, errors = [], []

        def run():
            try:
                result.append(action())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "Request thread did not return")
        self.assertEqual(errors, [])
        return result[0]

    def test_resident_survives_independent_request_threads(self):
        with patch(
            "decision_workbench.platform.runtime_broker.subprocess.Popen",
            side_effect=self.spawn,
        ) as launch:
            self.request(lambda: self.broker.preload("a" * 32, self.model, {}))
            process = self.broker.process
            time.sleep(0.2)
            self.assertIsNone(
                process.poll(), "The resident died with its launch request thread"
            )
            for _ in range(3):
                answers = self.request(
                    lambda: self.broker.predict(
                        "a" * 32, self.model, {}, "fictional evidence", 0.5
                    )
                )
                self.assertEqual(answers["decision"]["answer"], "accept")
                self.assertEqual(self.broker.process.pid, process.pid)
            self.assertEqual(launch.call_count, 1)

    def test_broker_shutdown_stops_resident_and_releases_capacity(self):
        with patch(
            "decision_workbench.platform.runtime_broker.subprocess.Popen",
            side_effect=self.spawn,
        ) as launch:
            self.request(lambda: self.broker.preload("a" * 32, self.model, {}))
            process = self.broker.process
            self.broker.close()
            self.assertIsNotNone(process.poll())
            with ModelResourceLease(self.root / "another").hold(timeout=5):
                pass
            with self.assertRaises(RuntimeUnavailable):
                self.broker.preload("a" * 32, self.model, {})
            self.assertEqual(launch.call_count, 1)

    def test_broker_process_death_ends_the_resident_after_request_exit(self):
        ready = self.root / "ready"
        parent = REAL_POPEN(
            [
                sys.executable,
                str(FIXTURE),
                "parent",
                str(self.root / "parent"),
                str(ready),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not ready.exists():
                if parent.poll() is not None:
                    self.fail(parent.communicate(timeout=5)[1].decode())
                if time.monotonic() > deadline:
                    self.fail("Broker parent did not become ready")
                time.sleep(0.05)
            parent.kill()
            parent.wait(timeout=10)
            with ModelResourceLease(self.root / "replacement").hold(timeout=10):
                pass
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)
            parent.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
