"""Explicit four-role reservations with shared family and time-leakage checks."""

from bisect import bisect_right
import copy
import csv
from datetime import timedelta
import hashlib
import io
import json

from .data import dumps, related_groups
from .temporal import ROLES, audit_splits, instant, strategy, times
from .training.contracts import validate_splits

MAX_ROWS = 20000
MAX_CSV_BYTES = 1024 * 1024


def normalize(policy):
    if not isinstance(policy, dict) or policy.get("mode") not in ("groups", "time"):
        raise ValueError("Choose explicit source groups or chronological cutoffs")
    expected = {
        "mode",
        "embargo_seconds",
        "group_roles" if policy["mode"] == "groups" else "cutoffs",
    }
    if set(policy) != expected:
        raise ValueError("Unexpected reservation policy fields")
    embargo = policy["embargo_seconds"]
    if type(embargo) is not int or not 0 <= embargo <= 31 * 86400:
        raise ValueError("Embargo must be an integer from zero to 31 days in seconds")
    result = {"mode": policy["mode"], "embargo_seconds": embargo}
    if policy["mode"] == "groups":
        mapping = policy["group_roles"]
        if (
            not isinstance(mapping, dict)
            or not 1 <= len(mapping) <= MAX_ROWS
            or any(
                not isinstance(key, str)
                or not key.strip()
                or len(key) > 256
                or value not in ROLES
                for key, value in mapping.items()
            )
        ):
            raise ValueError(
                "Assign each real source group to train, validation, calibration or test"
            )
        result["group_roles"] = copy.deepcopy(mapping)
    else:
        cutoffs = policy["cutoffs"]
        if not isinstance(cutoffs, list) or len(cutoffs) != 3:
            raise ValueError(
                "Provide validation, calibration and test start timestamps"
            )
        parsed = [instant(value) for value in cutoffs]
        if not parsed[0] < parsed[1] < parsed[2]:
            raise ValueError("Reservation cutoffs must be strictly increasing")
        result["cutoffs"] = [value.isoformat() for value in parsed]
    return result


def families(rows, related_pairs=None, training_only_ids=None):
    if len(rows) > MAX_ROWS:
        raise ValueError(
            "Explicit reservations support up to 20,000 eligible records; no sampling is applied"
        )
    groups = related_groups(rows, related_pairs=related_pairs)
    forced_ids = set(training_only_ids or [])
    result = []
    for group in groups:
        forced = any(
            r["metadata"]["origin"] == "synthetic" or r["id"] in forced_ids
            for r in group
        )
        result.append((group, forced))
    return result


def inventory(rows, related_pairs=None, training_only_ids=None):
    mode = strategy(rows)
    output = []
    for group, forced in families(rows, related_pairs, training_only_ids):
        identity = hashlib.sha256(
            dumps(sorted(r["id"] for r in group)).encode()
        ).hexdigest()
        dated = [
            t for r in group if r["metadata"]["origin"] == "real" and (t := times(r))
        ]
        for source in sorted(
            {r["group_id"] for r in group if r["metadata"]["origin"] == "real"}
        ):
            if len(source) > 256:
                raise ValueError(
                    "Explicit reservation group identifiers are limited to 256 characters"
                )
            output.append(
                {
                    "group_id": source,
                    "family_id": identity,
                    "family_rows": len(group),
                    "forced_train": forced,
                    "first_decision_at": (
                        min(t["decision_at"] for t in dated).isoformat()
                        if dated
                        else None
                    ),
                    "last_available_at": (
                        max(
                            max(t["decision_at"], t["label_available_at"])
                            for t in dated
                        ).isoformat()
                        if dated
                        else None
                    ),
                }
            )
    return {
        "strategy": mode,
        "groups": sorted(output, key=lambda item: item["group_id"]),
        "row_count": len(rows),
    }


def split(rows, policy, related_pairs=None, training_only_ids=None):
    policy = normalize(policy)
    grouped = families(rows, related_pairs, training_only_ids)
    mode = strategy(rows)
    if (
        policy["mode"] == "time" or policy["embargo_seconds"]
    ) and mode != "chronological":
        raise ValueError(
            "Cutoffs and embargoes require complete operational timestamps"
        )
    if policy["mode"] == "groups":
        real_groups = {r["group_id"] for r in rows if r["metadata"]["origin"] == "real"}
        if set(policy["group_roles"]) != real_groups:
            raise ValueError(
                "Assign exactly every included real source group; missing or extra groups are refused"
            )
    boundaries = [instant(value) for value in policy.get("cutoffs", [])]
    output = {role: [] for role in ROLES}
    for group, forced in grouped:
        real = [r for r in group if r["metadata"]["origin"] == "real"]
        if policy["mode"] == "groups":
            roles = {policy["group_roles"][r["group_id"]] for r in real}
        else:
            roles = {
                ROLES[bisect_right(boundaries, times(r)["decision_at"])] for r in real
            }
        if not real:
            roles = {"train"}
        if len(roles) != 1:
            raise ValueError(
                "Related records or a source family cross assigned roles/cutoffs; keep the whole family together"
            )
        role = roles.pop()
        if forced and role != "train":
            raise ValueError(
                "Synthetic and taxonomy-exposed families must remain training-only"
            )
        output[role].extend(group)
    if any(
        not any(r["metadata"]["origin"] == "real" for r in output[role])
        for role in ROLES
    ):
        raise ValueError(
            "Each role needs at least one independent reviewed real family"
        )
    validate_splits(output)
    audit_splits(output)
    if mode == "chronological":
        for i, role in enumerate(ROLES[:-1]):
            values = [t for r in output[role] if (t := times(r)) is not None]
            edge = max(max(t["decision_at"], t["label_available_at"]) for t in values)
            following = (
                boundaries[i]
                if boundaries
                else min(times(r)["decision_at"] for r in output[ROLES[i + 1]])
            )
            try:
                valid = edge + timedelta(seconds=policy["embargo_seconds"]) < following
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(
                    f"{role} decisions/labels and embargo must end strictly before the next role boundary; no records were discarded"
                )
    return output


def read_group_csv(body):
    if not isinstance(body, bytes) or len(body) > MAX_CSV_BYTES:
        raise ValueError("Group assignment CSV must be at most 1 MiB")
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != ["group_id", "role"]:
            raise ValueError("CSV needs exactly group_id,role columns in that order")
        result = {}
        for row in reader:
            group, role = row.get("group_id"), row.get("role")
            if (
                None in row
                or not group
                or len(group) > 256
                or group in result
                or role not in ROLES
            ):
                raise ValueError("CSV contains a duplicate/invalid group or role")
            result[group] = role
            if len(result) > MAX_ROWS:
                raise ValueError("Too many group assignments")
        if not result:
            raise ValueError("CSV has no assignments")
        return result
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Use a valid UTF-8 group assignment CSV") from exc


def group_csv(groups):
    if any(
        g["group_id"].lstrip()[:1] in ("=", "+", "-", "@")
        or any(c in g["group_id"] for c in "\t\r\n")
        for g in groups
    ):
        raise ValueError(
            "Some identifiers are unsafe to open in a spreadsheet; use the JSON template or page editor"
        )
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["group_id", "role"])
    writer.writerows(
        (g["group_id"], "train" if g["forced_train"] else "") for g in groups
    )
    return stream.getvalue().encode("utf-8")


def read_group_json(body):
    if not isinstance(body, bytes) or len(body) > MAX_CSV_BYTES:
        raise ValueError("Group assignment JSON must be at most 1 MiB")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate group identifiers in JSON")
            result[key] = value
        return result

    try:
        value = json.loads(body.decode("utf-8-sig"), object_pairs_hook=pairs)
        return normalize(
            {"mode": "groups", "embargo_seconds": 0, "group_roles": value}
        )["group_roles"]
    except (UnicodeError, RecursionError) as exc:
        raise ValueError(
            "Use a valid bounded UTF-8 group assignment JSON object"
        ) from exc
