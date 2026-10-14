"""Offline workflow fixture using declared orthogonal vectors, NOT a semantic model."""

import argparse
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from decision_workbench.training.semantic_runner import cosine_pairs


parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_bytes())
rows = request["rows"]
if any(row["state"].startswith("STALL") for row in rows):
    time.sleep(120)
ids = [row["id"] for row in rows]
vectors = np.eye(len(rows) + 1, dtype=np.float32)[1:]
for i, row in enumerate(rows):
    if row["state"].startswith("RELATED"):
        vectors[i] = 0
        vectors[i, 0] = 1
for i, row in enumerate(rows):
    if row["state"].startswith("NEAR:"):
        vectors[i] = vectors[ids.index(row["state"].split(":", 1)[1])]
pairs, sha = cosine_pairs(
    vectors, ids, request["threshold"], request["profile"]["max_pairs"]
)
report = {
    "format": "domain-semantic-audit-v1",
    "input_sha256": request["input_sha256"],
    "source_sha256": request["source_sha256"],
    "threshold": request["threshold"],
    "row_count": len(rows),
    "compared_pairs": len(rows) * (len(rows) - 1) // 2,
    "pairs": pairs,
    "embedding_sha256": sha,
    "dimension": vectors.shape[1],
    "max_observed_tokens": 8,
    "pooling": "attention-mask-mean-l2",
    "device": "cpu",
    "packages": request["profile"]["packages"],
    "scope": "Declared orthogonal vectors fixture, not model quality",
}
if any(row["state"].startswith("CORRUPT") for row in rows):
    report["input_sha256"] = "0" * 64
Path(args.out).write_text(json.dumps(report), encoding="utf-8")
