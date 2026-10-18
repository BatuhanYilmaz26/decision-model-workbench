"""Train a frozen portable native-model study without replacing its native contract."""

# ruff: noqa: E402 -- Arm the parent guard before importing model dependencies.

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

from .artifacts import file_sha, write_manifest
from .cloud_runner import ordinary
from .contracts import load_splits
from .native_remote import check_inputs, profile, read_request
from .d3_profiles import BACKENDS as D3_BACKENDS


def execute(request_path, data, output):
    request, request_sha = read_request(request_path)
    rows, hashes = load_splits(data)
    questions = rows["train"][0]["questions"]
    study = check_inputs(request, rows, hashes, questions)
    config = request["configuration"]
    backend = config["backend"]
    local, _, _ = profile(backend)
    output = ordinary(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Remote native output must be new or empty")
    work = ordinary(Path(request_path).parent)
    native = work / "native-export"
    local_path = work / "native-request.json"
    if native.exists() or local_path.exists():
        raise ValueError("Choose a fresh work directory for each remote native attempt")
    local_path.write_bytes(local.request_body(study))
    cache_name = {
        **{name: "DMT_D3_CACHE" for name in D3_BACKENDS},
        "liquid-omni": "DMT_OMNI_CACHE",
    }.get(backend, "DMT_LIQUID_CACHE")
    cache = os.environ.get(cache_name) or str(
        Path.cwd()
        / "data/model-cache"
        / {
            **{name: "d3-hub" for name in D3_BACKENDS},
            "liquid-omni": "liquid-omni-hub",
        }.get(backend, "liquid-hub")
    )
    args = SimpleNamespace(
        **{name: study["configuration"][name] for name in local.DEFAULTS},
        data=str(data),
        out=str(native),
        cache=cache,
        local_study_request=str(local_path),
    )
    if backend in D3_BACKENDS:
        from .d3_runner import train_d3

        args.backend = backend
        report = train_d3(args)
    elif backend == "liquid-omni":
        from .liquid_omni_runner import train_omni

        report = train_omni(args)
    else:
        from .liquid_runner import train_liquid

        args.trainer = "unsloth" if backend == "liquid-unsloth" else "trl"
        report = train_liquid(args)
    # This binding is added only after the unchanged native trainer executes.
    # Old standalone exports cannot acquire a remote identity via this entrypoint.
    current, current_sha = read_request(request_path)
    check_inputs(current, *load_splits(data), questions)
    if current_sha != request_sha:
        raise ValueError("Frozen remote request changed during training")
    report["remote_study_sha256"] = request_sha
    (native / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    manifest = json.loads((native / "manifest.json").read_text(encoding="utf-8"))
    checksum = write_manifest(native, backend, questions, manifest["checkpoint"])
    from decision_workbench.platform.remote import validate_export

    validate_export(
        native,
        checksum,
        {**request, "questions": questions, "request_sha256": request_sha},
    )
    # Providers may precreate an empty model/output directory. Publish the
    # manifest last; partial output can never pass independent admission.
    output.mkdir(parents=True, exist_ok=True)
    marker = output / ".native-incomplete"
    with marker.open("xb"):
        pass
    for item in native.iterdir():
        if item.name != "manifest.json":
            destination = output / item.name
            if destination.exists():
                raise ValueError("Remote native output was populated concurrently")
            shutil.move(str(item), str(destination))
    if (output / "manifest.json").exists():
        raise ValueError("Remote native manifest appeared concurrently")
    shutil.move(str(native / "manifest.json"), str(output / "manifest.json"))
    marker.unlink()
    native.rmdir()
    return {
        "backend": backend,
        "remote_study_sha256": request_sha,
        "manifest_sha256": file_sha(output / "manifest.json"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("request", "data", "out"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.request, args.data, args.out), allow_nan=False))


if __name__ == "__main__":
    main()
