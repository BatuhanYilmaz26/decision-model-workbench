"""Taxonomy parent loss stops its owned HTTP child without replaying a paid call."""

import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_taxonomy as fixtures
from decision_workbench.training.resources import ResourceLease


class TaxonomyProcessTests(unittest.TestCase):
    setUp = fixtures.TaxonomyTests.setUp
    tearDown = fixtures.TaxonomyTests.tearDown
    submit = fixtures.TaxonomyTests.submit
    remote_job = fixtures.TaxonomyTests.remote_job

    @unittest.skipUnless(
        sys.platform in ("win32", "linux"), "Owned data transport contract"
    )
    def test_parent_crash_stops_transport_keeps_unknown_call_and_does_not_publish(self):
        job = self.remote_job("parent-crash")
        marker = self.home / "taxonomy-child-ready.json"
        parent = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("data_process_fixture.py")),
                "taxonomy-parent",
                str(self.store.folder),
                str(marker),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists():
                self.assertIsNone(
                    parent.poll(), "Worker ended before transport was ready"
                )
                if time.monotonic() > deadline:
                    self.fail("Taxonomy transport did not arm its parent guard")
                time.sleep(0.02)
            parent.kill()
            parent.wait(timeout=10)
            with ResourceLease(
                self.store.folder / "jobs" / job / "taxonomy-transport.lock"
            ).hold(timeout=10):
                pass
            with self.store.transaction() as db:
                db.execute("UPDATE jobs SET lease=0 WHERE id=?", (job,))
            self.assertIsNone(self.store.claim())
            self.assertEqual(
                self.store.job(job, "A", self.project)["status"], "interrupted"
            )
            self.assertEqual(
                len(self.store.list("A", self.project, "taxonomy-discovery")), 1
            )
            self.assertEqual(self.store.list("A", self.project, "taxonomy-result"), [])
            self.assertEqual(
                self.store.list("A", self.project, "taxonomy-proposal"), []
            )
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)
