"""Serialized operator credential changes, published by atomic same-directory replace."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from .auth import read_config, validate_keys, principal, MAX_CONFIG_BYTES
from decision_workbench.training.resources import ResourceLease, ResourceBusy


def publish(temporary, path):
    """Retry transient Windows sharing conflicts while the writer lease is held."""
    deadline = time.monotonic() + 2
    while True:
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def credentials(path, action, tenant=None, actor=None, role=None):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ResourceLease(path.with_name(path.name + ".lock")).hold(timeout=10):
            if action != "provision" and not path.is_file():
                raise ValueError("Operator credential file does not exist")
            existing = validate_keys(read_config(path)) if path.exists() else []
            if action == "list":
                return [
                    {**principal(key), "fingerprint": key["key_sha256"][:12]}
                    for key in existing
                ]
            principal({"tenant": tenant, "actor": actor, "role": role or "admin"})
            matches = [
                key
                for key in existing
                if key["tenant"] == tenant and key["actor"] == actor
            ]
            if action == "provision" and matches:
                raise ValueError(
                    "Actor already provisioned; use rotate-key or revoke-key"
                )
            if action in ("rotate", "revoke") and not matches:
                raise ValueError("Credential principal not found")
            if action == "rotate" and len(matches) != 1:
                raise ValueError(
                    "Ambiguous principal; revoke its keys before reprovisioning"
                )
            token = None
            if action in ("provision", "rotate"):
                token = secrets.token_urlsafe(40)
                entry = {
                    "tenant": tenant,
                    "actor": actor,
                    "role": role or (matches[0]["role"] if matches else "admin"),
                    "key_sha256": hashlib.sha256(token.encode()).hexdigest(),
                }
                updated = [key for key in existing if key not in matches] + [entry]
            elif action == "revoke":
                updated = [key for key in existing if key not in matches]
            else:
                raise ValueError("Unknown credential action")
            validate_keys(updated)
            body = json.dumps(updated, indent=2).encode("utf-8")
            if len(body) > MAX_CONFIG_BYTES:
                raise ValueError(
                    "Credential update exceeds the 1 MiB live configuration limit"
                )
            descriptor, temporary = tempfile.mkstemp(
                prefix=".dmt-keys-", dir=path.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                publish(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {"action": action, "tenant": tenant, "actor": actor, "token": token}
    except ResourceBusy as exc:
        raise ValueError(
            "Another operator is editing this credential file; retry after it finishes"
        ) from exc
