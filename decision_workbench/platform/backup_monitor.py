"""Signed minimal backup health; API verification never receives backup decryption keys."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import logging
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import backup_schedule as schedule
from .store import encode

FORMAT = "domain-backup-health-v1"
PROFILE = "domain-backup-monitor-v1"
KEY_MAGIC = b"DMTMON01"
CONTEXT = b"domain-decisions/backup-health-v1\0"
STATES = {
    "not_configured": "Backup monitoring is not configured.",
    "unavailable": "Backup monitoring is unavailable; ask the operator to inspect its publisher and trust configuration.",
    "snapshot_stale": "The backup health publisher has stopped reporting fresh results.",
    "no_success": "No successful backup is recorded by the configured policy.",
    "backup_stale": "The last successful backup exceeds the operator's freshness limit.",
    "backup_missing": "The latest successful backup bundle is missing or failed its basic identity checks.",
    "capture_failed": "The latest backup attempt failed or was interrupted.",
    "capture_overdue": "A backup capture has exceeded its configured operation window.",
    "retention_attention": "Backup retention has unfinished deletion or unmanaged content requiring operator review.",
    "healthy": "The configured backup policy reports a recent successful capture.",
}


def workspace_id(home):
    return hashlib.sha256(
        os.path.normcase(str(Path(home).resolve())).encode()
    ).hexdigest()


def _read(path, limit):
    path = schedule._ordinary(path)
    if not path.is_file():
        raise ValueError("Backup monitor input is missing")
    with path.open("rb") as file:
        body = file.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Backup monitor input exceeds its metadata limit")
    return body


def create_keys(private_file, public_file):
    private, public = map(schedule._ordinary, (private_file, public_file))
    if (
        private == public
        or private.exists()
        or public.exists()
        or private.suffix.lower() != ".dmtkey"
        or public.suffix.lower() != ".dmtpub"
    ):
        raise ValueError(
            "Use new separate .dmtkey signing and .dmtpub verification files"
        )
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    verification = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    outputs = (
        (private, KEY_MAGIC + raw),
        (
            public,
            encode(
                {
                    "format": "domain-backup-monitor-key-v1",
                    "public_key": verification.hex(),
                }
            ).encode(),
        ),
    )
    for path, body in outputs:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as file:
            file.write(body)
            file.flush()
            os.fsync(file.fileno())
    return {"private_file": str(private), "public_file": str(public)}


class Publisher:
    def __init__(self, config, backup_key_file, signing_key_file, output):
        self.config, self.backup_key_file = config, backup_key_file
        self.policy, _, _ = schedule.load(config, backup_key_file)
        raw = _read(signing_key_file, len(KEY_MAGIC) + 32)
        if len(raw) != len(KEY_MAGIC) + 32 or not raw.startswith(KEY_MAGIC):
            raise ValueError(
                "Use a backup monitor signing key created by create-backup-monitor-key"
            )
        self.key = Ed25519PrivateKey.from_private_bytes(raw[len(KEY_MAGIC) :])
        self.output = schedule._ordinary(output)
        protected = {
            Path(config).resolve(),
            Path(backup_key_file).resolve(),
            Path(signing_key_file).resolve(),
            Path(self.policy["catalog"]),
        }
        if (
            not self.output.name.lower().startswith(".dmt-")
            or self.output in protected
            or self.output.is_relative_to(Path(self.policy["home"]))
            or self.output.is_relative_to(Path(self.policy["vault"]))
        ):
            raise ValueError("Use a private .dmt- prefixed health snapshot file")
        self.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def publish(self):
        with self.lock:
            return self._publish()

    def _publish(self):
        payload = {
            "format": FORMAT,
            "policy_id": self.policy["id"],
            "workspace_sha256": workspace_id(self.policy["home"]),
            "generated_at": time.time(),
            "available": False,
            "last_success_at": None,
            "last_attempt": None,
            "retention_attention": False,
            "latest_bundle_available": False,
            "capture_timeout_seconds": self.policy["timeout_seconds"],
        }
        try:
            report = schedule.status(self.config, self.backup_key_file)
            if report["policy"] != self.policy:
                raise ValueError("Backup publisher policy changed")
            runs = report["runs"]
            successful = [
                r
                for r in runs
                if r["status"] in ("succeeded", "deleted", "deleting", "delete_failed")
            ]
            payload["last_success_at"] = max(
                (r["finished"] for r in successful), default=None
            )
            kept = [r for r in runs if r["status"] == "succeeded"]
            if kept:
                try:
                    schedule.encrypted._metadata(
                        schedule._folder(self.policy, kept[0]["id"]),
                        kept[0]["receipt"]["encrypted_manifest_sha256"],
                    )
                    payload["latest_bundle_available"] = True
                except (ValueError, OSError):
                    pass
            if runs:
                run = runs[0]
                payload["last_attempt"] = {
                    "status": run["status"],
                    "started": run["started"],
                    "finished": run.get("finished"),
                }
            payload["retention_attention"] = bool(
                report["retention"]["unmanaged_entries"]
                or any(r["status"] in ("deleting", "delete_failed") for r in runs)
            )
            payload["available"] = True
        except (ValueError, OSError, sqlite3.DatabaseError):
            pass  # Sign unavailability; never publish catalog contents or exception details.
        payload["generated_at"] = time.time()
        signature = self.key.sign(CONTEXT + encode(payload).encode()).hex()
        body = encode({"payload": payload, "signature": signature}).encode()
        if len(body) > 4096:
            raise ValueError("Backup health snapshot exceeds its size limit")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".dmt-health-", dir=self.output.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.output)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"published": True, "available": payload["available"]}

    def start(self, interval=30):
        if (
            type(interval) not in (int, float)
            or not 1 <= interval <= 60
            or not math.isfinite(interval)
        ):
            raise ValueError(
                "Backup health heartbeat interval must be within [1,60] seconds"
            )
        if self.thread is not None:
            raise ValueError("Backup health publisher has already started")
        self.publish()

        def heartbeat():
            while not self.stop.wait(interval):
                try:
                    self.publish()
                except (ValueError, OSError):
                    logging.getLogger(__name__).error(
                        "Backup health publication failed; inspect private storage and publisher configuration"
                    )

        self.thread = threading.Thread(
            target=heartbeat, daemon=True, name="domain-backup-health-publisher"
        )
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                raise RuntimeError("Backup health publisher did not stop")


def _time(value):
    if (
        type(value) not in (int, float)
        or not 0 <= value <= 1e12
        or not math.isfinite(value)
    ):
        raise ValueError("Invalid backup health timestamp")
    return float(value)


class Reader:
    def __init__(self, home, profile_file=None):
        self.home = home
        self.profile_file = (
            profile_file
            if profile_file is not None
            else os.environ.get("DMT_BACKUP_MONITOR_PROFILE", "")
        )
        self.required = bool(self.profile_file)

    def read(self):
        if not self.profile_file:
            return {
                "configured": False,
                "state": "not_configured",
                "healthy": None,
                "required": False,
                "message": STATES["not_configured"],
            }
        required = self.required
        try:
            profile_path = schedule._ordinary(self.profile_file)
            profile = json.loads(_read(profile_path, 8192))
            fields = {
                "format",
                "policy_id",
                "health_file",
                "public_key_file",
                "required",
                "max_snapshot_age_seconds",
                "max_backup_age_seconds",
            }
            if (
                not isinstance(profile, dict)
                or set(profile) != fields
                or profile["format"] != PROFILE
                or type(profile["required"]) is not bool
            ):
                raise ValueError("Invalid backup monitor profile")
            if not isinstance(profile["policy_id"], str) or not schedule.ID.fullmatch(
                profile["policy_id"]
            ):
                raise ValueError("Invalid backup monitor policy identity")
            for field, lower, upper in (
                ("max_snapshot_age_seconds", 1, 86400),
                ("max_backup_age_seconds", 10, 31536000),
            ):
                value = profile[field]
                if type(value) is not int or not lower <= value <= upper:
                    raise ValueError("Invalid backup freshness limit")
            paths = {}
            for field in ("health_file", "public_key_file"):
                if not isinstance(profile[field], str) or not profile[field]:
                    raise ValueError("Invalid backup monitor file path")
                path = Path(profile[field])
                paths[field] = (
                    path if path.is_absolute() else profile_path.parent / path
                )
            required = self.required = profile["required"]
            public = json.loads(_read(paths["public_key_file"], 1024))
            if (
                not isinstance(public, dict)
                or set(public) != {"format", "public_key"}
                or public["format"] != "domain-backup-monitor-key-v1"
                or not isinstance(public["public_key"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", public["public_key"])
            ):
                raise ValueError("Invalid backup monitor public key")
            envelope = json.loads(_read(paths["health_file"], 4096))
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"payload", "signature"}
                or not isinstance(envelope["signature"], str)
                or not re.fullmatch(r"[a-f0-9]{128}", envelope["signature"])
            ):
                raise ValueError("Invalid backup health envelope")
            payload = envelope["payload"]
            Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(public["public_key"])
            ).verify(
                bytes.fromhex(envelope["signature"]), CONTEXT + encode(payload).encode()
            )
            expected = {
                "format",
                "policy_id",
                "workspace_sha256",
                "generated_at",
                "available",
                "last_success_at",
                "last_attempt",
                "retention_attention",
                "latest_bundle_available",
                "capture_timeout_seconds",
            }
            if (
                not isinstance(payload, dict)
                or set(payload) != expected
                or payload["format"] != FORMAT
                or payload["policy_id"] != profile["policy_id"]
                or payload["workspace_sha256"] != workspace_id(self.home)
                or type(payload["available"]) is not bool
                or type(payload["retention_attention"]) is not bool
                or type(payload["latest_bundle_available"]) is not bool
            ):
                raise ValueError(
                    "Backup health snapshot has the wrong identity or format"
                )
            now, generated = time.time(), _time(payload["generated_at"])
            if generated > now + 5:
                raise ValueError("Backup health snapshot is from the future")
            last_success = payload["last_success_at"]
            if last_success is not None and _time(last_success) > generated + 5:
                raise ValueError("Backup success timestamp is inconsistent")
            timeout = payload["capture_timeout_seconds"]
            if (
                type(timeout) not in (int, float)
                or not 0 < timeout <= 86400
                or not math.isfinite(timeout)
            ):
                raise ValueError("Invalid backup capture window")
            attempt = payload["last_attempt"]
            if attempt is not None:
                if (
                    not isinstance(attempt, dict)
                    or set(attempt) != {"status", "started", "finished"}
                    or attempt["status"]
                    not in (
                        "running",
                        "succeeded",
                        "failed",
                        "interrupted",
                        "deleted",
                        "deleting",
                        "delete_failed",
                    )
                ):
                    raise ValueError("Invalid backup attempt summary")
                if _time(attempt["started"]) > generated + 5 or (
                    attempt["finished"] is not None
                    and not _time(attempt["started"])
                    <= _time(attempt["finished"])
                    <= generated + 5
                ):
                    raise ValueError("Backup attempt timestamps are inconsistent")
            if now - generated > profile["max_snapshot_age_seconds"]:
                state = "snapshot_stale"
            elif not payload["available"]:
                state = "unavailable"
            elif attempt and attempt["status"] in ("failed", "interrupted"):
                state = "capture_failed"
            elif (
                attempt
                and attempt["status"] == "running"
                and now - attempt["started"] > timeout
            ):
                state = "capture_overdue"
            elif last_success is None:
                state = "no_success"
            elif not payload["latest_bundle_available"]:
                state = "backup_missing"
            elif now - last_success > profile["max_backup_age_seconds"]:
                state = "backup_stale"
            elif payload["retention_attention"]:
                state = "retention_attention"
            else:
                state = "healthy"
            return {
                "configured": True,
                "state": state,
                "healthy": state == "healthy",
                "required": required,
                "message": STATES[state],
                "last_success_at": last_success,
                "snapshot_age_seconds": max(0, now - generated),
            }
        except (OSError, ValueError, TypeError, KeyError, InvalidSignature):
            return {
                "configured": True,
                "state": "unavailable",
                "healthy": False,
                "required": required,
                "message": STATES["unavailable"],
            }


def configure_monitor(
    config,
    backup_key_file,
    public_key_file,
    health_file,
    output,
    max_snapshot_age=120,
    max_backup_age=90000,
    required=False,
):
    policy, _, _ = schedule.load(config, backup_key_file)
    public_path, health_path, output = map(
        schedule._ordinary, (public_key_file, health_file, output)
    )
    public = json.loads(_read(public_path, 1024))
    if (
        not isinstance(public, dict)
        or set(public) != {"format", "public_key"}
        or public["format"] != "domain-backup-monitor-key-v1"
        or not isinstance(public["public_key"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", public["public_key"])
    ):
        raise ValueError("Use the generated Ed25519 monitor public key file")
    if (
        type(max_snapshot_age) is not int
        or not 1 <= max_snapshot_age <= 86400
        or type(max_backup_age) is not int
        or not 10 <= max_backup_age <= 31536000
        or type(required) is not bool
    ):
        raise ValueError("Invalid monitor age/required settings")
    protected = {
        Path(config).resolve(),
        Path(backup_key_file).resolve(),
        Path(policy["catalog"]),
        public_path,
    }
    if (
        not output.name.lower().startswith(".dmt-")
        or not health_path.name.lower().startswith(".dmt-")
        or output == health_path
        or output in protected
        or health_path in protected
        or output.exists()
        or any(
            path.is_relative_to(Path(policy[field]))
            for path in (output, health_path)
            for field in ("home", "vault")
        )
    ):
        raise ValueError(
            "Use a new .dmt- prefixed monitor profile, separate from snapshot and public key"
        )

    def reference(path):
        return (
            path.relative_to(output.parent).as_posix()
            if path.is_relative_to(output.parent)
            else str(path)
        )

    profile = {
        "format": PROFILE,
        "policy_id": policy["id"],
        "health_file": reference(health_path),
        "public_key_file": reference(public_path),
        "required": required,
        "max_snapshot_age_seconds": max_snapshot_age,
        "max_backup_age_seconds": max_backup_age,
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as file:
        file.write(encode(profile).encode())
        file.flush()
        os.fsync(file.fileno())
    return {"profile": str(output), "policy_id": policy["id"], "required": required}
