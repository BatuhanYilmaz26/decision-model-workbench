"""Adapt the original omni decision head while retaining its frozen native trunk."""

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import random
import shutil

from decision_workbench.data import dumps
from .artifacts import compare_predictions, file_sha, write_manifest
from .liquid_data import dumps as ordered_dumps, load_inputs
from .liquid_omni_data import ASSETS, MODEL, REVISION, export_dataset, native_record
from .liquid_omni_local import DEFAULTS, settings, validate_request
from .liquid_omni_runtime import (
    OmniEngine,
    checked_runtime,
    features,
    head_logits,
    resolve_checkpoint,
    runtime_identity,
    checked_device,
)
from .liquid_runtime import fit_temperatures
from .metrics import evaluate_predictions
from .provenance import source_fingerprint


def frozen_digest(model):
    """Bind every untouched tensor, not just requires_grad flags."""
    value = hashlib.sha256()
    for name, tensor in model.named_parameters():
        if not name.startswith("head."):
            value.update(name.encode())
            value.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return value.hexdigest()


def train_omni(args):
    configuration = settings({name: getattr(args, name) for name in DEFAULTS})
    packages = runtime_identity()
    rows, bodies = load_inputs(args.data)
    if len(rows["calibration"]) < 8:
        raise ValueError("Omni calibration needs at least eight reserved real records")
    source = source_fingerprint()
    hashes = {role: hashlib.sha256(body).hexdigest() for role, body in bodies.items()}
    local_request = None
    if args.local_study_request:
        local_request = validate_request(
            args.local_study_request, args, hashes, source, rows
        )
    out = Path(args.out).absolute()
    if out.exists():
        raise ValueError("Choose a new omni run directory; existing runs are preserved")
    checked_device(args.device)
    checkpoint = resolve_checkpoint(args.cache, download=args.download)
    engine = OmniEngine(checkpoint, device=args.device, base=True)
    out.mkdir(parents=True)
    export_dataset(args.data, out / "data", engine.prompt, args.max_length)
    questions = rows["train"][0]["questions"]
    import torch

    torch.manual_seed(3407)
    model = engine.model
    model.requires_grad_(False)
    model.head.requires_grad_(True)
    model.head.train()
    trained = {
        name: parameter
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }
    if not trained or any(not name.startswith("head.") for name in trained):
        raise ValueError("Only the original native omni head may be updated")
    before = {
        name: parameter.detach().cpu().clone() for name, parameter in trained.items()
    }
    frozen_before = frozen_digest(model)
    optimizer = torch.optim.AdamW(
        trained.values(), lr=args.learning_rate, weight_decay=0.01
    )
    records = [
        native_record(engine.prompt, row, name, args.max_length)
        for row in rows["train"]
        for name in questions
    ]
    updates, losses = 0, []
    window_size = args.batch_size * args.grad_accum
    print(
        "Training the native option-marker head; text trunk and media encoders remain frozen...",
        flush=True,
    )
    for epoch in range(args.epochs):
        order = list(range(len(records)))
        random.Random(3407 + epoch).shuffle(order)
        for start in range(0, len(order), window_size):
            window = order[start : start + window_size]
            optimizer.zero_grad(set_to_none=True)
            loss_sum = 0.0
            for offset in range(0, len(window), args.batch_size):
                batch = [records[i] for i in window[offset : offset + args.batch_size]]
                hidden, pad = features(model, batch)
                logits = head_logits(model, batch, hidden, pad)
                targets = torch.zeros_like(logits)
                for index, record in enumerate(batch):
                    targets[index, : len(record["target_probabilities"])] = (
                        torch.tensor(
                            record["target_probabilities"], device=logits.device
                        )
                    )
                loss = -(targets * logits.log_softmax(-1)).sum(-1).mean()
                if not torch.isfinite(loss):
                    raise ValueError("Omni training produced nonfinite loss")
                (loss * (len(batch) / len(window))).backward()
                loss_sum += float(loss.detach()) * len(batch) / len(window)
            norm = torch.nn.utils.clip_grad_norm_(trained.values(), 1.0)
            if not torch.isfinite(norm):
                raise ValueError("Omni training produced nonfinite gradients")
            optimizer.step()
            updates += 1
            losses.append(loss_sum)
            print(f"Optimizer update {updates}: loss {loss_sum:.6f}", flush=True)
            if args.max_steps is not None and updates >= args.max_steps:
                break
        if args.max_steps is not None and updates >= args.max_steps:
            break
    delta = max(
        float((parameter.detach().cpu() - before[name]).abs().max())
        for name, parameter in trained.items()
    )
    if (
        updates < 1
        or not math.isfinite(delta)
        or delta <= 0
        or frozen_before != frozen_digest(model)
    ):
        raise ValueError(
            "Omni did not make a finite native-head update with unchanged frozen weights"
        )
    model.eval()
    # Calibrate the trained head once. The original publisher temperatures apply
    # to the base checkpoint and are not silently reused for a domain export.
    model.config.temperatures = {}
    metadata = {
        "format": "domain-liquid-omni-runtime-v1",
        "model": MODEL,
        "revision": REVISION,
        "training_method": "native-head",
        "questions": questions,
        "max_length": args.max_length,
        "temperatures": {name: 1.0 for name in questions},
    }
    engine.metadata, engine.max_length = metadata, args.max_length
    probes = rows["validation"][: min(8, len(rows["validation"]))]
    expected = [engine.predict(row["state"], questions) for row in probes]
    exported = out / "checkpoint"
    model.save_pretrained(exported, safe_serialization=True, max_shard_size="3GB")
    for name in ASSETS:
        if name != "config.json":
            shutil.copyfile(checkpoint / name, exported / name)
    config = checked_runtime(checkpoint, base=True)
    config["temperatures"] = {}
    (exported / "config.json").write_text(dumps(config), encoding="utf-8")
    metadata_path = exported / "dmt_omni.json"
    metadata_path.write_text(ordered_dumps(metadata), encoding="utf-8")
    trainable_parameters = sum(parameter.numel() for parameter in trained.values())
    del optimizer, before, trained, model, engine, hidden, logits, targets, loss
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print(
        "Reloading the portable native omni export and fitting reserved calibration...",
        flush=True,
    )
    served = OmniEngine(exported, device=args.device)
    actual = [served.predict(row["state"], questions) for row in probes]
    equivalence = compare_predictions(expected, actual, questions, 0.005)
    calibration_predictions = [
        served.predict(row["state"], questions) for row in rows["calibration"]
    ]
    fitted = fit_temperatures(rows["calibration"], calibration_predictions, questions)
    metadata["temperatures"] = {
        name: fit["temperature"] for name, fit in fitted.items()
    }
    served.metadata = metadata
    metadata_path.write_text(ordered_dumps(metadata), encoding="utf-8")
    metrics = {
        (
            "calibration_metrics" if role == "calibration" else role
        ): evaluate_predictions(
            rows[role],
            [served.predict(row["state"], questions) for row in rows[role]],
            questions,
        )
        for role in ("validation", "calibration", "test")
    }
    if source_fingerprint() != source or any(
        file_sha(Path(args.data) / (role + ".jsonl")) != value
        for role, value in hashes.items()
    ):
        raise ValueError("Omni source or reserved inputs changed during training")
    report = {
        "backend": "liquid-omni",
        "model": MODEL,
        "revision": REVISION,
        "schema_conditioned": True,
        "source_sha256": source,
        "data_sha256": hashes,
        "counts": {role: len(records) for role, records in rows.items()},
        "configuration": configuration,
        "local_study_sha256": local_request,
        "packages": packages,
        "training": {
            "method": "native-head",
            "optimizer_updates": updates,
            "trainable_parameters": trainable_parameters,
            "max_parameter_delta": delta,
            "frozen_parameters_sha256": frozen_before,
            "frozen_parameters_unchanged": True,
            "mean_loss": sum(losses) / len(losses),
            "dtype": "float32",
        },
        "calibration": fitted,
        **metrics,
        "reload_equivalence": True,
        "export_qualification": equivalence,
        "limitations": [
            "Native text/JSON head adaptation; media encoders frozen and media/domain quality not qualified",
            "No upstream fine-tuning recipe is implied by this project-defined head method",
            "Calibration is fit only on reserved real records",
        ],
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_manifest(out, "liquid-omni", questions, "checkpoint")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--local-study-request")
    for name in ("epochs", "batch_size", "grad_accum", "max_length", "max_steps"):
        parser.add_argument(
            "--" + name.replace("_", "-"), type=int, default=DEFAULTS[name]
        )
    parser.add_argument(
        "--learning-rate", type=float, default=DEFAULTS["learning_rate"]
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        train_omni(args)
        print("Saved the independently reloaded omni domain export.", flush=True)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Omni training failed: {exc}\n")


if __name__ == "__main__":
    main()
