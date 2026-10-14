"""Operator encrypted recovery with bounded authenticated records and fresh targets."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import recovery
from .store import encode

FORMAT = "domain-encrypted-backup-v1"
MARKER = "encrypted-backup.json"
PAYLOAD = "payload.dmtenc"
MAGIC = b"DMTENC01"
KEY_MAGIC = b"DMTKEY01"
HEADER_BYTES = len(MAGIC) + 32 + 32
CHUNK_BYTES = 1024 * 1024
MAX_PLAIN_BYTES = recovery.MAX_BYTES + recovery.MAX_MANIFEST_BYTES + 4
MAX_RECORDS = (MAX_PLAIN_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
MAX_CIPHER_BYTES = HEADER_BYTES + MAX_PLAIN_BYTES + MAX_RECORDS * 21 + 69


def _deadline(timeout):
    if (
        type(timeout) not in (int, float)
        or not 0 < timeout <= 86400
        or not math.isfinite(timeout)
    ):
        raise ValueError("Recovery timeout must be finite and within (0,86400] seconds")
    return time.monotonic() + timeout


def _check(deadline):
    if time.monotonic() > deadline:
        raise ValueError("Encrypted recovery exceeded its operator deadline")


def _checksum(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Provide the separately retained 64-character checksum")
    return value


def _key(path):
    path = Path(path)
    if recovery._linked(path) or not path.is_file():
        raise ValueError("Recovery key must be an ordinary private file")
    with path.open("rb") as stream:
        value = stream.read(len(KEY_MAGIC) + 33)
    if len(value) != len(KEY_MAGIC) + 32 or not value.startswith(KEY_MAGIC):
        raise ValueError("Use a binary .dmtkey created by create-backup-key")
    return value[len(KEY_MAGIC) :]


def key_id(value):
    return hashlib.sha256(value).hexdigest()[:32]


def create_key(destination):
    """Create a random binary key exclusively; never print or overwrite its bytes."""
    path = Path(destination)
    if path.suffix.lower() != ".dmtkey" or recovery._linked(path):
        raise ValueError("Use a new ordinary file with the .dmtkey extension")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            value = os.urandom(32)
            stream.write(KEY_MAGIC + value)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"key_file": str(path.resolve()), "key_id": key_id(value)}


def _cipher(key, salt):
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"domain-decisions/encrypted-backup-v1",
    ).derive(key)
    return AESGCM(derived)


class _Writer:
    def __init__(self, stream, key, checksum, deadline):
        salt = os.urandom(32)
        self.header = MAGIC + salt + bytes.fromhex(checksum)
        self.aes = _cipher(key, salt)
        self.stream, self.deadline = stream, deadline
        self.counter, self.total = 0, 0
        self.digest, self.buffer = hashlib.sha256(), bytearray()
        stream.write(self.header)

    def _record(self, flag, body):
        _check(self.deadline)
        if self.counter > MAX_RECORDS:
            raise ValueError("Encrypted backup record limit exceeded")
        nonce = self.counter.to_bytes(12, "big")
        prefix = struct.pack(">BI", flag, len(body) + 16)
        self.stream.write(prefix)
        self.stream.write(self.aes.encrypt(nonce, body, self.header + nonce + prefix))
        self.counter += 1

    def feed(self, body):
        view = memoryview(body)
        while view:
            count = min(CHUNK_BYTES - len(self.buffer), len(view))
            self.buffer.extend(view[:count])
            view = view[count:]
            if len(self.buffer) == CHUNK_BYTES:
                self._data()

    def _data(self):
        body = bytes(self.buffer)
        self.total += len(body)
        if self.total > MAX_PLAIN_BYTES:
            raise ValueError("Encrypted backup payload exceeds its size limit")
        self.digest.update(body)
        self._record(0, body)
        self.buffer.clear()

    def finish(self):
        if self.buffer:
            self._data()
        self._record(
            1, struct.pack(">QQ", self.counter, self.total) + self.digest.digest()
        )
        self.stream.flush()
        os.fsync(self.stream.fileno())


def _encrypt(plain, output, key, checksum, deadline):
    with (plain / recovery.MANIFEST).open("rb") as manifest_file:
        body = manifest_file.read(recovery.MAX_MANIFEST_BYTES + 1)
    if (
        len(body) > recovery.MAX_MANIFEST_BYTES
        or hashlib.sha256(body).hexdigest() != checksum
    ):
        raise ValueError("Captured backup manifest changed before encryption")
    manifest = json.loads(body)
    with output.open("xb") as stream:
        writer = _Writer(stream, key, checksum, deadline)
        writer.feed(struct.pack(">I", len(body)))
        writer.feed(body)
        for name in sorted(manifest["files"]):
            size, digest = 0, hashlib.sha256()
            with (plain / name).open("rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                    _check(deadline)
                    size += len(chunk)
                    digest.update(chunk)
                    writer.feed(chunk)
            if (
                size != manifest["files"][name]["bytes"]
                or digest.hexdigest() != manifest["files"][name]["sha256"]
            ):
                raise ValueError(
                    "Captured backup file changed during encryption; no bundle was published"
                )
        writer.finish()


def backup_encrypted(home, destination, key_file, timeout=900, *, before_publish=None):
    deadline = _deadline(timeout)
    key = _key(key_file)
    source, destination = recovery._new_target(home, destination)
    with (
        tempfile.TemporaryDirectory(
            prefix=".dmt-plain-capture-", dir=destination.parent
        ) as temporary,
        tempfile.TemporaryDirectory(
            prefix=".dmt-encrypted-", dir=destination.parent
        ) as encrypted_temporary,
    ):
        plain = Path(temporary) / "plain"
        captured = recovery.backup_workspace(source, plain, deadline - time.monotonic())
        output = Path(encrypted_temporary)
        _encrypt(plain, output / PAYLOAD, key, captured["manifest_sha256"], deadline)
        marker = {
            "format": FORMAT,
            "payload": PAYLOAD,
            "key_id": key_id(key),
            "manifest_sha256": captured["manifest_sha256"],
            "payload_sha256": recovery._sha(output / PAYLOAD),
            "payload_bytes": (output / PAYLOAD).stat().st_size,
            "created": time.time(),
        }
        body = encode(marker).encode()
        (output / MARKER).write_bytes(body)
        _check(deadline)
        if before_publish is not None:
            before_publish(
                {
                    "encrypted_manifest_sha256": hashlib.sha256(body).hexdigest(),
                    "key_id": marker["key_id"],
                    "payload_bytes": marker["payload_bytes"],
                    "payload_sha256": marker["payload_sha256"],
                }
            )
            _check(deadline)
        recovery._publish(output, destination, MARKER)
    return {
        "destination": str(destination),
        "encrypted_manifest_sha256": hashlib.sha256(body).hexdigest(),
        "key_id": marker["key_id"],
        "payload_bytes": marker["payload_bytes"],
    }


def _metadata(folder, checksum):
    _checksum(checksum)
    if recovery._linked(Path(folder)):
        raise ValueError("Encrypted backup must not be a linked directory")
    folder = Path(folder).resolve()
    from itertools import islice

    inventory = {p.name: p for p in islice(folder.iterdir(), 3)}
    if set(inventory) != {MARKER, PAYLOAD}:
        raise ValueError(
            "Encrypted backup inventory is incomplete or contains extra files"
        )
    if any(recovery._linked(path) or not path.is_file() for path in inventory.values()):
        raise ValueError("Encrypted backup entries must be ordinary unlinked files")
    path = inventory[MARKER]
    if path.stat().st_size > 4096:
        raise ValueError("Encrypted backup checksum/completion check failed")
    with path.open("rb") as stream:
        body = stream.read(4097)
    if len(body) > 4096:
        raise ValueError("Encrypted backup metadata exceeds its size limit")
    if hashlib.sha256(body).hexdigest() != checksum:
        raise ValueError("Encrypted backup checksum/completion check failed")
    marker = json.loads(body)
    if (
        not isinstance(marker, dict)
        or set(marker)
        != {
            "format",
            "payload",
            "key_id",
            "manifest_sha256",
            "payload_sha256",
            "payload_bytes",
            "created",
        }
        or marker["format"] != FORMAT
        or marker["payload"] != PAYLOAD
        or not isinstance(marker["key_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", marker["key_id"])
        or type(marker["payload_bytes"]) is not int
        or not HEADER_BYTES + 69 < marker["payload_bytes"] <= MAX_CIPHER_BYTES
        or type(marker["created"]) not in (int, float)
        or not 0 <= marker["created"] <= 1e12
        or not math.isfinite(marker["created"])
    ):
        raise ValueError("Unsupported or malformed encrypted backup metadata")
    _checksum(marker["manifest_sha256"])
    _checksum(marker["payload_sha256"])
    if inventory[PAYLOAD].stat().st_size != marker["payload_bytes"]:
        raise ValueError("Encrypted payload checksum/size check failed")
    return folder, marker


def _read(stream, count):
    body = stream.read(count)
    if len(body) != count:
        raise ValueError("Encrypted backup stream is truncated")
    return body


def _decrypt(source, output, key, marker, deadline):
    """Authenticate complete order, length and EOF before interpreting payload files."""
    try:
        with source.open("rb") as stream, output.open("xb") as plain:
            wire_digest, wire_bytes = hashlib.sha256(), 0

            def read(count):
                nonlocal wire_bytes
                body = _read(stream, count)
                wire_digest.update(body)
                wire_bytes += len(body)
                return body

            header = read(HEADER_BYTES)
            if header[: len(MAGIC)] != MAGIC or header[-32:] != bytes.fromhex(
                marker["manifest_sha256"]
            ):
                raise ValueError("Encrypted backup header differs from its manifest")
            aes = _cipher(key, header[len(MAGIC) : len(MAGIC) + 32])
            counter, total, short = 0, 0, False
            digest = hashlib.sha256()
            while True:
                _check(deadline)
                prefix = read(5)
                flag, count = struct.unpack(">BI", prefix)
                if (
                    flag not in (0, 1)
                    or counter > MAX_RECORDS
                    or not 17 <= count <= CHUNK_BYTES + 16
                    or (flag == 1 and count != 64)
                ):
                    raise ValueError("Invalid encrypted backup record framing")
                nonce = counter.to_bytes(12, "big")
                body = aes.decrypt(nonce, read(count), header + nonce + prefix)
                if flag == 1:
                    if (
                        counter == 0
                        or body != struct.pack(">QQ", counter, total) + digest.digest()
                        or stream.read(1)
                    ):
                        raise ValueError(
                            "Encrypted backup footer/order/EOF validation failed"
                        )
                    if (
                        wire_digest.hexdigest() != marker["payload_sha256"]
                        or wire_bytes != marker["payload_bytes"]
                    ):
                        raise ValueError(
                            "Encrypted payload checksum/size differs from its retained manifest"
                        )
                    plain.flush()
                    os.fsync(plain.fileno())
                    return
                if short or total + len(body) > MAX_PLAIN_BYTES:
                    raise ValueError(
                        "Encrypted backup payload is oversized or has a misplaced partial record"
                    )
                short = len(body) < CHUNK_BYTES
                total += len(body)
                digest.update(body)
                plain.write(body)
                counter += 1
    except InvalidTag as exc:
        raise ValueError(
            "Encrypted backup authentication failed; check key and bundle integrity"
        ) from exc


def _unpack(payload, destination, checksum, deadline):
    destination.mkdir(mode=0o700)
    with payload.open("rb") as stream:
        count = struct.unpack(">I", _read(stream, 4))[0]
        if not 1 <= count <= recovery.MAX_MANIFEST_BYTES:
            raise ValueError("Decrypted backup manifest exceeds its size limit")
        body = _read(stream, count)
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError("Decrypted backup manifest checksum differs")
        manifest = json.loads(body)
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if (
            not isinstance(files, dict)
            or not 1 <= len(files) <= recovery.MAX_FILES
            or "workspace.sqlite3" not in files
        ):
            raise ValueError("Invalid decrypted backup file inventory")
        total = 0
        for name, item in files.items():
            recovery._safe_name(name)
            if (
                not isinstance(item, dict)
                or type(item.get("bytes")) is not int
                or item["bytes"] < 0
            ):
                raise ValueError("Invalid decrypted file size")
            _checksum(item.get("sha256"))
            total += item["bytes"]
            if total > recovery.MAX_BYTES:
                raise ValueError("Decrypted workspace exceeds its file-size limit")
        (destination / recovery.MANIFEST).write_bytes(body)
        for name in sorted(files):
            _check(deadline)
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            remaining, digest = files[name]["bytes"], hashlib.sha256()
            with output.open("xb") as target:
                while remaining:
                    _check(deadline)
                    body = _read(stream, min(CHUNK_BYTES, remaining))
                    digest.update(body)
                    target.write(body)
                    remaining -= len(body)
            if digest.hexdigest() != files[name]["sha256"]:
                raise ValueError("Decrypted file checksum differs from its manifest")
        if stream.read(1):
            raise ValueError("Decrypted backup payload contains trailing data")


@contextmanager
def _verified_plain(folder, key_file, checksum, scratch, deadline):
    source, marker = _metadata(folder, checksum)
    _check(deadline)
    key = _key(key_file)
    if key_id(key) != marker["key_id"]:
        raise ValueError("Recovery key identity differs from the encrypted backup")
    payload = scratch / "authenticated-payload"
    _decrypt(source / PAYLOAD, payload, key, marker, deadline)
    plain = scratch / "verified-backup"
    _unpack(payload, plain, marker["manifest_sha256"], deadline)
    manifest = recovery.verify_backup(plain, marker["manifest_sha256"])
    _check(deadline)
    yield plain, marker, manifest


def verify_encrypted(folder, key_file, checksum, scratch_parent=None, timeout=900):
    deadline = _deadline(timeout)
    if scratch_parent is not None and Path(scratch_parent).resolve().is_relative_to(
        Path(folder).resolve()
    ):
        raise ValueError(
            "Verification scratch storage must be outside the encrypted backup"
        )
    with tempfile.TemporaryDirectory(
        prefix=".dmt-verify-encrypted-", dir=scratch_parent
    ) as temporary:
        with _verified_plain(folder, key_file, checksum, Path(temporary), deadline) as (
            _,
            marker,
            manifest,
        ):
            return {
                "verified": True,
                "encrypted_manifest_sha256": checksum,
                "key_id": marker["key_id"],
                "summary": manifest["summary"],
            }


def restore_encrypted(folder, destination, key_file, checksum, timeout=900):
    deadline = _deadline(timeout)
    _, destination = recovery._new_target(folder, destination)
    with tempfile.TemporaryDirectory(
        prefix=".dmt-restore-encrypted-", dir=destination.parent
    ) as temporary:
        with _verified_plain(folder, key_file, checksum, Path(temporary), deadline) as (
            plain,
            marker,
            _,
        ):
            result = recovery.restore_workspace(
                plain,
                destination,
                marker["manifest_sha256"],
                timeout=deadline - time.monotonic(),
            )
    return {**result, "encrypted_manifest_sha256": checksum, "key_id": marker["key_id"]}
