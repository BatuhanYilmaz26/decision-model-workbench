"""Inspectable dataset readiness diagnostics, distinct from domain efficacy."""

from collections import Counter, defaultdict
import math
from .data import dumps, options, state_key, validate


def percentile(values, fraction):
    ordered = sorted(values)
    return (
        ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]
        if ordered
        else 0
    )


def independent_real_families(rows):
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(b)] = find(a)

    groups, states = {}, {}
    by_id = {row["id"]: i for i, row in enumerate(rows)}
    for i, row in enumerate(rows):
        for seen, key in ((groups, row["group_id"]), (states, state_key(row))):
            if key in seen:
                union(i, seen[key])
            else:
                seen[key] = i
        seeds = row["metadata"].get("seed_ids", [])
        if not isinstance(seeds, list) or any(
            not isinstance(seed, str) for seed in seeds
        ):
            raise ValueError("seed_ids must be a list of record identifiers")
        for seed in seeds:
            if seed in by_id:
                union(i, by_id[seed])
    families = defaultdict(list)
    for i, row in enumerate(rows):
        families[find(i)].append(row)
    return sum(
        all(row["metadata"]["origin"] == "real" for row in family)
        for family in families.values()
    )


def quality_report(rows, questions):
    if not rows:
        raise ValueError("A quality report needs records")
    total_rows = len(rows)
    excluded_rows = []
    for row in rows:
        validate(row, allow_missing_labels=True)
        if row["questions"] != questions:
            raise ValueError("Dataset differs from the project schema")
        if row["metadata"].get("review_status") == "rejected":
            excluded_rows.append(row)
    rows = [row for row in rows if row["metadata"].get("review_status") != "rejected"]
    states, groups = defaultdict(list), defaultdict(list)
    lengths, byte_lengths = [], []
    per_question = {
        name: {
            "labeled_rows": 0,
            "missing_rows": 0,
            "reviewed_rows": 0,
            "soft_target_rows": 0,
            "class_counts": {key: 0 for key in options(q)},
            "target_mass": {key: 0.0 for key in options(q)},
        }
        for name, q in questions.items()
    }
    reviewed, missing_rows = 0, 0
    real_groups = set()
    for row in rows:
        validate(row, allow_missing_labels=True)
        if row["questions"] != questions:
            raise ValueError("Dataset differs from the project schema")
        reviewed += int(row["metadata"]["reviewed"])
        missing_rows += int(set(row["gold"]) != set(questions))
        states[state_key(row)].append(row)
        groups[row["group_id"]].append(row["id"])
        if row["metadata"]["origin"] == "real":
            real_groups.add(row["group_id"])
        text = row["state"] if isinstance(row["state"], str) else dumps(row["state"])
        lengths.append(len(text))
        byte_lengths.append(len(text.encode()))
        for name, item in per_question.items():
            target = row["gold"].get(name)
            if target is None:
                item["missing_rows"] += 1
                continue
            probabilities = target["probabilities"]
            item["labeled_rows"] += 1
            item["reviewed_rows"] += int(row["metadata"]["reviewed"])
            item["soft_target_rows"] += int(max(probabilities.values()) < 1 - 1e-6)
            best = max(item["class_counts"], key=lambda key: probabilities[key])
            item["class_counts"][best] += 1
            for key, value in probabilities.items():
                item["target_mass"][key] += value
    duplicates = [rs for rs in states.values() if len(rs) > 1]
    conflicts = [
        rs
        for rs in duplicates
        if any(
            len({dumps(r["gold"][name]) for r in rs if name in r["gold"]}) > 1
            for name in questions
        )
    ]
    real_families = independent_real_families(rows)
    blockers, warnings = [], []
    from .temporal import strategy, times

    temporal_coverage = {
        "real_rows": sum(row["metadata"]["origin"] == "real" for row in rows),
        "timestamped_real_rows": sum(
            row["metadata"]["origin"] == "real" and times(row) is not None
            for row in rows
        ),
    }
    try:
        temporal_coverage["strategy"] = strategy(rows)
    except ValueError as exc:
        temporal_coverage["strategy"] = "incomplete"
        blockers.append(str(exc))
    if not temporal_coverage["timestamped_real_rows"]:
        warnings.append(
            "No declared operational timestamps; temporal leakage has not been checked"
        )
    if not rows:
        blockers.append("No eligible records remain after explicit review exclusions")
    if reviewed != len(rows):
        blockers.append(f"{len(rows)-reviewed} records still need label review")
    if missing_rows:
        blockers.append(f"{missing_rows} records have missing question labels")
    if conflicts:
        blockers.append(
            f"{len(conflicts)} identical-state families have conflicting labels"
        )
    if real_families < 4:
        blockers.append(
            "Fewer than four real families after known group, exact-state and seed links; four independent split families are the format minimum"
        )
    for name, report in per_question.items():
        report["absent_classes"] = [
            key for key, count in report["class_counts"].items() if count == 0
        ]
        report["classes_below_five_examples"] = [
            key for key, count in report["class_counts"].items() if count < 5
        ]
        if report["absent_classes"]:
            warnings.append(name + ": allowed outcomes have no labeled examples")
        if report["classes_below_five_examples"]:
            warnings.append(
                name
                + ": some outcomes have fewer than five examples; this is a collection warning, not a universal quality threshold"
            )
    if duplicates:
        warnings.append(
            "Exact duplicate states will be grouped together; duplicates do not add independent evidence"
        )
    return {
        "rows": total_rows,
        "eligible_rows": len(rows),
        "rejected_rows": len(excluded_rows),
        "rejected_example_ids": [r["id"] for r in excluded_rows[:100]],
        "reviewed_rows": reviewed,
        "rows_with_missing_labels": missing_rows,
        "groups": len(groups),
        "real_source_groups": len(real_groups),
        "real_families_after_known_links": real_families,
        "duplicate_state_families": len(duplicates),
        "duplicate_extra_rows": sum(len(rs) - 1 for rs in duplicates),
        "conflicting_state_families": len(conflicts),
        "duplicate_example_ids": [[r["id"] for r in rs[:10]] for rs in duplicates[:10]],
        "length_characters": {
            "min": min(lengths, default=0),
            "median": percentile(lengths, 0.5),
            "p95": percentile(lengths, 0.95),
            "max": max(lengths, default=0),
        },
        "max_state_bytes": max(byte_lengths, default=0),
        "per_question": per_question,
        "temporal_coverage": temporal_coverage,
        "preparation_blockers": blockers,
        "warnings": warnings,
        "scope": "Dataset-format and annotation readiness; does not prove independent groups, semantic/time decontamination or enterprise domain quality",
    }


def check_class_minimums(splits, questions, criteria):
    """Apply owner thresholds frozen before training; legacy policies preserve their scope."""
    counts = {}
    for split, field in (
        ("train", "min_training_examples_per_class"),
        ("calibration", "min_calibration_examples_per_class"),
    ):
        minimum = criteria.get(field, 0)
        counts[split] = {
            name: {key: 0 for key in options(question)}
            for name, question in questions.items()
        }
        for row in splits[split]:
            validate(row, require_review=True)
            for name, classes in counts[split].items():
                probabilities = row["gold"][name]["probabilities"]
                classes[max(classes, key=lambda key: probabilities[key])] += 1
        for name, classes in counts[split].items():
            insufficient = {
                key: count for key, count in classes.items() if count < minimum
            }
            if insufficient:
                raise ValueError(
                    f"{split}/{name}: frozen data gate requires {minimum} reviewed examples per predominant outcome; observed {insufficient}"
                )
    return counts
