"""Explicit Vega engine readout with complete-prompt checks and preserved abstention."""

import hashlib
import importlib.metadata
import json
from pathlib import Path

from decision_workbench.data import dumps, options, validate_questions
from .metrics import normalize_answers


def installed_identity():
    package = importlib.metadata.distribution("vegaml")
    if package.version != "0.8.0":
        raise ValueError("This Vega adapter requires separately qualified vegaml 0.8.0")
    files = {
        str(p)
        .replace("\\", "/"): hashlib.sha256(Path(package.locate_file(p)).read_bytes())
        .hexdigest()
        for p in package.files
        if str(p).startswith("vegaml") and str(p).endswith(".py")
    }
    if not files:
        raise ValueError("Vega installed source identity is unavailable")
    return {"version": package.version, "python_sha256": files}


def translated_questions(questions):
    validate_questions(questions)
    canonical = json.loads(dumps(questions))
    return {
        name: {
            **question,
            "type": "boolean" if question["type"] == "noul" else question["type"],
        }
        for name, question in canonical.items()
    }


def checked_items(vega, state, questions):
    """Refuse every upstream slice, including the shorter query-first prefix."""
    import numpy as np
    from vegaml._common import (
        build_prompt,
        option_texts,
        serialize_state,
        TEMPLATES,
        QUERY_FIRST,
    )

    if (
        not isinstance(state, (str, dict, list))
        or state in ("", {}, [])
        or len(dumps(state).encode("utf-8")) > 64000
    ):
        raise ValueError("Vega requires nonempty finite state evidence within 64 KB")
    text = serialize_state(state)
    items = []
    for question in translated_questions(questions).values():
        q = vega._internal(question)
        tokenizer = vega.perc.tok
        enc = lambda s: tokenizer(s, add_special_tokens=False)["input_ids"]
        state_ids = enc(text)
        qids = enc(TEMPLATES[0]["q"].format(t=q["t"], ins=str(q["ins"])))
        if len(qids) > 256:
            raise ValueError(
                "Vega question instructions exceed the complete 256-token prompt span"
            )
        if any(
            len(enc(TEMPLATES[0]["opt"].format(o=value))) > vega.cfg["opt_max_tokens"]
            for value in option_texts(q)
        ):
            raise ValueError(
                "Vega option descriptions exceed the complete option-token budget"
            )
        query_limit = vega.cfg.get("query_first_max_state", 0)
        if (
            query_limit
            and len(state_ids) < query_limit
            and len(enc(QUERY_FIRST.format(t=q["t"], ins=str(q["ins"])))) > 128
        ):
            raise ValueError("Vega query-first instructions would be truncated")
        ids, spans = build_prompt(
            tokenizer,
            text,
            q,
            vega.cfg["max_len"],
            option_order=list(range(len(option_texts(q)))),
            opt_max_tokens=vega.cfg["opt_max_tokens"],
            query_first_max_state=query_limit,
        )
        if (
            len(ids) > vega.cfg["max_len"]
            or ids[spans["state"][0] : spans["state"][1]] != state_ids
            or ids[spans["question"][0] : spans["question"][1]] != qids
        ):
            raise ValueError("Vega token budget truncates state or question evidence")
        for value, (left, right) in zip(option_texts(q), spans["options"]):
            if ids[left:right] != enc(TEMPLATES[0]["opt"].format(o=value)):
                raise ValueError("Vega token budget loses an option")
        items.append({"ids": np.asarray(ids, dtype=np.int32), "spans": spans})
    return items


class VegaEngine:
    def __init__(
        self, checkpoint, device="auto", *, training_base=False, backbone=None
    ):
        identity = installed_identity()
        checkpoint = Path(checkpoint).resolve()
        from vegaml._api import Vega

        if not training_base:
            self.meta = json.loads(
                (checkpoint / "dmt_vega.json").read_text(encoding="utf-8")
            )
            if (
                self.meta.get("format") != "dmt-vega-domain-v1"
                or self.meta.get("views") != 1
                or self.meta.get("abstention_policy")
                != "gate-or-unbound-or-nonsingleton-conformal"
            ):
                raise ValueError(
                    "Vega export has an unsupported domain execution policy"
                )
            if self.meta["vegaml"] != identity:
                raise ValueError(
                    "Vega serving package differs from the export qualification"
                )
            for name in (
                "vegaml",
                "torch",
                "transformers",
                "numpy",
                "safetensors",
                "tokenizers",
            ):
                if self.meta["packages"].get(name) != importlib.metadata.version(name):
                    raise ValueError(
                        "Vega serving runtime differs from its qualified export"
                    )
            backbone = checkpoint / "backbone"
            if not backbone.is_dir():
                raise ValueError(
                    "Vega export must contain its qualified local backbone"
                )
            self.questions = self.meta["questions"]
            validate_questions(self.questions)
        else:
            self.meta = None
            self.questions = None
            backbone = Path(backbone).resolve() if backbone else checkpoint / "backbone"
            if not backbone.is_dir():
                raise ValueError("Training requires a pinned local Vega backbone")
        # The upstream _api class is the engine only; public load(auto) never participates.
        self.vega = Vega(
            str(checkpoint),
            backbone=str(backbone),
            device=None if device == "auto" else device,
            adapters="off" if training_base else "auto",
        )
        if not training_base and (
            len(self.vega.adapters) != 1 or self.vega.adapters[0]["name"] != "domain"
        ):
            raise ValueError(
                "Vega export did not load its single trained domain adapter"
            )

    def predict_details(self, state, questions):
        if self.questions is not None and questions != self.questions:
            raise ValueError("Vega decisions require the exact trained schema")
        checked_items(self.vega, state, questions)
        returned = self.vega.decide(state, translated_questions(questions), views=1)
        answers = returned["answers"]
        if set(answers) != set(questions):
            raise ValueError("Vega returned a different question set")
        result, uncertainty = {}, {}
        for name, question in questions.items():
            answer = answers[name]
            keys = options(question)
            probabilities = answer["probs"]
            source_keys = ["False", "True"] if question["type"] == "noul" else keys
            if set(probabilities) != set(source_keys):
                raise ValueError(
                    "Vega probabilities differ from the trained label order"
                )
            # Closed gate means this trained domain adapter declined applicability.
            closed = self.meta is not None and answer.get("adapter") != "domain"
            prediction_set = answer["prediction_set"]
            if prediction_set is not None:
                prediction_set = [
                    str(value).lower() if question["type"] == "noul" else str(value)
                    for value in prediction_set
                ]
                if not set(prediction_set) <= set(keys) or len(
                    set(prediction_set)
                ) != len(prediction_set):
                    raise ValueError("Vega returned a malformed conformal set")
            uncertain = self.meta is not None and (
                prediction_set is None or len(prediction_set) != 1
            )
            result[name] = {
                "probabilities": {
                    k: probabilities[v] for k, v in zip(keys, source_keys)
                },
                "model_abstained": bool(
                    closed or uncertain or answer["abstain"] or answer["unbound"]
                ),
            }
            uncertainty[name] = {
                "prediction_set": prediction_set,
                "adapter": answer.get("adapter"),
                "gate": answer.get("gate"),
                "unbound": answer["unbound"],
                "upstream_abstain": answer["abstain"],
            }
        return {
            "answers": normalize_answers(result, questions),
            "uncertainty": uncertainty,
        }

    def predict(self, state, questions):
        return self.predict_details(state, questions)["answers"]
