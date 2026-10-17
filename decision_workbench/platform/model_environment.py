"""Operator-selected model interpreters; request bodies never select executables."""

import os
from pathlib import Path
import sys


def model_python(backend, purpose, environment=None):
    environment = os.environ if environment is None else environment
    if purpose not in ("training", "serving"):
        raise ValueError("Unknown model interpreter purpose")
    if backend == "liquid-trl" and environment.get("DMT_LIQUID_PYTHON"):
        path = Path(environment["DMT_LIQUID_PYTHON"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError(
                "DMT_LIQUID_PYTHON must name an installed absolute Python executable"
            )
        return str(path)
    if purpose == "serving":
        return (
            environment.get("DMT_MODEL_PYTHON")
            or environment.get("DMT_WORKER_PYTHON")
            or sys.executable
        )
    return environment.get("DMT_WORKER_PYTHON") or sys.executable


def liquid_environment(home, environment=None):
    """Use writable private caches and the same source as the controlling service."""
    result = dict(os.environ if environment is None else environment)
    root = Path(__file__).resolve().parents[2]
    cache = Path(
        result.get("DMT_LIQUID_CACHE")
        or Path(home).parent / "model-cache" / "liquid-hub"
    ).absolute()
    result["PYTHONPATH"] = str(root)
    result.setdefault("HF_MODULES_CACHE", str(cache.parent / "liquid-modules"))
    result.setdefault("TRITON_CACHE_DIR", str(cache.parent / "liquid-triton"))
    result.setdefault("CUDA_CACHE_PATH", str(cache.parent / "liquid-cuda"))
    result.setdefault("TOKENIZERS_PARALLELISM", "false")
    result.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return result, root, cache
