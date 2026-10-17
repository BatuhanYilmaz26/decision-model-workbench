"""Prepare reviewed domain data for Liquid's d1-3B SFT notebooks on a CPU.

This module downloads no weights and does not start training. The native prompt
builder and tokenizer are pinned independently of the application's GPU runtime.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
from urllib.request import urlopen

from decision_workbench.data import options, read_records
from .contracts import SPLITS, validate_splits

MODEL = "LiquidAI/d1-3B"
REVISION = "051bcc464b01b9f92942b364d9586b0ef5912432"
COOKBOOK_REVISION = "744c5b628a27adb4f219ff4a1d11e3d70259b369"
ASSETS = {
    "prompt.py": "a20b5f52e41a6d29e694bb8c861a5a4803ce3eb69364edad79511ccf8edb5c31",
    "tokenizer.json": "8096ecb9f54599d756c8de728a598a340bc1e43c0deb77ddd62456c38349fcee",
    "tokenizer_config.json": "ef6770d12dd9ac58d334e979bf160c5cdfd41f13d205fd5d5516857645feb0ea",
    "LICENSE": "4d28ca14dedc0b3d0fcc2b3339f0e79931faa33874f3d24f522183a8fc70068c",
}
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_DECISIONS = 100000


def dumps(value):
    # Criteria order determines the native option aliases. Keep insertion order
    # in evaluation requests as well as in the rendered training prompts.
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def checked_asset(folder, name):
    """Verify bytes before parsing the tokenizer or executing the prompt builder."""
    path = Path(folder) / name
    # Hugging Face uses content-addressed cache links on Linux. Verify the
    # resolved bytes; exported domain bundles reject links in their inventory.
    if not path.is_file() or path.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError(f"Missing or invalid Liquid asset: {name}")
    with path.open("rb") as stream:
        body = stream.read(MAX_ASSET_BYTES + 1)
    if len(body) > MAX_ASSET_BYTES or sha(body) != ASSETS[name]:
        raise ValueError(
            f"Liquid asset hash differs from the reviewed revision: {name}"
        )
    return body


def download_assets(folder):
    """Fetch only the four reviewed public assets; keep existing cache files intact."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        if (folder / name).exists():
            checked_asset(folder, name)
            continue
        url = f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}"
        with urlopen(url, timeout=60) as response:
            body = response.read(MAX_ASSET_BYTES + 1)
        if len(body) > MAX_ASSET_BYTES or sha(body) != ASSETS[name]:
            raise ValueError(f"Downloaded Liquid asset failed identity checks: {name}")
        # Exclusive creation also protects a cache populated by another process.
        try:
            with (folder / name).open("xb") as stream:
                stream.write(body)
        except FileExistsError:
            checked_asset(folder, name)


class LiquidPrompt:
    """Liquid's exact pinned native formatting, without Torch or Transformers."""

    def __init__(self, folder):
        bodies = {name: checked_asset(folder, name) for name in ASSETS}
        try:
            from tokenizers import Tokenizer
        except ImportError as error:
            raise ValueError(
                "Install the optional liquid-data profile: "
                "python -m pip install -r requirements/liquid-data.txt"
            ) from error
        self.tokenizer = Tokenizer.from_str(bodies["tokenizer.json"].decode("utf-8"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        config = json.loads(bodies["tokenizer_config.json"])
        self.bos_token, self.eos_token = config["bos_token"], config["eos_token"]
        self.special_tokens = {
            value.content
            for value in self.tokenizer.get_added_tokens_decoder().values()
            if value.special
        }
        name = "decision_workbench._liquid_prompt_" + ASSETS["prompt.py"][:16]
        module = sys.modules.get(name)
        if module is None:
            module = ModuleType(name)
            sys.modules[name] = module  # dataclass resolves its declaring module
            try:
                exec(
                    compile(
                        bodies["prompt.py"], str(Path(folder) / "prompt.py"), "exec"
                    ),
                    module.__dict__,
                )
            except BaseException:
                sys.modules.pop(name, None)
                raise
        self.native = module

    def encode(self, text, add_special_tokens=False):
        return self.tokenizer.encode(text, add_special_tokens=add_special_tokens).ids

    def decision(self, state, question):
        if set(question) - {"type", "instructions", "criteria"}:
            raise ValueError(
                "Unsupported question fields in the Liquid text/JSON profile"
            )
        if question["type"] == "noul" and question.get("criteria") is not None:
            criteria = question["criteria"]
            if (
                not isinstance(criteria, dict)
                or set(criteria) != {"true", "false"}
                or any(
                    not isinstance(v, str) or not v.strip() for v in criteria.values()
                )
            ):
                raise ValueError(
                    "Liquid yes/no criteria need true and false descriptions"
                )
        raw = dumps({"state": state, "question": question})
        if any(token in raw for token in self.special_tokens):
            raise ValueError(
                "Evidence/questions contain a reserved Liquid control token"
            )
        native = self.native.as_question(question)
        prompt = self.native.render(self, state, native, bos=self.bos_token)
        keys = options(question)
        groups = self.native.readout_ids(self, native)
        if question["type"] == "choice":
            codes = dict(
                zip(keys, (code for code, _ in self.native.aliases(self, keys)))
            )
            readout = dict(zip(keys, groups))
        elif question["type"] == "noul":
            codes = {"false": "no", "true": "yes"}
            readout = {"true": groups[0], "false": groups[1]}
        else:
            codes = {key: key for key in keys}
            readout = dict(zip(keys, groups))
        return prompt, codes, readout


def load_inputs(folder):
    """Bound input files before using the common reviewed/four-role contract."""
    rows, bodies = {}, {}
    for split in SPLITS:
        path = Path(folder) / (split + ".jsonl")
        with path.open("rb") as stream:
            body = stream.read(MAX_INPUT_BYTES + 1)
        if len(body) > MAX_INPUT_BYTES:
            raise ValueError(f"{split}: canonical input exceeds 64 MiB")
        rows[split] = read_records(body, path.name, max_rows=20000)
        bodies[split] = body
    questions = validate_splits(rows)
    if sum(len(split) * len(questions) for split in rows.values()) > MAX_DECISIONS:
        raise ValueError("Export exceeds 100,000 decisions; prepare a smaller study")
    return rows, bodies


def native_record(adapter, row, name, max_length, training):
    """One native question, with explicit label identity and token-boundary checks."""
    question = row["questions"][name]
    prompt, codes, readout = adapter.decision(row["state"], question)
    prompt_ids = adapter.encode(prompt)
    if not prompt_ids or len(prompt_ids) > max_length:
        raise ValueError(f"{row['id']}/{name}: prompt exceeds the chosen token budget")
    lineage = {
        "id": row["id"],
        "question_id": name,
        "group_id": row["group_id"],
        "metadata": row["metadata"],
        "gold": row["gold"][name],
        "option_codes": codes,
        "option_order": list(codes),
        "readout_token_ids": readout,
        "prompt_tokens": len(prompt_ids),
    }
    if not training:
        return {"state": row["state"], "questions": {name: question}}, lineage
    probabilities = row["gold"][name]["probabilities"]
    targets = [key for key, value in probabilities.items() if value == 1]
    if len(targets) != 1 or any(
        value not in (0, 1) for value in probabilities.values()
    ):
        raise ValueError(
            f"{row['id']}/{name}: this SFT profile needs one-hot targets; "
            "soft labels are preserved in canonical data and must not be rounded"
        )
    code = codes[targets[0]]
    target_ids = adapter.encode(code)
    if len(target_ids) != 1 or target_ids[0] not in readout[targets[0]]:
        raise ValueError(
            f"{row['id']}/{name}: completion differs from the native readout"
        )
    completion = code + adapter.eos_token
    joined = adapter.encode(prompt + completion)
    if joined != prompt_ids + target_ids + adapter.encode(adapter.eos_token):
        raise ValueError(
            f"{row['id']}/{name}: tokenizer changes the completion boundary"
        )
    if len(joined) > max_length:
        raise ValueError(
            f"{row['id']}/{name}: completion exceeds the chosen token budget"
        )
    lineage["completion_tokens"] = len(joined) - len(prompt_ids)
    return {"prompt": prompt, "completion": completion}, lineage


def export_dataset(data, out, adapter, max_length=2048):
    """Write both official SFT representations and retain independent gold data."""
    if type(max_length) is not int or not 16 <= max_length <= 32768:
        raise ValueError("Choose a whole-number token budget from 16 to 32768")
    rows, bodies = load_inputs(data)
    out = Path(out).absolute()
    if out.exists():
        raise ValueError(
            "Choose a new export directory; existing data is never replaced"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    counts, maximums = {}, {}
    with tempfile.TemporaryDirectory(prefix=".liquid-export-", dir=out.parent) as temp:
        stage = Path(temp) / "bundle"
        for folder in ("canonical", "trl", "unsloth", "evaluation", "lineage"):
            (stage / folder).mkdir(parents=True, exist_ok=True)
        for split in SPLITS:
            (stage / "canonical" / (split + ".jsonl")).write_bytes(bodies[split])
            training = split in ("train", "validation")
            counts[split], maximums[split] = 0, 0
            with (stage / "lineage" / (split + ".jsonl")).open(
                "w", encoding="utf-8", newline="\n"
            ) as lineage_file:
                targets = ("trl", "unsloth") if training else ("evaluation",)
                streams = []
                try:
                    for folder in targets:
                        streams.append(
                            (stage / folder / (split + ".jsonl")).open(
                                "w", encoding="utf-8", newline="\n"
                            )
                        )
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
                                f"{row['id']}: unsupported top-level fields; this is a text/JSON profile"
                            )
                        for name in row["questions"]:
                            record, lineage = native_record(
                                adapter, row, name, max_length, training
                            )
                            lineage["split"] = split
                            lineage["native_sha256"] = sha(dumps(record).encode())
                            lineage_file.write(dumps(lineage) + "\n")
                            if training:
                                streams[0].write(dumps(record) + "\n")
                                streams[1].write(
                                    dumps(
                                        {
                                            "text": record["prompt"]
                                            + record["completion"]
                                        }
                                    )
                                    + "\n"
                                )
                            else:
                                streams[0].write(dumps(record) + "\n")
                            counts[split] += 1
                            maximums[split] = max(
                                maximums[split],
                                lineage["prompt_tokens"]
                                + lineage.get("completion_tokens", 0),
                            )
                finally:
                    for stream in streams:
                        stream.close()
        manifest = {
            "format": "domain-liquid-sft-data-v1",
            "model": MODEL,
            "revision": REVISION,
            "cookbook_revision": COOKBOOK_REVISION,
            "assets_sha256": ASSETS,
            "max_length": max_length,
            "counts": counts,
            "maximum_tokens": maximums,
            "input_sha256": {name: sha(body) for name, body in bodies.items()},
            "files": {
                p.relative_to(stage).as_posix(): sha(p.read_bytes())
                for p in sorted(stage.rglob("*"))
                if p.is_file()
            },
            "training_targets": "one-hot; native option token followed by end-of-turn",
            "modalities": ["text", "JSON"],
            "trained_model": False,
        }
        (stage / "manifest.json").write_text(dumps(manifest) + "\n", encoding="utf-8")
        # copytree creates the destination exclusively. On failure, retain an
        # incomplete export for inspection; only a completed manifest is usable.
        shutil.copytree(stage, out, ignore=shutil.ignore_patterns("manifest.json"))
        (out / "manifest.json").write_text(dumps(manifest) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        required=True,
        help="Folder containing four reviewed canonical JSONL splits",
    )
    parser.add_argument(
        "--out", required=True, help="New directory for the native data bundle"
    )
    parser.add_argument("--cache", default=f"data/model-cache/liquid-data/{REVISION}")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Fetch the pinned prompt/tokenizer/license assets (no weights)",
    )
    parser.add_argument("--max-length", type=int, default=2048)
    args = parser.parse_args()
    try:
        # Refuse invalid studies before initiating any optional network fetch.
        if not 16 <= args.max_length <= 32768:
            raise ValueError("Choose a whole-number token budget from 16 to 32768")
        load_inputs(args.data)
        if Path(args.out).exists():
            raise ValueError(
                "Choose a new export directory; existing data is never replaced"
            )
        if args.download:
            download_assets(args.cache)
        report = export_dataset(
            args.data, args.out, LiquidPrompt(args.cache), args.max_length
        )
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(
        json.dumps(
            {
                "export": str(Path(args.out).absolute()),
                "decisions": report["counts"],
                "trained_model": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
