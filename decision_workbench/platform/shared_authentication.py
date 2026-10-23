"""Atomic operator authorization revisions for API nodes sharing PostgreSQL."""

import json
import math
import time
import uuid

from .auth import Authentication, AuthenticationUnavailable, validate_keys, validate_sso
from .store import Conflict, digest, encode

KIND = "workspace-authentication"


def validate(value, namespace):
    if (
        not isinstance(value, dict)
        or set(value)
        != {"workspace", "revision", "keys", "sso", "previous_sha256", "sha256"}
        or value["workspace"] != namespace
        or type(value["revision"]) is not int
        or value["revision"] < 1
        or value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"})
    ):
        raise ValueError("Invalid shared authentication revision")
    validate_keys(value["keys"])
    if value["sso"] is not None:
        validate_sso(value["sso"])
    return value


def publish(store, configuration, expected_revision):
    """Append one operator revision, using the existing global metadata write fence."""
    if store.backend != "postgresql":
        raise ValueError("Shared authentication requires PostgreSQL")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Supply the expected authentication revision")
    keys = validate_keys(configuration.get("operator_keys", []))
    sso = configuration.get("sso")
    if sso is not None:
        validate_sso(sso)
    if "operator_keys" not in configuration and "sso" not in configuration:
        raise ValueError("Publish explicit workspace keys or SSO configuration")
    namespace = store.workspace_id
    with store.transaction() as db:
        previous = db.execute(
            "SELECT payload,created FROM entities WHERE tenant=? AND project=? AND kind=? ORDER BY created DESC,id DESC LIMIT 1",
            (namespace, namespace, KIND),
        ).fetchone()
        old = validate(json.loads(previous["payload"]), namespace) if previous else None
        if old and old["keys"] == keys and old["sso"] == sso:
            return {
                "revision": old["revision"],
                "sha256": old["sha256"],
                "changed": False,
            }
        current = old["revision"] if old else 0
        if current != expected_revision:
            raise Conflict(
                "Authentication revision changed; inspect it before publishing"
            )
        value = {
            "workspace": namespace,
            "revision": current + 1,
            "keys": keys,
            "sso": sso,
            "previous_sha256": old["sha256"] if old else None,
        }
        value["sha256"] = digest(value)
        validate(value, namespace)
        created = (
            max(time.time(), math.nextafter(previous["created"], math.inf))
            if previous
            else time.time()
        )
        db.execute(
            "INSERT INTO entities(id,kind,tenant,project,payload,created) VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, KIND, namespace, namespace, encode(value), created),
        )
    return {"revision": value["revision"], "sha256": value["sha256"], "changed": True}


class SharedAuthentication(Authentication):
    def __init__(self, store):
        if store.backend != "postgresql":
            raise ValueError("Shared authentication requires PostgreSQL")
        self.database = store._postgres
        self.namespace = store.workspace_id
        super().__init__(configuration_reader=self.read)

    def read(self):
        """Read without the write fence, including when reauthorizing inside a write."""
        import psycopg
        from psycopg import sql

        try:
            with psycopg.connect(
                self.database._dsn, connect_timeout=self.database.timeout
            ) as db:
                db.execute("SET TRANSACTION READ ONLY")
                db.execute(
                    "SELECT pg_catalog.set_config('statement_timeout',%s,true)",
                    (str(self.database.timeout * 1000),),
                )
                query = sql.SQL(
                    "SELECT payload FROM {}.entities WHERE tenant=%s AND project=%s AND kind=%s ORDER BY created DESC,id DESC LIMIT 1"
                ).format(sql.Identifier(self.database.schema))
                row = db.execute(
                    query, (self.namespace, self.namespace, KIND)
                ).fetchone()
                if not row:
                    raise ValueError(
                        "Publish an operator authentication revision before starting APIs"
                    )
                return validate(json.loads(row[0]), self.namespace)
        except (psycopg.Error, ValueError, TypeError, KeyError, RecursionError):
            raise AuthenticationUnavailable(
                "Shared authentication configuration is unavailable"
            ) from None
