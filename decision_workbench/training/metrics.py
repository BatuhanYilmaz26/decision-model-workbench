"""Common per-question metrics for actual model probability outputs."""

import math
import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support
from decision_workbench.data import options, validate_questions


def normalize_answers(answers, questions, threshold=0.0):
    validate_questions(questions)
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("Model answer schema differs from the pinned project")
    result = {}
    for name, question in questions.items():
        entry = answers[name]
        values = entry.get("probabilities") if isinstance(entry, dict) else None
        keys = options(question)
        if not isinstance(values, dict) or set(values) != set(keys):
            raise ValueError("Model probabilities do not cover the pinned options")
        probabilities = [values[key] for key in keys]
        if any(
            isinstance(p, bool)
            or not isinstance(p, (int, float))
            or not math.isfinite(p)
            or not 0 <= p <= 1
            for p in probabilities
        ) or not math.isclose(sum(probabilities), 1.0, abs_tol=1e-5):
            raise ValueError("Model returned invalid probabilities")
        index = int(np.argmax(probabilities))
        key = keys[index]
        answer = (
            key
            if question["type"] == "choice"
            else key == "true" if question["type"] == "noul" else int(key)
        )
        result[name] = {
            "answer": answer,
            "probabilities": {key: float(values[key]) for key in keys},
            "confidence": float(probabilities[index]),
            "abstained": bool(probabilities[index] < threshold),
        }
        if "model_abstained" in entry:
            if type(entry["model_abstained"]) is not bool:
                raise ValueError("Model abstention must be an explicit boolean")
            result[name]["model_abstained"] = entry["model_abstained"]
            result[name]["abstained"] = (
                entry["model_abstained"] or result[name]["abstained"]
            )
        if question["type"] == "score":
            result[name]["expected_score"] = sum(int(k) * values[k] for k in keys)
    return result


def wilson_interval(correct, count):
    z = 1.959963984540054
    p = correct / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    radius = (
        z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    )
    return [max(0.0, center - radius), min(1.0, center + radius)]


def evaluate_predictions(rows, predictions, questions, label_order=None):
    if not rows or len(rows) != len(predictions):
        raise ValueError("Evaluation requires one probability response per row")
    responses = [normalize_answers(p, questions) for p in predictions]
    if label_order is not None and set(label_order) != set(questions):
        raise ValueError("Metric label order must cover the project schema")
    result = {}
    for name, question in questions.items():
        keys = options(question) if label_order is None else label_order[name]
        if (
            not isinstance(keys, list)
            or len(keys) != len(options(question))
            or set(keys) != set(options(question))
        ):
            raise ValueError("Metric label order differs from allowed outcomes")
        for row in rows:
            gold = row.get("gold", {}).get(name, {}).get("probabilities")
            if (
                not isinstance(gold, dict)
                or set(gold) != set(keys)
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0 <= value <= 1
                    or not math.isfinite(value)
                    for value in gold.values()
                )
                or not math.isclose(sum(gold.values()), 1.0, abs_tol=1e-6)
            ):
                raise ValueError(
                    "Evaluation gold must provide complete finite normalized probabilities"
                )
        probs = np.asarray(
            [[p[name]["probabilities"][k] for k in keys] for p in responses]
        )
        targets = np.asarray(
            [[r["gold"][name]["probabilities"][k] for k in keys] for r in rows]
        )
        target = targets.argmax(axis=1)
        prediction = probs.argmax(axis=1)
        gold_mass = targets / targets.sum(axis=1, keepdims=True)
        mass_confusion = np.zeros((len(keys), len(keys)))
        for predicted_index in range(len(keys)):
            mass_confusion[:, predicted_index] = gold_mass[
                prediction == predicted_index
            ].sum(axis=0)
        correct = prediction == target
        confidence = probs.max(axis=1)
        bins, ece = [], 0.0
        for i in range(10):
            selected = (confidence >= i / 10) & (
                (confidence <= 1) if i == 9 else (confidence < (i + 1) / 10)
            )
            count = int(selected.sum())
            if count:
                accuracy = float(correct[selected].mean())
                mean_conf = float(confidence[selected].mean())
                ece += count / len(rows) * abs(mean_conf - accuracy)
                bins.append(
                    {
                        "lower": i / 10,
                        "count": count,
                        "confidence": mean_conf,
                        "accuracy": accuracy,
                    }
                )
        precision, recall, f1, support = precision_recall_fscore_support(
            target, prediction, labels=list(range(len(keys))), zero_division=0
        )
        entry = {
            "count": len(rows),
            "accuracy": float(correct.mean()),
            "accuracy_wilson_95": wilson_interval(int(correct.sum()), len(rows)),
            "macro_f1": float(
                f1_score(
                    target,
                    prediction,
                    labels=list(range(len(keys))),
                    average="macro",
                    zero_division=0,
                )
            ),
            "nll": float(
                -(targets * np.log(np.maximum(probs, 1e-12))).sum(axis=1).mean()
            ),
            "brier_sum_over_options": float(
                ((probs - targets) ** 2).sum(axis=1).mean()
            ),
            "ece_10_equal_width_bins": float(ece),
            "bins": bins,
            "labels": keys,
            "confusion": confusion_matrix(
                target, prediction, labels=list(range(len(keys)))
            ).tolist(),
            "gold_mass_confusion": mass_confusion.tolist(),
            "test_class_counts": {
                key: int((target == i).sum()) for i, key in enumerate(keys)
            },
            "per_class": {
                key: {
                    "precision": float(precision[i]),
                    "recall": float(recall[i]),
                    "f1": float(f1[i]),
                    "support": int(support[i]),
                }
                for i, key in enumerate(keys)
            },
            "soft_gold_rows": int((targets.max(axis=1) < 1 - 1e-6).sum()),
        }
        if question["type"] == "score":
            levels = np.asarray([int(key) for key in keys])
            entry["ordinal_mae"] = float(
                np.abs(levels[prediction] - levels[target]).mean()
            )
            entry["expected_score_mae"] = float(
                np.abs(probs @ levels - targets @ levels).mean()
            )
        result[name] = entry
    return result
