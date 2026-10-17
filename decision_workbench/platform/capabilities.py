"""Bounded on-demand inspection of configured model runtimes; no downloads."""

import json
import os
import subprocess
import sys
import threading
import time

PROBE = """
import importlib.metadata as m
import json
import platform
packages = {}
for name in ("scikit-learn", "numpy", "torch", "laya", "unsloth", "unsloth_zoo", "vegaml", "transformers", "trl", "peft", "datasets", "tokenizers", "triton", "triton-windows"):
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
    pass
if packages["torch"]:
    try:
        import torch
        result["cuda"] = torch.cuda.is_available()
        result["mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        if result["cuda"]:
            for i in range(min(torch.cuda.device_count(), 16)):
                p = torch.cuda.get_device_properties(i)
                result["gpus"].append({"name": p.name, "vram_gib": round(p.total_memory / 1024**3, 1)})
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
    identity = (worker, model, os.environ.get("DMT_LIQUID_PYTHON"))
    with _lock:
        if _cached and _cached[0] == identity and time.monotonic() - _cached[1] < 30:
            return _cached[2]
        from .model_environment import model_python

        targets = [("training", worker), ("serving", model)]
        for purpose in ("training", "serving"):
            try:
                executable = model_python("liquid-trl", purpose)
            except ValueError:
                executable = ""
            targets.append(("liquid-" + purpose, executable))
        runtimes, probed = {}, {}
        for kind, executable in targets:
            if executable in probed:
                runtimes[kind] = probed[executable]
                continue
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
                runtimes[kind] = value
            except (OSError, ValueError, subprocess.TimeoutExpired):
                runtimes[kind] = {
                    "error": "Model interpreter could not be inspected. Check the configured worker/model Python or DMT_LIQUID_PYTHON.",
                    "packages": {},
                    "cuda": False,
                    "mps": False,
                    "gpus": [],
                }
            probed[executable] = runtimes[kind]
        liquid = {
            purpose: runtimes.pop("liquid-" + purpose)
            for purpose in ("training", "serving")
        }
        result = {
            "runtimes": runtimes,
            "model_profiles": {"liquid-trl": liquid},
            "teacher_configured": bool(
                os.environ.get("DMT_TEACHER_BASE_URL")
                and os.environ.get("DMT_TEACHER_MODEL")
            ),
            "cpu_baseline": bool(
                runtimes["training"]["packages"].get("scikit-learn")
                and runtimes["training"]["packages"].get("numpy")
            ),
        }
        _cached = (identity, time.monotonic(), result)
        return result
