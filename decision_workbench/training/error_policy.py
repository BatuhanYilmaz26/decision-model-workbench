"""Frozen domain error costs and critical-label gates from exported-runtime metrics."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from decision_workbench.data import dumps, options, validate_questions

FORMAT = "domain-error-policy-v1"
EVALUATION = "domain-error-evaluation-v1"
MAX_POLICY_BYTES = 65536


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def number(value, maximum, name):
    if type(value) not in (int, float):
        raise ValueError(name + " must be a finite nonnegative number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(name + " exceeds its numeric limit") from exc
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(name + " is outside its allowed range")
    return value


def normalize_policy(policy, questions):
    validate_questions(questions)
    if (
        not isinstance(policy, dict)
        or set(policy) != {"format", "questions"}
        or policy["format"] != FORMAT
    ):
        raise ValueError("Use the domain-error-policy-v1 format")
    definitions = policy["questions"]
    if (
        not isinstance(definitions, dict)
        or not definitions
        or not set(definitions) <= set(questions)
    ):
        raise ValueError("Error policy must name existing project questions")
    if len(dumps(policy).encode()) > MAX_POLICY_BYTES:
        raise ValueError("Error policy exceeds 64 KB")
    result = {"format": FORMAT, "questions": {}}
    for name, definition in definitions.items():
        if (
            not isinstance(definition, dict)
            or not definition
            or set(definition)
            - {"cost_unit", "costs", "max_mean_cost", "critical_labels"}
        ):
            raise ValueError(name + ": unsupported error-policy fields")
        keys = options(questions[name])
        entry = {}
        cost_fields = {"cost_unit", "costs", "max_mean_cost"}
        if cost_fields & set(definition):
            if not cost_fields <= set(definition):
                raise ValueError(
                    name
                    + ": provide cost_unit, complete costs and max_mean_cost together"
                )
            unit = definition["cost_unit"]
            if (
                not isinstance(unit, str)
                or not 1 <= len(unit.strip()) <= 64
                or any(ord(c) < 32 for c in unit)
            ):
                raise ValueError(
                    name + ": give a plain-text cost unit up to 64 characters"
                )
            costs = definition["costs"]
            if not isinstance(costs, dict) or set(costs) != set(keys):
                raise ValueError(name + ": cost rows must cover every true label")
            normalized = {}
            for true_label in keys:
                row = costs[true_label]
                if not isinstance(row, dict) or set(row) != set(keys):
                    raise ValueError(
                        name + ": cost columns must cover every predicted label"
                    )
                normalized[true_label] = {
                    pred: number(row[pred], 1e12, name + " cost") for pred in keys
                }
                if normalized[true_label][true_label] != 0:
                    raise ValueError(
                        name + ": correct decisions must have zero error cost"
                    )
            entry.update(
                cost_unit=unit.strip(),
                costs=normalized,
                max_mean_cost=number(
                    definition["max_mean_cost"], 1e12, name + " mean cost"
                ),
            )
        if "critical_labels" in definition:
            critical = definition["critical_labels"]
            if (
                not isinstance(critical, dict)
                or not critical
                or not set(critical) <= set(keys)
            ):
                raise ValueError(
                    name + ": critical labels must be nonempty allowed outcomes"
                )
            entry["critical_labels"] = {}
            required = {
                "max_false_negative_rate",
                "max_false_positive_rate",
                "min_positive_rows",
                "min_negative_rows",
            }
            for label, limits in critical.items():
                if (
                    not isinstance(limits, dict)
                    or not required <= set(limits)
                    or set(limits) - required - {"rate_bound"}
                ):
                    raise ValueError(
                        name
                        + ": critical labels need error-rate limits and positive/negative minimums"
                    )
                bounds = {
                    key: number(limits[key], 1, name + " " + key)
                    for key in ("max_false_negative_rate", "max_false_positive_rate")
                }
                for key in ("min_positive_rows", "min_negative_rows"):
                    if type(limits[key]) is not int or not 1 <= limits[key] <= 20000:
                        raise ValueError(
                            name
                            + ": critical sample minimums must be integers in [1,20000]"
                        )
                    bounds[key] = limits[key]
                bounds["rate_bound"] = limits.get("rate_bound", "wilson_95_upper")
                if bounds["rate_bound"] not in ("observed", "wilson_95_upper"):
                    raise ValueError(
                        name + ": use observed or wilson_95_upper rate bounds"
                    )
                entry["critical_labels"][label] = bounds
        result["questions"][name] = entry
    if len(dumps(result).encode()) > MAX_POLICY_BYTES:
        raise ValueError("Normalized error policy exceeds 64 KB")
    return result


def validated_metrics(metrics, question):
    """Do not infer hard labels or expected costs from older incomplete reports."""
    keys = metrics.get("labels") if isinstance(metrics, dict) else None
    expected = options(question)
    count = metrics.get("count") if isinstance(metrics, dict) else None
    soft = metrics.get("soft_gold_rows") if isinstance(metrics, dict) else None
    if (
        not isinstance(keys, list)
        or any(not isinstance(key, str) for key in keys)
        or len(keys) != len(expected)
        or set(keys) != set(expected)
        or type(count) is not int
        or not 1 <= count <= 2**31 - 1
        or type(soft) is not int
        or not 0 <= soft <= count
    ):
        raise ValueError(
            "Error evaluation requires complete label/count/soft-gold metadata"
        )
    confusion, mass = metrics.get("confusion"), metrics.get("gold_mass_confusion")
    if (
        not isinstance(confusion, list)
        or not isinstance(mass, list)
        or len(confusion) != len(keys)
        or len(mass) != len(keys)
    ):
        raise ValueError(
            "Error evaluation requires hard and gold-mass confusion matrices"
        )
    for hard_row, mass_row in zip(confusion, mass):
        if (
            not isinstance(hard_row, list)
            or not isinstance(mass_row, list)
            or len(hard_row) != len(keys)
            or len(mass_row) != len(keys)
            or any(type(v) is not int or v < 0 for v in hard_row)
        ):
            raise ValueError("Invalid error-evaluation confusion matrix")
        for value in mass_row:
            number(value, count + 1, "Gold mass")
    if sum(map(sum, confusion)) != count or not math.isclose(
        sum(map(sum, mass)), count, rel_tol=1e-8, abs_tol=1e-7
    ):
        raise ValueError("Confusion totals differ from evaluation count")
    for index in range(len(keys)):
        if not math.isclose(
            sum(row[index] for row in mass),
            sum(row[index] for row in confusion),
            rel_tol=1e-8,
            abs_tol=1e-7,
        ):
            raise ValueError("Gold-mass predicted counts differ from hard confusion")
    if metrics.get("test_class_counts") != {
        key: sum(confusion[i]) for i, key in enumerate(keys)
    }:
        raise ValueError("Evaluation class counts differ from confusion")
    return keys, count, soft, confusion, mass


def measure(metrics, question, definition):
    from .metrics import wilson_interval

    keys, count, soft, confusion, mass = validated_metrics(metrics, question)
    result = {"count": count, "soft_gold_rows": soft, "forced_argmax": True}
    if "costs" in definition:
        total = sum(
            mass[i][j] * definition["costs"][actual][predicted]
            for i, actual in enumerate(keys)
            for j, predicted in enumerate(keys)
        )
        result.update(
            cost_unit=definition["cost_unit"],
            total_policy_cost=total,
            mean_policy_cost=total / count,
            cost_basis="normalized_gold_mass; not provider billing or realized operational loss",
        )
    if "critical_labels" in definition:
        result["critical_labels"] = {}
        for label in definition["critical_labels"]:
            i = keys.index(label)
            positive = sum(confusion[i])
            tp = confusion[i][i]
            fn = positive - tp
            fp = sum(row[i] for row in confusion) - tp
            negative = count - positive
            result["critical_labels"][label] = {
                "positive_rows": positive,
                "negative_rows": negative,
                "true_positive": tp,
                "false_negative": fn,
                "false_positive": fp,
                "true_negative": negative - fp,
                "false_negative_rate": fn / positive if positive else None,
                "false_positive_rate": fp / negative if negative else None,
                "false_negative_wilson_95": (
                    wilson_interval(fn, positive) if positive else None
                ),
                "false_positive_wilson_95": (
                    wilson_interval(fp, negative) if negative else None
                ),
                "hard_gold_required": True,
            }
    return result


def evaluate_report(report, questions, policy):
    policy = normalize_policy(policy, questions)
    evaluation = {
        "format": EVALUATION,
        "policy_sha256": fingerprint(policy),
        "schema_sha256": fingerprint(questions),
        "decision_rule": "forced_argmax_including_model_abstentions",
        "splits": {},
    }
    for role in ("validation", "test"):
        metrics = report.get(role)
        if not isinstance(metrics, dict) or set(metrics) != set(questions):
            raise ValueError(
                "Error evaluation requires complete validation and test metrics"
            )
        evaluation["splits"][role] = {
            name: measure(metrics[name], questions[name], definition)
            for name, definition in policy["questions"].items()
        }
    return evaluation


def attach_evaluation(report, questions, policy):
    if policy is not None:
        report["error_evaluation"] = evaluate_report(report, questions, policy)
    return report


def release_failures(report, questions, policy):
    policy = normalize_policy(policy, questions)
    evaluation = evaluate_report(report, questions, policy)
    if report.get("error_evaluation") != evaluation:
        raise ValueError("Frozen error-policy evaluation is missing or inconsistent")
    failures = []
    for name, limits in policy["questions"].items():
        result = evaluation["splits"]["test"][name]
        if "costs" in limits and result["mean_policy_cost"] > limits["max_mean_cost"]:
            failures.append(name + ": mean error cost exceeds its frozen limit")
        for label, bounds in limits.get("critical_labels", {}).items():
            critical = result["critical_labels"][label]
            prefix = name + "/" + label + ": "
            if result["soft_gold_rows"]:
                failures.append(
                    prefix + "critical gates require hard-labeled test rows"
                )
                continue
            if (
                critical["positive_rows"] < bounds["min_positive_rows"]
                or critical["negative_rows"] < bounds["min_negative_rows"]
            ):
                failures.append(
                    prefix + "insufficient positive or negative test support"
                )
                continue
            for error in ("false_negative", "false_positive"):
                rate = (
                    critical[error + "_rate"]
                    if bounds["rate_bound"] == "observed"
                    else critical[error + "_wilson_95"][1]
                )
                if rate > bounds["max_" + error + "_rate"]:
                    failures.append(
                        prefix
                        + error.replace("_", " ")
                        + " "
                        + bounds["rate_bound"]
                        + " exceeds its frozen limit"
                    )
    return failures


def main():
    parser = argparse.ArgumentParser(
        description="Analyze domain error costs and critical labels; this does not activate a model or establish a pre-test policy freeze."
    )
    parser.add_argument("--report", required=True)
    parser.add_argument(
        "--schema", required=True, help="Project specification with a questions object"
    )
    parser.add_argument("--policy", required=True)
    args = parser.parse_args()
    try:

        def read(path, limit):
            with Path(path).open("rb") as file:
                body = file.read(limit + 1)
            if len(body) > limit:
                raise ValueError("Input exceeds its metadata size limit")
            return json.loads(body)

        report = read(args.report, 16 * 1024 * 1024)
        questions = read(args.schema, 16 * 1024 * 1024)["questions"]
        policy = read(args.policy, MAX_POLICY_BYTES)
        evaluation = evaluate_report(report, questions, policy)
        report = {**report, "error_evaluation": evaluation}
        print(
            json.dumps(
                {
                    "evaluation": evaluation,
                    "release_failures": release_failures(report, questions, policy),
                    "scope": "Diagnostic only; no release or immutable-policy attestation",
                },
                indent=2,
                allow_nan=False,
            )
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
