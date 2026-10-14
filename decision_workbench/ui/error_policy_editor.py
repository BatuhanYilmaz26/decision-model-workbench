"""Convert guided cost and critical-outcome tables to the server policy contract."""

from collections import Counter
import json

from decision_workbench.data import options, dumps
from decision_workbench.training.error_policy import MAX_POLICY_BYTES, normalize_policy

OVERRIDE_TEMPLATE = {"question": None, "actual": None, "predicted": None, "cost": 0.0}
CRITICAL_TEMPLATE = {
    "question": None,
    "outcome": None,
    "max_false_negative_rate": 0.2,
    "max_false_positive_rate": 0.2,
    "min_positive_rows": 30,
    "min_negative_rows": 30,
    "rate_bound": "wilson_95_upper",
}


def editor_rows(questions, policy=None):
    """Existing matrices remain exact when decomposed into shared cost and exceptions."""
    selected = normalize_policy(policy, questions)["questions"] if policy else {}
    rules, overrides, critical = [], [], []
    for name, question in questions.items():
        definition = selected.get(name, {})
        labels = options(question)
        costs = definition.get("costs")
        default = (
            Counter(costs[a][p] for a in labels for p in labels if a != p).most_common(
                1
            )[0][0]
            if costs
            else 1.0
        )
        rules.append(
            {
                "question": name,
                "include_costs": costs is not None,
                "cost_unit": definition.get("cost_unit", "loss_points"),
                "default_error_cost": default,
                "max_mean_cost": definition.get("max_mean_cost", 0.15),
            }
        )
        if costs:
            overrides.extend(
                {"question": name, "actual": a, "predicted": p, "cost": costs[a][p]}
                for a in labels
                for p in labels
                if a != p and costs[a][p] != default
            )
        critical.extend(
            {"question": name, "outcome": label, **limits}
            for label, limits in definition.get("critical_labels", {}).items()
        )
    return {
        "costs": rules,
        "overrides": overrides or [dict(OVERRIDE_TEMPLATE)],
        "critical": critical or [dict(CRITICAL_TEMPLATE)],
    }


def _rows(draft, field):
    value = draft.get(field)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("Invalid " + field + " table")
    return value


def _placeholder(row, template):
    return row == template or all(
        value is None or value == "" for value in row.values()
    )


def policy_from_editor(body, questions):
    """Validate on explicit save; partial table edits never change server criteria."""
    if body is None:
        return None
    if isinstance(body, str):
        return normalize_policy(json.loads(body), questions)
    if not isinstance(body, dict) or set(body) != {"costs", "overrides", "critical"}:
        raise ValueError("Use guided tables or an existing JSON error policy")
    definitions, seen, matrix_minimum_bytes = {}, set(), 0
    for row in _rows(body, "costs"):
        name = row.get("question")
        if not isinstance(name, str) or name not in questions or name in seen:
            raise ValueError("Cost rules must name each project question once")
        seen.add(name)
        enabled = row.get("include_costs")
        if type(enabled) is not bool:
            raise ValueError("Use the Include costs checkbox for each question")
        if not enabled:
            continue
        labels = options(questions[name])
        # Refuse large matrices before allocating their complete nested dictionaries.
        matrix_minimum_bytes += (len(labels) + 1) * sum(
            len(dumps(label).encode()) for label in labels
        ) + len(labels) ** 2
        if matrix_minimum_bytes > MAX_POLICY_BYTES:
            raise ValueError(
                "Cost matrices exceed the 64 KB policy limit; use fewer cost questions or critical-only gates"
            )
        definitions[name] = {
            "cost_unit": row.get("cost_unit"),
            "costs": {
                a: {p: 0 if a == p else row.get("default_error_cost") for p in labels}
                for a in labels
            },
            "max_mean_cost": row.get("max_mean_cost"),
        }
    if seen != set(questions):
        raise ValueError("Cost rules must cover the complete project schema")
    seen_overrides = set()
    for row in _rows(body, "overrides"):
        if _placeholder(row, OVERRIDE_TEMPLATE):
            continue
        name, actual, predicted = (
            row.get("question"),
            row.get("actual"),
            row.get("predicted"),
        )
        if not isinstance(name, str) or name not in questions:
            raise ValueError("Specific error costs need a project question")
        if name not in definitions or "costs" not in definitions[name]:
            continue  # The owner explicitly unchecked costs for this question.
        labels = options(questions[name])
        if actual not in labels or predicted not in labels or actual == predicted:
            raise ValueError(
                name + ": specific costs need different allowed true/predicted outcomes"
            )
        key = (name, actual, predicted)
        if key in seen_overrides:
            raise ValueError(name + ": duplicate specific error cost")
        seen_overrides.add(key)
        definitions[name]["costs"][actual][predicted] = row.get("cost")
    seen_critical = set()
    for row in _rows(body, "critical"):
        if _placeholder(row, CRITICAL_TEMPLATE):
            continue
        name, label = row.get("question"), row.get("outcome")
        if (
            not isinstance(name, str)
            or name not in questions
            or label not in options(questions[name])
        ):
            raise ValueError(
                "Critical limits need a project question and one of its allowed outcomes"
            )
        key = (name, label)
        if key in seen_critical:
            raise ValueError(name + ": duplicate critical outcome")
        seen_critical.add(key)
        definitions.setdefault(name, {}).setdefault("critical_labels", {})[label] = {
            field: row.get(field)
            for field in CRITICAL_TEMPLATE
            if field not in ("question", "outcome")
        }
    if not definitions:
        raise ValueError(
            "Select at least one cost rule or critical outcome, or turn off domain error gates"
        )
    return normalize_policy(
        {"format": "domain-error-policy-v1", "questions": definitions}, questions
    )
