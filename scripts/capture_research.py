"""Capture public source metadata and hashes; never download weights."""

import argparse
import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SOURCES = {
    "clef": "https://huggingface.co/Cloudflare/clef/raw/main/README.md",
    "clef-flash": "https://huggingface.co/Cloudflare/clef-flash/raw/main/README.md",
    "perplexity-v1": "https://huggingface.co/perplexity-ai/pplx-decider-v1-27b/raw/main/README.md",
    "perplexity-v1.1": "https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/raw/main/README.md",
    "kev": "https://huggingface.co/jaredpalmer/kev-27b/raw/main/README.md",
    "lev": "https://raw.githubusercontent.com/InterfazeAI/lev/main/README.md",
    "strands": "https://huggingface.co/StrandsAgents/strands-decider-2B-hobson-v19/raw/main/README.md",
    "typed-decisions": "https://huggingface.co/datasets/LocalLLaMA/typed-decisions/raw/main/README.md",
    "jevbench-manifest": "https://benchmarkheaven.com/api/jevbench/manifest",
    "jevbench-results": "https://benchmarkheaven.com/api/decision-results/jevbench/v1.6.1/json",
    "jevbench-method": "https://raw.githubusercontent.com/fstandhartinger/jevbench/main/README.md",
    "decision-index": "https://huggingface.co/spaces/multimodalart/jev-decision-index/raw/main/README.md",
    "decision-index-methodology-32425c2": "https://huggingface.co/spaces/multimodalart/jev-decision-index/raw/32425c2/data/methodology.json",
    "decision-index-index-32425c2": "https://huggingface.co/spaces/multimodalart/jev-decision-index/raw/32425c2/data/index.json",
    "unsloth-installation": "https://unsloth.ai/docs/get-started/install/pip-install.md",
    "unsloth-training": "https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth.md",
    "unsloth-code": "https://raw.githubusercontent.com/unslothai/unsloth/main/unsloth/models/decision.py",
    "laya-code": "https://raw.githubusercontent.com/NandhaKishorM/laya/main/laya/train.py",
    "laya-benchmarks": "https://raw.githubusercontent.com/NandhaKishorM/laya/main/BENCHMARKS.md",
}


def capture(item, capture_dir):
    name, url = item
    record = {
        "id": name,
        "url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with urlopen(
            Request(url, headers={"User-Agent": "Decision-Model-Training research"}),
            timeout=40,
        ) as response:
            body = response.read(12_000_001)
            if len(body) > 12_000_000:
                raise ValueError("Source exceeds capture bound")
            record.update(
                status=response.status,
                resolved_url=response.url,
                sha256=hashlib.sha256(body).hexdigest(),
                bytes=len(body),
            )
            (capture_dir / (name + ".txt")).write_bytes(body)
    except Exception as exc:
        record["error_type"] = type(exc).__name__
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New dated source metadata JSON; existing files are refused",
    )
    parser.add_argument(
        "--capture-dir",
        type=Path,
        required=True,
        help="New private raw-capture directory, normally under .research",
    )
    args = parser.parse_args()
    if args.output.exists() or args.capture_dir.exists():
        parser.error(
            "Choose new output and capture paths; historical captures are not overwritten"
        )
    args.capture_dir.mkdir(parents=True, exist_ok=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        records = list(
            pool.map(lambda item: capture(item, args.capture_dir), SOURCES.items())
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(records, indent=2))
    for record in records:
        print(record["id"], record.get("status", record.get("error_type")))
