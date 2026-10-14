"""Pure study gates and validation-only applicability policy selection."""

import math
import hashlib
import numpy as np
from decision_workbench.data import options, state_key


def calibration_partition(positive, negative):
    """Reserve disjoint probability-fit/conformal families using declared relatives."""
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        trail = []
        while parents[key] != key:
            trail.append(key)
            key = parents[key]
        for child in trail:
            parents[child] = key
        return key

    def union(left, right):
        parents[root(left)] = root(right)

    for row in positive + negative:
        key = ("record", row["id"])
        union(key, ("group", row["group_id"]))
        union(key, ("state", state_key(row)))
        for name in ("semantic_links", "seed_ids"):
            for related in row["metadata"].get(name, []):
                union(key, ("record", related))
    groups = {}
    for index, row in enumerate(positive):
        groups.setdefault(root(("record", row["id"])), []).append(index)
    ordered = sorted(
        groups.values(),
        key=lambda indexes: hashlib.sha256(
            "\n".join(sorted(positive[i]["id"] for i in indexes)).encode()
        ).hexdigest(),
    )
    if len(ordered) < 60:
        raise ValueError(
            "Vega needs at least sixty independent calibration families after merging known relatives: thirty each for probability fitting and conformal thresholds"
        )
    middle = len(ordered) // 2
    return {
        "probability_fit": sorted(i for group in ordered[:middle] for i in group),
        "conformal": sorted(i for group in ordered[middle:] for i in group),
        "family_counts": {
            "probability_fit": middle,
            "conformal": len(ordered) - middle,
        },
    }


def validate_settings(args, positive, negative, questions):
    for role, rows in negative.items():
        if any(
            not any(name in row["negative_for"] for row in rows) for name in questions
        ):
            raise ValueError(
                "Every Vega role needs applicability negatives for every question"
            )
    for name, lower, upper in (
        ("epochs", 1, 100),
        ("batch_size", 1, 64),
        ("rank", 1, 64),
        ("max_steps", 0, 100000),
    ):
        value = getattr(args, name)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{name} is outside the bounded training profile")
    for name, lower, upper in (
        ("learning_rate", 1e-7, 0.1),
        ("gate_weight", 0.01, 100),
    ):
        value = getattr(args, name)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not lower <= value <= upper
        ):
            raise ValueError(f"{name} must be finite and inside its training bounds")
    partition = calibration_partition(positive["calibration"], negative["calibration"])
    # Refuse oversized RAM feature caches before downloading/loading any model.
    count = sum(len(rows) * len(questions) for rows in positive.values())
    count += sum(len(row["negative_for"]) for rows in negative.values() for row in rows)
    largest = max(len(options(q)) for q in questions.values())
    estimate = count * (6 + largest + 16) * 1024 * 4
    if estimate > 2 * 1024**3:
        raise ValueError("The frozen-feature cache exceeds the two-GiB study limit")
    return {
        "decisions": count,
        "estimated_feature_bytes": estimate,
        "calibration_partition": partition,
    }


def select_gate_threshold(positive, negative):
    """Maximize validation balanced accuracy; tie-break toward refusing more."""
    pos, neg = np.asarray(positive, dtype=float), np.asarray(negative, dtype=float)
    if pos.ndim != 1 or neg.ndim != 1 or not len(pos) or not len(neg):
        raise ValueError("Gate selection needs positive and negative validation scores")
    all_values = np.concatenate((pos, neg))
    if (
        not np.isfinite(all_values).all()
        or (all_values < 0).any()
        or (all_values > 1).any()
    ):
        raise ValueError("Gate scores must be finite probabilities")
    candidates = np.unique(
        np.concatenate(([0.0, 1.0], all_values, np.nextafter(all_values, np.inf)))
    )
    candidates = candidates[candidates <= 1]
    scores = np.asarray(
        [((pos >= t).mean() + (neg < t).mean()) / 2 for t in candidates]
    )
    threshold = float(candidates[np.flatnonzero(scores == scores.max())[-1]])
    return {
        "threshold": threshold,
        "balanced_accuracy": float(scores.max()),
        "positive_false_closed_rate": float((pos < threshold).mean()),
        "negative_false_open_rate": float((neg >= threshold).mean()),
        "positive_count": len(pos),
        "negative_count": len(neg),
        "selection_role": "validation",
    }


def gate_metrics(scores, applicable, threshold):
    scores = np.asarray(scores, dtype=float)
    if not len(scores) or not np.isfinite(scores).all():
        raise ValueError("Applicability evaluation needs finite nonempty scores")
    opened = scores >= threshold
    return {
        "count": len(scores),
        "open_rate": float(opened.mean()),
        "error_rate": float((~opened if applicable else opened).mean()),
    }
