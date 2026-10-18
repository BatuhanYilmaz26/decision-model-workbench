"""Offline native Perplexity prediction with its saved calibrated readout."""

import importlib.metadata
import math
import os
from pathlib import Path
import sys

from decision_workbench.data import options, validate_questions
from decision_workbench.json_tools import strict_json
from .metrics import normalize_answers
from .perplexity_data import PerplexityPrompt
from .perplexity_profile import MODEL, REVISION, PARAMETERS, WEIGHT_BYTES
from .perplexity_checkpoint import asset, native_files, publisher_module, tensor_digest

PACKAGES = {
    "torch": "2.14.0",
    "torchvision": "0.29.0",
    "transformers": "5.17.0",
    "accelerate": "1.15.0",
    "pillow": "12.3.0",
    "safetensors": "0.8.0",
    "numpy": "2.5.3",
    "huggingface-hub": "1.31.0",
    "tokenizers": "0.23.3",
    "jinja2": "3.1.6",
    "flash-linear-attention": "0.5.2",
}


def runtime_identity():
    if sys.version_info < (3, 12):
        raise ValueError("The publisher's native code requires Python 3.12 or later")
    try:
        installed = {name: importlib.metadata.version(name) for name in PACKAGES}
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError(
            "Install the separate requirements/perplexity-native.txt environment; a native dependency is missing"
        ) from exc
    if any(
        installed[name].split("+")[0] != version for name, version in PACKAGES.items()
    ):
        raise ValueError(
            "Use the separate requirements/perplexity-native.txt environment"
        )
    return installed


def available_ram():
    if sys.platform != "linux":
        raise ValueError(
            "This native execution profile requires Linux; use WSL2 or a Linux GPU host"
        )
    lines = Path("/proc/meminfo").read_text().splitlines()
    return (
        int(next(line.split()[1] for line in lines if line.startswith("MemAvailable:")))
        * 1024
    )


def memory_guard(device, *, training=False):
    import torch

    if device not in ("cpu", "cuda"):
        raise ValueError("Choose CPU or CUDA for native Perplexity execution")
    ram = available_ram()
    if device == "cuda":
        if not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable in this model environment")
        minimum = int(WEIGHT_BYTES * (2.2 if training else 1.2))
        if torch.cuda.mem_get_info(0)[0] < minimum:
            raise ValueError(
                "Insufficient free GPU memory for the native weights/gradients; see the Perplexity guide"
            )
    if training:
        if device != "cuda":
            raise ValueError("This full-weight training profile requires CUDA")
        minimum_ram = int(PARAMETERS * 16 * 1.1)
    else:
        # The publisher constructs the backbone on CPU before moving it to
        # CUDA, so GPU inference also needs host staging memory.
        minimum_ram = int(WEIGHT_BYTES * (2.3 if device == "cpu" else 1.2))
    if ram < minimum_ram:
        raise ValueError(
            "Insufficient available host RAM for the native runtime/CPU optimizer states"
        )


class PerplexityEngine:
    """Use only complete domain exports produced by perplexity_runner."""

    def __init__(self, checkpoint, device="auto"):
        runtime_identity()
        self.folder = Path(checkpoint).resolve()
        metadata_path = self.folder / "dmt_perplexity.json"
        if metadata_path.stat().st_size > 1024**2:
            raise ValueError("Native domain metadata exceeds 1 MiB")
        self.metadata = strict_json(metadata_path.read_bytes())
        if (
            not isinstance(self.metadata, dict)
            or self.metadata.get("format") != "domain-perplexity-runtime-v1"
            or self.metadata.get("model") != MODEL
            or self.metadata.get("revision") != REVISION
        ):
            raise ValueError("Native domain metadata differs from this profile")
        if type(self.metadata.get("temperature")) not in (int, float):
            raise ValueError("Native domain temperature must be numeric")
        self.questions = self.metadata["questions"]
        validate_questions(self.questions)
        if any(q["type"] not in ("choice", "noul") for q in self.questions.values()):
            raise ValueError("This native profile supports Choice and Noul only")
        maximum = self.metadata.get("max_length")
        if type(maximum) is not int or not 64 <= maximum <= 8192:
            raise ValueError("Native export token budget must be 64–8192")
        self.publisher = self.folder.parent / "publisher"
        self.prompt = PerplexityPrompt(self.publisher)
        config, mapping, _ = native_files(self.folder, self.publisher)
        for name in (
            "config.json",
            "processor_config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "chat_template.jinja",
        ):
            if (self.folder / name).read_bytes() != asset(self.publisher, name):
                raise ValueError(
                    "Export processor/architecture differs from the pinned profile"
                )
        if tensor_digest(self.folder, mapping, readout=True) != self.metadata.get(
            "readout_payload_sha256"
        ):
            raise ValueError("Native readout differs from its domain metadata")
        if config["temperature"] != self.metadata.get("temperature"):
            raise ValueError(
                "Native calibration temperature differs from its domain metadata"
            )
        import torch

        picked = (
            "cuda"
            if device == "auto" and torch.cuda.is_available()
            else "cpu" if device == "auto" else device
        )
        memory_guard(picked)
        os.environ.update(
            HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1"
        )
        module = publisher_module(self.publisher, "model")
        self.model = module.DecisionModel(
            self.folder, device=picked, max_image_pixels=512 * 512
        )

    def predict(self, state, questions):
        if questions != self.questions or any(
            list(options(questions[k])) != list(options(self.questions[k]))
            for k in self.questions
        ):
            raise ValueError(
                "Native prediction must use the frozen domain schema and option order"
            )
        records = []
        for question in questions.values():
            self.prompt.prepare(state, question, self.metadata["max_length"])
            records.append({"state": state, "question": question})
        distributions = self.model.predict(records, batch_size=1)
        if len(distributions) != len(questions):
            raise ValueError("Native prediction omitted a question")
        answers = {}
        for (name, question), values in zip(questions.items(), distributions):
            keys = options(question)
            if (
                len(values) != len(keys)
                or any(
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or not 0 <= v <= 1
                    for v in values
                )
                or not math.isclose(sum(values), 1.0, abs_tol=1e-5)
            ):
                raise ValueError(
                    "Native probabilities differ from the complete allowed outcomes"
                )
            answers[name] = {"probabilities": dict(zip(keys, values))}
        # DecisionModel.predict already applied the saved temperature. Do not
        # fit/apply a second temperature to these native probabilities.
        return normalize_answers(answers, questions)
