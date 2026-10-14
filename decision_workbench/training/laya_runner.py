"""Native Laya fine-tuning with externally reserved calibration and locked test."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from .contracts import load_splits, pinned_checkpoint
from .engines import NativeEngine, native_items
from .metrics import evaluate_predictions
from .artifacts import compare_predictions, write_manifest


def train_native(args):
    rows, hashes = load_splits(args.data)
    from .provenance import training_provenance

    provenance = training_provenance(args, hashes)
    questions = rows["train"][0]["questions"]
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Choose an empty output directory")
    import torch
    from safetensors.torch import save_file
    from laya.train import (
        TrainConfig,
        train_model,
        calibration_records,
        calibration_report,
        optimizer_updates,
    )
    from laya.calibrate import fit_temperature_map

    checkpoint = pinned_checkpoint(
        args.model,
        args.revision,
        os.environ.get("DMT_MODEL_CACHE", "data/model-cache/hub"),
    )
    engine = NativeEngine(checkpoint, args.device)
    config = engine.config
    config["max_len"] = args.max_length or config["max_len"]
    config["head_max_len"] = int(config.get("head_max_len", 192))
    # Base-study calibrators must not survive an external domain calibration fit.
    for key in (
        "temperature_by_options",
        "lang_temperatures",
        "binning_map",
        "training",
    ):
        config.pop(key, None)
    items = {
        name: native_items(engine.tokenizer, config, split)
        for name, split in rows.items()
    }
    settings = TrainConfig(
        epochs=args.epochs,
        micro_batch=args.batch_size,
        grad_accum=args.grad_accum,
        freeze_encoder=not args.train_encoder,
        loss="soft-ce",
        seed=3407,
        max_len=config["max_len"],
        head_max_len=config["head_max_len"],
        shuffle_options=("choice", "noul"),
        log_every=10,
        gradient_checkpointing=True,
    )
    if engine.device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
        free, total = torch.cuda.mem_get_info()
        weight_bytes = sum(
            p.numel() * p.element_size() for p in engine.model.parameters()
        )
        estimated = weight_bytes * 3 if args.train_encoder else 512 * 1024 * 1024
        if free < estimated:
            raise ValueError(
                "Insufficient measured free GPU memory for the selected native training profile; use encoder LoRA or a frozen encoder"
            )
    before = {
        name: p.detach().cpu().clone()
        for name, p in engine.model.named_parameters()
        if not name.startswith("encoder.")
    }
    history = train_model(
        engine.model,
        engine.tokenizer,
        items["train"],
        settings,
        engine.device,
        config["max_len"],
        config["head_max_len"],
        parallel=config.get("option_layout") == "parallel",
    )
    delta = max(
        float((p.detach().cpu() - before[name]).abs().max())
        for name, p in engine.model.named_parameters()
        if name in before
    )
    if not delta > 0:
        raise ValueError("Training did not update a decision-head parameter")
    records = calibration_records(
        engine.model,
        engine.tokenizer,
        items["calibration"],
        engine.device,
        config["max_len"],
        config["head_max_len"],
        batch_size=args.batch_size,
        parallel=config.get("option_layout") == "parallel",
    )
    fitted = fit_temperature_map(records)
    config["temperature"] = fitted["temperature"]
    config["temperature_by_options"] = fitted["temperature_by_options"]
    calibration = calibration_report(records, config["temperature"])
    probes = rows["validation"][: min(20, len(rows["validation"]))]
    expected = [engine.predict(row["state"], questions) for row in probes]
    merged = out / "merged"
    merged.mkdir(parents=True)
    (merged / "encoder").mkdir()
    # Preserve fp32 checkpoint values: native convenience export casts to fp16.
    save_file(
        {
            name: t.detach().cpu().contiguous()
            for name, t in engine.model.state_dict().items()
        },
        str(merged / "model.safetensors"),
    )
    engine.model.encoder.config.save_pretrained(merged / "encoder")
    engine.tokenizer.save_pretrained(merged / "tokenizer")
    config.update(
        fine_tuned=True,
        training={
            "protocol": "external-four-split-v1",
            "base_revision": args.revision,
            "freeze_encoder": not args.train_encoder,
            "seed": 3407,
        },
    )
    (merged / "rl_agent_config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    device = str(engine.device)
    peak = (
        int(torch.cuda.max_memory_allocated()) if engine.device.type == "cuda" else None
    )
    del engine, items, before, records
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    served = NativeEngine(merged, args.device)
    actual = [served.predict(row["state"], questions) for row in probes]
    equivalence = compare_predictions(expected, actual, questions, 1e-6)
    validation = [served.predict(row["state"], questions) for row in rows["validation"]]
    # Final test metrics use the exact exported serving runtime, after calibration.
    test = [served.predict(row["state"], questions) for row in rows["test"]]
    report = {
        "backend": "laya-native",
        "schema_conditioned": True,
        "model": args.model,
        "revision": args.revision,
        "data_sha256": hashes,
        **provenance,
        "configuration": vars(args),
        "counts": {name: len(split) for name, split in rows.items()},
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("laya", "torch", "transformers", "numpy", "safetensors")
        },
        "hardware": {
            "device": device,
            "peak_gpu_bytes": peak,
            "gpu": torch.cuda.get_device_name() if device.startswith("cuda") else None,
        },
        "training": {
            "loss": history,
            "optimizer_updates": optimizer_updates(
                len(rows["train"]) * len(questions),
                args.batch_size,
                args.grad_accum,
                args.epochs,
            ),
            "head_max_parameter_delta": delta,
            "encoder_frozen": not args.train_encoder,
        },
        "calibration": calibration,
        "validation": evaluate_predictions(rows["validation"], validation, questions),
        "test": evaluate_predictions(rows["test"], test, questions),
        "reload_equivalence": True,
        "export_qualification": equivalence,
        "export_precision": "float32",
        "limitations": [
            "Mechanical acceptance is separate from reviewed enterprise gold quality",
            "Type/bucket temperatures were fitted exclusively on the reserved calibration split",
        ],
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_manifest(out, "laya-native", questions)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--out", required=True)
    parser.add_argument("--study-request", help="Frozen remote-study request.json")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-length", type=int)
    parser.add_argument("--train-encoder", action="store_true")
    parser.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda", "mps"]
    )
    args = parser.parse_args()
    if min(args.epochs, args.batch_size, args.grad_accum) < 1:
        parser.error("Training counts must be positive")
    report = train_native(args)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
