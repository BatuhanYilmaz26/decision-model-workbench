"""Train and package a domain model with Perplexity's pinned native trainer."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import contextlib
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from decision_workbench.data import options
from decision_workbench.json_tools import strict_json
from .artifacts import compare_predictions, file_sha, write_manifest
from .liquid_data import load_inputs, dumps
from .metrics import evaluate_predictions, normalize_answers
from .perplexity_data import PerplexityPrompt, export_dataset
from .perplexity_checkpoint import (
    ASSETS,
    asset,
    base_checkpoint,
    native_files,
    publisher_module,
    tensor_digest,
)
from .perplexity_profile import MODEL, REVISION, WEIGHT_BYTES
from .perplexity_runtime import PerplexityEngine, memory_guard, runtime_identity
from .provenance import source_fingerprint
from .perplexity_local import settings as profile_settings, DEFAULTS, validate_request


def settings(args):
    return profile_settings(
        {name: getattr(args, name, default) for name, default in DEFAULTS.items()}
    )


def invoke(publisher, phase, arguments, log):
    """Run checked source in-process; no shell, external API or child interpreter."""
    module = publisher_module(publisher, phase)
    previous = sys.argv
    try:
        sys.argv = ["autojev-" + phase, *[str(value) for value in arguments]]
        with (
            Path(log).open("w", encoding="utf-8") as stream,
            contextlib.redirect_stdout(stream),
            contextlib.redirect_stderr(stream),
        ):
            module.main()
    finally:
        sys.argv = previous
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def saved_validation(path, rows, questions, temperature):
    expected = {}
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if len(line.encode()) > 1024**2:
                raise ValueError("Publisher validation prediction exceeds 1 MiB")
            row = strict_json(line)
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError(
                    "Publisher validation predictions need object rows with string IDs"
                )
            if row["id"] in expected or row.get("temperature") != temperature:
                raise ValueError(
                    "Duplicate or differently calibrated publisher prediction"
                )
            expected[row["id"]] = row
    result = []
    for row in rows:
        answers = {}
        for name, question in questions.items():
            identifier = hashlib.sha256(dumps([row["id"], name]).encode()).hexdigest()
            saved = expected.pop(identifier, None)
            keys = options(question)
            labels = [False, True] if question["type"] == "noul" else keys
            if (
                not saved
                or saved.get("suite") != name
                or saved.get("family") != row["group_id"]
                or saved.get("options") != labels
                or any(type(a) is not type(b) for a, b in zip(saved["options"], labels))
                or len(saved.get("probabilities", [])) != len(keys)
            ):
                raise ValueError(
                    "Publisher validation prediction differs from the reserved domain"
                )
            answers[name] = {"probabilities": dict(zip(keys, saved["probabilities"]))}
        result.append(normalize_answers(answers, questions))
    if expected:
        raise ValueError("Publisher validation predictions contain extra decisions")
    return result


def require_linux():
    if sys.platform != "linux":
        raise ValueError(
            "The pinned publisher training logger requires Linux; use WSL2 or a Linux GPU host"
        )


def train_perplexity(args):
    configuration = settings(args)
    rows, bodies = load_inputs(args.data)
    hashes = {role: hashlib.sha256(body).hexdigest() for role, body in bodies.items()}
    source = source_fingerprint()
    local_study = validate_request(
        getattr(args, "local_study_request", None), configuration, hashes, source, rows
    )
    require_linux()
    packages = runtime_identity()
    memory_guard("cuda", training=True)
    if any(
        os.environ.get(key, "0") not in ("0", "1", "")
        for key in ("WORLD_SIZE", "LOCAL_WORLD_SIZE")
    ) or any(
        os.environ.get(key, "0") not in ("0", "") for key in ("RANK", "LOCAL_RANK")
    ):
        raise ValueError(
            "This launcher supports one GPU process; do not invoke it under torchrun"
        )
    git = shutil.which("git")
    if not git:
        raise ValueError("Install Git and run training from the cloned repository")
    commit = subprocess.check_output([git, "rev-parse", "HEAD"], text=True).strip()
    if len(rows["calibration"]) < 8:
        raise ValueError(
            "Native calibration requires at least eight reserved real records"
        )
    questions = rows["train"][0]["questions"]
    out = Path(args.out).absolute()
    checkpoint = Path(args.checkpoint).resolve()
    if out.exists() or out.resolve().is_relative_to(checkpoint):
        raise ValueError("Choose a new run directory outside the initial checkpoint")
    parent = out.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < WEIGHT_BYTES * 3 + 4 * 1024**3:
        raise ValueError(
            "Reserve storage for native checkpoints and the complete domain export"
        )
    base_checkpoint(checkpoint)
    original_map = strict_json(asset(checkpoint, "model.safetensors.index.json"))[
        "weight_map"
    ]
    original_backbone = tensor_digest(checkpoint, original_map)
    original_head = tensor_digest(checkpoint, original_map, readout=True)
    prompt = PerplexityPrompt(checkpoint)
    # Complete CPU validation occurs before invoking the first native model.
    export_dataset(args.data, out / "data", prompt, args.max_length)
    native = out / "data/native"
    run, checkpoints = out / "study", out / "checkpoints"
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        AUTOJEV_EVENTS=str(out / "events.jsonl"),
    )
    print(
        "Evaluating the original local checkpoint on validation; see baseline.log",
        flush=True,
    )
    invoke(
        checkpoint,
        "evaluate",
        [
            "--local",
            "--checkpoint",
            checkpoint,
            "--data",
            native / "validation.jsonl",
            "--output",
            out / "baseline.json",
            "--batch-size",
            1,
        ],
        out / "baseline.log",
    )
    print(
        "Training all native weights with a fresh CPU-offloaded optimizer; see training.log",
        flush=True,
    )
    invoke(
        checkpoint,
        "train",
        [
            "--initial-checkpoint",
            checkpoint,
            "--train",
            native / "train.jsonl",
            "--development",
            native / "validation.jsonl",
            "--temperature",
            native / "calibration.jsonl",
            "--reference",
            out / "baseline.predictions.jsonl",
            "--run",
            run,
            "--output",
            checkpoints,
            "--attention-mode",
            "noncausal_full_attention",
            "--pooling",
            "last",
            "--epochs",
            args.epochs,
            "--batch-size",
            args.batch_size,
            "--effective-batch-size",
            args.batch_size * args.grad_accum,
            "--max-length",
            args.max_length,
            "--token-budget",
            args.max_length * args.batch_size,
            "--lr",
            args.learning_rate,
            "--cpu-threads",
            args.cpu_threads,
            "--eval-every",
            args.eval_every,
            "--save-final-only",
        ],
        out / "training.log",
    )
    summary = strict_json((run / "summary.json").read_bytes())
    if (
        not isinstance(summary, dict)
        or summary.get("complete") is not True
        or type(summary.get("steps")) is not int
        or summary["steps"] < 1
        or summary.get("saved_step") != summary["steps"]
    ):
        raise ValueError("Publisher training did not complete and save the final stage")
    selected = (checkpoints / "selected").resolve()
    if (
        not selected.is_relative_to(checkpoints.resolve())
        or selected.parent != checkpoints.resolve()
    ):
        raise ValueError("Selected native checkpoint escaped the owned run directory")
    config, mapping, names = native_files(selected, checkpoint)
    trained_backbone = tensor_digest(selected, mapping)
    trained_head = tensor_digest(selected, mapping, readout=True)
    if trained_backbone == original_backbone or trained_head == original_head:
        raise ValueError(
            "Full-weight training must change both backbone and native readout payloads"
        )
    expected = saved_validation(
        run / f"development-{summary['saved_step']:05d}.jsonl",
        rows["validation"],
        questions,
        config["temperature"],
    )
    export = out / "export"
    exported = export / "checkpoint"
    exported.mkdir(parents=True)
    for name in names:
        shutil.copyfile(selected / name, exported / name)
    publisher = export / "publisher"
    for name in ASSETS:
        target = publisher / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(asset(checkpoint, name))
    # Preserve the original architecture/processor bytes; only learned native
    # tensors and decision_config's calibration/provenance change in this profile.
    for name in (
        "config.json",
        "processor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
    ):
        (exported / name).write_bytes(asset(checkpoint, name))
    metadata = {
        "format": "domain-perplexity-runtime-v1",
        "model": MODEL,
        "revision": REVISION,
        "questions": questions,
        "max_length": args.max_length,
        "temperature": config["temperature"],
        "readout_payload_sha256": trained_head,
        "backbone_payload_sha256": trained_backbone,
    }
    (exported / "dmt_perplexity.json").write_text(dumps(metadata), encoding="utf-8")
    print(
        "Reloading the domain export and checking saved validation probabilities...",
        flush=True,
    )
    served = PerplexityEngine(exported, device="cuda")
    validation_predictions = [
        served.predict(row["state"], questions) for row in rows["validation"]
    ]
    equivalence = compare_predictions(expected, validation_predictions, questions, 1e-5)
    metrics = {
        "validation": evaluate_predictions(
            rows["validation"], validation_predictions, questions
        )
    }
    for role in ("calibration", "test"):
        metrics["calibration_metrics" if role == "calibration" else role] = (
            evaluate_predictions(
                rows[role],
                [served.predict(row["state"], questions) for row in rows[role]],
                questions,
            )
        )
    hashes = {role: hashlib.sha256(body).hexdigest() for role, body in bodies.items()}
    if source_fingerprint() != source or any(
        file_sha(Path(args.data) / (role + ".jsonl")) != sha
        for role, sha in hashes.items()
    ):
        raise ValueError("Workbench source or reserved data changed during training")
    base_checkpoint(checkpoint)
    report = {
        "backend": "perplexity-native",
        "model": MODEL,
        "revision": REVISION,
        "source_sha256": source,
        "git_commit": commit,
        "data_sha256": hashes,
        "counts": {role: len(records) for role, records in rows.items()},
        "configuration": configuration,
        "local_study_sha256": local_study,
        "packages": packages,
        "schema_conditioned": True,
        "training": {
            "method": "publisher-full-weight",
            "device": "cuda:0",
            "optimizer_updates": summary["steps"],
            "checkpoint_policy": "final_only",
            "backbone_updated": True,
            "readout_updated": True,
            "original_backbone_payload_sha256": original_backbone,
            "backbone_payload_sha256": trained_backbone,
            "original_readout_payload_sha256": original_head,
            "readout_payload_sha256": trained_head,
        },
        "calibration": {
            name: {
                "temperature": config["temperature"],
                "records": len(rows["calibration"]),
                "method": "publisher-global-across-questions",
                "decision_fit_records": len(rows["calibration"]) * len(questions),
            }
            for name in questions
        },
        **metrics,
        "reload_equivalence": True,
        "export_qualification": equivalence,
        "reference": {
            "model": MODEL,
            "revision": REVISION,
            "kind": "original-local-checkpoint",
            "paid_api_calls": 0,
            "note": "Upstream report fields named jev refer to this local comparison, not Jev",
        },
        "limitations": [
            "Linux CUDA full-weight standalone profile; application backend/cloud admission not integrated",
            "Final-epoch checkpoint with separately fitted global temperature, not development-selected weights",
            "Evaluate representative operational data and domain action costs before deployment",
        ],
    }
    (export / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    sha = write_manifest(export, "perplexity-native", questions, "checkpoint")
    return {"export": str(export), "manifest_sha256": sha, "report": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "out", "checkpoint"):
        parser.add_argument("--" + name, required=True)
    for name, default in (
        ("epochs", 1),
        ("batch-size", 1),
        ("grad-accum", 4),
        ("max-length", 2048),
        ("cpu-threads", 32),
        ("eval-every", 20),
    ):
        parser.add_argument("--" + name, type=int, default=default)
    parser.add_argument("--learning-rate", type=float, default=2e-6)
    parser.add_argument("--device", choices=("cuda",), default="cuda")
    parser.add_argument("--local-study-request")
    args = parser.parse_args()
    try:
        print(dumps(train_perplexity(args)), flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Perplexity native training failed: {exc}\n")


if __name__ == "__main__":
    main()
