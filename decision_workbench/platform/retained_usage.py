"""Minimal quota reservations retained after their source project is deleted."""

from decimal import Decimal, InvalidOperation
import json
import math
import re

from .store import digest, encode

FORMAT = "domain-retained-usage-v1"
SCOPE = "__retained_usage__"
TYPES = ("hosted", "generation", "cloud", "schema")


def scope_id(kind, identity):
    if kind not in TYPES:
        raise ValueError("Unknown retained usage kind")
    return digest({"kind": kind, "identity": identity})


def checked(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "format",
            "type",
            "scope",
            "estimated_usd",
            "reported_usd",
            "created",
            "sha256",
        }
        or value["format"] != FORMAT
        or value["type"] not in TYPES
        or not isinstance(value["scope"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["scope"])
        or type(value["created"]) not in (int, float)
        or not 0 <= value["created"] <= 1e12
        or not math.isfinite(value["created"])
        or value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"})
    ):
        raise ValueError("Retained usage integrity check failed")
    for name in ("estimated_usd", "reported_usd"):
        price = value[name]
        if price is None:
            continue
        if type(price) not in (str, int, float) or len(str(price)) > 128:
            raise ValueError("Invalid retained usage price")
        try:
            number = Decimal(price)
        except (InvalidOperation, ValueError):
            raise ValueError("Invalid retained usage price") from None
        if not number.is_finite() or not 0 <= number <= Decimal("1e15"):
            raise ValueError("Invalid retained usage price")
    return value


def make(source, kind, identity, estimated=None, reported=None):
    value = {
        "format": FORMAT,
        "type": kind,
        "scope": scope_id(kind, identity),
        "estimated_usd": estimated,
        "reported_usd": reported,
        "created": source["created"],
    }
    value["sha256"] = digest(value)
    checked(value)
    return {
        "id": digest({"retained_usage": source["id"], "tenant": source["tenant"]})[:32],
        "tenant": source["tenant"],
        "payload": value,
    }


def insert(db, rows):
    for row in rows:
        value = checked(row["payload"])
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                row["id"],
                "retained-usage",
                row["tenant"],
                SCOPE,
                encode(value),
                value["created"],
            ),
        )


def count(db, tenant, kind, identity, since):
    where = "tenant=? AND " if tenant is not None else ""
    params = ([tenant] if tenant is not None else []) + [
        kind,
        scope_id(kind, identity),
        since,
    ]
    return db.execute(
        "SELECT count(*) FROM entities WHERE "
        + where
        + "kind='retained-usage' AND json_extract(payload,'$.type')=? AND json_extract(payload,'$.scope')=? AND created>=?",
        params,
    ).fetchone()[0]


def generation_rows(db, tenant, identity, since):
    rows = db.execute(
        "SELECT payload FROM entities WHERE tenant=? AND kind='retained-usage' AND json_extract(payload,'$.type')='generation' AND json_extract(payload,'$.scope')=? AND created>=? LIMIT 101",
        (tenant, scope_id("generation", identity), since),
    ).fetchall()
    result = []
    for row in rows:
        value = checked(json.loads(row["payload"]))
        result.append(
            {
                "payload": encode({"estimate": {"usd": value["estimated_usd"]}}),
                "reported_usd": value["reported_usd"],
            }
        )
    return result


def validate_history(db):
    for row in db.execute(
        "SELECT project,payload,created FROM entities WHERE kind='retained-usage'"
    ):
        value = checked(json.loads(row["payload"]))
        if row["project"] != SCOPE or row["created"] != value["created"]:
            raise ValueError("Retained usage scope/timestamp changed")
