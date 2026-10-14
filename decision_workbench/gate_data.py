"""Applicability evidence is separate from positive business decision gold."""

import copy
import json
import random
from pathlib import Path

from .data import dumps, state_key
from .intake import check_upload, read_table
from .temporal import audit_splits, times

FIELDS = {"id", "group_id", "state", "negative_for", "metadata", "reason"}
ROLES = ("train", "validation", "calibration", "test")


def validate_record(row, questions, *, require_review=False):
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError(
            "Applicability records need id, group_id, state, negative_for, metadata and reason; no decision gold"
        )
    if (
        any(
            not isinstance(row[k], str) or not row[k].strip()
            for k in ("id", "group_id", "reason")
        )
        or max(len(row["id"]), len(row["group_id"])) > 256
        or len(row["reason"]) > 1000
    ):
        raise ValueError(
            "Identify the source family and explain inapplicability within the text bounds"
        )
    if (
        not isinstance(row["state"], (str, dict, list))
        or not row["state"]
        or len(dumps(row["state"]).encode()) > 64000
    ):
        raise ValueError("Provide nonempty finite applicability evidence within 64 KB")
    names = row["negative_for"]
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(k, str) for k in names)
        or len(set(names)) != len(names)
        or not set(names) <= set(questions)
    ):
        raise ValueError("Bind applicability negatives to known unique question IDs")
    meta = row["metadata"]
    if (
        not isinstance(meta, dict)
        or meta.get("origin") != "real"
        or type(meta.get("reviewed")) is not bool
    ):
        raise ValueError(
            "Applicability examples require real source evidence and a boolean review status"
        )
    status = meta.get("review_status", "approved" if meta["reviewed"] else "pending")
    if (
        status not in ("approved", "pending", "rejected")
        or (status == "approved") != meta["reviewed"]
        or (require_review and not meta["reviewed"])
    ):
        raise ValueError(
            "Applicability evidence needs a consistent authenticated approval before reservation"
        )
    for key in ("semantic_links", "seed_ids"):
        links = meta.get(key, [])
        if (
            not isinstance(links, list)
            or any(
                not isinstance(i, str) or not i.strip() or len(i) > 256 for i in links
            )
            or len(set(links)) != len(links)
            or row["id"] in links
        ):
            raise ValueError("Malformed applicability source relationship")
    times(row)
    if len(dumps(row).encode()) > 128000:
        raise ValueError("Applicability record metadata exceeds the bounded input size")


def read_upload(content, filename, questions, mapping=None):
    check_upload(content, filename)
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in (".json", ".jsonl"):
            if mapping is not None:
                raise ValueError("Column mappings apply to CSV, TSV or XLSX")
            text = content.decode("utf-8-sig")
            rows = (
                json.loads(text)
                if suffix == ".json"
                else [json.loads(line) for line in text.splitlines() if line.strip()]
            )
        elif suffix in (".csv", ".tsv", ".xlsx"):
            mapping = mapping or {
                "id_column": "id",
                "group_column": "group_id",
                "state_column": "state",
                "questions_column": "negative_for",
                "reason_column": "reason",
            }
            if (
                not isinstance(mapping, dict)
                or set(mapping)
                - {
                    "worksheet",
                    "id_column",
                    "group_column",
                    "state_column",
                    "questions_column",
                    "reason_column",
                    "metadata_column",
                }
                or not all(
                    isinstance(mapping.get(k), str) and mapping[k]
                    for k in (
                        "id_column",
                        "group_column",
                        "state_column",
                        "questions_column",
                        "reason_column",
                    )
                )
            ):
                raise ValueError(
                    "Map id, family, state, inapplicable questions and reason columns"
                )
            table = read_table(content, filename, mapping.get("worksheet"))
            columns = [value for key, value in mapping.items() if key != "worksheet"]
            if any(value not in table["columns"] for value in columns):
                raise ValueError("Applicability mapping references a missing column")
            rows = []
            for number, source in table["rows"]:
                try:
                    row = {
                        field: source[mapping[key]]
                        for field, key in (
                            ("id", "id_column"),
                            ("group_id", "group_column"),
                            ("state", "state_column"),
                            ("negative_for", "questions_column"),
                            ("reason", "reason_column"),
                        )
                    }
                    row["negative_for"] = json.loads(row["negative_for"])
                    row["metadata"] = (
                        json.loads(source[mapping["metadata_column"]])
                        if mapping.get("metadata_column")
                        else {"origin": "real"}
                    )
                    rows.append(row)
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(
                        f"Source row {number}: question IDs and optional metadata must be JSON"
                    ) from exc
        else:
            raise ValueError(
                "Applicability uploads support CSV, TSV, XLSX, JSON or JSONL"
            )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Applicability files need UTF-8 and well-formed JSON") from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
        raise ValueError("Provide 1–20,000 applicability records")
    return copy.deepcopy(rows)


def reserve(positive, rows, related=()):
    """Keep known relatives together and respect already immutable positive roles."""
    from .training.vega_contracts import validate_gate_splits

    anchors = [r for role in ROLES for r in positive[role]]
    if len({r["id"] for r in anchors + rows}) != len(anchors) + len(rows):
        raise ValueError(
            "Positive and applicability record identifiers must be distinct"
        )
    parent = {}

    def root(key):
        parent.setdefault(key, key)
        trail = []
        while parent[key] != key:
            trail.append(key)
            key = parent[key]
        for item in trail:
            parent[item] = key
        return key

    def union(a, b):
        parent[root(a)] = root(b)

    included = {r["id"] for r in anchors + rows}
    for row in anchors + rows:
        key = ("id", row["id"])
        union(key, ("group", row["group_id"]))
        union(key, ("state", state_key(row)))
        for kind in ("semantic_links", "seed_ids"):
            for identifier in row["metadata"].get(kind, []):
                if kind == "semantic_links" and identifier not in included:
                    raise ValueError(
                        "A declared semantic relative is absent from this study"
                    )
                union(key, ("id", identifier))
    assigned = {}
    for left, right in related:
        if left not in included or right not in included:
            raise ValueError("A reviewed relationship references missing study data")
        union(("id", left), ("id", right))
    for role in ROLES:
        for row in positive[role]:
            group = root(("id", row["id"]))
            if group in assigned and assigned[group] != role:
                raise ValueError(
                    "Applicability relatives connect different frozen positive roles"
                )
            assigned[group] = role
    groups = {}
    for row in rows:
        groups.setdefault(root(("id", row["id"])), []).append(row)
    mode = audit_splits(positive)["strategy"]
    combined = [*anchors, *rows]
    from .temporal import strategy

    if strategy(combined) != mode:
        raise ValueError(
            "Applicability evidence must preserve the positive study's temporal strategy"
        )
    output = {role: [] for role in ROLES}
    free = []
    for group, records in groups.items():
        if group in assigned:
            output[assigned[group]].extend(records)
        elif mode == "chronological":
            start = min(times(row)["decision_at"] for row in records)
            starts = [
                min(times(row)["decision_at"] for row in positive[role] if times(row))
                for role in ROLES
            ]
            role = max(
                (index for index, boundary in enumerate(starts) if start >= boundary),
                default=0,
            )
            output[ROLES[role]].extend(records)
        else:
            free.append(records)
    free.sort(key=lambda group: min(row["id"] for row in group))
    random.Random(3407).shuffle(free)
    if free:
        counts = [int(len(free) * fraction) for fraction in (0.7, 0.1, 0.1, 0.1)]
        counts[0] += len(free) - sum(counts)
        # Fill empty roles with whole free families first; never move anchored records.
        for index, role in enumerate(ROLES):
            if not output[role] and counts[index] == 0:
                donor = max(range(4), key=lambda i: counts[i])
                if counts[donor] <= 1:
                    raise ValueError(
                        "Collect more independent applicability families for all four roles"
                    )
                counts[donor] -= 1
                counts[index] += 1
        offset = 0
        for role, count in zip(ROLES, counts):
            output[role].extend(
                row for group in free[offset : offset + count] for row in group
            )
            offset += count
    validate_gate_splits(positive, output)
    return output
