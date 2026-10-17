"""Portable native Liquid LoRA exports without rounding adapter updates away."""

import json
from pathlib import Path
import shutil

from .artifacts import file_sha
from .liquid_data import MODEL, REVISION

TARGET_MODULES = r".*language_model.*\.(q_proj|k_proj|v_proj|out_proj|in_proj|w1|w2|w3)"


def checked_adapter(folder, identity):
    folder = Path(folder) / "adapter"
    if (
        not isinstance(identity, dict)
        or set(identity) != {"format", "config_sha256", "weights_sha256"}
        or identity.get("format") != "domain-liquid-lora-v1"
        or folder.is_symlink()
        or (hasattr(folder, "is_junction") and folder.is_junction())
        or not folder.is_dir()
        or {p.name for p in folder.iterdir()}
        != {"adapter_config.json", "adapter_model.safetensors"}
    ):
        raise ValueError("Liquid adapter identity or file inventory is invalid")
    for name, field, limit in (
        ("adapter_config.json", "config_sha256", 1024 * 1024),
        ("adapter_model.safetensors", "weights_sha256", 1024**3),
    ):
        path = folder / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > limit
            or file_sha(path) != identity[field]
        ):
            raise ValueError("Liquid adapter file identity check failed")
    config = json.loads((folder / "adapter_config.json").read_text(encoding="utf-8"))
    rank = config.get("r")
    if (
        config.get("peft_type") != "LORA"
        or config.get("target_modules") != TARGET_MODULES
        or type(rank) is not int
        or not 1 <= rank <= 128
        or config.get("lora_alpha") != 2 * rank
        or config.get("lora_dropout") != 0
        or config.get("bias") != "none"
        or config.get("base_model_name_or_path") != MODEL
        or config.get("revision") != REVISION
        or config.get("auto_mapping") is not None
        or config.get("task_type") != "CAUSAL_LM"
        or config.get("modules_to_save") is not None
        or config.get("use_dora", False)
        or config.get("use_rslora", False)
    ):
        raise ValueError("Liquid adapter changes the pinned language-only LoRA profile")
    return folder, config


def export_adapter(source, checkpoint):
    """Save only safe tensors/config, removing machine-specific base references."""
    source, checkpoint = Path(source), Path(checkpoint)
    folder = checkpoint / "adapter"
    folder.mkdir(parents=True, exist_ok=False)
    config = json.loads((source / "adapter_config.json").read_text(encoding="utf-8"))
    config.update(base_model_name_or_path=MODEL, revision=REVISION, auto_mapping=None)
    (folder / "adapter_config.json").write_text(
        json.dumps(config, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    shutil.copyfile(
        source / "adapter_model.safetensors", folder / "adapter_model.safetensors"
    )
    identity = {
        "format": "domain-liquid-lora-v1",
        "config_sha256": file_sha(folder / "adapter_config.json"),
        "weights_sha256": file_sha(folder / "adapter_model.safetensors"),
    }
    checked_adapter(checkpoint, identity)
    return identity
