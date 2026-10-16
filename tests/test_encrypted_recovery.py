"""Real encrypted recovery, authenticated framing and adversarial publication refusal."""

import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import tracemalloc
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform import encrypted_recovery as encrypted
from decision_workbench.platform import recovery
from decision_workbench.platform.store import encode, Store
import test_recovery as fixtures
from test_platform import keys, QUESTIONS, fixture_rows


class EncryptedRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.RecoveryTests.setUp(self)
        self.key = self.folder / "private.dmtkey"
        encrypted.create_key(self.key)

    tearDown = fixtures.RecoveryTests.tearDown
    train = fixtures.RecoveryTests.train

    def capture(self, name="encrypted"):
        return encrypted.backup_encrypted(self.home, self.folder / name, self.key)

    def reseal(self, folder):
        marker = json.loads((folder / encrypted.MARKER).read_bytes())
        marker["payload_sha256"] = recovery._sha(folder / encrypted.PAYLOAD)
        marker["payload_bytes"] = (folder / encrypted.PAYLOAD).stat().st_size
        body = encode(marker).encode()
        (folder / encrypted.MARKER).write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    def test_real_cpu_release_predictions_review_history_and_unfinished_job_fencing(
        self,
    ):
        model = self.train()
        state = "invoice payment routine"
        expected = self.service.predict(self.owner, self.project, state, 0.8)
        other_owner = {"tenant": "B", "actor": "other-owner", "role": "admin"}
        other_project = self.service.create_project(
            other_owner,
            "Other fictional tenant",
            "Independent fictional routing policy",
            QUESTIONS,
        )["id"]
        other_dataset = self.service.save_dataset(
            other_owner, other_project, fixture_rows(2), {"fixture": True}
        )
        queued = self.service.submit(
            self.owner,
            self.project,
            "prepare",
            {"datasets": [self.root["id"]]},
            "encrypted-copied-queue",
        )
        captured = self.capture()
        bundle = Path(captured["destination"])
        self.assertEqual(
            set(p.name for p in bundle.iterdir()), {encrypted.MARKER, encrypted.PAYLOAD}
        )
        self.assertNotIn(
            b"invoice payment routine", (bundle / encrypted.PAYLOAD).read_bytes()
        )
        verified = encrypted.verify_encrypted(
            bundle, self.key, captured["encrypted_manifest_sha256"], self.folder
        )
        self.assertTrue(verified["verified"])
        destination = self.folder / "recovered"
        result = encrypted.restore_encrypted(
            bundle, destination, self.key, captured["encrypted_manifest_sha256"]
        )
        self.assertEqual(result["restored_schema"], 8)
        restored = Store(destination)
        self.assertEqual(restored.active("A", self.project)["model_id"], model)
        self.assertEqual(
            restored.job(queued["id"], "A", self.project)["status"], "interrupted"
        )
        self.assertEqual(
            restored.verify_dataset(self.root["id"], "A", self.project),
            self.store.verify_dataset(self.root["id"], "A", self.project),
        )
        self.assertEqual(
            restored.verify_dataset(other_dataset["id"], "B", other_project),
            self.store.verify_dataset(other_dataset["id"], "B", other_project),
        )
        app = create_app(destination, keys())
        with TestClient(app) as client:
            response = client.post(
                f"/v1/projects/{self.project}/decisions",
                headers={"Authorization": "Bearer a-secret"},
                json={"state": state, "threshold": 0.8},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(
                client.get(
                    f"/v1/projects/{other_project}",
                    headers={"Authorization": "Bearer a-secret"},
                ).status_code,
                404,
            )
        self.assertEqual(
            self.store.job(queued["id"], "A", self.project)["status"], "queued"
        )
        self.assertFalse(list(self.folder.glob(".dmt-*-encrypted-*")))

    def test_unique_capture_keys_and_wrong_key_or_checksum_refuse_without_publishing(
        self,
    ):
        first, second = self.capture("first"), self.capture("second")
        headers = [
            (Path(item["destination"]) / encrypted.PAYLOAD).read_bytes()[
                : encrypted.HEADER_BYTES
            ]
            for item in (first, second)
        ]
        self.assertNotEqual(headers[0][8:40], headers[1][8:40])
        other = self.folder / "other.dmtkey"
        encrypted.create_key(other)
        for key, checksum in (
            (other, first["encrypted_manifest_sha256"]),
            (self.key, "0" * 64),
        ):
            target = self.folder / "refused"
            with self.assertRaises(ValueError):
                encrypted.restore_encrypted(first["destination"], target, key, checksum)
            self.assertFalse(target.exists())

    def test_bitflip_salt_truncation_extra_data_and_absent_footer_never_parse_plaintext(
        self,
    ):
        captured = self.capture()
        original = (Path(captured["destination"]) / encrypted.PAYLOAD).read_bytes()
        for defect in (
            "cipher-bit",
            "salt-bit",
            "truncated",
            "no-footer",
            "trailing",
            "large-record",
        ):
            bundle = self.folder / defect
            shutil.copytree(captured["destination"], bundle)
            body = bytearray(original)
            if defect == "cipher-bit":
                body[encrypted.HEADER_BYTES + 16] ^= 1
            elif defect == "salt-bit":
                body[12] ^= 1
            elif defect == "truncated":
                body = body[:-1]
            elif defect == "no-footer":
                body = body[:-69]
            elif defect == "trailing":
                body += b"extra"
            else:
                body[encrypted.HEADER_BYTES : encrypted.HEADER_BYTES + 5] = struct.pack(
                    ">BI", 0, 2**32 - 1
                )
            (bundle / encrypted.PAYLOAD).write_bytes(body)
            checksum = self.reseal(bundle)
            with (
                self.subTest(defect=defect),
                patch.object(
                    encrypted,
                    "_unpack",
                    side_effect=AssertionError("Unvalidated stream interpreted"),
                ),
            ):
                with self.assertRaises(ValueError):
                    encrypted.restore_encrypted(
                        bundle, self.folder / (defect + "-target"), self.key, checksum
                    )
            self.assertFalse((self.folder / (defect + "-target")).exists())

    def test_extra_entries_and_preexisting_restore_target_are_refused(self):
        captured = self.capture()
        bundle = Path(captured["destination"])
        (bundle / "extra").mkdir()
        with self.assertRaises(ValueError):
            encrypted.verify_encrypted(
                bundle, self.key, captured["encrypted_manifest_sha256"], self.folder
            )
        (bundle / "extra").rmdir()
        target = self.folder / "occupied"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("User content remains")
        with self.assertRaises(ValueError):
            encrypted.restore_encrypted(
                bundle, target, self.key, captured["encrypted_manifest_sha256"]
            )
        self.assertEqual(sentinel.read_text(), "User content remains")
        with self.assertRaises(ValueError):
            encrypted.verify_encrypted(
                bundle, self.key, captured["encrypted_manifest_sha256"], bundle
            )

    def test_authenticated_malicious_inventory_cannot_escape_scratch(self):
        bundle = self.folder / "malicious"
        bundle.mkdir()
        empty = hashlib.sha256(b"").hexdigest()
        manifest = {
            "files": {
                "workspace.sqlite3": {"bytes": 0, "sha256": empty},
                "../escape": {"bytes": 0, "sha256": empty},
            }
        }
        body = encode(manifest).encode()
        checksum = hashlib.sha256(body).hexdigest()
        key = encrypted._key(self.key)
        with (bundle / encrypted.PAYLOAD).open("wb") as stream:
            writer = encrypted._Writer(stream, key, checksum, time.monotonic() + 30)
            writer.feed(struct.pack(">I", len(body)) + body)
            writer.finish()
        marker = {
            "format": encrypted.FORMAT,
            "payload": encrypted.PAYLOAD,
            "key_id": encrypted.key_id(key),
            "manifest_sha256": checksum,
            "payload_sha256": recovery._sha(bundle / encrypted.PAYLOAD),
            "payload_bytes": (bundle / encrypted.PAYLOAD).stat().st_size,
            "created": time.time(),
        }
        body = encode(marker).encode()
        (bundle / encrypted.MARKER).write_bytes(body)
        with self.assertRaises(ValueError):
            encrypted.restore_encrypted(
                bundle,
                self.folder / "unsafe-target",
                self.key,
                hashlib.sha256(body).hexdigest(),
            )
        self.assertFalse((self.folder / "escape").exists())
        self.assertFalse((self.folder / "unsafe-target").exists())

    def test_large_stream_bounded_python_memory_and_reordered_chunk_authentication(
        self,
    ):
        source, target = self.folder / "large.dmtenc", self.folder / "large-plain"
        key = encrypted._key(self.key)
        manifest_checksum = "a" * 64
        block = b"Fictional bounded stream evidence\n" * 8192
        digest = hashlib.sha256()
        total = 0
        tracemalloc.start()
        try:
            with source.open("wb") as stream:
                writer = encrypted._Writer(
                    stream, key, manifest_checksum, time.monotonic() + 60
                )
                for _ in range(256):
                    writer.feed(block)
                    digest.update(block)
                    total += len(block)
                writer.finish()
            marker = {
                "manifest_sha256": manifest_checksum,
                "payload_sha256": recovery._sha(source),
                "payload_bytes": source.stat().st_size,
            }
            encrypted._decrypt(source, target, key, marker, time.monotonic() + 60)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertGreater(total, 64 * 1024**2)
        self.assertLess(peak, 16 * 1024**2)
        self.assertEqual(target.stat().st_size, total)
        self.assertEqual(recovery._sha(target), digest.hexdigest())
        record_size = 5 + encrypted.CHUNK_BYTES + 16
        with source.open("r+b") as stream:
            stream.seek(encrypted.HEADER_BYTES)
            first, second = stream.read(record_size), stream.read(record_size)
            stream.seek(encrypted.HEADER_BYTES)
            stream.write(second + first)
        marker.update(payload_sha256=recovery._sha(source))
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            encrypted._decrypt(
                source,
                self.folder / "reorder-refused",
                key,
                marker,
                time.monotonic() + 60,
            )

    def test_restore_deadline_and_failed_encryption_never_publish(self):
        plain = recovery.backup_workspace(self.home, self.folder / "plain")
        with self.assertRaisesRegex(ValueError, "deadline"):
            recovery.restore_workspace(
                plain["destination"],
                self.folder / "too-late",
                plain["manifest_sha256"],
                timeout=1e-12,
            )
        self.assertFalse((self.folder / "too-late").exists())
        with patch.object(
            encrypted._Writer, "finish", side_effect=OSError("Fictional disk failure")
        ):
            with self.assertRaises(OSError):
                self.capture("failed")
        self.assertFalse((self.folder / "failed").exists())
        self.assertFalse(list(self.folder.glob(".dmt-encrypted-*")))

    def test_key_creation_is_exclusive_private_binary_and_cli_never_prints_secret(self):
        original = self.key.read_bytes()
        self.assertTrue(original.startswith(encrypted.KEY_MAGIC))
        with self.assertRaises(FileExistsError):
            encrypted.create_key(self.key)
        self.assertEqual(self.key.read_bytes(), original)
        destination = self.folder / "cli-key.dmtkey"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.cli",
                "create-backup-key",
                "--output",
                str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(encrypted._key(destination).hex(), result.stdout)
        for value in (b"weak password", b"x" * 32):
            invalid = self.folder / "invalid.dmtkey"
            invalid.write_bytes(value)
            with self.assertRaises(ValueError):
                encrypted._key(invalid)

    def test_cli_capture_verify_restore_and_wrong_key_fail_with_no_target(self):
        def run(*args):
            return subprocess.run(
                [sys.executable, "-m", "decision_workbench.platform.cli", *args],
                capture_output=True,
                text=True,
                timeout=60,
            )

        capture = run(
            "backup-encrypted",
            "--home",
            str(self.home),
            "--output",
            str(self.folder / "cli-backup"),
            "--key-file",
            str(self.key),
        )
        self.assertEqual(capture.returncode, 0, capture.stderr)
        checksum = json.loads(capture.stdout)["encrypted_manifest_sha256"]
        common = (
            "--backup",
            str(self.folder / "cli-backup"),
            "--key-file",
            str(self.key),
            "--encrypted-manifest-sha256",
            checksum,
        )
        verified = run(
            "verify-encrypted-backup", *common, "--scratch-parent", str(self.folder)
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        restored = run(
            "restore-encrypted", *common, "--home", str(self.folder / "cli-restored")
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["restored_schema"], 8)
        bad = run(
            "restore-encrypted",
            *common[:-1],
            "0" * 64,
            "--home",
            str(self.folder / "cli-refused"),
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse((self.folder / "cli-refused").exists())

    def test_completion_marker_failure_cleans_exclusively_reserved_destination(self):
        original = Path.rename
        before = recovery._sha(self.store.database)

        def interrupted(path, destination):
            if path.name == encrypted.MARKER:
                raise OSError("Fictional completion-marker publication failure")
            return original(path, destination)

        with patch.object(Path, "rename", interrupted):
            with self.assertRaises(OSError):
                self.capture("unpublished")
        self.assertFalse((self.folder / "unpublished").exists())
        self.assertEqual(recovery._sha(self.store.database), before)
        self.assertFalse(list(self.folder.glob(".dmt-encrypted-*")))
        self.assertFalse(list(self.folder.glob(".dmt-plain-capture-*")))

    def test_ciphertext_replacement_is_refused_even_if_same_plaintext_authenticates(
        self,
    ):
        key = encrypted._key(self.key)
        paths = [self.folder / "first.dmtenc", self.folder / "replacement.dmtenc"]
        for path in paths:
            with path.open("wb") as stream:
                writer = encrypted._Writer(stream, key, "a" * 64, time.monotonic() + 30)
                writer.feed(b"Identical fictional payload")
                writer.finish()
        marker = {
            "manifest_sha256": "a" * 64,
            "payload_sha256": recovery._sha(paths[0]),
            "payload_bytes": paths[0].stat().st_size,
        }
        with self.assertRaisesRegex(ValueError, "retained manifest"):
            encrypted._decrypt(
                paths[1],
                self.folder / "refused-plaintext",
                key,
                marker,
                time.monotonic() + 30,
            )

    def test_capture_mutation_before_encryption_refuses_published_bundle(self):
        original = encrypted._encrypt
        before = recovery._sha(self.store.database)

        def changed_capture(plain, *args):
            with (plain / "workspace.sqlite3").open("r+b") as stream:
                stream.seek(100)
                value = stream.read(1)
                stream.seek(100)
                stream.write(bytes([value[0] ^ 1]))
            return original(plain, *args)

        with patch.object(encrypted, "_encrypt", changed_capture):
            with self.assertRaisesRegex(ValueError, "changed during encryption"):
                self.capture("changed-capture")
        self.assertFalse((self.folder / "changed-capture").exists())
        self.assertEqual(recovery._sha(self.store.database), before)
