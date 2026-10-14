"""Declared point-in-time checks; no inference of undocumented history."""

import re
from datetime import datetime, timezone

FIELDS = ("decision_at", "evidence_available_at", "label_available_at")
STRATEGIES = ("auto", "group-random", "chronological")
ROLES = ("train", "validation", "calibration", "test")


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
        value,
    ):
        raise ValueError(
            "Temporal timestamps need ISO 8601 seconds and an explicit timezone"
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid temporal timestamp") from exc


def times(row):
    """Optional, complete availability declaration for the entire input state."""
    metadata = row["metadata"]
    if "temporal" not in metadata:
        return None
    declaration = metadata["temporal"]
    if not isinstance(declaration, dict) or set(declaration) != set(FIELDS):
        raise ValueError("metadata.temporal requires exactly " + ", ".join(FIELDS))
    values = {field: instant(declaration[field]) for field in FIELDS}
    if values["evidence_available_at"] > values["decision_at"]:
        raise ValueError("Input evidence became available after the decision")
    return values


def strategy(rows, requested="auto"):
    if requested not in STRATEGIES:
        raise ValueError("Choose auto, group-random or chronological splitting")
    real = [row for row in rows if row["metadata"]["origin"] == "real"]
    declared = sum(times(row) is not None for row in real)
    if declared and declared != len(real):
        raise ValueError(
            "Temporal reservations require timestamps on every real record"
        )
    if requested == "chronological" and (not real or not declared):
        raise ValueError(
            "Chronological reservations require timestamps on every real record"
        )
    if requested == "group-random" and declared:
        raise ValueError("Timestamped records require chronological reservations")
    return "chronological" if declared else "group-random"


def chronological_groups(groups):
    """Join overlapping decision/label-availability intervals without splitting families."""
    intervals = []
    for group in groups:
        values = [times(row) for row in group]
        start = min(value["decision_at"] for value in values)
        end = max(
            max(value["decision_at"], value["label_available_at"]) for value in values
        )
        intervals.append((start, end, group))
    intervals.sort(key=lambda item: (item[0], min(row["id"] for row in item[2])))
    output, latest = [], None
    for start, end, group in intervals:
        if latest is not None and start <= latest:
            output[-1].extend(group)
            latest = max(latest, end)
        else:
            output.append(list(group))
            latest = end
    return output


def audit_splits(splits):
    """Validate declared chronology each time a reserved snapshot is consumed."""
    rows = [row for name in ROLES for row in splits[name]]
    mode = strategy(rows)
    report = {
        "protocol": "declared-temporal-v1",
        "strategy": mode,
        "real_rows": sum(row["metadata"]["origin"] == "real" for row in rows),
        "timestamped_real_rows": sum(
            row["metadata"]["origin"] == "real" and times(row) is not None
            for row in rows
        ),
        "ranges": {},
        "scope": "Declared state availability and ordered role boundaries; not semantic decontamination or proof of truthful timestamps",
    }
    if mode == "group-random":
        return report
    previous_end, previous_role = None, None
    for name in ROLES:
        # Synthetic generation dates are not operational decision dates. Only
        # explicitly declared synthetic decision windows participate in this check.
        values = [value for row in splits[name] if (value := times(row)) is not None]
        if not values:
            raise ValueError(
                "Chronological reservations require dated records in every role"
            )
        start = min(value["decision_at"] for value in values)
        end = max(
            max(value["decision_at"], value["label_available_at"]) for value in values
        )
        if previous_end is not None and previous_end >= start:
            raise ValueError(
                f"Temporal leakage between {previous_role} and {name}: decisions and labels must precede the next role"
            )
        report["ranges"][name] = {
            "first_decision_at": start.isoformat(),
            "last_decision_at": max(
                value["decision_at"] for value in values
            ).isoformat(),
            "last_label_available_at": max(
                value["label_available_at"] for value in values
            ).isoformat(),
            "dated_rows": len(values),
            "undated_synthetic_rows": len(splits[name]) - len(values),
        }
        previous_end, previous_role = end, name
    return report
