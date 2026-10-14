# Helper commands

The application workflow starts in [local setup](../docs/setup.md). These commands support training, reproducible checks and research maintenance; they do not replace the browser/API workflow.

| Command | Purpose | Guide |
|---|---|---|
| `python scripts/check_repository.py` | Validate public links, Python examples, syntax, data examples and benchmark identity | [Testing](../docs/verification.md) |
| `python scripts/verify_containers.py --help` | Run an isolated fictional CPU or Vega lifecycle and retain a local report | [CPU containers](../docs/container-guide.md), [model containers](../docs/model-container-guide.md) |
| `python scripts/train_unsloth.py --help` | Thin entry point for the shared Unsloth trainer | [Training](../docs/training-guide.md) |
| `python scripts/predict_unsloth.py --help` | Load a selected Unsloth checkpoint and typed request | [Training](../docs/training-guide.md) |
| `python scripts/create_semantic_profile.py --help` | Record operator-selected encoder assets for local semantic review | [Semantic data](../docs/semantic-data-guide.md) |

## Capture a new source snapshot

`capture_research.py` fetches the URLs in its `SOURCES` table and records retrieval metadata and hashes. Review that table for the intended versions first. Use a new dated metadata file and a new private raw-capture directory; existing output paths are refused. A capture hash identifies bytes, not the accuracy of the source.

```bash
python scripts/capture_research.py --output research/source-snapshot-YYYY-MM-DD.json --capture-dir /private/source-captures/decision-models-YYYY-MM-DD
```

Replace the date and private directory before running. Keep raw pages outside public source control. The script reports sanitized error types; failed captures are not successful source verification. Review and cite new facts before updating guides, and preserve earlier snapshots.

## Extract a versioned benchmark

The extractor supports **JevBench v1.6.1 only**. Supply its captured JSON, the actual review date and a new output directory:

```bash
python scripts/extract_benchmarks.py --input /private/source-captures/jevbench-results.txt --output-dir research/jevbench-v1.6.1-YYYY-MM-DD --review-date YYYY-MM-DD
```

The command writes a selected-row CSV and method metadata with hashes. It refuses another benchmark version or an existing destination. A newer benchmark needs its own method review and extractor; changing a version string does not establish comparable scores. [Benchmark interpretation](../docs/benchmarks.md)
