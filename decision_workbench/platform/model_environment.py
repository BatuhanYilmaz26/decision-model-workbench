"""Operator-selected model interpreters; request bodies never select executables."""

import os
from pathlib import Path
import sys


def model_python(backend, purpose, environment=None):
    environment = os.environ if environment is None else environment
    if purpose not in ("training", "serving"):
        raise ValueError("Unknown model interpreter purpose")
    variable = {
        "liquid-trl": "DMT_LIQUID_PYTHON",
        "liquid-unsloth": "DMT_LIQUID_UNSLOTH_PYTHON",
        "liquid-omni": "DMT_OMNI_PYTHON",
    }.get(backend)
    if variable and environment.get(variable):
        path = Path(environment[variable])
        if not path.is_absolute() or not path.is_file():
            raise ValueError(
                f"{variable} must name an installed absolute Python executable"
            )
        return str(path)
    if purpose == "serving":
        return (
            environment.get("DMT_MODEL_PYTHON")
            or environment.get("DMT_WORKER_PYTHON")
            or sys.executable
        )
    return environment.get("DMT_WORKER_PYTHON") or sys.executable


def liquid_environment(home, environment=None, *, backend="liquid-trl"):
    """Use writable private caches and the same source as the controlling service."""
    result = dict(os.environ if environment is None else environment)
    root = Path(__file__).resolve().parents[2]
    family = "liquid-omni" if backend == "liquid-omni" else "liquid"
    cache = Path(
        result.get("DMT_OMNI_CACHE" if backend == "liquid-omni" else "DMT_LIQUID_CACHE")
        or Path(home).parent / "model-cache" / (family + "-hub")
    ).absolute()
    result["PYTHONPATH"] = str(root)
    result.setdefault("HF_MODULES_CACHE", str(cache.parent / (family + "-modules")))
    result.setdefault("TRITON_CACHE_DIR", str(cache.parent / (family + "-triton")))
    result.setdefault("CUDA_CACHE_PATH", str(cache.parent / (family + "-cuda")))
    result.setdefault("TOKENIZERS_PARALLELISM", "false")
    result.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if backend == "liquid-unsloth":
        result["UNSLOTH_COMPILE_LOCATION"] = str(
            cache.parent / "liquid-unsloth-compiled"
        )
    return result, root, cache
