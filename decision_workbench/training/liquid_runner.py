"""Fine-tune d1-3B with native completion loss and independently reserved gold."""

# Parent ownership must be armed before importing numerical/model libraries.
# ruff: noqa: E402

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import gc
import json
import math
from pathlib import Path
import shutil

from decision_workbench.data import dumps
from .artifacts import compare_predictions, file_sha, write_manifest
from .liquid_data import MODEL, REVISION, export_dataset, load_inputs, sha
from .liquid_runtime import (
    LiquidEngine,
    LiquidPrompt,
    RUNTIME_ASSETS,
    checked_runtime,
    fit_temperatures,
    require_cpu_memory,
    resolve_checkpoint,
    runtime_identity,
)
from .metrics import evaluate_predictions
from .provenance import source_fingerprint


def validate_settings(args):
    for name, low, high in (
        ("epochs", 1, 20),
        ("rank", 1, 128),
        ("batch_size", 1, 32),
        ("grad_accum", 1, 128),
        ("max_length", 64, 32768),
    ):
        value = getattr(args, name)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
    if args.max_steps is not None and (
        type(args.max_steps) is not int or not 1 <= args.max_steps <= 100000
    ):
        raise ValueError("max_steps must be between 1 and 100000")
    if not math.isfinite(args.learning_rate) or not 0 < args.learning_rate <= 0.01:
        raise ValueError("learning_rate must be finite and within (0, 0.01]")


def encoded_completion(prompt, completion, tokenizer):
    """Keep only the decision token and end-of-turn as loss-bearing labels."""
    prefix = tokenizer.encode(prompt, add_special_tokens=False)
    suffix = tokenizer.encode(completion, add_special_tokens=False)
    joined = tokenizer.encode(prompt + completion, add_special_tokens=False)
    if len(suffix) != 2 or joined != prefix + suffix:
        raise ValueError(
            "Liquid SFT needs an intact single decision token and end-of-turn"
        )
    return {
        "input_ids": joined,
        "attention_mask": [1] * len(joined),
        "labels": [-100] * len(prefix) + suffix,
    }


class CompletionCollator:
    """Right-pad inputs without assigning loss to evidence or padding."""

    def __init__(self, pad_token_id):
        self.pad_token_id = pad_token_id

    def __call__(self, items):
        import torch

        length = max(len(item["input_ids"]) for item in items)
        batch = {"input_ids": [], "attention_mask": [], "labels": []}
        for item in items:
            pad = length - len(item["input_ids"])
            batch["input_ids"].append(item["input_ids"] + [self.pad_token_id] * pad)
            batch["attention_mask"].append(item["attention_mask"] + [0] * pad)
            batch["labels"].append(item["labels"] + [-100] * pad)
        return {
            key: torch.tensor(values, dtype=torch.long) for key, values in batch.items()
        }


def optimizer_counter():
    from transformers import TrainerCallback

    class Counter(TrainerCallback):
        updates = 0
        skipped = 0

        def on_optimizer_step(self, args, state, control, optimizer=None, **kwargs):
            if getattr(optimizer, "step_was_skipped", False):
                self.skipped += 1
            else:
                self.updates += 1
            return control

    return Counter()


def train_liquid(args):
    validate_settings(args)
    packages = runtime_identity()
    rows, bodies = load_inputs(args.data)
    if len(rows["calibration"]) < 8:
        raise ValueError(
            "Reserve at least eight real calibration records before training"
        )
    questions = rows["train"][0]["questions"]
    out = Path(args.out)
    if out.exists():
        raise ValueError(
            "Choose a new output directory; previous runs are never replaced"
        )
    import torch

    device = (
        "cuda"
        if args.device == "auto" and torch.cuda.is_available()
        else "cpu" if args.device == "auto" else args.device
    )
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError(
            "CUDA is unavailable; use a compatible GPU environment or --device cpu"
        )
    if device == "cpu":
        require_cpu_memory(training=True)
    print("Checking the pinned checkpoint and native data contract...", flush=True)
    checkpoint = resolve_checkpoint(args.cache, download=args.download)
    prompt = LiquidPrompt(checkpoint)
    preparation = export_dataset(args.data, out / "data", prompt, args.max_length)
    if preparation["input_sha256"] != {
        name: sha(body) for name, body in bodies.items()
    }:
        raise ValueError(
            "Reserved inputs changed while resolving the checkpoint; freeze the study and retry"
        )
    source = source_fingerprint()

    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForImageTextToText,
        AutoTokenizer,
        Trainer,
        set_seed,
    )
    from trl import SFTConfig, SFTTrainer

    dtype = (
        torch.float32
        if device == "cpu"
        else (
            torch.bfloat16
            if torch.cuda.is_bf16_supported(including_emulation=False)
            else torch.float16
        )
    )
    set_seed(3407)
    tokenizer = AutoTokenizer.from_pretrained(str(checkpoint), local_files_only=True)
    if tokenizer.pad_token_id is None:
        raise ValueError("The pinned tokenizer is missing its padding token")
    datasets = {}
    for split in ("train", "validation"):
        native = [
            json.loads(line)
            for line in (out / "data" / "trl" / (split + ".jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        datasets[split] = Dataset.from_list(
            [
                encoded_completion(item["prompt"], item["completion"], tokenizer)
                for item in native
            ]
        )
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    print("Loading d1-3B for language-layer LoRA training...", flush=True)
    model = AutoModelForImageTextToText.from_pretrained(
        str(checkpoint),
        local_files_only=True,
        use_safetensors=True,
        dtype=dtype,
        device_map={"": device},
        attn_implementation="sdpa",
    )
    model.config.use_cache = False
    model = get_peft_model(
        model,
        LoraConfig(
            r=args.rank,
            lora_alpha=2 * args.rank,
            lora_dropout=0,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|out_proj|in_proj|w1|w2|w3)",
        ),
    )
    parameters = {
        name: value.detach().float().cpu().clone()
        for name, value in model.named_parameters()
        if value.requires_grad
    }
    if not parameters or any("vision" in name for name in parameters):
        raise ValueError(
            "Liquid LoRA must update language layers and keep vision weights frozen"
        )
    trainable_count = sum(value.numel() for value in parameters.values())
    # TRL's fused head requires a CUDA/Triton kernel. CPU SFT uses the same
    # completion labels with Transformers' ordinary causal-LM cross entropy.
    trainer_class = Trainer if device == "cpu" else SFTTrainer
    counter = optimizer_counter()
    trainer = trainer_class(
        model=model,
        processing_class=tokenizer,
        train_dataset=datasets["train"],
        eval_dataset=datasets["validation"],
        data_collator=CompletionCollator(tokenizer.pad_token_id),
        callbacks=[counter],
        args=SFTConfig(
            output_dir=str(out / "checkpoints"),
            num_train_epochs=args.epochs,
            max_steps=args.max_steps or -1,
            per_device_train_batch_size=args.batch_size,
            per_device_eval_batch_size=1,
            gradient_accumulation_steps=args.grad_accum,
            gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            learning_rate=args.learning_rate,
            warmup_steps=0.05,
            lr_scheduler_type="cosine",
            eval_strategy="epoch",
            save_strategy="no",
            logging_steps=1,
            report_to="none",
            seed=3407,
            dataloader_num_workers=0,
            bf16=dtype == torch.bfloat16,
            fp16=dtype == torch.float16,
            use_cpu=device == "cpu",
            optim="adamw_torch",
            max_length=args.max_length,
            packing=False,
            dataset_kwargs={"skip_prepare_dataset": True},
            remove_unused_columns=False,
            prediction_loss_only=True,
        ),
    )
    print(
        "Training on decision completions; held-out calibration/test data remain reserved...",
        flush=True,
    )
    result = trainer.train()
    attempts = int(trainer.state.global_step)
    updates = counter.updates
    delta = max(
        float((value.detach().float().cpu() - parameters[name]).abs().max())
        for name, value in model.named_parameters()
        if name in parameters
    )
    if updates < 1 or not math.isfinite(delta) or delta <= 0:
        raise ValueError("Training did not make a finite parameter update")
    model.eval()
    live = LiquidEngine.__new__(LiquidEngine)
    live.model, live.prompt, live.device = model, prompt, device
    live.metadata, live.max_length = None, args.max_length
    probes = rows["validation"][: min(8, len(rows["validation"]))]
    expected = [live.predict(row["state"], questions) for row in probes]
    model.save_pretrained(out / "adapter", safe_serialization=True)
    merged = model.merge_and_unload()
    merged.save_pretrained(out / "merged", safe_serialization=True)
    tokenizer.save_pretrained(out / "merged")
    # Restore the publisher's reviewed native API/code/tokenizer assets, never
    # model Python written by a third-party trainer or uploaded by an end user.
    for name in RUNTIME_ASSETS:
        shutil.copyfile(checkpoint / name, out / "merged" / name)
    config = checked_runtime(checkpoint)
    for item in (config, config["text_config"], config["vision_config"]):
        item["dtype"] = str(dtype).removeprefix("torch.")
    (out / "merged" / "config.json").write_text(dumps(config), encoding="utf-8")
    metadata = {
        "format": "domain-liquid-runtime-v1",
        "model": MODEL,
        "revision": REVISION,
        "questions": questions,
        "max_length": args.max_length,
        "temperatures": {name: 1.0 for name in questions},
        "native_assets_sha256": RUNTIME_ASSETS,
    }
    metadata_path = out / "merged" / "dmt_liquid.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    peak = int(torch.cuda.max_memory_allocated()) if device == "cuda" else None
    del parameters, live, trainer, model, merged, tokenizer, datasets
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    print(
        "Reloading the exported native runtime and checking validation predictions...",
        flush=True,
    )
    served = LiquidEngine(out / "merged", device=device)
    actual = [served.predict(row["state"], questions) for row in probes]
    equivalence = compare_predictions(expected, actual, questions, 0.005)
    calibration_predictions = [
        served.predict(row["state"], questions) for row in rows["calibration"]
    ]
    calibration = fit_temperatures(
        rows["calibration"], calibration_predictions, questions
    )
    metadata["temperatures"] = {
        name: value["temperature"] for name, value in calibration.items()
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    served.metadata = metadata
    validation = [served.predict(row["state"], questions) for row in rows["validation"]]
    test = [served.predict(row["state"], questions) for row in rows["test"]]
    if source_fingerprint() != source:
        raise ValueError(
            "Training source changed during the run; repeat from a frozen checkout"
        )
    for name, expected_hash in preparation["files"].items():
        if file_sha(out / "data" / name) != expected_hash:
            raise ValueError("Native/canonical training data changed during the run")
    report = {
        "backend": "liquid-trl",
        "schema_conditioned": True,
        "model": MODEL,
        "revision": REVISION,
        "source_sha256": source,
        "data_sha256": preparation["input_sha256"],
        "configuration": vars(args),
        "counts": {name: len(split) for name, split in rows.items()},
        "packages": packages,
        "preparation": preparation,
        "hardware": {
            "device": device,
            "gpu": torch.cuda.get_device_name() if device == "cuda" else None,
            "peak_gpu_bytes": peak,
        },
        "training": {
            "trainer": trainer_class.__name__,
            "optimizer_updates": updates,
            "optimizer_attempts": attempts,
            "optimizer_skipped": counter.skipped,
            "trainable_parameters": trainable_count,
            "max_parameter_delta": delta,
            "metrics": result.metrics,
            "loss_target": "native decision token and end-of-turn only",
            "vision_frozen": True,
        },
        "calibration": calibration,
        "validation": evaluate_predictions(rows["validation"], validation, questions),
        "test": evaluate_predictions(rows["test"], test, questions),
        "export_qualification": equivalence,
        "reload_equivalence": True,
        "export_precision": str(dtype).removeprefix("torch."),
        "limitations": [
            "Local mechanics do not establish domain quality or a sufficient training budget",
            "Calibration was fitted exclusively on the reserved calibration role",
            "Managed platform/cloud admission requires separate integration",
        ],
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_manifest(out, "liquid-trl", questions)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cache", default="data/model-cache/liquid-hub")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Allow downloading the pinned public 6.25-GB checkpoint",
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    args = parser.parse_args()
    try:
        report = train_liquid(args)
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        parser.exit(1, f"Training failed: {error}\n")
    print(
        json.dumps(
            {
                "export": str(Path(args.out).absolute()),
                "optimizer_updates": report["training"]["optimizer_updates"],
                "reload_qualified": report["export_qualification"]["passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
