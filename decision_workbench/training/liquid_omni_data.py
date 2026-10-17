"""Native option-marker data for the pinned Liquid d1-omni-600M checkpoint."""

import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from urllib.request import urlopen

from decision_workbench.data import options, validate_questions
from .contracts import SPLITS
from .liquid_data import dumps, load_inputs

MODEL = "LiquidAI/d1-omni-600M"
REVISION = "02b55d7076f15129e59ab3f94783f32c4b088674"
BASE_WEIGHTS = "0713bb05270c2685ad106522f4092bceeeb3a93cf79b401f399a712296c911e1"
ASSETS = {
    "modeling_d1.py": "2b71a5c909ec5d3aa6307378a41a0fd24af1e17d5c1c1f8d510582147051a686",
    "encoder.py": "5f2e20319ee42a7367febf06c7653d7b3f9b28ef99a64cb9ec7739563dcfcffe",
    "prompt.py": "a6b29a55ec8345f1fc1fdbfcc4b64d80d473dc4316f095a62c51cb6cce1194cf",
    "audio.py": "c5ff09d52d6a079e79d0505c89dbd0582ba2bc9abf2b26595d8d5b1d0c347bdf",
    "vision.py": "43ad71029f82e20fd26b97574627773d47639d7a3742c8b04fe5bf55f41c4c28",
    "config.json": "dfccae241822f81752426629c88c8b8d7efdd03dd19418b6645c604fad43d619",
    "tokenizer_config.json": "03efc74b752b899b6292ff7e607738be59e6f206fec9aca32bbc05e9184090fc",
    "tokenizer.json": "1efc3a6609abf6b63b1f47188d139f3b59973a6a434dffe970a7261a51ed2711",
    "LICENSE": "9953509cbd8c1c6ce7b06ddf74fcc204a892692316201dd8f7400a84790d4120",
}


def sha(body):
    return hashlib.sha256(body).hexdigest()


def checked_asset(folder, name):
    path = Path(folder) / name
    if not path.is_file() or path.stat().st_size > 16 * 1024**2:
        raise ValueError(f"Missing or oversized pinned omni asset: {name}")
    body = path.read_bytes()
    if sha(body) != ASSETS[name]:
        raise ValueError(f"Omni asset differs from the reviewed revision: {name}")
    return body


def download_assets(folder):
    """Fetch only reviewed small assets; no weights or GPU packages."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        path = folder / name
        if path.exists():
            checked_asset(folder, name)
            continue
        with urlopen(
            f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}", timeout=30
        ) as response:
            body = response.read(16 * 1024**2 + 1)
        if sha(body) != ASSETS[name]:
            raise ValueError(f"Downloaded omni asset failed its pinned hash: {name}")
        with path.open("xb") as target:
            target.write(body)
    return folder


class OmniPrompt:
    """The publisher's exact tokenizer/rendering, with silent truncation refused."""

    def __init__(self, folder):
        from tokenizers import Tokenizer

        body = checked_asset(folder, "prompt.py")
        checked_asset(folder, "tokenizer.json")
        checked_asset(folder, "tokenizer_config.json")
        self.tokenizer = Tokenizer.from_file(str(Path(folder) / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.bos_token_id = self.convert_tokens_to_ids("<|startoftext|>")
        self.pad_token_id = self.convert_tokens_to_ids("<|pad|>")
        name = "decision_workbench._omni_prompt_" + ASSETS["prompt.py"][:16]
        module = sys.modules.get(name)
        if module is None:
            module = ModuleType(name)
            sys.modules[name] = module
            try:
                exec(
                    compile(body, str(Path(folder) / "prompt.py"), "exec"),
                    module.__dict__,
                )
            except BaseException:
                sys.modules.pop(name, None)
                raise
        self.native = module

    def convert_tokens_to_ids(self, token):
        value = self.tokenizer.token_to_id(token)
        if value is None:
            raise ValueError("Pinned omni tokenizer is missing a control token")
        return value

    def __call__(self, text, add_special_tokens=False):
        return {
            "input_ids": self.tokenizer.encode(
                text, add_special_tokens=add_special_tokens
            ).ids
        }

    def decision(self, state, question, max_length):
        if type(max_length) is not int or not 64 <= max_length <= 16384:
            raise ValueError("Omni token budget must be between 64 and 16384")
        validate_questions({"decision": question})
        if set(question) - {"type", "instructions", "criteria"}:
            raise ValueError("Unsupported fields in the omni text/JSON question")
        criteria = question.get("criteria")
        if (
            question["type"] == "noul"
            and criteria is not None
            and (
                not isinstance(criteria, dict)
                or set(criteria) not in ({"false", "true"}, {"no", "yes"})
                or any(
                    not isinstance(v, str) or not v.strip() for v in criteria.values()
                )
            )
        ):
            raise ValueError("Omni yes/no criteria need two explicit descriptions")
        q = self.native.as_question(question)

        def encode(text):
            return self(self.native.escape(text))["input_ids"]

        texts = self.native.render_options(q)
        budget = max(96, min(len(texts) * 24 + 32, max_length // 2))
        per = max(2, (budget - 3 * len(texts)) // len(texts))
        instruction = [self.convert_tokens_to_ids(self.native.DELIM["q"])] + encode(
            q.instructions
        )
        if len(instruction) > max(16, budget) or any(
            len(encode(" " + text)) > per for text in texts
        ):
            raise ValueError(
                "Omni native instruction/option budget would truncate the policy"
            )
        # Native encode always budgets each option independently, even with a
        # long overall context. Refuse both policy and state truncation.
        question_length = (
            len(instruction) + sum(3 + len(encode(" " + text)) for text in texts) + 1
        )
        state_ids = encode(self.native.serialize(state))
        if len(state_ids) > max(0, max_length - question_length - 2):
            raise ValueError("Omni state exceeds the complete native token budget")
        ids, markers = self.native.encode(self, state, q, max_length)
        if (
            len(ids) != 2 + len(state_ids) + question_length
            or len(markers) != len(options(question))
            or any(
                ids[pos] != self.convert_tokens_to_ids(self.native.MARKER)
                for pos in markers
            )
        ):
            raise ValueError(
                "Omni input/marker boundaries differ from the native format"
            )
        return {
            "input_ids": ids,
            "marker_positions": markers,
            "question_type": self.native.QTYPES[q.type],
            "option_order": options(question),
        }


def native_record(prompt, row, name, max_length):
    record = prompt.decision(row["state"], row["questions"][name], max_length)
    return {
        "id": row["id"],
        "question_id": name,
        **record,
        "target_probabilities": [
            row["gold"][name]["probabilities"][key] for key in record["option_order"]
        ],
    }


def export_dataset(data, out, prompt, max_length=2048):
    rows, bodies = load_inputs(data)
    out = Path(out).absolute()
    if out.exists():
        raise ValueError(
            "Choose a new omni export directory; existing data is never replaced"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    counts, maximums = {}, {}
    with tempfile.TemporaryDirectory(prefix=".omni-export-", dir=out.parent) as temp:
        stage = Path(temp) / "bundle"
        for name in ("canonical", "native", "lineage"):
            (stage / name).mkdir(parents=True)
        for split in SPLITS:
            (stage / "canonical" / (split + ".jsonl")).write_bytes(bodies[split])
            counts[split], maximums[split] = 0, 0
            with (
                (stage / "native" / (split + ".jsonl")).open(
                    "w", encoding="utf-8", newline="\n"
                ) as native,
                (stage / "lineage" / (split + ".jsonl")).open(
                    "w", encoding="utf-8", newline="\n"
                ) as lineage,
            ):
                for row in rows[split]:
                    if set(row) - {
                        "id",
                        "group_id",
                        "state",
                        "questions",
                        "gold",
                        "metadata",
                    }:
                        raise ValueError(
                            "This omni profile accepts text/JSON records; media needs its own asset contract"
                        )
                    for name in row["questions"]:
                        record = native_record(prompt, row, name, max_length)
                        native.write(dumps(record) + "\n")
                        lineage.write(
                            dumps(
                                {
                                    "id": row["id"],
                                    "question_id": name,
                                    "group_id": row["group_id"],
                                    "metadata": row["metadata"],
                                    "gold": row["gold"][name],
                                    "split": split,
                                    "native_sha256": sha(dumps(record).encode()),
                                }
                            )
                            + "\n"
                        )
                        counts[split] += 1
                        maximums[split] = max(maximums[split], len(record["input_ids"]))
        files = {
            p.relative_to(stage).as_posix(): {
                "sha256": sha(p.read_bytes()),
                "bytes": p.stat().st_size,
            }
            for p in stage.rglob("*.jsonl")
        }
        manifest = {
            "format": "domain-liquid-omni-data-v1",
            "model": MODEL,
            "revision": REVISION,
            "max_length": max_length,
            "native_order": "false,true for noul; canonical option order otherwise",
            "counts": counts,
            "max_tokens": maximums,
            "files": files,
        }
        (stage / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
        if out.exists():
            raise ValueError("Omni export destination was created concurrently")
        stage.rename(out)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--assets", required=True)
    parser.add_argument("--download-assets", action="store_true")
    parser.add_argument("--max-length", type=int, default=2048)
    args = parser.parse_args()
    try:
        if args.download_assets:
            download_assets(args.assets)
        print(
            dumps(
                export_dataset(
                    args.data, args.out, OmniPrompt(args.assets), args.max_length
                )
            )
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Omni preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
