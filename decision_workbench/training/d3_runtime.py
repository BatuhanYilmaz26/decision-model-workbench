"""Hash-checked native D3 Edge inference and frozen text features."""

import importlib.metadata
import hashlib
import math
import os
from pathlib import Path
import sys
import types

from decision_workbench.data import options, validate_questions
from decision_workbench.json_tools import strict_json
from .artifacts import file_sha
from .d3_profiles import get_profile
from .d3_data import D3Prompt, checked_asset, resolve_assets
from .liquid_runtime import apply_temperature
from .metrics import normalize_answers

PACKAGES = {
    "transformers": "5.17.0",
    "tokenizers": "0.23.3",
    "huggingface-hub": "1.33.0",
    "safetensors": "0.8.0",
    "numpy": "2.5.2",
}


def runtime_identity():
    values = {name: importlib.metadata.version(name) for name in (*PACKAGES, "torch")}
    if (
        any(values[k] != v for k, v in PACKAGES.items())
        or values["torch"].split("+")[0] != "2.10.0"
    ):
        raise ValueError(
            "Use the compatible requirements/d3-native.txt model environment"
        )
    return values


def checked_runtime(folder, *, base=False, backend="d3-edge"):
    profile = get_profile(backend)
    folder = Path(folder)
    for name in profile.assets:
        checked_asset(folder, name, backend)
    for name, expected in profile.weights.items():
        if (folder / name).stat().st_size != expected["bytes"] or file_sha(
            folder / name
        ) != expected["sha256"]:
            raise ValueError(
                "D3 frozen backbone weights differ from the pinned checkpoint"
            )
    head = folder / "readout.safetensors"
    if (
        not head.is_file()
        or not 1 <= head.stat().st_size <= profile.readout_bytes + 16384
    ):
        raise ValueError("D3 readout is missing or exceeds this profile")
    if base and file_sha(head) != profile.readout_sha256:
        raise ValueError("D3 base readout differs from the pinned checkpoint")
    return folder


def resolve_checkpoint(cache, *, download=False, backend="d3-edge"):
    from huggingface_hub import snapshot_download

    profile = get_profile(backend)
    if download:
        from .d3_storage import cache_storage

        cache_storage(cache, profile)
    resolve_assets(cache, download=download, backend=backend)
    folder = Path(
        snapshot_download(
            profile.model,
            revision=profile.revision,
            token=False,
            cache_dir=str(cache),
            local_files_only=not download,
            allow_patterns=[*profile.assets, *profile.weights, "readout.safetensors"],
        )
    )
    return checked_runtime(folder, base=True, backend=backend)


def publisher_module(folder, backend="d3-edge"):
    """Execute pinned source bytes; never read/write a Python bytecode cache."""
    profile = get_profile(backend)
    for name in profile.assets:
        if name.endswith(".py"):
            checked_asset(folder, name, backend)
    folder = Path(folder).absolute()
    package_name = "dmt_d3_" + hashlib.sha256(str(folder).encode()).hexdigest()[:24]
    module_name = package_name + ".d3_runtime"
    if module_name not in sys.modules:
        package = types.ModuleType(package_name)
        # Only the reviewed modules below are importable under this package.
        package.__path__ = []
        sys.modules[package_name] = package
        try:
            for name in ("d3_format", "d3_fast", "d3_runtime"):
                qualified = package_name + "." + name
                module = types.ModuleType(qualified)
                module.__file__ = str(folder / (name + ".py"))
                module.__package__ = package_name
                sys.modules[qualified] = module
                setattr(package, name, module)
                body = checked_asset(folder, name + ".py", backend)
                exec(compile(body, module.__file__, "exec"), module.__dict__)
        except BaseException:
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    del sys.modules[name]
            raise
    return sys.modules[module_name]


def memory_guard(device, *, batch=1, length=0, startup=False, profile=None):
    import torch
    import psutil

    available = (
        torch.cuda.mem_get_info(device)[0]
        if device.type == "cuda"
        else psutil.virtual_memory().available
    )
    profile = profile or get_profile()
    minimum = (
        (profile.cuda_gib if device.type == "cuda" else profile.cpu_gib) * 1024**3
        if startup
        else 256 * 1024**2
    )
    estimate = batch * (
        max(16, profile.heads) * length**2 * 4
        + max(64, profile.layers) * length * profile.hidden * 2
    )
    if available < minimum + estimate:
        raise ValueError(
            "D3 needs more available memory; reduce context/batch or choose a larger CPU/GPU host"
        )


def features(runtime, records):
    """Keep the native left padding, positions and noncausal full-attention encoder."""
    import torch

    width = max(len(record["input_ids"]) for record in records)
    memory_guard(
        runtime.device,
        batch=len(records),
        length=width,
        profile=getattr(runtime, "dmt_profile", None),
    )
    ids = torch.full(
        (len(records), width), runtime.pad_id, dtype=torch.long, device=runtime.device
    )
    present = torch.zeros_like(ids)
    for index, record in enumerate(records):
        sequence = record["input_ids"]
        ids[index, -len(sequence) :] = torch.tensor(sequence, device=runtime.device)
        present[index, -len(sequence) :] = 1
    native = sys.modules[type(runtime).__module__]
    with torch.no_grad(), native.sdpa_backends(runtime.device):
        if runtime.encoder is None:
            raise ValueError("This D3 profile requires its native noncausal encoder")
        hidden = runtime.encoder(runtime.backbone.get_input_embeddings()(ids), present)[
            :, -1
        ]
    return hidden.detach().float()


class D3Engine:
    def __init__(self, checkpoint, device="auto", *, base=False, backend="d3-edge"):
        import torch

        runtime_identity()
        self.profile = get_profile(backend)
        folder = Path(checkpoint)
        self.metadata = None
        self.max_length = 32768
        if not base:
            path = folder / "dmt_d3.json"
            with path.open("rb") as stream:
                body = stream.read(16 * 1024**2 + 1)
            if len(body) > 16 * 1024**2:
                raise ValueError("D3 domain metadata exceeds 16 MiB")
            self.metadata = strict_json(body)
            m = self.metadata
            if (
                not isinstance(m, dict)
                or m.get("format") != "domain-d3-runtime-v1"
                or m.get("model") != self.profile.model
                or m.get("revision") != self.profile.revision
                or m.get("training_method") != "native-readout"
                or type(m.get("max_length")) is not int
                or not 64 <= m["max_length"] <= 32768
            ):
                raise ValueError(
                    "D3 metadata does not describe this native domain profile"
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
                raise ValueError("D3 domain temperatures are invalid")
            self.max_length = m["max_length"]
            if m.get("device") not in (None, "cpu", "cuda"):
                raise ValueError("D3 calibrated serving device is invalid")
            if device == "auto" and m.get("device"):
                device = m["device"]
        if device not in ("auto", "cpu", "cuda"):
            raise ValueError("Choose auto, CPU or CUDA for this D3 profile")
        picked = torch.device(
            "cuda:0"
            if device == "cuda" or device == "auto" and torch.cuda.is_available()
            else "cpu"
        )
        if picked.type == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable; use CPU or a compatible cloud GPU")
        memory_guard(picked, startup=True, profile=self.profile)
        folder = checked_runtime(folder, base=base, backend=backend)
        native = publisher_module(folder, backend)
        self.prompt = D3Prompt(folder, backend)
        # The original model manifest remains a publisher artifact; a domain
        # export uses the workbench's independently checked bundle manifest.
        previous = os.environ.get("D3_FAST")
        os.environ["D3_FAST"] = "0"
        try:
            self.runtime = native.D3(
                folder,
                device=str(picked),
                batch_size=1,
                max_length=self.max_length,
                permutation_average=False,
            )
        finally:
            if previous is None:
                os.environ.pop("D3_FAST", None)
            else:
                os.environ["D3_FAST"] = previous
        self.runtime.dmt_profile = self.profile
        if (
            self.runtime.temperature != 1.0
            or self.runtime.readout_dtype != "float32"
            or self.runtime.attention_mode != "noncausal_full_attention"
        ):
            raise ValueError(
                "D3 native temperature, dtype or attention contract differs"
            )

    def predict(self, state, questions):
        validate_questions(questions)
        if self.metadata is not None and (
            questions != self.metadata["questions"]
            or any(
                options(question) != options(self.metadata["questions"][name])
                for name, question in questions.items()
            )
        ):
            raise ValueError("D3 questions differ from the trained domain schema")
        answers = {}
        for name, question in questions.items():
            record = self.prompt.prepare(state, question, self.max_length)
            memory_guard(
                self.runtime.device,
                length=len(record["input_ids"]),
                profile=self.profile,
            )
            values = self.runtime.probabilities(
                [record["input_ids"]], [len(record["option_keys"])]
            )[0]
            if self.metadata is not None:
                values = apply_temperature(values, self.metadata["temperatures"][name])
            answers[name] = {"probabilities": dict(zip(record["option_keys"], values))}
        return normalize_answers(answers, questions)
