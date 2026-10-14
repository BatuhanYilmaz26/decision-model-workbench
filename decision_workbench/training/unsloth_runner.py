"""Pinned Unsloth four-split fine-tuning with exported-runtime qualification."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import gc
import importlib.metadata
import json
import os
from pathlib import Path
from .contracts import load_splits, pinned_checkpoint
from .engines import UnslothEngine, native_items
from .metrics import evaluate_predictions
from .artifacts import compare_predictions, write_manifest


def train_unsloth(args):
    rows, hashes = load_splits(args.data)
    from .provenance import training_provenance

    provenance = training_provenance(args, hashes)
    questions = rows["train"][0]["questions"]
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Choose an empty output directory")
    # Import Unsloth before Transformers, including in the isolated serving process.
    from unsloth import FastDecisionModel, DecisionTrainer, is_bfloat16_supported
    import torch
    from transformers import TrainingArguments

    checkpoint = pinned_checkpoint(
        args.model,
        args.revision,
        os.environ.get("DMT_MODEL_CACHE", "data/model-cache/hub"),
    )
    encoder_checkpoint = (checkpoint / "rl_agent_config.json").is_file() and not (
        checkpoint / "clef_config.json"
    ).is_file()
    model, tokenizer = FastDecisionModel.from_pretrained(
        model_name=str(checkpoint),
        local_files_only=True,
        max_seq_length=args.max_length,
        load_in_4bit=not (args.full_precision or encoder_checkpoint),
        random_state=3407,
    )
    model = FastDecisionModel.get_peft_model(
        model,
        r=args.rank,
        lora_alpha=args.rank,
        lora_dropout=0,
        random_state=3407,
        use_gradient_checkpointing="unsloth",
    )
    items, reports = {}, {}
    for name, split in rows.items():
        if not getattr(model, "is_clef", False):
            native_items(tokenizer, model.decision_config, split)
        items[name], reports[name] = FastDecisionModel.build_dataset(
            split, tokenizer, model
        )
        if (
            not items[name]
            or reports[name].get("skipped")
            or reports[name].get("truncated")
        ):
            raise ValueError(name + " has skipped or truncated decision inputs")
    for key in (
        "temperature_by_options",
        "lang_temperatures",
        "binning_map",
        "head_temperature",
    ):
        model.decision_config.pop(key, None)
    model.decision_config["temperature"] = [1.0, 1.0, 1.0]
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    if not trainable:
        raise ValueError("The checkpoint has no trainable decision parameters")
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    trainer = DecisionTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=items["train"],
        eval_dataset=items["validation"],
        args=TrainingArguments(
            output_dir=str(out / "checkpoints"),
            num_train_epochs=args.epochs,
            max_steps=args.max_steps or -1,
            per_device_train_batch_size=args.batch_size,
            per_device_eval_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            learning_rate=2e-4,
            eval_strategy="epoch",
            save_strategy="no",
            logging_steps=5,
            bf16=is_bfloat16_supported(),
            fp16=not is_bfloat16_supported(),
            report_to="none",
            seed=3407,
            dataloader_num_workers=0,
        ),
    )
    trained = trainer.train()
    optimizer_updates = int(trainer.state.global_step)
    if optimizer_updates < 1:
        raise ValueError("Training made no optimizer updates")
    calibration = FastDecisionModel.calibrate(
        model, tokenizer, items["calibration"], batch_size=args.batch_size
    )
    required_types = (
        {item["qtype"] for item in items["calibration"]}
        if not getattr(model, "is_clef", False)
        else None
    )
    if (
        required_types is not None
        and set(calibration.get("fitted_types", [])) != required_types
    ):
        raise ValueError(
            "Reserved calibration has too few items to fit every question type"
        )
    FastDecisionModel.for_inference(model)
    live = UnslothEngine.__new__(UnslothEngine)
    live.api = FastDecisionModel
    live.model = model
    live.tokenizer = tokenizer
    probes = rows["validation"][: min(20, len(rows["validation"]))]
    expected = [live.predict(row["state"], questions) for row in probes]
    # Both encoder and language-backbone heads support complete merged checkpoints.
    # Encoder wrappers do not expose the language-head save_pretrained adapter API.
    model.save_pretrained_merged(str(out / "merged"))
    peak = int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else None
    del live, trainer, model, tokenizer, items
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    served = UnslothEngine(out / "merged")
    actual = [served.predict(row["state"], questions) for row in probes]
    qualification = compare_predictions(expected, actual, questions, 0.005)
    validation = [served.predict(row["state"], questions) for row in rows["validation"]]
    test = [served.predict(row["state"], questions) for row in rows["test"]]
    report = {
        "backend": "unsloth",
        "schema_conditioned": True,
        "model": args.model,
        "revision": args.revision,
        "data_sha256": hashes,
        **provenance,
        "configuration": vars(args),
        "preparation": reports,
        "counts": {name: len(split) for name, split in rows.items()},
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "unsloth",
                "unsloth_zoo",
                "transformers",
                "torch",
                "peft",
                "laya",
            )
        },
        "hardware": {
            "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else None,
            "peak_gpu_bytes": peak,
        },
        "training": {
            "metrics": trained.metrics,
            "optimizer_updates": optimizer_updates,
            "trainable_parameters": trainable,
        },
        "calibration": calibration,
        "validation": evaluate_predictions(rows["validation"], validation, questions),
        "test": evaluate_predictions(rows["test"], test, questions),
        "reload_equivalence": True,
        "export_qualification": qualification,
        "export_precision": "merged_16bit",
        "probability_source": "Unsloth 2026.10.3 raw logits and served temperatures, without public-output rounding",
        "limitations": [
            "Export tolerance was fixed before training and is reported per artifact",
            "Mechanical lifecycle acceptance is separate from enterprise domain gold quality",
        ],
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_manifest(out, "unsloth", questions)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--out", required=True)
    parser.add_argument("--study-request", help="Frozen remote-study request.json")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-length", type=int)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--full-precision", action="store_true")
    args = parser.parse_args()
    if min(args.epochs, args.rank, args.batch_size, args.grad_accum) < 1 or (
        args.max_steps is not None and args.max_steps < 1
    ):
        parser.error("Training counts must be positive")
    print(json.dumps(train_unsloth(args), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
