"""Extract selected facts from the captured, versioned JevBench artifact."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
from datetime import date

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--input", type=Path, required=True, help="Captured JevBench v1.6.1 JSON"
)
parser.add_argument(
    "--output-dir",
    type=Path,
    required=True,
    help="New extraction directory; existing directories are refused",
)
parser.add_argument(
    "--review-date",
    type=date.fromisoformat,
    required=True,
    help="Actual source review date, YYYY-MM-DD",
)
args = parser.parse_args()
if args.output_dir.exists():
    parser.error(
        "Choose a new output directory; historical extracts are not overwritten"
    )
path = args.input
body = path.read_bytes()
data = json.loads(body)
if data["version"] != "v1.6.1":
    raise SystemExit(
        "This extractor is for v1.6.1; review newer methods before changing it."
    )
terms = ("quyet", "decisio", "jev 1.13", "h2o-lightning", "perplexity", "clef", "laya")
rows = [
    row for row in data["rows"] if any(term in row["name"].lower() for term in terms)
]
columns = [
    "key",
    "name",
    "board",
    "model_pin",
    "last_measured_on",
    "source_url",
    "ranked",
    "capability_eligible",
    "capability",
    "composite",
    "intelligence",
    "calibration",
    "usd_per_1000_decisions",
    "price_kind",
    "price_basis",
    "p50_raw_s",
    "p50_adjusted_s",
    "latency_adjustment",
]
args.output_dir.mkdir(parents=True, exist_ok=False)
out = args.output_dir / "benchmark-extract.csv"
with out.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
metadata = {
    "benchmark": data["benchmark"],
    "version": data["version"],
    "published_at": data["published_at"],
    "review_date": args.review_date.isoformat(),
    "publisher_source_sha256": data["source_sha256"],
    "retrieved_bytes_sha256": hashlib.sha256(body).hexdigest(),
    "source_url": "https://benchmarkheaven.com/api/decision-results/jevbench/v1.6.1/json",
    "method": data["method"],
    "scope": data["scope"],
    "rows_selected": len(rows),
    "extract_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
}
(args.output_dir / "benchmark-method.json").write_text(
    json.dumps(metadata, indent=2), encoding="utf-8"
)
print(f"Extracted {len(rows)} selected rows")
