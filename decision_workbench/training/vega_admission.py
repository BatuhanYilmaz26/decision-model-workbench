"""Independent exported Vega applicability and uncertainty measurements."""

import math
from .metrics import evaluate_predictions
from .vega_study import gate_metrics


def compare_uncertainty(expected, actual, questions, tolerance=0.005):
    if len(expected) != len(actual) or not expected:
        raise ValueError("Vega uncertainty comparison needs matching nonempty probes")
    for before, after in zip(expected, actual):
        for name in questions:
            a, b = before["uncertainty"][name], after["uncertainty"][name]
            if any(
                a[k] != b[k]
                for k in ("prediction_set", "adapter", "unbound", "upstream_abstain")
            ):
                raise ValueError("Vega reload changed applicability or uncertainty")
            if (
                any(
                    type(x["gate"]) not in (int, float)
                    or not math.isfinite(x["gate"])
                    or not 0 <= x["gate"] <= 1
                    for x in (a, b)
                )
                or abs(a["gate"] - b["gate"]) > tolerance
            ):
                raise ValueError(
                    "Vega reload changed or returned invalid gate probabilities"
                )


def measure(engine, rows, negatives, questions, threshold):
    if (
        type(threshold) not in (int, float)
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
    ):
        raise ValueError("Exported Vega gate threshold must be a finite probability")
    details, negative_details, metrics, applicability = {}, {}, {}, {}
    for role in ("validation", "calibration", "test"):
        pos = [engine.predict_details(row["state"], questions) for row in rows[role]]
        neg = [
            engine.predict_details(row["state"], questions) for row in negatives[role]
        ]
        compare_uncertainty(pos + neg, pos + neg, questions)
        details[role], negative_details[role] = pos, neg
        metrics[role] = evaluate_predictions(
            rows[role], [p["answers"] for p in pos], questions
        )
        applicability[role] = {
            name: {
                "positive": gate_metrics(
                    [p["uncertainty"][name]["gate"] for p in pos], True, threshold
                ),
                "negative": gate_metrics(
                    [
                        p["uncertainty"][name]["gate"]
                        for row, p in zip(negatives[role], neg)
                        if name in row["negative_for"]
                    ],
                    False,
                    threshold,
                ),
                "positive_accepted_fraction": sum(
                    not p["answers"][name]["abstained"] for p in pos
                )
                / len(pos),
            }
            for name in questions
        }
    return details, negative_details, metrics, applicability
