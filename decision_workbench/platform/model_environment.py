"""Operator-selected model interpreters; request bodies never select executables."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

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
        **{name: "DMT_D3_PYTHON" for name in D3_BACKENDS},
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


def native_environment(home, environment=None, *, backend="liquid-trl"):
    """Use writable private caches and the same source as the controlling service."""
    result = dict(os.environ if environment is None else environment)
    root = Path(__file__).resolve().parents[2]
    family = {"liquid-omni": "liquid-omni", **{name: "d3" for name in D3_BACKENDS}}.get(
        backend, "liquid"
    )
    variable = {
        "liquid-omni": "DMT_OMNI_CACHE",
        **{name: "DMT_D3_CACHE" for name in D3_BACKENDS},
    }.get(backend, "DMT_LIQUID_CACHE")
    cache = Path(
        result.get(variable) or Path(home).parent / "model-cache" / (family + "-hub")
    ).absolute()
    result["PYTHONPATH"] = str(root)
    result.setdefault("HF_MODULES_CACHE", str(cache.parent / (family + "-modules")))
    result.setdefault("TRITON_CACHE_DIR", str(cache.parent / (family + "-triton")))
    result.setdefault("CUDA_CACHE_PATH", str(cache.parent / (family + "-cuda")))
    result.setdefault("TOKENIZERS_PARALLELISM", "false")
    result.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if backend in D3_BACKENDS:
        result["D3_FAST"] = "0"
        result.setdefault("OMP_NUM_THREADS", "4")
        result.setdefault("MKL_NUM_THREADS", "4")
    if backend == "liquid-unsloth":
        result["UNSLOTH_COMPILE_LOCATION"] = str(
            cache.parent / "liquid-unsloth-compiled"
        )
    return result, root, cache


def liquid_environment(home, environment=None, *, backend="liquid-trl"):
    """Compatibility entry point for existing Liquid operator integrations."""
    return native_environment(home, environment, backend=backend)
