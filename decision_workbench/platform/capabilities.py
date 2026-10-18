"""Bounded on-demand inspection of configured model runtimes; no downloads."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PROBE = """
import importlib.metadata as m
import json
import platform
packages = {}
for name in ("scikit-learn", "numpy", "torch", "laya", "unsloth", "unsloth_zoo", "vegaml", "transformers", "trl", "peft", "datasets", "tokenizers", "triton", "triton-windows", "huggingface-hub", "safetensors", "torchvision", "accelerate", "pillow", "jinja2", "flash-linear-attention"):
    try:
        packages[name] = m.version(name)
    except m.PackageNotFoundError:
        packages[name] = None
result = {"system": platform.system(), "python": platform.python_version(), "packages": packages,
          "cuda": False, "mps": False, "gpus": [], "error": None, "ram_available_gib": None}
try:
    import psutil
    result["ram_available_gib"] = round(psutil.virtual_memory().available / 1024**3, 1)
except ImportError:
    if platform.system() == "Linux":
        from pathlib import Path
        try:
            memory = next(line for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
            result["ram_available_gib"] = round(int(memory.split()[1]) * 1024 / 1024**3, 1)
        except (OSError, ValueError, StopIteration):
            pass
if platform.system() == "Linux":
    try:
        from decision_workbench.training.memory_limits import available_linux_memory
        bounded = round(available_linux_memory() / 1024**3, 1)
        result["ram_available_gib"] = min(result.get("ram_available_gib", bounded), bounded)
    except (ImportError, OSError, ValueError, StopIteration):
        result["ram_available_gib"] = None
if packages["torch"]:
    try:
        import torch
        result["cuda"] = torch.cuda.is_available()
        result["mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        if result["cuda"]:
            for i in range(min(torch.cuda.device_count(), 16)):
                p = torch.cuda.get_device_properties(i)
                result["gpus"].append({"name": p.name, "vram_gib": round(p.total_memory / 1024**3, 1), "free_vram_gib": round(torch.cuda.mem_get_info(i)[0] / 1024**3, 1)})
    except Exception:
        result["error"] = "Torch could not inspect devices; check the model environment and drivers"
print(json.dumps(result))
"""
_lock = threading.Lock()
_cached = None


def runtime_capabilities():
    global _cached
    worker = os.environ.get("DMT_WORKER_PYTHON") or sys.executable
    model = os.environ.get("DMT_MODEL_PYTHON") or worker
    identity = (
        worker,
        model,
        os.environ.get("DMT_LIQUID_PYTHON"),
        os.environ.get("DMT_LIQUID_UNSLOTH_PYTHON"),
        os.environ.get("DMT_OMNI_PYTHON"),
        os.environ.get("DMT_D3_PYTHON"),
        os.environ.get("DMT_PERPLEXITY_PYTHON"),
        os.environ.get("DMT_PERPLEXITY_CHECKPOINT"),
    )
    with _lock:
        if _cached and _cached[0] == identity and time.monotonic() - _cached[1] < 30:
            return _cached[2]
        from .model_environment import model_python

        targets = [("training", worker), ("serving", model)]
        for backend in (
            "liquid-trl",
            "liquid-unsloth",
            "liquid-omni",
            "perplexity-native",
            *D3_BACKENDS,
        ):
            for purpose in ("training", "serving"):
                try:
                    executable = model_python(backend, purpose)
                except ValueError:
                    executable = ""
                targets.append((backend + "-" + purpose, executable))

        def probe(executable):
            try:
                process = subprocess.run(
                    [executable, "-c", PROBE],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    shell=False,
                )
                if process.returncode or len(process.stdout) > 16384:
                    raise ValueError("Invalid probe result")
                value = json.loads(process.stdout)
                if not isinstance(value, dict) or "packages" not in value:
                    raise ValueError("Invalid probe result")
                return value
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return {
                    "error": "Model interpreter could not be inspected. Check the configured worker/model or native-profile Python executable.",
                    "packages": {},
                    "cuda": False,
                    "mps": False,
                    "gpus": [],
                }

        unique = list(dict.fromkeys(executable for _, executable in targets))
        with ThreadPoolExecutor(max_workers=4) as pool:
            probed = dict(zip(unique, pool.map(probe, unique)))
        runtimes = {kind: probed[executable] for kind, executable in targets}
        model_profiles = {
            backend: {
                purpose: runtimes.pop(backend + "-" + purpose)
                for purpose in ("training", "serving")
            }
            for backend in (
                "liquid-trl",
                "liquid-unsloth",
                "liquid-omni",
                "perplexity-native",
                *D3_BACKENDS,
            )
        }
        result = {
            "runtimes": runtimes,
            "model_profiles": model_profiles,
            "teacher_configured": bool(
                os.environ.get("DMT_TEACHER_BASE_URL")
                and os.environ.get("DMT_TEACHER_MODEL")
            ),
            "perplexity_checkpoint_configured": bool(
                os.environ.get("DMT_PERPLEXITY_CHECKPOINT")
            ),
            "cpu_baseline": bool(
                runtimes["training"]["packages"].get("scikit-learn")
                and runtimes["training"]["packages"].get("numpy")
            ),
        }
        _cached = (identity, time.monotonic(), result)
        return result
