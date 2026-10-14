"""A hosted model study with calibration-only fitting and immutable local release policy."""

import hashlib
import json
import math
import time
import uuid
import numpy as np
from decision_workbench.data import options
from decision_workbench.training.metrics import evaluate_predictions, normalize_answers
from .store import Conflict, digest, encode
from .hosted import profile_for, request_body, HostedQuota


def calibrated(answers, questions, temperatures, threshold=0.0):
    values = {}
    for name, q in questions.items():
        keys = options(q)
        z = (
            np.log(np.maximum([answers[name]["probabilities"][k] for k in keys], 1e-12))
            / temperatures[name]
        )
        z -= z.max()
        probabilities = np.exp(z)
        probabilities /= probabilities.sum()
        values[name] = {"probabilities": dict(zip(keys, probabilities.tolist()))}
    return normalize_answers(values, questions, threshold)


def fit(rows, predictions, questions):
    result = {}
    for name, q in questions.items():
        keys = options(q)
        targets = np.asarray(
            [[r["gold"][name]["probabilities"][k] for k in keys] for r in rows]
        )
        best = (math.inf, math.inf, 1.0)
        for temperature in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
            temps = {n: temperature if n == name else 1.0 for n in questions}
            probabilities = np.asarray(
                [
                    [
                        calibrated(p, questions, temps)[name]["probabilities"][k]
                        for k in keys
                    ]
                    for p in predictions
                ]
            )
            loss = float(
                -(targets * np.log(np.maximum(probabilities, 1e-12))).sum(axis=1).mean()
            )
            best = min(best, (loss, abs(math.log(temperature)), temperature))
        result[name] = best[2]
    return result


def evaluate(worker, job, snapshot, questions, coverage, cancelled):
    store = worker.store
    config = job["config"]
    profile = config["profile"]
    principal = {"tenant": job["tenant"], "actor": job["actor"], "role": "admin"}
    current = profile_for(job["tenant"], profile["id"], profile)
    # Validate every wire request before exposing any data or reserving paid calls.
    for split in ("calibration", "validation", "test"):
        for row in snapshot["splits"][split]:
            request_body(current, row["state"], questions)
    total = sum(
        len(snapshot["splits"][s]) for s in ("calibration", "validation", "test")
    )
    with store.transaction() as db:
        used = db.execute(
            "SELECT count(*) FROM entities WHERE tenant=? AND kind='provider-call' AND json_extract(payload,'$.profile_id')=? AND created>=?",
            (job["tenant"], profile["id"], time.time() - 86400),
        ).fetchone()[0]
    if used + total > current["max_calls_day"]:
        raise HostedQuota(
            "Remaining decision quota cannot cover this study; no evaluation data were sent"
        )
    predictions, calls, usages = {}, [], []
    started = time.monotonic()
    for split in ("calibration", "validation", "test"):
        predictions[split] = []
        for index, row in enumerate(snapshot["splits"][split]):
            if cancelled.is_set():
                raise Conflict("Hosted evaluation cancelled")
            if time.monotonic() - started > 3600:
                raise ValueError(
                    "Hosted study exceeded its one-hour deadline; prior calls will not be replayed"
                )
            store.heartbeat(job, f"Hosted evaluation {len(calls) + 1}/{total}; {split}")
            result = worker.service.hosted.call(
                principal,
                job["project"],
                profile,
                row["state"],
                questions,
                job["id"] + ":" + split + ":" + str(index),
                {
                    "job": job["id"],
                    "snapshot_sha256": snapshot["sha256"],
                    "schema_sha256": digest(questions),
                },
                job,
            )
            predictions[split].append(result["answers"])
            calls.append(result["call_id"])
            usages.append(result["identity"]["usage"])
    store.heartbeat(job, "Fitting local temperatures on reserved calibration only")
    temperatures = fit(
        snapshot["splits"]["calibration"], predictions["calibration"], questions
    )
    report = {
        "backend": "hosted",
        "counts": {s: len(v) for s, v in snapshot["splits"].items()},
        "profile": profile,
        "profile_sha256": digest(profile),
        "temperatures": temperatures,
        "provider_calls": calls,
        "provider_usage": {
            "calls": len(calls),
            "reported_input_tokens": (
                sum(u["input_tokens"] for u in usages)
                if all("input_tokens" in u for u in usages)
                else None
            ),
            "reported_output_tokens": (
                sum(u["output_tokens"] for u in usages)
                if all("output_tokens" in u for u in usages)
                else None
            ),
            "reported_cost_sum": (
                sum(u["cost"] for u in usages)
                if all("cost" in u for u in usages)
                else None
            ),
            "cost_basis": "Provider usage.cost when present; an unspecified route currency is not inferred",
        },
        "calibration_raw": evaluate_predictions(
            snapshot["splits"]["calibration"], predictions["calibration"], questions
        ),
        "limitations": [
            "Provider weights are remote and are not trained or exported by this study",
            "Temperatures are fitted exclusively on the real reserved calibration split",
            "Softmax log probabilities use a 1e-12 floor; reported provider confidence is separate",
            "Response identity cannot establish an undisclosed provider weight revision",
            "Costs are provider-reported usage when available; call quotas are not a dollar billing cutoff",
            "No automatic retry or replay of pending/ambiguous calls",
        ],
    }
    for split in ("calibration", "validation", "test"):
        outputs = [calibrated(p, questions, temperatures) for p in predictions[split]]
        report[split] = evaluate_predictions(
            snapshot["splits"][split], outputs, questions
        )
    from decision_workbench.training.error_policy import attach_evaluation

    attach_evaluation(report, questions, config.get("error_policy"))
    bundle = {
        "format": "domain-hosted-decision-v1",
        "questions": questions,
        "profile": profile,
        "profile_sha256": digest(profile),
        "temperatures": temperatures,
    }
    exported = encode(bundle).encode("utf-8")
    reloaded = json.loads(exported)
    before = calibrated(predictions["validation"][0], questions, temperatures)
    after = calibrated(
        predictions["validation"][0], reloaded["questions"], reloaded["temperatures"]
    )
    if before != after:
        raise ValueError("Hosted calibration policy changed after reload")
    report["reload_equivalence"] = True
    report["reload_scope"] = (
        "Local schema/profile/calibration JSON; no claim of provider weight reload equivalence"
    )
    artifact_id = uuid.uuid4().hex
    folder = store.folder / "artifacts" / artifact_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "model.json").write_bytes(exported)
    (folder / "report.json").write_text(encode(report), encoding="utf-8", newline="\n")
    model = store.add(
        "model",
        job["tenant"],
        job["project"],
        job["actor"],
        {
            "backend": "hosted",
            "artifact_id": artifact_id,
            "model_sha256": hashlib.sha256(exported).hexdigest(),
            "snapshot": config["snapshot"],
            "snapshot_sha256": snapshot["sha256"],
            "schema_sha256": digest(questions),
            "job_id": job["id"],
            "report": report,
            "configuration": config,
            "data_gate_counts": coverage,
        },
        job=job,
    )
    return {"model": model["id"], "backend": "hosted"}
