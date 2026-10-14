"""Atomic credential replacement retains data and fails on permanent denial."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.platform.credentials import credentials


def sharing_conflict():
    error = PermissionError("Fictional Windows sharing conflict")
    error.winerror = 32
    return error


class CredentialPublicationTests(unittest.TestCase):
    def test_transient_sharing_conflict_preserves_one_atomic_update(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "keys.json"
            first = credentials(target, "provision", "A", "first")
            import os

            replace = os.replace
            observations = []

            def blocked_once(source, destination):
                observations.append(json.loads(target.read_text()))
                if len(observations) == 1:
                    raise sharing_conflict()
                return replace(source, destination)

            with patch(
                "decision_workbench.platform.credentials.os.replace", blocked_once
            ):
                second = credentials(target, "provision", "A", "second")
            self.assertEqual(len(observations), 2)
            self.assertEqual(observations[0], observations[1])
            self.assertEqual(len(observations[0]), 1)
            self.assertEqual(len(json.loads(target.read_text())), 2)
            self.assertNotEqual(first["token"], second["token"])
            self.assertEqual(list(Path(folder).glob(".dmt-keys-*")), [])

    def test_permanent_or_expired_denial_keeps_existing_keys(self):
        for error in (
            PermissionError("Fictional permanent denial"),
            sharing_conflict(),
        ):
            with self.subTest(winerror=getattr(error, "winerror", None)):
                with tempfile.TemporaryDirectory() as folder:
                    target = Path(folder) / "keys.json"
                    credentials(target, "provision", "A", "first")
                    before = target.read_bytes()
                    with patch(
                        "decision_workbench.platform.credentials.os.replace",
                        side_effect=error,
                    ):
                        with self.assertRaises(PermissionError):
                            credentials(target, "provision", "A", "second")
                    self.assertEqual(target.read_bytes(), before)
                    self.assertEqual(list(Path(folder).glob(".dmt-keys-*")), [])
