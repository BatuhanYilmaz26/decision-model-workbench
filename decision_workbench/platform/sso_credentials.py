"""Atomic operator revocation of one JWT access-token identifier."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

from decision_workbench.training.resources import ResourceLease, ResourceBusy
from .auth import MAX_CONFIG_BYTES, read_config, validate_sso
from .credentials import publish


def revoke_access_token(path, issuer, jti):
    """Deny a configured issuer/JTI pair without storing the bearer token."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("The trusted SSO configuration file must already exist")
    if not isinstance(jti, str) or not 1 <= len(jti) <= 256:
        raise ValueError("Supply the access token's exact 1-256 character jti")
    try:
        with ResourceLease(path.with_name(path.name + ".lock")).hold(timeout=10):
            config = validate_sso(read_config(path))
            if issuer not in {p["issuer"] for p in config["issuers"]}:
                raise ValueError("Issuer is not configured for this workspace API")
            denied = {"issuer": issuer, "jti": jti}
            records = config.setdefault("revoked_tokens", [])
            if denied not in records:
                records.append(denied)
                validate_sso(config)
                body = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
                if len(body) > MAX_CONFIG_BYTES:
                    raise ValueError(
                        "Revocation exceeds the 1 MiB live configuration limit"
                    )
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".dmt-sso-", dir=path.parent
                )
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    publish(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            return {
                "revoked": True,
                "issuer": issuer,
                "fingerprint": hashlib.sha256(
                    (issuer + "\0" + jti).encode()
                ).hexdigest()[:16],
            }
    except ResourceBusy:
        raise ValueError(
            "Another operator is updating SSO configuration; retry after it finishes"
        ) from None
