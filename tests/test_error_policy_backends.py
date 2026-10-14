"""Hosted fixture and independently remeasured remote-export risk contracts."""

import json
from pathlib import Path
import unittest

from decision_workbench.training.error_policy import fingerprint
from decision_workbench.training.admission_runner import qualify
from test_error_policy import policy
import test_platform as platform_fixtures
import test_remote_admission as remote_fixtures
import test_hosted as hosted_fixtures


def selected_policy():
    result = policy()
    result["questions"]["refund"] = result["questions"].pop("flag")
    limits = result["questions"]["refund"]["critical_labels"]["true"]
    limits.update(max_false_negative_rate=1.0, max_false_positive_rate=1.0)
    return result


class BackendErrorPolicyTests(unittest.TestCase):
    def test_remote_study_carries_policy_and_admission_replaces_supplied_claims(self):
        fixture = remote_fixtures.RemoteAdmissionTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        saved = fixture.store.release_policy("A", fixture.project)["policy"]
        fixture.service.configure_release_policy(
            remote_fixtures.OWNER,
            fixture.project,
            0,
            0,
            1,
            10,
            saved["id"],
            error_policy=selected_policy(),
        )
        study = fixture.study()
        request = study["payload"]["request"]
        normalized = request["configuration"]["error_policy"]
        folder, checksum, _ = fixture.export(study)
        data = fixture.store.folder / "jobs" / study["id"] / "remote-study/data"
        request = {
            **request,
            "questions": platform_fixtures.QUESTIONS,
            "request_sha256": study["payload"]["request_sha256"],
        }
        result = qualify(
            folder,
            data,
            checksum,
            request,
            engine_factory=lambda *_: remote_fixtures.FixtureEngine(),
        )
        measured = result["report"]["error_evaluation"]
        self.assertEqual(measured["policy_sha256"], fingerprint(normalized))
        rows = fixture.store.get(fixture.snapshot, "A", fixture.project, "snapshot")[
            "payload"
        ]["splits"]["test"]
        # The independent fixture engine always predicts the first outcome (false).
        expected = (
            100
            * sum(row["gold"]["refund"]["probabilities"]["true"] for row in rows)
            / len(rows)
        )
        self.assertAlmostEqual(
            measured["splits"]["test"]["refund"]["mean_policy_cost"], expected
        )
        self.assertGreater(expected, 0)
        self.assertNotIn("untrusted", result["report"]["test"])
        persisted = json.loads((folder / "report.json").read_text())
        self.assertEqual(persisted["error_evaluation"], measured)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertIn("report.json", manifest["files"])

    def test_hosted_calibrated_fixture_outputs_use_frozen_error_policy(self):
        fixture = hosted_fixtures.HostedTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        saved = fixture.store.release_policy("A", fixture.project)["policy"]
        fixture.service.configure_release_policy(
            hosted_fixtures.OWNER,
            fixture.project,
            0,
            0,
            1,
            10,
            saved["id"],
            error_policy=selected_policy(),
        )
        identifier = fixture.study()
        report = fixture.store.get(identifier, "A", fixture.project, "model")[
            "payload"
        ]["report"]
        self.assertIn("error_evaluation", report)
        self.assertEqual(
            report["error_evaluation"]["splits"]["test"]["refund"]["mean_policy_cost"],
            0,
        )
        self.assertEqual(report["provider_usage"]["calls"], 54)
