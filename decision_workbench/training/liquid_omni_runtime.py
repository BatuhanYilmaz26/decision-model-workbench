"""Native bidirectional trunk and option-marker head for Liquid omni exports."""

import copy
import importlib.metadata
import math
from pathlib import Path

from decision_workbench.data import dumps, options, validate_questions
from decision_workbench.json_tools import strict_json
from .artifacts import file_sha
from .liquid_omni_data import (
    ASSETS,
    BASE_WEIGHTS,
    MODEL,
    REVISION,
    OmniPrompt,
    checked_asset,
)
from .liquid_runtime import apply_temperature
from .metrics import normalize_answers

CONFIG_STRUCTURE = "9e1ba94a26e3564b4814a72b410dd1368d66a41ae4e199b0ce12f2a5a863391c"
PACKAGES = {
    "transformers": "5.19.0",
    "tokenizers": "0.23.3",
    "huggingface-hub": "1.33.0",
    "safetensors": "0.8.0",
    "numpy": "2.5.2",
}


def runtime_identity():
    installed = {key: importlib.metadata.version(key) for key in PACKAGES}
    installed["torch"] = importlib.metadata.version("torch")
    if (
        any(installed[key] != value for key, value in PACKAGES.items())
        or installed["torch"].split("+")[0] != "2.10.0"
    ):
        raise ValueError(
            "Use the compatible requirements/liquid-omni.txt model environment"
        )
    return installed


def checked_runtime(folder, *, base=False):
    import hashlib

    folder = Path(folder)
    for name in ASSETS:
        if name != "config.json" or base:
            checked_asset(folder, name)
    path = folder / "config.json"
    if path.stat().st_size > 65536:
        raise ValueError("Omni configuration exceeds 64 KB")
    config = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Omni configuration must be a JSON object")
    structure = copy.deepcopy(config)
    temperatures = structure.pop("temperatures", {})
    if hashlib.sha256(dumps(structure).encode()).hexdigest() != CONFIG_STRUCTURE or (
        not base and temperatures != {}
    ):
        raise ValueError(
            "Omni architecture or native temperature policy differs from this profile"
        )
    weights = folder / "model.safetensors"
    if not weights.is_file() or weights.stat().st_size > 3 * 1024**3:
        raise ValueError("Omni native weights are missing or exceed this profile")
    if base and file_sha(weights) != BASE_WEIGHTS:
        raise ValueError("Omni weights differ from the pinned public checkpoint")
    return config


def resolve_checkpoint(cache, *, download=False):
    from huggingface_hub import snapshot_download

    folder = Path(
        snapshot_download(
            MODEL,
            revision=REVISION,
            token=False,
            cache_dir=str(cache),
            local_files_only=not download,
            allow_patterns=[*ASSETS, "model.safetensors"],
        )
    )
    checked_runtime(folder, base=True)
    return folder


def checked_device(device):
    """Keep the published CPU/CUDA floors before resolving or loading weights."""
    if device not in ("auto", "cpu", "cuda"):
        raise ValueError("Choose auto, CPU or CUDA for omni")
    if device == "cpu":
        from .memory_limits import available_cpu_memory

        if available_cpu_memory() < 6 * 1024**3:
            raise ValueError(
                "Omni float32 inference needs at least 6 GiB available system RAM"
            )
        return "cpu"
    import torch

    picked = (
        "cuda"
        if device == "auto" and torch.cuda.is_available()
        else "cpu" if device == "auto" else device
    )
    if picked == "cuda":
        if not torch.cuda.is_available() or torch.cuda.mem_get_info()[0] < 4 * 1024**3:
            raise ValueError(
                "Omni float32 needs at least 4 GiB free CUDA memory; choose CPU or a remote GPU"
            )
        return picked
    return checked_device("cpu")


def features(model, records):
    """Frozen native text trunk; padding never becomes evidence for the head."""
    import torch

    device = model.device
    longest = max(len(record["input_ids"]) for record in records)
    # The native trunk creates a dense additive mask. Head training can also
    # retain attention intermediates; refuse the estimated envelope before
    # allocating it instead of relying on a late GPU/host OOM.
    quadratic = 16 * 3 if model.head.training else 2
    estimate = len(records) * (quadratic * longest**2 * 4 + 64 * longest * 1024 * 4)
    if device.type == "cuda":
        available = torch.cuda.mem_get_info(device)[0]
    else:
        from .memory_limits import available_cpu_memory

        available = available_cpu_memory()
    if estimate + 512 * 1024**2 > available:
        raise ValueError(
            "Omni batch/context exceeds available memory; reduce batch size or input length, or use a larger CPU/GPU host"
        )
    sequences = [
        torch.tensor(record["input_ids"], dtype=torch.long, device=device)
        for record in records
    ]
    lengths = torch.tensor([len(ids) for ids in sequences], device=device)
    with torch.no_grad():
        embedded = torch.nn.utils.rnn.pad_sequence(
            [model.encoder.embed_tokens(ids) for ids in sequences], batch_first=True
        )
        pad = torch.arange(embedded.shape[1], device=device)[None] < lengths[:, None]
        hidden = model.encoder(
            embedded, pad, torch.zeros(len(records), device=device, dtype=torch.long)
        )
    return hidden.detach(), pad


def head_logits(model, records, hidden, pad):
    import torch

    width = max(len(record["marker_positions"]) for record in records)
    positions = torch.zeros(len(records), width, dtype=torch.long, device=hidden.device)
    valid = torch.zeros_like(positions, dtype=torch.bool)
    for index, record in enumerate(records):
        n = len(record["marker_positions"])
        positions[index, :n] = torch.tensor(
            record["marker_positions"], device=hidden.device
        )
        valid[index, :n] = True
    types = torch.tensor(
        [record["question_type"] for record in records], device=hidden.device
    )
    return model.head(hidden, pad, positions, valid, types)


class OmniEngine:
    def __init__(self, checkpoint, device="auto", *, base=False):
        runtime_identity()
        checkpoint = Path(checkpoint)
        checked_runtime(checkpoint, base=base)
        self.metadata = None
        self.max_length = 16384
        if not base:
            path = checkpoint / "dmt_omni.json"
            if path.stat().st_size > 16 * 1024**2:
                raise ValueError("Omni domain metadata exceeds 16 MiB")
            self.metadata = strict_json(path.read_text(encoding="utf-8"))
            m = self.metadata
            if (
                not isinstance(m, dict)
                or m.get("format") != "domain-liquid-omni-runtime-v1"
                or m.get("model") != MODEL
                or m.get("revision") != REVISION
                or m.get("training_method") != "native-head"
                or type(m.get("max_length")) is not int
                or not 64 <= m["max_length"] <= 16384
            ):
                raise ValueError(
                    "Omni metadata does not match the pinned native-head profile"
                )
            validate_questions(m.get("questions"))
            if (
                not isinstance(m.get("temperatures"), dict)
                or set(m["temperatures"]) != set(m["questions"])
                or any(
                    type(t) not in (int, float)
                    or not math.isfinite(t)
                    or not 0.25 <= t <= 10
                    for t in m["temperatures"].values()
                )
            ):
                raise ValueError("Omni calibration must cover every pinned question")
            self.max_length = m["max_length"]
        self.device = checked_device(device)
        import torch
        from transformers import AutoModel

        self.model = AutoModel.from_pretrained(
            str(checkpoint),
            trust_remote_code=True,
            local_files_only=True,
            use_safetensors=True,
            dtype=torch.float32,
            device_map={"": self.device},
        ).eval()
        self.prompt = OmniPrompt(checkpoint)

    def predict(self, state, questions):
        validate_questions(questions)
        if self.metadata is not None:
            if questions != self.metadata["questions"]:
                raise ValueError("Questions differ from the fine-tuned omni policy")
            questions = self.metadata["questions"]
        if (
            not isinstance(state, (str, dict, list))
            or not state
            or len(dumps(state).encode()) > 64000
        ):
            raise ValueError("Provide nonempty text/JSON evidence under 64 KB")
        import torch

        records = [
            self.prompt.decision(state, q, self.max_length) for q in questions.values()
        ]
        answers = {}
        named = list(questions.items())
        with torch.inference_mode():
            for start in range(0, len(records), 8):
                batch = records[start : start + 8]
                # Split long batches further to bound native dense-mask memory.
                limit = max(
                    1, min(len(batch), 8192 // max(len(r["input_ids"]) for r in batch))
                )
                for offset in range(0, len(batch), limit):
                    current = batch[offset : offset + limit]
                    hidden, pad = features(self.model, current)
                    logits = head_logits(self.model, current, hidden, pad)
                    for (name, question), record, values in zip(
                        named[start + offset : start + offset + limit], current, logits
                    ):
                        values = values[: len(record["option_order"])]
                        if self.metadata is None:
                            native_q = self.prompt.native.as_question(question)
                            temperature = self.model.config.temperatures.get(
                                self.prompt.native.temperature_key(native_q),
                                self.model.config.temperatures.get(
                                    question["type"], 1.0
                                ),
                            )
                            probabilities = (values / temperature).softmax(-1).tolist()
                        else:
                            probabilities = apply_temperature(
                                values.softmax(-1).tolist(),
                                self.metadata["temperatures"][name],
                            )
                        answers[name] = {
                            "probabilities": dict(zip(options(question), probabilities))
                        }
        return normalize_answers(answers, questions)
