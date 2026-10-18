"""Tiny fictional native files for orchestration tests; never a real model."""

import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import patch

from decision_workbench.data import hard_gold, prepare, options
from decision_workbench.training.liquid_data import dumps
from decision_workbench.training import perplexity_checkpoint as checkpoint
from decision_workbench.training import perplexity_runner as runner
from decision_workbench.training.perplexity_assets import BASE_MODEL, BASE_REVISION
from decision_workbench.training.perplexity_runtime import PACKAGES
from test_perplexity_data import PromptFixture

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "Continue only when the fields are ready.",
        "criteria": {
            "review": "Required fields missing",
            "continue": "Required fields ready",
        },
    },
    "ready": {"type": "noul", "instructions": "Are the fields ready?"},
}


def tensor(path, values, *, metadata=None):
    offset = 0
    header = {}
    if metadata is not None:
        header["__metadata__"] = metadata
    bodies = []
    for key, (shape, value) in values.items():
        count = 1
        for n in shape:
            count *= n
        body = (b"\x00\x00" if value == 0 else b"\x80\x3f") * count
        header[key] = {
            "dtype": "BF16",
            "shape": shape,
            "data_offsets": [offset, offset + len(body)],
        }
        bodies.append(body)
        offset += len(body)
    data = json.dumps(header, separators=(",", ":")).encode()
    data += b" " * ((-len(data)) % 8)
    Path(path).write_bytes(struct.pack("<Q", len(data)) + data + b"".join(bodies))


class NativeFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.base = self.root / "base"
        self.base.mkdir()
        self.data = self.root / "canonical"
        self.data.mkdir()
        self.out = self.root / "run"
        self.mapping = {"fictional.backbone.weight": "model-00001-of-00001.safetensors"}
        config = {
            "format_version": 1,
            "base_model": BASE_MODEL,
            "revision": BASE_REVISION,
            "codes": ["A", "B"],
            "token_ids": [1, 2],
            "temperature": 1.0,
            "attention_mode": "noncausal_full_attention",
            "pooling": "last",
        }
        values = {
            "decision_config.json": config,
            "model.safetensors.index.json": {"weight_map": self.mapping},
            "config.json": {"fictional": True},
            "processor_config.json": {},
            "tokenizer.json": {},
            "tokenizer_config.json": {},
        }
        for name, value in values.items():
            (self.base / name).write_text(json.dumps(value), encoding="utf-8")
        (self.base / "chat_template.jinja").write_text("fictional template")
        for name, value in {
            "LICENSE": "Fictional test license",
            "NOTICE": "Fictional test notice",
            "source/src/autojev/__init__.py": "",
            "source/src/autojev/types.py": "TOKEN = 17\n",
            "source/src/autojev/model.py": "from autojev.types import TOKEN\nVALUE = TOKEN\n",
        }.items():
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        self.assets = {
            p.relative_to(self.base).as_posix(): {
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in self.base.rglob("*")
            if p.is_file()
        }
        tensor(
            self.base / self.mapping["fictional.backbone.weight"],
            {"fictional.backbone.weight": ([2], 0)},
        )
        tensor(self.base / "readout.safetensors", {"weight": ([255, 5120], 0)})
        self.weights = {
            p.name: {
                "bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in self.base.glob("*.safetensors")
        }
        rows = [
            {
                "id": f"fixture-{i}",
                "group_id": f"family-{i}",
                "state": {"ready": bool(i % 2), "index": i},
                "questions": QUESTIONS,
                "gold": hard_gold(
                    QUESTIONS,
                    {"action": "continue" if i % 2 else "review", "ready": bool(i % 2)},
                ),
                "metadata": {"origin": "real", "reviewed": True, "fixture": True},
            }
            for i in range(100)
        ]
        self.rows = prepare(rows)
        for role, records in self.rows.items():
            (self.data / (role + ".jsonl")).write_text(
                "".join(dumps(row) + "\n" for row in records), encoding="utf-8"
            )
        self.args = SimpleNamespace(
            data=str(self.data),
            out=str(self.out),
            checkpoint=str(self.base),
            epochs=1,
            batch_size=1,
            grad_accum=4,
            max_length=2048,
            learning_rate=2e-6,
            cpu_threads=4,
            eval_every=20,
        )
        self.calls = []

    def invoke(self, publisher, phase, arguments, log):
        values = [str(v) for v in arguments]
        self.calls.append((phase, values))
        Path(log).write_text("Fictional upstream phase, not model execution\n")

        def option(name):
            return values[values.index(name) + 1]

        if phase == "evaluate":
            Path(option("--output")).with_suffix(".predictions.jsonl").write_text(
                "fictional reference\n"
            )
            return
        assert "--public" not in values and "--save-final-only" in values
        selected = Path(option("--output")) / "step-00001"
        selected.mkdir(parents=True)
        for name in self.assets:
            if "/" not in name:
                (selected / name).write_bytes((self.base / name).read_bytes())
        tensor(
            selected / self.mapping["fictional.backbone.weight"],
            {"fictional.backbone.weight": ([2], 1)},
        )
        tensor(selected / "readout.safetensors", {"weight": ([255, 5120], 1)})
        config = json.loads((selected / "decision_config.json").read_text())
        config["temperature"] = 2.0
        (selected / "decision_config.json").write_text(json.dumps(config))
        # A real upstream run uses a symlink; using a directory lets these
        # orchestration fixtures also run on Windows without symlink privileges.
        import shutil

        shutil.copytree(selected, selected.parent / "selected")
        run = Path(option("--run"))
        run.mkdir()
        (run / "summary.json").write_text(
            json.dumps({"complete": True, "steps": 1, "saved_step": 1})
        )
        predictions = []
        for row in self.rows["validation"]:
            for name, question in row["questions"].items():
                probabilities = [
                    0.8 if row["gold"][name]["probabilities"][key] == 1 else 0.2
                    for key in options(question)
                ]
                predictions.append(
                    {
                        "id": hashlib.sha256(
                            dumps([row["id"], name]).encode()
                        ).hexdigest(),
                        "suite": name,
                        "family": row["group_id"],
                        "options": (
                            [False, True]
                            if name == "ready"
                            else list(question["criteria"])
                        ),
                        "temperature": 2.0,
                        "probabilities": probabilities,
                    }
                )
        (run / "development-00001.jsonl").write_text(
            "".join(dumps(p) + "\n" for p in predictions)
        )

    def patches(self, stack):
        stack.enter_context(patch.object(checkpoint, "ASSETS", self.assets))
        stack.enter_context(patch.object(checkpoint, "WEIGHTS", self.weights))
        stack.enter_context(patch.object(runner, "ASSETS", self.assets))
        stack.enter_context(
            patch.object(
                runner,
                "runtime_identity",
                return_value={**PACKAGES, "fixture": "fictional"},
            )
        )
        stack.enter_context(patch.object(runner, "memory_guard"))
        stack.enter_context(
            patch.object(runner, "PerplexityPrompt", return_value=PromptFixture())
        )
        stack.enter_context(patch.object(runner, "invoke", side_effect=self.invoke))
        stack.enter_context(patch.object(runner, "PerplexityEngine", FakeEngine))
        stack.enter_context(patch.object(runner, "require_linux"))
        stack.enter_context(
            patch.object(
                runner.shutil, "disk_usage", return_value=SimpleNamespace(free=10**13)
            )
        )
        stack.enter_context(patch.object(runner.shutil, "which", return_value="git"))
        stack.enter_context(
            patch.object(runner.subprocess, "check_output", return_value="0" * 40)
        )
        stack.enter_context(
            patch.dict(
                "os.environ",
                {
                    "WORLD_SIZE": "1",
                    "LOCAL_WORLD_SIZE": "1",
                    "RANK": "0",
                    "LOCAL_RANK": "0",
                },
            )
        )


class FakeEngine:
    def __init__(self, checkpoint, device="cuda"):
        assert device == "cuda"
        self.metadata = json.loads(
            (Path(checkpoint) / "dmt_perplexity.json").read_text()
        )

    def predict(self, state, questions):
        result = {}
        for name, question in questions.items():
            answer = (
                ("continue" if state["ready"] else "review")
                if question["type"] == "choice"
                else ("true" if state["ready"] else "false")
            )
            result[name] = {
                "probabilities": {
                    key: 0.8 if key == answer else 0.2 for key in options(question)
                }
            }
        return result
