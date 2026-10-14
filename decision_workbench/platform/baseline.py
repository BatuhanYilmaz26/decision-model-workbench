"""Real fixed-schema text baseline. JSON weights; no pickle or synthetic quality claims."""

import json
import importlib.metadata
import math
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from decision_workbench.data import dumps, options


def text(state):
    return state if isinstance(state, str) else dumps(state)


def probabilities(bundle, states, name):
    head = bundle["heads"][name]
    vectorizer = TfidfVectorizer(
        vocabulary=bundle["vocabulary"], ngram_range=(1, 2), sublinear_tf=True
    )
    vectorizer.idf_ = np.asarray(bundle["idf"])
    values = vectorizer.transform([text(s) for s in states])
    logits = np.asarray(values @ np.asarray(head["coef"]).T) + np.asarray(
        head["intercept"]
    )
    if logits.shape[1] == 1:
        logits = np.column_stack([np.zeros(len(states)), logits[:, 0]])
    logits /= head["temperature"]
    logits -= logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def gold_labels(rows, name):
    return [
        max(r["gold"][name]["probabilities"], key=r["gold"][name]["probabilities"].get)
        for r in rows
    ]


def evaluate(bundle, rows):
    from decision_workbench.training.metrics import evaluate_predictions

    states = [row["state"] for row in rows]
    responses = [{} for _ in rows]
    order = {}
    for name, head in bundle["heads"].items():
        order[name] = head["classes"]
        values = probabilities(bundle, states, name)
        for response, probabilities_row in zip(responses, values):
            response[name] = {
                "probabilities": dict(zip(head["classes"], probabilities_row.tolist()))
            }
    return evaluate_predictions(rows, responses, bundle["questions"], label_order=order)


def train(splits, questions, output):
    for split in splits.values():
        if not split or any(r["questions"] != questions for r in split):
            raise ValueError(
                "Baseline requires nonempty splits and a single fixed project schema"
            )
    for row in splits["train"]:
        if any(
            max(g["probabilities"].values()) < 1 - 1e-6 for g in row["gold"].values()
        ):
            raise ValueError(
                "CPU baseline accepts hard labels; use the decision-head trainer for soft targets"
            )
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2), sublinear_tf=True, max_features=20000
    )
    values = vectorizer.fit_transform([text(r["state"]) for r in splits["train"]])
    bundle = {
        "format": "domain-text-baseline-v1",
        "questions": questions,
        "vocabulary": {k: int(v) for k, v in vectorizer.vocabulary_.items()},
        "idf": vectorizer.idf_.tolist(),
        "heads": {},
    }
    for name, question in questions.items():
        labels = gold_labels(splits["train"], name)
        if set(labels) != set(options(question)):
            raise ValueError(
                f"{name}: every allowed label needs training examples; collect more reviewed data"
            )
        classifier = LogisticRegression(max_iter=500, random_state=3407).fit(
            values, labels
        )
        head = {
            "classes": classifier.classes_.tolist(),
            "coef": classifier.coef_.tolist(),
            "intercept": classifier.intercept_.tolist(),
            "temperature": 1.0,
        }
        bundle["heads"][name] = head
        calibration = splits["calibration"]
        target = np.array(
            [head["classes"].index(v) for v in gold_labels(calibration, name)]
        )
        best = (math.inf, 1.0)
        for temperature in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
            head["temperature"] = temperature
            probs = probabilities(bundle, [r["state"] for r in calibration], name)
            loss = float(
                -np.log(np.maximum(probs[np.arange(len(target)), target], 1e-12)).mean()
            )
            best = min(best, (loss, temperature))
        head["temperature"] = best[1]
    report = {
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("scikit-learn", "numpy", "scipy")
        },
        "seed": 3407,
        "backend": "cpu-baseline",
        "schema_conditioned": False,
        "counts": {k: len(v) for k, v in splits.items()},
        "validation": evaluate(bundle, splits["validation"]),
        "test": evaluate(bundle, splits["test"]),
        "temperatures": {n: h["temperature"] for n, h in bundle["heads"].items()},
        "limitations": [
            "Fixed-schema lexical classifier, not a pretrained decision head",
            "Temperature chosen on calibration only",
            "Per-class and domain adequacy need owner acceptance",
        ],
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "model.json").write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    reloaded = json.loads((output / "model.json").read_text(encoding="utf-8"))
    for name in questions:
        expected = probabilities(bundle, [r["state"] for r in splits["test"]], name)
        actual = probabilities(reloaded, [r["state"] for r in splits["test"]], name)
        if not np.allclose(expected, actual, atol=1e-12, rtol=1e-12):
            raise ValueError(
                "Reloaded baseline predictions differ from training runtime"
            )
    report["reload_equivalence"] = True
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def predict(bundle, state, threshold):
    result = {}
    for name, question in bundle["questions"].items():
        head = bundle["heads"][name]
        values = probabilities(bundle, [state], name)[0]
        key = head["classes"][int(values.argmax())]
        answer = (
            key
            if question["type"] == "choice"
            else key == "true" if question["type"] == "noul" else int(key)
        )
        entry = {
            "answer": answer,
            "probabilities": dict(zip(head["classes"], values.tolist())),
            "confidence": float(values.max()),
            "abstained": bool(values.max() < threshold),
        }
        if question["type"] == "score":
            entry["expected_score"] = float(
                sum(int(k) * p for k, p in entry["probabilities"].items())
            )
        result[name] = entry
    return result
