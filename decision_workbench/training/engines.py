"""Pinned decision-head loaders; no chat completion or uploaded executable weights."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS

from pathlib import Path
import json
import importlib.metadata
import numpy as np
from decision_workbench.data import hard_gold, options
from .metrics import normalize_answers


def dummy_row(state, questions):
    return {
        "id": "inference",
        "group_id": "inference",
        "state": state,
        "questions": questions,
        "gold": hard_gold(
            questions, {name: options(q)[0] for name, q in questions.items()}
        ),
        "metadata": {"origin": "real", "reviewed": True},
    }


def native_items(tokenizer, config, rows):
    from laya import common
    from laya.train import to_internal, items_from_rows

    max_len = int(config["max_len"])
    head_len = int(config["head_max_len"])
    for row in rows:
        for name, question in row["questions"].items():
            q = to_internal(name, question)
            ids, markers, stats, truncation = common.build_sequence(
                tokenizer,
                row["state"],
                q,
                max_len,
                head_len,
                return_stats=True,
                return_truncation_stats=True,
            )
            # Check the actual tokenizer/layout, including question and option budgets.
            full_head, _, _ = common.build_head(tokenizer, q, 65536)
            head, _, _ = common.build_head(tokenizer, q, head_len)
            long_option = any(
                len(
                    common.encode_text(
                        tokenizer,
                        " " + o.replace(tokenizer.mask_token, " "),
                        add_special_tokens=False,
                    )["input_ids"]
                )
                > 48
                for o in common.render_options(q)
            )
            if truncation["truncated"] or len(full_head) > len(head) or long_option:
                raise ValueError(
                    f"{name}: checkpoint token budget truncates state, instructions or options"
                )
            if (
                len(markers) != len(options(question))
                or stats["options_distinct"] != stats["options"]
            ):
                raise ValueError(f"{name}: checkpoint layout loses an option")
    items, skipped = items_from_rows(tokenizer, rows, max_len, head_len)
    if skipped or len(items) != sum(len(row["questions"]) for row in rows):
        raise ValueError("Native checkpoint preparation skipped a decision")
    return items


class NativeEngine:
    def __init__(self, checkpoint, device="auto"):
        from laya.train import load_checkpoint, resolve_device

        self.model, self.tokenizer, self.config = load_checkpoint(str(checkpoint))
        self.model.encoder.config.reference_compile = False
        self.device = resolve_device(device)
        self.model.to(self.device).eval()

    def predict(self, state, questions):
        from laya import common
        from laya.train import calibration_records

        items = native_items(self.tokenizer, self.config, [dummy_row(state, questions)])
        records = calibration_records(
            self.model,
            self.tokenizer,
            items,
            self.device,
            self.config["max_len"],
            self.config["head_max_len"],
            batch_size=1,
            parallel=self.config.get("option_layout") == "parallel",
        )
        answers = {}
        for (name, question), (qt, logits, target, k) in zip(
            questions.items(), records
        ):
            temperature = self.config.get("temperature_by_options", {}).get(
                common.temp_bucket(qt, k), self.config["temperature"][qt]
            )
            z = np.asarray(logits, dtype=float) / common.clamp_temperature(temperature)
            p = np.exp(z - z.max())
            p /= p.sum()
            answers[name] = {"probabilities": dict(zip(options(question), p.tolist()))}
        return normalize_answers(answers, questions)


class UnslothEngine:
    def __init__(self, checkpoint):
        from unsloth import FastDecisionModel

        self.api = FastDecisionModel
        config_file = Path(checkpoint) / "rl_agent_config.json"
        max_length = (
            json.loads(config_file.read_text(encoding="utf-8")).get("max_len")
            if config_file.is_file()
            else None
        )
        self.model, self.tokenizer = self.api.from_pretrained(
            str(checkpoint),
            local_files_only=True,
            load_in_4bit=False,
            max_seq_length=max_length,
        )
        self.api.for_inference(self.model)

    def predict(self, state, questions):
        row = dummy_row(state, questions)
        if not getattr(self.model, "is_clef", False):
            native_items(self.tokenizer, self.model.decision_config, [row])
        items, report = self.api.build_dataset([row], self.tokenizer, self.model)
        if report.get("skipped") or report.get("truncated") or not items:
            raise ValueError("Inference exceeds the qualified checkpoint input budget")
        # Public predict rounds probabilities to four decimals. Use the pinned
        # calibrated logits path for evaluation and serving, preserving normalization.
        if importlib.metadata.version("unsloth") != "2026.10.3":
            raise ValueError(
                "This raw-probability adapter requires the qualified Unsloth 2026.10.3 profile"
            )
        from unsloth.models.decision import _decision_logits, _served_temperatures

        logits, scored_items = _decision_logits(
            self.model, self.tokenizer, items, batch_size=1
        )
        temperatures = _served_temperatures(
            self.model.decision_config, logits, scored_items
        )
        if len(logits) != len(questions):
            raise ValueError("Model omitted a pinned question")
        answers = {}
        for (name, question), z, temperature in zip(
            questions.items(), logits, temperatures
        ):
            values = (z / temperature).softmax(-1).tolist()
            answers[name] = {"probabilities": dict(zip(options(question), values))}
        return normalize_answers(answers, questions)


def load_engine(backend, checkpoint):
    checkpoint = Path(checkpoint)
    if not checkpoint.is_dir():
        raise ValueError("Trained checkpoint is missing")
    if backend == "laya-native":
        return NativeEngine(checkpoint)
    if backend == "unsloth":
        return UnslothEngine(checkpoint)
    if backend == "vega":
        from .vega_engine import VegaEngine

        return VegaEngine(checkpoint)
    if backend in ("liquid-trl", "liquid-unsloth"):
        from .liquid_runtime import LiquidEngine

        return LiquidEngine(checkpoint, profile=backend)
    if backend == "liquid-omni":
        from .liquid_omni_runtime import OmniEngine

        return OmniEngine(checkpoint)
    if backend in D3_BACKENDS:
        from .d3_runtime import D3Engine

        return D3Engine(checkpoint, backend=backend)
    if backend == "perplexity-native":
        from .perplexity_runtime import PerplexityEngine

        return PerplexityEngine(checkpoint, device="cuda")
    raise ValueError("Unknown trained decision backend")
