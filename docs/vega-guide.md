# Vega: integrate and adapt a frozen-feature decision model

Train and serve a Vega domain adapter using frozen language-model features, a learned engine and separate applicability examples. This guide covers the application's paired data workflow and standalone commands. Laya and Unsloth use a [different four-file protocol](training-guide.md).

Review [runtime compatibility and GPU dependency advisories](compatibility-and-upgrades.md) before selecting the pinned Torch environment. Resolve the applicable advisories and validate the replacement runtime on your hardware before a production rollout.

## Freeze a complete execution profile

Use the following pinned vegaml 0.8.0 execution profile:

| Component | Reviewed identity |
|---|---|
| vegaml package | `0.8.0` |
| Code | `3bf76b71437fc58d5ffa21b680e30fb5887a1bb4` |
| Engine/adapter repository | `nandakishorm/vega-08b-public-intents` at `42ac8d4fa9efb61f96e53d80962cf28f6dfe667e` |
| 0.8B backbone | `Qwen/Qwen3.5-0.8B` at `2fc06364715b967f1860aea9cf38778875588b17` |

See [dated source hashes](../research/provenance/vega-sources-2026-10-12.json). Lock Torch/Transformers/tokenizer dependencies too. The engine revision and backbone revision are separate identities. A small engine or adapter file does not contain the complete frozen language model. Inspect code, weights and backbone licenses independently for the selected distribution.

The installed 0.8.0 loader accepts a local checkpoint and forwards an explicit local backbone. Prefer prepared ordinary directories with only reviewed configuration, tokenizer and safetensors assets for an offline serving profile. The source's Hub fetch allowlist excludes downloaded Python and pickle checkpoints. [Pinned Hub loader](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/vegaml/hub.py), [pinned public entrypoint](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/vegaml/__init__.py)

## Select the readout explicitly

`mode="engine"` selects the engine path. Version 0.8.0's default `auto` can select a fitted TTT head for a question; that head does not carry the engine's uncertainty contract. Record the returned readout and avoid mixing its thresholds or benchmark results with engine-mode studies. [Pinned mode implementation](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/vegaml/__init__.py)

Prepare and verify both asset directories before running this inference example. The call loads local assets; it does not download data or train an adapter:

```python
import os
import vegaml

model = vegaml.load(
    os.environ["VEGA_CHECKPOINT_DIR"],
    backbone=os.environ["VEGA_BACKBONE_DIR"],
    mode="engine",
    device="cuda",
)
result = model.decide(
    {"description": "Monthly subscription for an internal issue tracker"},
    {"category": {
        "type": "choice",
        "instructions": "Choose the invoice review category; use other if uncertain.",
        "criteria": {
            "software": "Hosted software subscriptions",
            "travel": "Transport or accommodation",
            "other": "Other or insufficient evidence",
        },
    }},
)
answer = result["answers"]["category"]
print(answer["choice"], answer["probs"], answer["abstain"])
```

Use the [application integration boundary](api-integration-guide.md) before connecting actions. Vega uses `boolean` and `probs`; the platform contract uses `noul` and `probabilities`. Translate explicitly, validate exact question/outcome IDs and preserve upstream abstention. A normalized distribution does not authorize a business action.

## Check the complete prompt

Current prompt construction caps instruction spans at 256 tokens and option spans at the configured 32 tokens. A short-state query prefix has a separate 128-token bound; total-prompt pressure can further shorten evidence. An outer oversized-state refusal does not remove these inner slices. Check the actual tokenizer's complete state, question, option and prefix spans before inference or feature extraction. [Pinned prompt builder](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/vegaml/_common.py)

## Design a domain adapter study

The upstream fine-tuning script is an emotion example. Replace its domain loader; keep applicability negatives and separate model selection from calibration. Cache frozen features using the same memory/prompt path as serving. Train only the intended adapter and gate, verify actual gradients and optimizer updates, and select the adapter/threshold using validation. [Pinned training script](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/notebooks/vega_finetune.py)

Keep the four canonical positive files from the [data guide](data-guide.md), plus four separately reviewed applicability files: `gate_train.jsonl`, `gate_validation.jsonl`, `gate_calibration.jsonl`, `gate_test.jsonl`. An applicability negative says that this domain question should not be applied; it is not a business label such as `other`. Do not create fake uniform decision gold for an inapplicable record. The emerging contract is:

```json
{
  "id": "weather-001",
  "group_id": "weather-source-001",
  "state": "Weather forecast for next weekend",
  "negative_for": ["category"],
  "metadata": {"origin": "real", "reviewed": true, "review_status": "approved"},
  "reason": "Contains no invoice or business expense evidence"
}
```

This row illustrates shape only; it is fictional and must not be used as real gold. The contract checks IDs, families, exact-state conflicts, known relationships and temporal declarations across positive and negative roles. Use the [authenticated application workflow](applicability-guide.md) to collect/review/reserve negatives. Statistical adequacy and representative domain qualification require separate acceptance.

Refit uncertainty on reserved calibration data. Upstream thermal/conformal fitting skips question types with fewer than thirty decisions. This trainer requires at least **sixty calibration source families**, merges exact/group/declared semantic/seed relatives (including bridges through negative records), and assigns whole families to two deterministic, disjoint parts: at least thirty for thermal probability fitting/unbound selection and thirty for conformal quantiles. It fails insufficient studies rather than retaining another domain's calibration. These minima are implementation constraints, not statistical adequacy. Unknown semantic relationships remain a data-review obligation. Evaluate positive coverage and negative false-open rates separately. [Pinned fitting and routing code](https://github.com/NandhaKishorM/vegaml/blob/3bf76b71437fc58d5ffa21b680e30fb5887a1bb4/vegaml/_common.py)

Export and reload the exact backbone, engine, adapter, gate, calibration and question schema. Compare probabilities, selected adapter, prediction sets and abstention before using locked test records. Conditional coverage after an applicability filter needs its own evaluation; generic split-conformal claims do not automatically prove coverage for the selected production traffic.

The upstream quantile function uses the argmax of reviewed target distributions and pools decisions by question type. Soft gold is not an observed categorical outcome; multiple questions from one source family may be dependent. Independent partitions prevent fitting reuse, but these label/dependence assumptions and exchangeability still need domain-specific validation before any formal coverage claim.

## Train and release in the local application

Configure the [separate worker and serving interpreter](setup.md#configure-model-training-and-serving) with the [Vega runtime profile](../requirements/vega-runtime.txt). Keep both pinned engine/backbone snapshots in the operator-selected cache when choosing offline training. The lightweight API/browser does not import the model weights.

1. Create and confirm the domain questions, import positive evidence, review its labels and reserve the four positive roles.
2. Collect real inapplicable evidence in **Applicability**, review each record and reserve it against that exact positive snapshot. Every question needs negatives in every role. Known relationships joining frozen roles are refused.
3. Before the first training submission, the owner enables Vega acceptance criteria alongside accuracy, F1, ECE, real-test and outcome-count gates. Specify the minimum positive accepted fraction, maximum negative false-open rate and minimum negative test rows **per question**. Do not adopt another domain's thresholds without measuring its error costs.
4. Select **Vega** and the matching applicability snapshot. Set bounded epochs, batch size, adapter rank, learning rate and gate loss weight; a zero maximum-step setting uses the selected epochs. Training requires sixty independent positive calibration families after merging known relatives, split into disjoint probability-fitting and conformal parts. This minimum does not establish sample adequacy.
5. Submit the job and inspect its durable status/private operator log. The server freezes both reservations, source/schema identities, eight serialized input hashes, settings and the owner policy. The worker rechecks these before model work and holds the shared GPU lease throughout its supervised child. It registers the export only after actual reload/uncertainty qualification.
6. Inspect ordinary held-out metrics and the report's `applicability.test` for every question. Positive accepted fraction includes all model abstention causes; negative false-open rate measures the applicability gate. Both have explicit denominators and serve different purposes. Release requires their frozen limits and the ordinary quality gates. Owner activation preloads the checked resident model. Rollback uses the same eligibility rules and preserves release history.
7. Call the [authenticated decisions endpoint](api-integration-guide.md). Preserve `model_abstained` even with a zero confidence threshold. Queued/running Vega training unloads the shared resident runtime; callers receive the existing 503/retry behavior until training finishes or is cancelled.

The API accepts the same flow. These identifiers are illustrative; obtain them from successful scoped preparation jobs:

```json
{
  "kind": "train",
  "config": {
    "backend": "vega",
    "snapshot": "positive-snapshot-id",
    "gate_snapshot": "paired-applicability-snapshot-id",
    "epochs": 2,
    "batch_size": 8,
    "rank": 8,
    "learning_rate": 0.0003,
    "gate_weight": 1.0,
    "max_steps": 0,
    "offline": true
  },
  "request_key": "domain-vega-study-001"
}
```

Owner `PUT /v1/projects/{project}/release-policy` accepts an optional `vega` object with exactly `min_positive_accepted_fraction`, `max_negative_false_open_rate` and `min_negative_test_rows`. Rates must be finite in `[0,1]`; the count is an integer from 10 to 20,000. It is mandatory for Vega and freezes at first training. Existing projects whose frozen policy omitted it need an owner-confirmed successor project. The server supplies the separately pinned profile and internal contract; clients cannot forge its provenance fields. Model/backbone upgrades need separate qualification.

Worker inputs use canonical UTF-8/LF bytes on Windows and Linux so frozen hashes match the runner's eight files. The internal `--local-study-request` argument identifies a worker-owned request; the parent already owns the GPU lease. Normal standalone CLI execution acquires its own lease. This internal contract is distinct from the four-file remote study protocol. Reviewed flags in a portable file alone cannot replace authenticated application review/reservation replay.

Snapshot references must be the application's 32-character lowercase identifiers. Malformed lists, objects, numbers, missing fields and invalid strings are rejected before database access. The worker also rechecks the negative test-count floor against the policy atomically frozen by the store. This covers an owner changing criteria between initial request validation and submission; an insufficient study stops before artifact creation or model loading.

The evaluation view includes **Applicability and model abstention**. Compare each question's applicable/inapplicable test counts, positive gate open rate, positive accepted fraction and negative gate false-open rate with its frozen limits. A gate can open on every positive example while uncertainty makes the model abstain on all of them. The applicability status covers those additional criteria; ordinary accuracy, F1, calibration and class-coverage gates still apply. An unavailable or mismatched policy displays unavailable eligibility rather than a pass.

## Run the standalone trainer

Prepare all eight files from reviewed domain data and install the [Vega runtime profile](../requirements/vega-runtime.txt) in a separate model environment. Match your platform using [dependency profiles](../requirements/README.md). The trainer downloads the pinned engine/config and backbone/tokenizer/safetensors assets; your records remain local.

```bash
python -m decision_workbench.training.vega_runner \
  --data domain-study --out domain-export \
  --epochs 2 --batch-size 8 --rank 8 --device cuda
```

Use `--offline` after preparing both pinned snapshots in the cache selected by `DMT_MODEL_CACHE`. `DMT_HOME` selects the platform workspace and shared GPU lease; its default is `data/workspace`. Choose an empty output directory. Both gate classes participate in training; validation selects the adapter and applicability threshold. The backbone and original engine are frozen. No optimizer resume or multi-GPU mode is provided.

The export contains `merged/engine.safetensors`, the complete `merged/backbone`, one domain adapter, calibration/policy/schema/runtime metadata, `report.json` and the complete hash inventory in `manifest.json`. The report records source/input hashes, disjoint calibration indexes, gate gradients and parameter changes, selection history, applicability errors, reload comparison and held-out evaluation. Portable `reviewed` fields alone do not establish authenticated annotation.

For standalone inference, verify the manifest against a separately trusted SHA-256 before loading. Preserve `model_abstained` and send it to the approved fallback:

```python
import json
from pathlib import Path
from decision_workbench.training.artifacts import verify_manifest
from decision_workbench.training.engines import load_engine

bundle = Path("domain-export")
questions = json.loads((bundle / "merged/dmt_vega.json").read_text())["questions"]
trusted_sha = Path("approved-manifest.sha256").read_text().strip()
verify_manifest(bundle, trusted_sha, "vega", questions)
model = load_engine("vega", bundle / "merged")
result = model.predict_details({"description": "Your complete domain evidence"}, questions)
print(result)
```

Serving rejects a changed question schema or qualified runtime. A closed domain gate, unbound evidence or a missing/non-singleton conformal set preserves model abstention. The base route is not an automatically qualified fallback. Runtime package changes require fresh training/export qualification.

The [five remote providers](remote-training-guide.md) can host custom training containers. The application implements a distinct [eight-file operator study and independent Vega admission](remote-admission-guide.md#vega-eight-file-studies-and-independent-admission). It binds paired reservations and measures both applicability and uncertainty in the admission environment. Managed cloud launches explicitly refuse Vega until their runner/image support is qualified.
