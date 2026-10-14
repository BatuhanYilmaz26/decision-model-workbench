"""Single-host scheduled encrypted captures with authenticated receipts and retention."""

from contextlib import contextmanager, closing
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import encrypted_recovery as encrypted
from . import recovery
from .store import encode

FORMAT = "domain-backup-schedule-v1"
OWNER = ".dmt-vault.json"
MAX_EVENTS = 100000
MAX_CATALOG_BYTES = 512 * 1024**2
MAX_VAULT_ENTRIES = 10000
ID = re.compile(r"[a-f0-9]{32}")


def _mac_key(key):
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"domain-decisions/backup-schedule-auth-v1",
    ).derive(key)


def _signed(value, key):
    return {
        **value,
        "hmac_sha256": hmac.new(
            key, encode(value).encode(), hashlib.sha256
        ).hexdigest(),
    }


def _verified(value, key):
    if not isinstance(value, dict) or not isinstance(value.get("hmac_sha256"), str):
        raise ValueError("Backup policy/catalog authentication failed")
    body = {name: item for name, item in value.items() if name != "hmac_sha256"}
    expected = _signed(body, key)["hmac_sha256"]
    if not hmac.compare_digest(value["hmac_sha256"], expected):
        raise ValueError("Backup policy/catalog authentication failed")
    return body


def _hash(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def _ordinary(path):
    path = Path(path).absolute()
    if recovery._linked(path) or path.resolve() != path:
        raise ValueError(
            "Backup paths must not contain linked or redirected components"
        )
    return path


@contextmanager
def _lease(path):
    """Process-owned local file lock, independent of GPU/model job ownership."""
    path = _ordinary(path)
    descriptor = os.open(
        path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600
    )
    with os.fdopen(descriptor, "r+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError(
                "Another backup service or operation owns this local lock"
            ) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _controls(interval, keep_last, timeout, retry, auto_prune):
    if type(interval) is not int or not 10 <= interval <= 31536000:
        raise ValueError("Backup interval must be an integer in [10,31536000] seconds")
    if type(keep_last) is not int or not 1 <= keep_last <= 1000:
        raise ValueError("Retain between 1 and 1000 completed backups")
    if (
        type(timeout) not in (int, float)
        or not 0 < timeout <= 86400
        or not math.isfinite(timeout)
    ):
        raise ValueError("Backup timeout must be within (0,86400] seconds")
    if type(retry) is not int or not 10 <= retry <= interval:
        raise ValueError(
            "Retry interval must be an integer between 10 and the backup interval"
        )
    if type(auto_prune) is not bool:
        raise ValueError("Automatic pruning must be an explicit boolean")


def configure(
    home,
    vault,
    catalog,
    config,
    key_file,
    *,
    interval=86400,
    keep_last=7,
    timeout=900,
    retry=300,
    auto_prune=False,
):
    _controls(interval, keep_last, timeout, retry, auto_prune)
    key = encrypted._key(key_file)
    home, vault, catalog, config = map(_ordinary, (home, vault, catalog, config))
    if not (home / "workspace.sqlite3").is_file() or recovery._linked(
        home / "workspace.sqlite3"
    ):
        raise ValueError("Configure an existing ordinary workspace")
    if config.suffix.lower() != ".dmtplan" or catalog.suffix.lower() != ".sqlite3":
        raise ValueError("Use a private .dmtplan policy and .sqlite3 catalog")
    if (
        vault.is_relative_to(home)
        or home.is_relative_to(vault)
        or catalog.is_relative_to(vault)
        or config.is_relative_to(vault)
        or catalog.is_relative_to(home)
        or config.is_relative_to(home)
        or Path(key_file).resolve().is_relative_to(vault)
        or Path(key_file).resolve().is_relative_to(home)
        or vault in (catalog, config)
    ):
        raise ValueError(
            "Keep vault, catalog/policy and key storage outside the workspace and separate from the vault"
        )
    if vault.exists() or catalog.exists() or config.exists():
        raise ValueError("Vault, catalog and policy must be new paths")
    for path in (vault, catalog, config):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy = {
        "format": FORMAT,
        "id": uuid.uuid4().hex,
        "home": str(home),
        "vault": str(vault),
        "catalog": str(catalog),
        "key_id": encrypted.key_id(key),
        "interval_seconds": interval,
        "keep_last": keep_last,
        "timeout_seconds": float(timeout),
        "retry_seconds": retry,
        "auto_prune": auto_prune,
    }
    signed = _signed(policy, _mac_key(key))
    body = encode(signed).encode()
    if len(body) > 8192:
        raise ValueError("Backup policy exceeds its metadata limit")
    vault.mkdir(mode=0o700, exist_ok=False)
    (vault / OWNER).write_bytes(body)
    # Never remove preexisting paths when a later configuration step fails.
    descriptor = os.open(catalog, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(catalog)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE metadata (body TEXT NOT NULL)")
        db.execute("INSERT INTO metadata VALUES (?)", (body.decode(),))
        db.execute(
            "CREATE TABLE events (sequence INTEGER PRIMARY KEY, body TEXT NOT NULL, sha256 TEXT NOT NULL)"
        )
        db.commit()
    descriptor = os.open(
        config, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "config": str(config),
        "policy_id": policy["id"],
        "policy_sha256": _hash(signed),
        "auto_prune": auto_prune,
    }


def load(config, key_file):
    key = encrypted._key(key_file)
    path = _ordinary(config)
    with path.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("Backup policy exceeds its metadata limit")
    signed = json.loads(raw)
    policy = _verified(signed, _mac_key(key))
    fields = {
        "format",
        "id",
        "home",
        "vault",
        "catalog",
        "key_id",
        "interval_seconds",
        "keep_last",
        "timeout_seconds",
        "retry_seconds",
        "auto_prune",
    }
    if (
        set(policy) != fields
        or policy["format"] != FORMAT
        or not isinstance(policy["id"], str)
        or not ID.fullmatch(policy["id"])
    ):
        raise ValueError("Unsupported backup policy")
    if policy["key_id"] != encrypted.key_id(key):
        raise ValueError("Backup policy requires its original key")
    _controls(
        policy["interval_seconds"],
        policy["keep_last"],
        policy["timeout_seconds"],
        policy["retry_seconds"],
        policy["auto_prune"],
    )
    for field in ("home", "vault", "catalog"):
        if (
            not isinstance(policy[field], str)
            or str(_ordinary(policy[field])) != policy[field]
        ):
            raise ValueError("Backup policy paths changed identity")
    vault = Path(policy["vault"])
    owner_path = _ordinary(vault / OWNER)
    if not owner_path.is_file():
        raise ValueError("Backup vault ownership marker is missing")
    with owner_path.open("rb") as stream:
        owner = stream.read(8193)
    if owner != raw:
        raise ValueError("Backup vault ownership differs from the configured policy")
    return policy, signed, _mac_key(key)


@contextmanager
def _db(policy, readonly=False):
    path = _ordinary(policy["catalog"])
    if not path.is_file() or path.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError("Backup catalog is missing or exceeds its size limit")
    db = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
        uri=True,
        timeout=10,
        isolation_level=None,
    )
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _state(policy, signed, key):
    state = {"head": None, "sequence": 0, "runs": {}}
    with _db(policy, readonly=True) as db:
        metadata = db.execute("SELECT body FROM metadata").fetchall()
        if metadata != [(encode(signed),)]:
            raise ValueError("Backup catalog ownership differs from the policy")
        cursor = db.execute("SELECT sequence,body,sha256 FROM events ORDER BY sequence")
        try:
            for sequence, raw, checksum in cursor:
                if (
                    sequence != state["sequence"] + 1
                    or sequence > MAX_EVENTS
                    or not isinstance(raw, str)
                    or len(raw.encode()) > 4096
                ):
                    raise ValueError("Backup catalog sequence/size validation failed")
                value = json.loads(raw)
                event = _verified(value, key)
                if (
                    set(event)
                    != {
                        "sequence",
                        "previous",
                        "operation",
                        "action",
                        "created",
                        "details",
                    }
                    or type(event["sequence"]) is not int
                    or event["sequence"] != sequence
                    or event["previous"] != state["head"]
                    or _hash(value) != checksum
                    or not isinstance(event["operation"], str)
                    or not ID.fullmatch(event["operation"])
                    or type(event["created"]) not in (int, float)
                    or not 0 <= event["created"] <= 1e12
                    or not isinstance(event["details"], dict)
                ):
                    raise ValueError("Backup catalog event validation failed")
                action, run_id, details = (
                    event["action"],
                    event["operation"],
                    event["details"],
                )
                runs = state["runs"]
                if action == "capture.begin":
                    if run_id in runs:
                        raise ValueError("Duplicate backup operation identity")
                    runs[run_id] = {
                        "id": run_id,
                        "sequence": sequence,
                        "status": "running",
                        "started": event["created"],
                        "receipt": None,
                    }
                else:
                    run = runs.get(run_id)
                    if run is None:
                        raise ValueError("Backup catalog event has no capture")
                    if (
                        action == "capture.prepared"
                        and run["status"] == "running"
                        and run["receipt"] is None
                    ):
                        _receipt(details, policy)
                        run["receipt"] = details
                    elif (
                        action
                        in (
                            "capture.succeeded",
                            "capture.failed",
                            "capture.interrupted",
                        )
                        and run["status"] == "running"
                    ):
                        if action == "capture.succeeded" and run["receipt"] is None:
                            raise ValueError(
                                "Successful backup lacks its durable prepared receipt"
                            )
                        run.update(
                            status=action.split(".")[1], finished=event["created"]
                        )
                    elif action == "prune.begin" and run["status"] == "succeeded":
                        run["status"] = "deleting"
                    elif action == "prune.done" and run["status"] in (
                        "deleting",
                        "delete_failed",
                    ):
                        run["status"] = "deleted"
                    elif action == "prune.failed" and run["status"] in (
                        "deleting",
                        "delete_failed",
                    ):
                        run["status"] = "delete_failed"
                    else:
                        raise ValueError(
                            "Backup catalog contains an invalid state transition"
                        )
                state.update(head=checksum, sequence=sequence)
        finally:
            cursor.close()
    return state


def _receipt(value, policy):
    if set(value) != {
        "encrypted_manifest_sha256",
        "payload_sha256",
        "payload_bytes",
        "key_id",
    }:
        raise ValueError("Backup prepared receipt is malformed")
    encrypted._checksum(value["encrypted_manifest_sha256"])
    encrypted._checksum(value["payload_sha256"])
    if (
        value["key_id"] != policy["key_id"]
        or type(value["payload_bytes"]) is not int
        or not 0 < value["payload_bytes"] <= encrypted.MAX_CIPHER_BYTES
    ):
        raise ValueError("Backup receipt key/size identity differs")


def _append(policy, key, state, operation, action, details=None):
    if state["sequence"] >= MAX_EVENTS:
        raise ValueError(
            "Backup catalog event limit reached; archive and replace the policy"
        )
    event = {
        "sequence": state["sequence"] + 1,
        "previous": state["head"],
        "operation": operation,
        "action": action,
        "created": time.time(),
        "details": details or {},
    }
    signed = _signed(event, key)
    raw, checksum = encode(signed), _hash(signed)
    if len(raw.encode()) > 4096:
        raise ValueError("Backup catalog event exceeds its size limit")
    with _db(policy) as db:
        previous = db.execute(
            "SELECT sequence,sha256 FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if previous != (
            (state["sequence"], state["head"]) if state["sequence"] else None
        ):
            raise ValueError("Backup catalog changed during this operation")
        db.execute(
            "INSERT INTO events VALUES (?,?,?)", (event["sequence"], raw, checksum)
        )
    state.update(sequence=event["sequence"], head=checksum)


def _folder(policy, run_id):
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):
        raise ValueError("Invalid backup operation ID")
    vault = _ordinary(policy["vault"])
    folder = _ordinary(vault / ("run-" + run_id))
    if folder.parent != vault:
        raise ValueError("Backup target escaped its configured vault")
    return folder


def _verify_run(policy, key_file, run):
    return encrypted.verify_encrypted(
        _folder(policy, run["id"]),
        key_file,
        run["receipt"]["encrypted_manifest_sha256"],
        Path(policy["catalog"]).parent,
        policy["timeout_seconds"],
    )


def _recover(policy, signed, key, key_file, state):
    # Owning the operation lock proves the previous synchronous capture no longer owns it.
    for run in list(state["runs"].values()):
        if run["status"] != "running":
            continue
        folder = _folder(policy, run["id"])
        if run["receipt"] is not None and (folder / encrypted.MARKER).is_file():
            try:
                _verify_run(policy, key_file, run)
            except (ValueError, OSError):
                _append(
                    policy,
                    key,
                    state,
                    run["id"],
                    "capture.failed",
                    {"reason": "RecoveredBundleInvalid"},
                )
            else:
                _append(
                    policy,
                    key,
                    state,
                    run["id"],
                    "capture.succeeded",
                    {"recovered": True},
                )
        else:
            _append(policy, key, state, run["id"], "capture.interrupted")
    return _state(policy, signed, key)


def _plan(policy, signed, state):
    successful = sorted(
        (r for r in state["runs"].values() if r["status"] == "succeeded"),
        key=lambda r: r["sequence"],
        reverse=True,
    )
    keep = successful[: policy["keep_last"]]
    remove = successful[policy["keep_last"] :] + [
        r
        for r in state["runs"].values()
        if r["status"] in ("deleting", "delete_failed")
    ]
    unknown = []
    for count, entry in enumerate(Path(policy["vault"]).iterdir(), 1):
        if count > MAX_VAULT_ENTRIES:
            raise ValueError("Backup vault exceeds its entry limit")
        if entry.name == OWNER or entry.name.startswith(".dmt-"):
            continue
        run_id = entry.name[4:] if entry.name.startswith("run-") else ""
        if run_id not in state["runs"] or recovery._linked(entry) or not entry.is_dir():
            unknown.append(entry.name)
        elif state["runs"][run_id]["status"] in (
            "failed",
            "interrupted",
            "deleted",
            "running",
        ):
            unknown.append(entry.name)
    plan = {
        "policy_sha256": _hash(signed),
        "catalog_head": state["head"],
        "keep": [r["id"] for r in keep],
        "remove": [
            {"id": r["id"], "receipt": r["receipt"], "status": r["status"]}
            for r in remove
        ],
        "unmanaged_entries": sorted(unknown),
    }
    return {**plan, "plan_sha256": _hash(plan)}


def status(config, key_file):
    policy, signed, key = load(config, key_file)
    state = _state(policy, signed, key)
    return {
        "policy": policy,
        "policy_sha256": _hash(signed),
        "catalog_head": state["head"],
        "runs": sorted(
            state["runs"].values(), key=lambda r: r["sequence"], reverse=True
        ),
        "retention": _plan(policy, signed, state),
    }


def _delete(policy, key, state, run):
    folder = _folder(policy, run["id"])
    if run["status"] == "succeeded":
        _append(policy, key, state, run["id"], "prune.begin")
    try:
        if folder.exists():
            entries = list(folder.iterdir())
            if len(entries) > 2 or any(
                p.name not in (encrypted.MARKER, encrypted.PAYLOAD)
                or recovery._linked(p)
                or not p.is_file()
                for p in entries
            ):
                raise ValueError("Retention encountered unexpected or linked files")
            for path in entries:
                expected = (
                    run["receipt"]["encrypted_manifest_sha256"]
                    if path.name == encrypted.MARKER
                    else run["receipt"]["payload_sha256"]
                )
                if recovery._sha(path) != expected:
                    raise ValueError("Retention refuses changed backup bytes")
            # Only these known files are unlinked; never recursively delete a directory.
            for path in entries:
                path.unlink()
            folder.rmdir()
        _append(policy, key, state, run["id"], "prune.done")
    except (OSError, ValueError) as exc:
        _append(
            policy,
            key,
            state,
            run["id"],
            "prune.failed",
            {"error_type": type(exc).__name__},
        )
        raise


def _apply_prune(policy, signed, key, key_file, state, expected):
    plan = _plan(policy, signed, state)
    if expected != plan["plan_sha256"]:
        raise ValueError(
            "Retention plan changed; review the current plan before applying"
        )
    if plan["unmanaged_entries"] or not plan["keep"]:
        raise ValueError(
            "Retention requires verified kept backups and a vault without unmanaged entries"
        )
    if (
        state["sequence"]
        + sum(2 if r["status"] == "succeeded" else 1 for r in plan["remove"])
        > MAX_EVENTS
    ):
        raise ValueError(
            "Backup catalog lacks room to record retention; archive and replace the policy"
        )
    for run_id in plan["keep"]:
        _verify_run(policy, key_file, state["runs"][run_id])
    for item in plan["remove"]:
        run = state["runs"][item["id"]]
        if run["status"] == "succeeded":
            _verify_run(policy, key_file, run)
        _delete(policy, key, state, run)
    return {"pruned": [r["id"] for r in plan["remove"]], "kept": plan["keep"]}


def prune(config, key_file, expected):
    policy, signed, key = load(config, key_file)
    with _lease(Path(policy["catalog"] + ".operation.lock")):
        state = _state(policy, signed, key)
        if any(r["status"] == "running" for r in state["runs"].values()):
            raise ValueError(
                "Recover interrupted captures with backup-run before applying retention"
            )
        return _apply_prune(policy, signed, key, key_file, state, expected)


def run_due(config, key_file):
    policy, signed, key = load(config, key_file)
    with _lease(Path(policy["catalog"] + ".operation.lock")):
        state = _recover(policy, signed, key, key_file, _state(policy, signed, key))
        now = time.time()
        terminal = [r for r in state["runs"].values() if "finished" in r]
        latest = max(terminal, key=lambda r: r["sequence"]) if terminal else None
        next_at = (
            latest["finished"]
            + (
                policy["interval_seconds"]
                if latest["status"]
                in ("succeeded", "deleted", "deleting", "delete_failed")
                else policy["retry_seconds"]
            )
            if latest
            else now
        )
        if now < next_at:
            return {"captured": False, "next_at": next_at}
        if state["sequence"] + 3 > MAX_EVENTS:
            raise ValueError(
                "Backup catalog lacks room for a complete capture; archive and replace the policy"
            )
        run_id = uuid.uuid4().hex
        _append(policy, key, state, run_id, "capture.begin")
        folder = _folder(policy, run_id)

        def prepared(receipt):
            _receipt(receipt, policy)
            _append(policy, key, state, run_id, "capture.prepared", receipt)

        try:
            encrypted.backup_encrypted(
                policy["home"],
                folder,
                key_file,
                policy["timeout_seconds"],
                before_publish=prepared,
            )
        except (ValueError, OSError) as exc:
            _append(
                policy,
                key,
                state,
                run_id,
                "capture.failed",
                {"error_type": type(exc).__name__},
            )
            raise
        _append(policy, key, state, run_id, "capture.succeeded")
        completed = _state(policy, signed, key)["runs"][run_id]
        result = {
            "captured": True,
            "run_id": run_id,
            "destination": str(folder),
            "receipt": completed["receipt"],
        }
        if policy["auto_prune"]:
            state = _state(policy, signed, key)
            result["retention"] = _apply_prune(
                policy,
                signed,
                key,
                key_file,
                state,
                _plan(policy, signed, state)["plan_sha256"],
            )
        return result


def serve(config, key_file, *, stop=None, on_result=None):
    policy, _, _ = load(config, key_file)
    stop = stop if stop is not None else threading.Event()
    with _lease(Path(policy["home"]) / ".dmt-backup-service.lock"):
        with _lease(Path(policy["catalog"] + ".service.lock")):
            while not stop.is_set():
                try:
                    result = run_due(config, key_file)
                except (ValueError, OSError, sqlite3.DatabaseError) as exc:
                    if on_result:
                        on_result(
                            {
                                "error_type": type(exc).__name__,
                                "message": "Backup operation failed; inspect the private catalog and operator configuration",
                            }
                        )
                    stop.wait(min(30, policy["retry_seconds"]))
                else:
                    if on_result:
                        on_result(result)
                    stop.wait(
                        min(
                            30,
                            max(
                                0.1,
                                result.get(
                                    "next_at", time.time() + policy["interval_seconds"]
                                )
                                - time.time(),
                            ),
                        )
                    )
