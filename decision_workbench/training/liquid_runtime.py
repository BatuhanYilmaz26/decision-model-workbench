"""Pinned d1-3B assets, native readout and domain calibration for local exports."""

import copy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path

from decision_workbench.data import dumps, options, validate, validate_questions
from .artifacts import file_sha
from .liquid_data import ASSETS, MODEL, REVISION, LiquidPrompt
from .metrics import normalize_answers

RUNTIME_ASSETS = {
    **ASSETS,
    "api.py": "94e9f82eabfdf92c4abe1153e9a6dc4dd9821f9cc2fec3221e79e70723d8ada0",
    "hybrid.py": "ce579827b952a6978a3560f70e74be9e228f9029de46048996b0c5f58d7f1f39",
    "lfm2_vl.py": "f9e8d3354572a169237865283594b0364209fdb858901121b9f99147328b5d95",
    "modeling_d1.py": "7fc6a001f80fc2d146f2a51128478f3b3bd4f1423a3ac129bc10f3fd0caf1d98",
    "runner.py": "24d3ae718e9f9717771c0df39c93d324d00aa27bf3e12bab085c0922096cb0dd",
    "processor_config.json": "b9b82ad34a69e2f70a9033741e11f82b8a98bb5f8e6c13b3b2d5efdd3935d916",
    "chat_template.jinja": "86f4770449a4797c9b4212d110b0cc70fb993c1ce960095bcfb12eea22f61cca",
}
CONFIG_STRUCTURE = "e6b2fc30cd2b81aa23cc14693e8787b489420f7d43149e2fdb773df2fdfae8cf"
BASE_WEIGHTS = "50e03317847caf6df9a9aee27ed40f20554a86a21e60d1d47ba41a422b546c0c"
PINNED_PACKAGES = {
    "transformers": "5.19.0",
    "trl": "1.15.0",
    "peft": "0.21.2",
    "datasets": "4.8.5",
    "tokenizers": "0.23.3",
}
TEMPERATURES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)


def require_cpu_memory(*, training=False):
    import psutil

    gib = 20 if training else 16
    if psutil.virtual_memory().available < gib * 1024**3:
        raise ValueError(
            f"This float32 d1-3B CPU profile requires at least {gib} GiB available RAM; "
            "use a compatible CUDA GPU, a remote GPU, or the CPU comparison baseline"
        )


def runtime_identity():
    installed = {name: importlib.metadata.version(name) for name in PINNED_PACKAGES}
    if installed != PINNED_PACKAGES:
        raise ValueError("Use the separate requirements/liquid-trl.txt environment")
    installed["torch"] = importlib.metadata.version("torch")
    if installed["torch"].split("+")[0] != "2.10.0":
        raise ValueError("The Liquid TRL profile requires Torch 2.10.0")
    return installed


def checked_runtime(folder, *, base=False):
    """Check every executable/templating asset before enabling HF custom code."""
    folder = Path(folder)
    for name, expected in RUNTIME_ASSETS.items():
        path = folder / name
        if (
            not path.is_file()
            or path.stat().st_size > 32 * 1024 * 1024
            or file_sha(path) != expected
        ):
            raise ValueError(
                f"Liquid runtime asset differs from the reviewed revision: {name}"
            )
    config_path = folder / "config.json"
    if config_path.stat().st_size > 65536:
        raise ValueError("Liquid configuration exceeds 64 KB")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    structure = copy.deepcopy(config)
    for item in (
        structure,
        structure.get("text_config", {}),
        structure.get("vision_config", {}),
    ):
        for key in ("dtype", "torch_dtype", "transformers_version"):
            item.pop(key, None)
    if hashlib.sha256(dumps(structure).encode()).hexdigest() != CONFIG_STRUCTURE:
        raise ValueError(
            "Liquid configuration changes the pinned architecture or code mapping"
        )
    if base and file_sha(folder / "model.safetensors") != BASE_WEIGHTS:
        raise ValueError("Liquid base weights differ from the pinned checkpoint")
    return config


def resolve_checkpoint(cache, *, download=False):
    from huggingface_hub import snapshot_download

    path = Path(
        snapshot_download(
            MODEL,
            revision=REVISION,
            token=False,
            cache_dir=str(cache),
            local_files_only=not download,
            allow_patterns=[*RUNTIME_ASSETS, "config.json", "model.safetensors"],
        )
    )
    checked_runtime(path, base=True)
    return path


def apply_temperature(probabilities, temperature):
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0.25 <= temperature <= 10
    ):
        raise ValueError("Liquid temperature must be finite and between 0.25 and 10")
    if not probabilities or any(
        isinstance(p, bool)
        or not isinstance(p, (int, float))
        or not math.isfinite(p)
        or not 0 <= p <= 1
        for p in probabilities
    ):
        raise ValueError("Liquid probabilities must be finite and within [0,1]")
    if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-5):
        raise ValueError("Liquid probabilities must sum to one")
    logits = [math.log(max(p, 1e-12)) / temperature for p in probabilities]
    maximum = max(logits)
    values = [math.exp(z - maximum) for z in logits]
    return [p / sum(values) for p in values]


def fit_temperatures(rows, predictions, questions):
    """Choose a fixed-grid temperature from reserved calibration only."""
    if len(rows) < 8 or len(rows) != len(predictions):
        raise ValueError(
            "Liquid calibration needs at least eight independent reviewed records"
        )
    for row in rows:
        validate(row, require_review=True)
        if row["metadata"]["origin"] != "real" or row["questions"] != questions:
            raise ValueError(
                "Liquid calibration requires reserved real gold with the pinned schema"
            )
    normalized = [normalize_answers(p, questions) for p in predictions]
    fitted = {}
    for name, question in questions.items():
        keys = options(question)
        losses = []
        for temperature in TEMPERATURES:
            loss = 0.0
            for row, prediction in zip(rows, normalized):
                target = row["gold"][name]["probabilities"]
                if (
                    set(target) != set(keys)
                    or any(
                        not math.isfinite(p) or not 0 <= p <= 1 for p in target.values()
                    )
                    or not math.isclose(sum(target.values()), 1, abs_tol=1e-6)
                ):
                    raise ValueError(
                        "Calibration gold must be complete normalized probabilities"
                    )
                values = apply_temperature(
                    [prediction[name]["probabilities"][k] for k in keys], temperature
                )
                loss -= sum(
                    target[k] * math.log(max(p, 1e-12)) for k, p in zip(keys, values)
                )
            losses.append((loss / len(rows), abs(math.log(temperature)), temperature))
        loss, _, temperature = min(losses)
        fitted[name] = {"temperature": temperature, "nll": loss, "records": len(rows)}
    return fitted


class LiquidEngine:
    """Native option-token scoring; no generated text or random replacement head."""

    def __init__(self, checkpoint, device="auto", *, base=False):
        runtime_identity()
        checkpoint = Path(checkpoint)
        checked_runtime(checkpoint, base=base)
        metadata_path = checkpoint / "dmt_liquid.json"
        if not base and metadata_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Liquid export metadata exceeds 16 MiB")
        self.metadata = (
            None if base else json.loads(metadata_path.read_text(encoding="utf-8"))
        )
        if self.metadata is not None:
            if (
                self.metadata.get("format") != "domain-liquid-runtime-v1"
                or self.metadata.get("revision") != REVISION
            ):
                raise ValueError("Liquid export metadata does not match this runtime")
            validate_questions(self.metadata["questions"])
            if set(self.metadata["temperatures"]) != set(self.metadata["questions"]):
                raise ValueError("Liquid calibration must cover every pinned question")
            for value in self.metadata["temperatures"].values():
                apply_temperature([0.5, 0.5], value)
            if (
                type(self.metadata["max_length"]) is not int
                or not 16 <= self.metadata["max_length"] <= 32768
            ):
                raise ValueError("Liquid export has an invalid input budget")
        self.max_length = 32768 if base else self.metadata["max_length"]
        import torch
        from transformers import AutoModel

        self.device = (
            "cuda"
            if device == "auto" and torch.cuda.is_available()
            else "cpu" if device == "auto" else device
        )
        if self.device == "cuda" and not torch.cuda.is_available():
            raise ValueError(
                "CUDA is unavailable; choose CPU inference or a remote GPU"
            )
        if self.device == "cpu":
            require_cpu_memory()
        self.dtype = (
            torch.float32
            if self.device == "cpu"
            else (
                torch.bfloat16
                if torch.cuda.is_bf16_supported(including_emulation=False)
                else torch.float16
            )
        )
        self.model = AutoModel.from_pretrained(
            str(checkpoint),
            local_files_only=True,
            trust_remote_code=True,
            use_safetensors=True,
            dtype=self.dtype,
            device_map={"": self.device},
            attn_implementation="sdpa",
        ).eval()
        self.prompt = LiquidPrompt(checkpoint)

    def predict(self, state, questions):
        validate_questions(questions)
        if self.metadata is not None and questions != self.metadata["questions"]:
            raise ValueError("Questions differ from the fine-tuned Liquid schema")
        if self.metadata is not None:
            questions = self.metadata["questions"]  # preserve trained option order
        if (
            not isinstance(state, (str, dict, list))
            or not state
            or len(dumps(state).encode()) > 64000
        ):
            raise ValueError("Provide nonempty text/JSON evidence under 64 KB")
        import torch

        answers = {}
        for name, question in questions.items():
            prompt, _, _ = self.prompt.decision(state, question)
            ids = self.prompt.encode(prompt)
            if len(ids) > self.max_length:
                raise ValueError("Liquid input exceeds the trained token budget")
            with torch.inference_mode():
                logits = (
                    self.model(
                        input_ids=torch.tensor([ids], device=self.device),
                        logits_to_keep=1,
                    )
                    .logits[0, -1]
                    .float()
                )
            values = self.prompt.native.readout(
                self.prompt, self.prompt.native.as_question(question), logits
            )
            # Native noul readout order is yes/no; canonical order is false/true.
            if question["type"] == "noul":
                values = [values[1], values[0]]
            temperature = (
                1.0 if self.metadata is None else self.metadata["temperatures"][name]
            )
            values = apply_temperature(values, temperature)
            answers[name] = {"probabilities": dict(zip(options(question), values))}
        return normalize_answers(answers, questions)
