"""Train a gated Vega domain adapter with independently reserved uncertainty data."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import copy
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
from pathlib import Path

import numpy as np
from decision_workbench.data import dumps, options
from .artifacts import compare_predictions, file_sha, write_manifest
from .contracts import load_splits, SPLITS
from .metrics import evaluate_predictions
from .provenance import source_fingerprint
from .resources import ModelResourceLease, ResourceLease
from .vega_contracts import load_gate_splits
from .vega_engine import (
    VegaEngine,
    checked_items,
    installed_identity,
    translated_questions,
)
from .vega_study import gate_metrics, select_gate_threshold, validate_settings
from .vega_local import validate_request

MODEL = "nandakishorm/vega-08b-public-intents"
REVISION = "42ac8d4fa9efb61f96e53d80962cf28f6dfe667e"
BACKBONE = "Qwen/Qwen3.5-0.8B"
BACKBONE_REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
RUNTIME_PACKAGES = (
    "vegaml",
    "torch",
    "transformers",
    "numpy",
    "safetensors",
    "tokenizers",
)


def pinned_assets(args):
    from huggingface_hub import snapshot_download

    if args.model != MODEL or args.backbone_model != BACKBONE:
        raise ValueError(
            "This qualified Vega profile supports the public 0.8B engine/backbone only"
        )
    for revision in (args.revision, args.backbone_revision):
        if not re.fullmatch(r"[a-f0-9]{40}", revision or ""):
            raise ValueError("Pin both Vega engine and backbone to exact Hub revisions")
    common = {
        "cache_dir": os.environ.get("DMT_MODEL_CACHE", "data/model-cache/hub"),
        "token": False,
        "local_files_only": getattr(args, "offline", False),
    }
    engine = Path(
        snapshot_download(
            args.model,
            revision=args.revision,
            allow_patterns=["engine.safetensors", "vega_config.json"],
            **common,
        )
    )
    backbone = Path(
        snapshot_download(
            args.backbone_model,
            revision=args.backbone_revision,
            allow_patterns=[
                "*.json",
                "*.safetensors",
                "*.txt",
                "*.model",
                "*.jinja",
                "LICENSE",
                "README.md",
            ],
            ignore_patterns=["*.py", "*.bin", "*.pt", "*.pkl"],
            **common,
        )
    )
    return engine, backbone


def _extract(wrapper, rows, questions, negative, batch_size, width):
    """Match _api.Vega._view, including its memory chunks; retain complete rows."""
    import torch
    from vegaml._common import QTYPES

    definitions = translated_questions(questions)
    specs = [
        (row, name)
        for row in rows
        for name in definitions
        if not negative or name in row["negative_for"]
    ]
    saved = {
        key: []
        for key in (
            "features",
            "options",
            "mask",
            "chunks",
            "chunk_mask",
            "qtype",
            "target",
        )
    }
    vega = wrapper.vega
    chunks_count = vega.engine.memory_chunks if vega.engine.memory is not None else 0
    with torch.no_grad():
        for start in range(0, len(specs), batch_size):
            part = specs[start : start + batch_size]
            items = [
                checked_items(vega, row["state"], {name: questions[name]})[0]
                for row, name in part
            ]
            kwargs = {"prefix_min": vega.cfg.get("prefix_min_tokens", 4096)}
            if chunks_count:
                features, flat, chunks, chunk_mask = vega.perc.pooled(
                    items,
                    chunks=chunks_count,
                    chunk_min_tokens=vega.cfg.get("memory_min_tokens", 2048),
                    chunk_tokens=vega.cfg.get("memory_chunk_tokens", 128),
                    **kwargs,
                )
            else:
                features, flat = vega.perc.pooled(items, **kwargs)
                chunks = features.new_zeros((len(part), 0, features.shape[-1]))
                chunk_mask = torch.zeros(
                    (len(part), 0), dtype=torch.bool, device=features.device
                )
            opts = flat.new_zeros((len(part), width) + flat.shape[1:])
            mask = torch.zeros((len(part), width), dtype=torch.bool, device=flat.device)
            target = torch.zeros((len(part), width), dtype=torch.float32)
            offset = 0
            for index, (row, name) in enumerate(part):
                keys = options(json.loads(dumps(questions[name])))
                length = len(keys)
                opts[index, :length] = flat[offset : offset + length]
                mask[index, :length] = True
                offset += length
                if not negative:
                    target[index, :length] = torch.tensor(
                        [row["gold"][name]["probabilities"][k] for k in keys]
                    )
            tensors = (
                features,
                opts,
                mask,
                chunks,
                chunk_mask,
                torch.tensor([QTYPES[definitions[name]["type"]] for row, name in part]),
                target,
            )
            for key, value in zip(saved, tensors):
                saved[key].append(value.detach().cpu())
            print(
                f"features {'negative' if negative else 'positive'} {start + len(part)}/{len(specs)}",
                flush=True,
            )
    return {key: torch.cat(values) for key, values in saved.items()}


def _forward(engine, cache, indexes, device, *, halt):
    values = {key: cache[key][indexes].to(device) for key in cache}
    output = engine(
        values["features"],
        values["options"],
        values["mask"],
        values["qtype"],
        halt=halt,
        return_traj=False,
        chunks=values["chunks"],
        chunk_mask=values["chunk_mask"],
    )
    logits = output["logits"].masked_fill(~values["mask"], -1e4)
    return output, logits, values


def _collect(engine, cache, device, batch_size):
    import torch
    from vegaml._common import thermal_features

    collected = {name: [] for name in ("logits", "physics", "gate", "nearest")}
    engine.eval()
    with torch.no_grad():
        for start in range(0, len(cache["qtype"]), batch_size):
            out, logits, values = _forward(
                engine, cache, slice(start, start + batch_size), device, halt=True
            )
            collected["logits"].append(logits.float().cpu().numpy())
            collected["physics"].append(
                thermal_features(out, values["mask"], engine.max_steps).cpu().numpy()
            )
            collected["gate"].append(out["gates"][:, 0].float().cpu().numpy())
            collected["nearest"].append(out["nearest"].float().cpu().numpy())
    return {key: np.concatenate(value) for key, value in collected.items()}


def _train_adapter(wrapper, positive, negative, args):
    import torch
    from torch.nn import functional as F
    from vegaml._common import ADAPTER_TARGETS, adapter_state, attach_adapter

    engine, device = wrapper.vega.engine, wrapper.vega.device
    adapter = attach_adapter(engine, rank=args.rank, alpha=float(args.rank))
    if adapter != 0:
        raise ValueError("Training requires a clean engine with no existing adapters")
    engine.adapter_mode = "auto"
    for parameter in engine.parameters():
        parameter.requires_grad_(False)
    for path in ADAPTER_TARGETS:
        layer = engine
        for part in path.split("."):
            layer = getattr(layer, part) if not part.isdigit() else layer[int(part)]
        layer.lora_A[adapter].requires_grad_(True)
        layer.lora_B[adapter].requires_grad_(True)
    for parameter in engine.gate_nets[adapter].parameters():
        parameter.requires_grad_(True)
    initial = adapter_state(engine, adapter)
    parameters = [p for p in engine.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=args.learning_rate, weight_decay=0.01)
    history, best, best_value = [], None, float("inf")
    updates, maximum_gate_gradient = 0, 0.0
    for epoch in range(args.epochs):
        engine.train()
        pos_batches = torch.randperm(len(positive["train"]["qtype"])).split(
            args.batch_size
        )
        neg_batches = torch.randperm(len(negative["train"]["qtype"])).split(
            args.batch_size
        )
        total_loss, seen_positive, seen_negative = 0.0, 0, 0
        for index in range(max(len(pos_batches), len(neg_batches))):
            if args.max_steps and updates >= args.max_steps:
                break
            pindex, nindex = (
                pos_batches[index % len(pos_batches)],
                neg_batches[index % len(neg_batches)],
            )
            pout, plogits, pvalues = _forward(
                engine, positive["train"], pindex, device, halt=False
            )
            nout, _, _ = _forward(engine, negative["train"], nindex, device, halt=False)
            decision_loss = (
                -(pvalues["target"] * F.log_softmax(plogits, dim=-1)).sum(-1).mean()
            )
            pgate, ngate = pout["gates"][:, 0], nout["gates"][:, 0]
            gate_loss = F.binary_cross_entropy(
                pgate, torch.ones_like(pgate)
            ) + F.binary_cross_entropy(ngate, torch.zeros_like(ngate))
            loss = decision_loss + args.gate_weight * gate_loss
            if not bool(torch.isfinite(loss)):
                raise ValueError("Vega training produced a nonfinite loss")
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            gradient = max(
                float(p.grad.abs().max())
                for p in engine.gate_nets[0].parameters()
                if p.grad is not None
            )
            maximum_gate_gradient = max(maximum_gate_gradient, gradient)
            torch.nn.utils.clip_grad_norm_(parameters, 1.0)
            optimizer.step()
            updates += 1
            seen_positive += len(pindex)
            seen_negative += len(nindex)
            total_loss += float(loss.detach())
        pv = _collect(engine, positive["validation"], device, args.batch_size)
        nv = _collect(engine, negative["validation"], device, args.batch_size)
        z = pv["logits"] - pv["logits"].max(-1, keepdims=True)
        logp = z - np.log(np.exp(z).sum(-1, keepdims=True))
        ce = float(-(positive["validation"]["target"].numpy() * logp).sum(-1).mean())
        bce = float(
            -np.log(np.maximum(pv["gate"], 1e-12)).mean()
            - np.log(np.maximum(1 - nv["gate"], 1e-12)).mean()
        )
        objective = ce + args.gate_weight * bce
        if not math.isfinite(objective):
            raise ValueError("Vega validation produced nonfinite selection loss")
        if objective < best_value:
            best, best_value = {
                k: v.clone() for k, v in adapter_state(engine, adapter).items()
            }, objective
        history.append(
            {
                "epoch": epoch + 1,
                "optimizer_updates": updates,
                "training_loss_sum": total_loss,
                "positive_decisions_seen": seen_positive,
                "negative_decisions_seen": seen_negative,
                "validation_objective": objective,
            }
        )
        print(
            f"epoch {epoch + 1} updates={updates} validation={objective:.6f}",
            flush=True,
        )
        if args.max_steps and updates >= args.max_steps:
            break
    if not updates or not maximum_gate_gradient > 0 or best is None:
        raise ValueError("Vega training did not update a learned applicability gate")
    # Restore the selected adapter in place, without attaching a second adapter.
    with torch.no_grad():
        for path in ADAPTER_TARGETS:
            layer = engine
            for part in path.split("."):
                layer = getattr(layer, part) if not part.isdigit() else layer[int(part)]
            layer.lora_A[0].copy_(best[path + ".lora_A"])
            layer.lora_B[0].copy_(best[path + ".lora_B"])
        engine.gate_nets[0].load_state_dict(
            {k[5:]: v for k, v in best.items() if k.startswith("gate.")}
        )
    engine.eval()
    delta = max(float((best[k] - initial[k]).abs().max()) for k in best)
    gate_delta = max(
        float((best[k] - initial[k]).abs().max()) for k in best if k.startswith("gate.")
    )
    lora_delta = max(
        float((best[k] - initial[k]).abs().max()) for k in best if ".lora_" in k
    )
    if min(delta, gate_delta, lora_delta) <= 0:
        raise ValueError(
            "The selected Vega adapter/gate has no measured parameter update"
        )
    return best, {
        "optimizer_updates": updates,
        "adapter_parameter_delta": delta,
        "gate_parameter_delta": gate_delta,
        "lora_parameter_delta": lora_delta,
        "maximum_gate_gradient": maximum_gate_gradient,
        "history": history,
        "trainable_parameters": sum(p.numel() for p in parameters),
        "selection_role": "validation",
        "seed": 3407,
        "encoder_frozen": True,
    }


def train_vega(args):
    rows, hashes = load_splits(args.data)
    negatives, negative_hashes = load_gate_splits(args.data, rows)
    questions = json.loads(dumps(rows["train"][0]["questions"]))
    size = validate_settings(args, rows, negatives, questions)
    from .vega_remote import validate_request as validate_remote_request

    remote_request_sha = validate_remote_request(
        args, hashes, negative_hashes, questions, size
    )
    local_request_sha = validate_request(args, hashes, negative_hashes, questions, size)
    output = Path(args.out).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty output directory")
    import torch
    from safetensors.torch import save_file
    from vegaml._common import (
        fit_thermal,
        conformal_thresholds,
        softmax_np,
        thermal_tau,
        QTYPES,
    )

    torch.manual_seed(3407)
    np.random.seed(3407)
    engine_path, backbone_path = pinned_assets(args)
    wrapper = VegaEngine(
        engine_path, args.device, training_base=True, backbone=backbone_path
    )
    cfg = wrapper.vega.cfg
    if (
        cfg["d_model"] != 1024
        or cfg["feature_layers"] != [13, 19]
        or cfg["option_layers"] != [19]
    ):
        raise ValueError("Vega engine is outside the selected 0.8B feature profile")
    if wrapper.vega.device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    width = max(len(options(q)) for q in questions.values())
    positive = {
        role: _extract(wrapper, rows[role], questions, False, args.batch_size, width)
        for role in SPLITS[:-1]
    }
    negative = {
        role: _extract(
            wrapper, negatives[role], questions, True, args.batch_size, width
        )
        for role in SPLITS[:-1]
    }
    adapter, training = _train_adapter(wrapper, positive, negative, args)
    pv = _collect(
        wrapper.vega.engine,
        positive["validation"],
        wrapper.vega.device,
        args.batch_size,
    )
    nv = _collect(
        wrapper.vega.engine,
        negative["validation"],
        wrapper.vega.device,
        args.batch_size,
    )
    policy = select_gate_threshold(pv["gate"], nv["gate"])
    cal = _collect(
        wrapper.vega.engine,
        positive["calibration"],
        wrapper.vega.device,
        args.batch_size,
    )
    qt, target = (
        positive["calibration"]["qtype"].numpy(),
        positive["calibration"]["target"].numpy(),
    )
    partition = size["calibration_partition"]
    # _extract emits every schema question for each positive row in canonical order.
    fit_indices = np.asarray(
        [
            row * len(questions) + q
            for row in partition["probability_fit"]
            for q in range(len(questions))
        ]
    )
    conformal_indices = np.asarray(
        [
            row * len(questions) + q
            for row in partition["conformal"]
            for q in range(len(questions))
        ]
    )
    for value in set(qt.tolist()):
        if (
            min(
                int((qt[fit_indices] == value).sum()),
                int((qt[conformal_indices] == value).sum()),
            )
            < 30
        ):
            raise ValueError(
                "Vega cannot silently skip a type in either calibration partition"
            )
    theta = fit_thermal(
        cal["logits"][fit_indices],
        target[fit_indices],
        qt[fit_indices],
        cal["physics"][fit_indices],
    )
    probabilities = softmax_np(cal["logits"], thermal_tau(theta, qt, cal["physics"]))
    conformal = conformal_thresholds(
        probabilities[conformal_indices],
        target[conformal_indices].argmax(-1),
        qt[conformal_indices],
    )
    expected_types = {
        translated_questions(questions)[name]["type"] for name in questions
    }
    if set(conformal) != expected_types or not np.isfinite(theta).all():
        raise ValueError("Vega uncertainty fitting omitted a represented question type")
    unbound = float(np.quantile(cal["nearest"][fit_indices], 0.995, method="higher"))
    calibration = {
        "mode": "thermal",
        "theta": theta.tolist(),
        "conformal": conformal,
        "abstain_threshold": {"0.1": 0.0},
        "unbound_threshold": unbound,
        "default_risk": "0.1",
    }
    meta = {
        "format": "dmt-vega-domain-v1",
        "questions": questions,
        "vegaml": installed_identity(),
        "packages": {
            name: importlib.metadata.version(name) for name in RUNTIME_PACKAGES
        },
        "model": args.model,
        "revision": args.revision,
        "backbone_model": args.backbone_model,
        "backbone_revision": args.backbone_revision,
        "abstention_policy": "gate-or-unbound-or-nonsingleton-conformal",
        "views": 1,
    }
    adapter_meta = {
        "name": "domain",
        "rank": args.rank,
        "alpha": float(args.rank),
        "mode": "auto",
        "gate_trained": True,
        "gate_threshold": policy["threshold"],
        "calibration": calibration,
        "base_engine_sha256": wrapper.vega.base_sha256(),
    }
    merged = output / "merged"
    merged.mkdir(parents=True)
    shutil.copyfile(engine_path / "engine.safetensors", merged / "engine.safetensors")
    # Both routes use the locally fitted block. A closed gate is still abstained by the domain policy.
    exported_cfg = copy.deepcopy(cfg)
    exported_cfg.pop("calibration_teacher", None)
    exported_cfg.pop("training", None)
    exported_cfg.update(backbone="backbone", calibration=calibration)
    (merged / "vega_config.json").write_text(
        json.dumps(exported_cfg, indent=2, allow_nan=False), encoding="utf-8"
    )
    (merged / "dmt_vega.json").write_text(
        json.dumps(meta, indent=2, allow_nan=False), encoding="utf-8"
    )
    (merged / "adapters").mkdir()
    save_file(adapter, str(merged / "adapters/domain.safetensors"))
    (merged / "adapters/domain.json").write_text(
        json.dumps(adapter_meta, indent=2, allow_nan=False), encoding="utf-8"
    )
    (merged / "backbone").mkdir()
    for path in backbone_path.iterdir():
        if path.is_file() and (
            path.suffix in {".json", ".safetensors", ".txt", ".model", ".jinja"}
            or path.name in {"LICENSE", "README.md"}
        ):
            shutil.copyfile(path, merged / "backbone" / path.name)
    # In-memory qualification uses exactly the exported domain policy, not the old public calibrator.
    vega = wrapper.vega
    vega.theta, vega.conformal, vega.abstain, vega.unbound = (
        theta,
        conformal,
        {"0.1": 0.0},
        unbound,
    )
    vega.adapters = [
        {
            "name": "domain",
            "meta": adapter_meta,
            "threshold": policy["threshold"],
            "cal": {
                "theta": theta,
                "conformal": conformal,
                "abstain": {"0.1": 0.0},
                "unbound": unbound,
            },
        }
    ]
    wrapper.meta, wrapper.questions = meta, questions
    probes = rows["validation"][:20]
    expected = [wrapper.predict_details(row["state"], questions) for row in probes]
    device = str(vega.device)
    peak = int(torch.cuda.max_memory_allocated()) if device.startswith("cuda") else None
    del wrapper, vega, positive, negative, adapter
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    served = VegaEngine(merged, args.device)
    actual = [served.predict_details(row["state"], questions) for row in probes]
    equivalence = compare_predictions(
        [p["answers"] for p in expected],
        [p["answers"] for p in actual],
        questions,
        0.005,
    )
    for before, after in zip(expected, actual):
        for name in questions:
            a, b = before["uncertainty"][name], after["uncertainty"][name]
            if (
                any(
                    a[key] != b[key]
                    for key in (
                        "prediction_set",
                        "adapter",
                        "unbound",
                        "upstream_abstain",
                    )
                )
                or abs(a["gate"] - b["gate"]) > 0.005
            ):
                raise ValueError(
                    "Reloaded Vega changes applicability or uncertainty on validation probes"
                )
    results = {}
    for role in ("validation", "calibration", "test"):
        pos = [served.predict_details(row["state"], questions) for row in rows[role]]
        neg = [
            served.predict_details(row["state"], questions) for row in negatives[role]
        ]
        metrics = evaluate_predictions(
            rows[role], [p["answers"] for p in pos], questions
        )
        gates = {}
        for name in questions:
            gates[name] = {
                "positive": gate_metrics(
                    [p["uncertainty"][name]["gate"] for p in pos],
                    True,
                    policy["threshold"],
                ),
                "negative": gate_metrics(
                    [
                        p["uncertainty"][name]["gate"]
                        for row, p in zip(negatives[role], neg)
                        if name in row["negative_for"]
                    ],
                    False,
                    policy["threshold"],
                ),
                "positive_accepted_fraction": float(
                    np.mean([not p["answers"][name]["abstained"] for p in pos])
                ),
            }
        results[role] = {"metrics": metrics, "applicability": gates}
    report = {
        "backend": "vega",
        "schema_conditioned": True,
        "model": args.model,
        "revision": args.revision,
        "backbone_model": args.backbone_model,
        "backbone_revision": args.backbone_revision,
        "data_sha256": hashes,
        "gate_data_sha256": negative_hashes,
        "source_sha256": source_fingerprint(),
        "remote_study_sha256": remote_request_sha,
        "local_study_sha256": local_request_sha,
        "configuration": vars(args),
        "packages": meta["packages"],
        "vegaml": meta["vegaml"],
        "counts": {role: len(rows[role]) for role in SPLITS},
        "gate_counts": {role: len(negatives[role]) for role in SPLITS},
        "feature_cache": size,
        "training": training,
        "gate_selection": policy,
        "calibration": calibration,
        "validation": results["validation"]["metrics"],
        "test": results["test"]["metrics"],
        "applicability": {
            role: result["applicability"] for role, result in results.items()
        },
        "hardware": {
            "device": device,
            "peak_gpu_bytes": peak,
            "gpu": torch.cuda.get_device_name() if device.startswith("cuda") else None,
        },
        "reload_equivalence": True,
        "export_qualification": {**equivalence, "same_uncertainty": True},
        "export_precision": "frozen-backbone-original-engine-adapter-float32",
        "limitations": [
            "Domain quality requires reviewed operational gold and owner acceptance",
            "Portable reviewed flags do not prove authenticated annotation",
            "Conformal thresholds pool by question type; gate-selected conditional coverage is not guaranteed",
            "Sixty calibration families (thirty per disjoint fitting partition) is a minimum, not statistical adequacy",
            "No optimizer resume or multi-GPU qualification is implemented for Vega",
        ],
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    write_manifest(output, "vega", questions)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--backbone-model", default=BACKBONE)
    parser.add_argument("--backbone-revision", default=BACKBONE_REVISION)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--rank", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=0.0003)
    parser.add_argument("--gate-weight", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument(
        "--study-request",
        help="Frozen eight-file remote Vega request; standalone execution owns its resource lease",
    )
    parser.add_argument(
        "--local-study-request",
        help="Frozen eight-file request supplied by the application worker that already owns the GPU lease",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Require both pinned snapshots in the selected local cache",
    )
    parser.add_argument(
        "--resource-lock",
        help="Override the shared model lease with an explicit standalone lock file",
    )
    args = parser.parse_args()
    if args.local_study_request:
        # The supervised application worker owns the shared lease for this child.
        # Request/source/input checks still run before importing/loading the model.
        report = train_vega(args)
    else:
        lease = (
            ResourceLease(args.resource_lock)
            if args.resource_lock
            else ModelResourceLease(Path(os.environ.get("DMT_HOME", "data/workspace")))
        )
        with lease.hold(timeout=0):
            report = train_vega(args)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
