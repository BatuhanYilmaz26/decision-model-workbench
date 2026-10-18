"""Independently measure an imported export in the local serving environment."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import gc
import importlib.metadata
import json
from pathlib import Path
from .artifacts import compare_predictions, verify_manifest, write_manifest
from .contracts import load_splits
from .engines import load_engine
from .metrics import evaluate_predictions
from .provenance import source_fingerprint


def qualify(folder, data, expected_sha, request, engine_factory=None):
    rows, hashes = load_splits(data)
    questions = rows["train"][0]["questions"]
    config = request["configuration"]
    negatives = None
    if config["backend"] == "vega":
        from .vega_contracts import load_gate_splits
        from .vega_remote import check_inputs

        negatives, negative_hashes = load_gate_splits(data, rows)
        check_inputs(request, rows, hashes, negatives, negative_hashes, questions)
    if hashes != request["data_sha256"]:
        raise ValueError("Local admission inputs differ from the frozen remote study")
    if source_fingerprint() != request["source_sha256"]:
        raise ValueError(
            "Admission environment source differs from frozen study; qualify upgrades explicitly"
        )
    manifest = verify_manifest(folder, expected_sha, config["backend"], questions)
    folder = Path(folder)
    source_report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    from .native_remote import BACKENDS

    allowed_native_configs = set()
    if config["backend"] in BACKENDS:
        from decision_workbench.platform.remote import validate_export
        from .native_remote import approved_configs, check_inputs

        check_inputs(request, rows, hashes, questions)
        validate_export(folder, expected_sha, request)
        allowed_native_configs = approved_configs(
            config["backend"], manifest["checkpoint"]
        )
    for path in folder.rglob("*.json"):
        if path.stat().st_size > 16 * 1024**2:
            raise ValueError("Model JSON configuration/report exceeds 16 MiB")
        obj = json.loads(path.read_text(encoding="utf-8"))
        if (
            path.relative_to(folder).as_posix() not in allowed_native_configs
            and isinstance(obj, dict)
            and (obj.get("auto_map") or obj.get("trust_remote_code") is True)
        ):
            raise ValueError("Remote executable model configuration is not accepted")
    factory = engine_factory or load_engine
    engine = factory(config["backend"], folder / manifest["checkpoint"])
    extra = {}
    if config["backend"] == "vega":
        from .vega_admission import measure, compare_uncertainty

        threshold = json.loads(
            (folder / manifest["checkpoint"] / "adapters/domain.json").read_text()
        )["gate_threshold"]
        details, negative_details, metrics, applicability = measure(
            engine, rows, negatives, questions, threshold
        )
        predictions = {
            role: [p["answers"] for p in probes] for role, probes in details.items()
        }
        extra = {
            "applicability": applicability,
            "gate_counts": {k: len(v) for k, v in negatives.items()},
        }
    else:
        predictions = {
            name: [engine.predict(row["state"], questions) for row in rows[name]]
            for name in ("validation", "calibration", "test")
        }
    del engine
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
    # A second load checks deterministic reload using the same exported runtime.
    fresh = factory(config["backend"], folder / manifest["checkpoint"])
    probes = rows["validation"][:20]
    actual = [fresh.predict(row["state"], questions) for row in probes]
    tolerance = 1e-6 if config["backend"] == "laya-native" else 0.005
    qualification = compare_predictions(
        predictions["validation"][:20], actual, questions, tolerance
    )
    if config["backend"] == "vega":
        refreshed = [fresh.predict_details(row["state"], questions) for row in probes]
        negative_probes = negatives["validation"][:20]
        refreshed_negative = [
            fresh.predict_details(row["state"], questions) for row in negative_probes
        ]
        compare_uncertainty(details["validation"][:20], refreshed, questions)
        compare_uncertainty(
            negative_details["validation"][:20], refreshed_negative, questions
        )
        compare_predictions(
            [p["answers"] for p in negative_details["validation"][:20]],
            [p["answers"] for p in refreshed_negative],
            questions,
            tolerance,
        )
        qualification.update(
            same_uncertainty=True, negative_probes=len(negative_probes)
        )
    del fresh
    gc.collect()
    report = {
        **source_report,
        **extra,
        "validation": evaluate_predictions(
            rows["validation"], predictions["validation"], questions
        ),
        "test": evaluate_predictions(rows["test"], predictions["test"], questions),
        "counts": {k: len(v) for k, v in rows.items()},
        "reload_equivalence": True,
        "export_qualification": qualification,
        "admission": {
            "source_manifest_sha256": expected_sha,
            "study_request_sha256": request["request_sha256"],
            "source_sha256": source_fingerprint(),
            "calibration_runtime_metrics": evaluate_predictions(
                rows["calibration"], predictions["calibration"], questions
            ),
            "recalibrated": False,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "scikit-learn")
            },
            "note": "Local exported-runtime measurements replace supplied validation/test metrics. Cloud receipt remains operator-attested.",
        },
    }
    if config["backend"] in ("liquid-omni", "d3-edge"):
        report["calibration_metrics"] = report["admission"][
            "calibration_runtime_metrics"
        ]
    (folder / "training_report.json").write_text(
        json.dumps(source_report, indent=2, allow_nan=False), encoding="utf-8"
    )
    from .error_policy import attach_evaluation

    attach_evaluation(report, questions, config.get("error_policy"))
    (folder / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    checksum = write_manifest(
        folder, config["backend"], questions, manifest["checkpoint"]
    )
    return {"manifest_sha256": checksum, "report": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    result = qualify(
        args.export,
        args.data,
        args.manifest_sha256,
        json.loads(Path(args.request).read_text(encoding="utf-8")),
    )
    print(result["manifest_sha256"])


if __name__ == "__main__":
    main()
