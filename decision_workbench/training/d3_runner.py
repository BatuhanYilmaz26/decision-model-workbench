"""Adapt D3's original answer-code readout with its native frozen backbone."""

# ruff: noqa: E402 -- Join the owning process job before model imports.
from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import random
import shutil

from .artifacts import compare_predictions, file_sha, write_manifest
from .d3_assets import ASSETS, MODEL, REVISION, WEIGHTS
from .d3_data import export_dataset, native_record
from .d3_local import DEFAULTS, settings, validate_request
from .d3_runtime import D3Engine, features, resolve_checkpoint, runtime_identity
from .liquid_data import load_inputs, dumps as ordered_dumps
from .liquid_runtime import fit_temperatures
from .metrics import evaluate_predictions
from .provenance import source_fingerprint


def frozen_digest(model):
    import torch

    value = hashlib.sha256()
    for name, tensor in model.named_parameters():
        value.update(name.encode())
        value.update(
            tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes()
        )
    return value.hexdigest()


def train_d3(args):
    configuration = settings({name: getattr(args, name) for name in DEFAULTS})
    packages = runtime_identity()
    rows, bodies = load_inputs(args.data)
    if len(rows["calibration"]) < 8:
        raise ValueError("D3 needs at least eight reserved real calibration records")
    questions = rows["train"][0]["questions"]
    source = source_fingerprint()
    hashes = {role: hashlib.sha256(body).hexdigest() for role, body in bodies.items()}
    local_request = (
        validate_request(args.local_study_request, args, hashes, source, rows)
        if args.local_study_request
        else None
    )
    out = Path(args.out).absolute()
    if out.exists():
        raise ValueError("Choose a new D3 run directory; existing runs are preserved")
    checkpoint = resolve_checkpoint(args.cache, download=args.download)
    engine = D3Engine(checkpoint, device=args.device, base=True)
    engine.max_length = args.max_length
    out.mkdir(parents=True)
    export_dataset(args.data, out / "data", engine.prompt, args.max_length)
    import torch
    from safetensors.torch import save_file

    torch.manual_seed(3407)
    runtime = engine.runtime
    if any(parameter.requires_grad for parameter in runtime.backbone.parameters()):
        raise ValueError("The native D3 backbone must remain frozen")
    frozen_before = frozen_digest(runtime.backbone)
    head = torch.nn.Parameter(runtime.readout.detach().clone())
    before = head.detach().cpu().clone()
    runtime.readout = head
    optimizer = torch.optim.AdamW([head], lr=args.learning_rate, weight_decay=0.01)
    records = [
        native_record(engine.prompt, row, name, args.max_length)
        for row in rows["train"]
        for name in questions
    ]
    updates, losses = 0, []
    window_size = args.batch_size * args.grad_accum
    print(
        "Training the original D3 answer-code readout; native backbone remains frozen...",
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
                batch = [
                    records[index]
                    for index in window[offset : offset + args.batch_size]
                ]
                hidden = features(runtime, batch)
                logits = hidden @ head.T
                terms = []
                for index, record in enumerate(batch):
                    target = torch.tensor(
                        record["target_probabilities"], device=logits.device
                    )
                    terms.append(
                        -(target * logits[index, : len(target)].log_softmax(-1)).sum()
                    )
                loss = torch.stack(terms).mean()
                if not torch.isfinite(loss):
                    raise ValueError("D3 training produced nonfinite loss")
                (loss * (len(batch) / len(window))).backward()
                loss_sum += float(loss.detach()) * len(batch) / len(window)
            norm = torch.nn.utils.clip_grad_norm_([head], 1.0)
            if not torch.isfinite(norm):
                raise ValueError("D3 training produced nonfinite gradients")
            optimizer.step()
            updates += 1
            losses.append(loss_sum)
            print(f"Optimizer update {updates}: loss {loss_sum:.6f}", flush=True)
            if args.max_steps is not None and updates >= args.max_steps:
                break
        if args.max_steps is not None and updates >= args.max_steps:
            break
    delta = float((head.detach().cpu() - before).abs().max())
    if (
        updates < 1
        or not math.isfinite(delta)
        or delta <= 0
        or frozen_digest(runtime.backbone) != frozen_before
    ):
        raise ValueError(
            "D3 requires finite readout updates and an unchanged frozen backbone"
        )
    metadata = {
        "format": "domain-d3-runtime-v1",
        "model": MODEL,
        "revision": REVISION,
        "training_method": "native-readout",
        "questions": questions,
        "max_length": args.max_length,
        "device": runtime.device.type,
        "temperatures": {name: 1.0 for name in questions},
    }
    engine.metadata = metadata
    probes = rows["validation"][: min(8, len(rows["validation"]))]
    expected = [engine.predict(row["state"], questions) for row in probes]
    exported = out / "checkpoint"
    exported.mkdir()
    for name in (*ASSETS, WEIGHTS):
        shutil.copyfile(checkpoint / name, exported / name)
    save_file(
        {"weight": head.detach().cpu().contiguous()},
        str(exported / "readout.safetensors"),
    )
    metadata_path = exported / "dmt_d3.json"
    metadata_path.write_text(ordered_dumps(metadata), encoding="utf-8")
    device = str(runtime.device)
    trainable_parameters = head.numel()
    del optimizer, before, head, runtime, engine, hidden, logits, loss, terms
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print(
        "Reloading the D3 domain bundle and fitting reserved calibration...", flush=True
    )
    served = D3Engine(exported, device=args.device)
    actual = [served.predict(row["state"], questions) for row in probes]
    equivalence = compare_predictions(expected, actual, questions, 1e-5)
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
        "calibration_metrics" if role == "calibration" else role: evaluate_predictions(
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
        raise ValueError("D3 source or reserved inputs changed during training")
    report = {
        "backend": "d3-edge",
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
            "method": "native-readout",
            "optimizer_updates": updates,
            "trainable_parameters": trainable_parameters,
            "max_parameter_delta": delta,
            "frozen_parameters_sha256": frozen_before,
            "frozen_parameters_unchanged": True,
            "mean_loss": sum(losses) / len(losses),
            "backbone_dtype": "bfloat16",
            "readout_dtype": "float32",
            "device": device,
        },
        "calibration": fitted,
        **metrics,
        "reload_equivalence": True,
        "export_qualification": equivalence,
        "limitations": [
            "Project-defined native readout adaptation, not an upstream fine-tuning recipe",
            "Text/JSON only; no media, optimized kernels or other D3 variants qualified",
            "Fitted temperatures apply only to the reserved domain schema/runtime",
        ],
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_manifest(out, "d3-edge", questions, "checkpoint")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "out", "cache"):
        parser.add_argument("--" + name, required=True)
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
        train_d3(args)
        print("Saved the independently reloaded D3 domain bundle.", flush=True)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"D3 training failed: {exc}\n")


if __name__ == "__main__":
    main()
