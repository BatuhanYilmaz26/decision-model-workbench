"""Independent, explicitly reviewed applicability negatives for Vega domain gates."""

import hashlib
import json
from pathlib import Path

from decision_workbench.data import dumps, state_key
from .contracts import SPLITS, validate_splits


def validate_gate_splits(positive, negative):
    questions = validate_splits(positive)
    if set(negative) != set(SPLITS) or any(not negative[k] for k in SPLITS):
        raise ValueError("Provide four nonempty applicability-negative roles")
    combined = {}
    for role in SPLITS:
        if not isinstance(negative[role], list):
            raise ValueError("Gate roles contain lists of reviewed source records")
        seen = {r["id"] for r in positive[role]}
        for row in negative[role]:
            if not isinstance(row, dict) or set(row) != {
                "id",
                "group_id",
                "state",
                "negative_for",
                "metadata",
                "reason",
            }:
                raise ValueError(
                    "Gate records need exact applicability-negative fields"
                )
            if (
                any(
                    not isinstance(row[k], str) or not row[k].strip()
                    for k in ("id", "group_id", "reason")
                )
                or len(row["reason"]) > 1000
            ):
                raise ValueError(
                    "Identify the source family and explain why the question is inapplicable"
                )
            if row["id"] in seen:
                raise ValueError("Duplicate positive/negative source ID")
            seen.add(row["id"])
            if not isinstance(row["state"], (str, dict, list)) or row["state"] in (
                "",
                {},
                [],
            ):
                raise ValueError("Provide nonempty source evidence for gate review")
            if len(dumps(row["state"]).encode()) > 64000:
                raise ValueError("Gate states exceed the 64 KB platform bound")
            names = row["negative_for"]
            if (
                not isinstance(names, list)
                or not names
                or any(not isinstance(k, str) for k in names)
                or len(set(names)) != len(names)
                or not set(names) <= set(questions)
            ):
                raise ValueError("Bind applicability negatives to known question IDs")
            meta = row["metadata"]
            if (
                not isinstance(meta, dict)
                or meta.get("origin") != "real"
                or meta.get("reviewed") is not True
                or meta.get("review_status", "approved") != "approved"
            ):
                raise ValueError(
                    "Gate examples must be explicitly reviewed real source records"
                )
            for key in ("semantic_links", "seed_ids"):
                links = meta.get(key, [])
                if (
                    not isinstance(links, list)
                    or any(not isinstance(i, str) or not i for i in links)
                    or len(set(links)) != len(links)
                ):
                    raise ValueError("Malformed gate source relationship")
        if set(questions) != {q for r in negative[role] for q in r["negative_for"]}:
            raise ValueError(
                "Every question needs applicability negatives in every role"
            )
        combined[role] = positive[role] + negative[role]
    for index, left in enumerate(SPLITS):
        a = combined[left]
        for right in SPLITS[index + 1 :]:
            b = combined[right]
            for field in ("id", "group_id"):
                if {r[field] for r in a} & {r[field] for r in b}:
                    raise ValueError(
                        "Positive/negative source-family leakage across roles"
                    )
            if {state_key(r) for r in a} & {state_key(r) for r in b}:
                raise ValueError(
                    "Exact positive/negative evidence leakage across roles"
                )
            aid, bid = {r["id"] for r in a}, {r["id"] for r in b}
            if any(
                set(r["metadata"].get("semantic_links", [])) & bid
                or set(r["metadata"].get("seed_ids", [])) & bid
                for r in a
            ) or any(
                set(r["metadata"].get("semantic_links", [])) & aid
                or set(r["metadata"].get("seed_ids", [])) & aid
                for r in b
            ):
                raise ValueError(
                    "Known gate/positive relatives cross reservation roles"
                )
    # A state cannot be both an applicable labelled example and a negative for that question.
    positives = {state_key(r) for rows in positive.values() for r in rows}
    if any(state_key(r) in positives for rows in negative.values() for r in rows):
        raise ValueError(
            "Applicability annotation contradicts labelled positive evidence"
        )
    from decision_workbench.temporal import audit_splits

    audit_splits(combined)
    return questions


def load_gate_splits(folder, positive):
    rows, hashes = {}, {}
    for role in SPLITS:
        data = (Path(folder) / ("gate_" + role + ".jsonl")).read_bytes()
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("Gate role file exceeds its bounded input size")
        rows[role] = [
            json.loads(line)
            for line in data.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
        if len(rows[role]) > 20000:
            raise ValueError("Gate role contains more than twenty thousand records")
        hashes[role] = hashlib.sha256(data).hexdigest()
    validate_gate_splits(positive, rows)
    return rows, hashes
