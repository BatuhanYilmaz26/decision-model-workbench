# Prepare domain data for Perplexity's open Decider

[Hosted API](perplexity.md) · [Model directory](README.md) · [Project overview](../../README.md)

Convert reviewed domain examples into Perplexity's native training/evaluation
format on a CPU, without downloading model weights. The output preserves the
four data roles, source lineage and file checksums.

This profile targets `perplexity-ai/pplx-decider-v1.1-27b`, revision
`14c9d90137962d300f3de95633f7b6535e80c5ce`. It is separate from hosted v1.2.
The workbench provides data conversion here; native app-managed training,
release, serving and cloud admission are not yet available for this checkpoint.
Use the [hosted workflow](perplexity.md) for app-managed Perplexity decisions.

## Prepare compatible reviewed records

Use text/JSON evidence with **Choice** or **Noul** questions. Describe the
allowed outcomes and keep their order fixed. The publisher's inference formatter
also represents Score questions, but its supplied training/evaluation path
accepts Choice and Noul only. This exporter refuses Score, media and unsupported
fields instead of silently changing them. See the pinned
[formatter](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/model.py),
[trainer](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/train.py)
and [evaluator](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/evaluate.py).

1. Follow the [app walkthrough](../app-guide.md#first-project-walkthrough) to
   describe your domain, define outcomes and enter or upload examples. The app
   accepts CSV, XLSX, TSV, JSON and JSONL. Review and approve the records.
2. Reserve independent real validation, calibration and test data. Optional
   [synthetic generation](../app-guide.md#generate-and-review-training-candidates)
   uses a separate teacher. Review the candidates and extend only training,
   preserving the original holdouts.
3. In **Prepare and generate**, select the **Reserved data snapshot** and expand
   **Download reserved study data**. Approve the download, prepare it, then save
   **Download reserved canonical study**. Extract the ZIP privately with all four
   JSONL files together.

Alternatively, supply reviewed canonical files using the [data contract](../data-guide.md).
The converter checks declared review, origin, schema and family separation.
You remain responsible for those declarations and representative domain coverage;
setting metadata does not establish human review or independence.

## Install the CPU tools and convert

From the repository root, use a separate Python 3.12 environment. In Bash:

```bash
python3.12 -m venv .venv-perplexity-data
source .venv-perplexity-data/bin/activate
python -m pip install -r requirements/perplexity-data.txt
python -m pip check
```

On Windows, create it with `py -3.12 -m venv .venv-perplexity-data` and activate
`.venv-perplexity-data\Scripts\Activate.ps1`; the pip commands stay the same.
See [setup](../setup.md) for activation troubleshooting. The
[profile](../../requirements/perplexity-data.txt) installs Tokenizers and Jinja2,
without Torch, Transformers or GPU libraries.

Place `train.jsonl`, `validation.jsonl`, `calibration.jsonl` and `test.jsonl` at
the top of `data/perplexity-canonical`. Choose a new output directory:

```bash
python -m decision_workbench.training.perplexity_data \
  --data data/perplexity-canonical \
  --out data/perplexity-native \
  --assets data/perplexity-data-assets \
  --download \
  --max-length 2048
```

For PowerShell, put this conversion command on one line or replace each Bash
continuation with PowerShell's backtick. `--download` fetches seven pinned
tokenizer, template, configuration, source and license assets, about 20 MB total.
Their SHA-256 values are checked before use. It downloads no weights and does
not execute the downloaded model source. Omit the flag for an existing offline
asset directory.

The converter checks exact prompt token counts and refuses excessive inputs
without truncation. Choose 64–8,192 tokens. A larger budget increases eventual
model memory/compute needs. Limits are 20,000 records and 64 MiB per input role,
100,000 decisions overall, 256 MiB generated data, and 64 KB serialized evidence
plus question. Reserved control-token markers are refused.

## Read the output

| Output | Purpose |
|---|---|
| `canonical/*.jsonl` | Byte-preserved original records in their four roles |
| `native/train.jsonl` | Native targets, one question per row |
| `native/validation.jsonl` | Development evaluation and selection |
| `native/calibration.jsonl` | Separate temperature fitting |
| `native/test.jsonl` | Final evaluation after freezing the model and policy |
| `lineage/*.jsonl` | Original IDs, family, metadata, gold, option order and prompt/native hashes |
| `manifest.json` | Pinned model/assets, counts, token maxima and file checksums |

A fictional native Choice row looks like this:

```json
{
  "id": "fictional-decision-1",
  "suite": "next_step",
  "family": "fictional-record-1",
  "state": {"required_fields_present": false},
  "question": {
    "type": "choice",
    "instructions": "Select the next workflow step from the available evidence.",
    "criteria": {
      "continue": "All required fields are available",
      "request_details": "Required fields are missing"
    }
  },
  "label": "request_details",
  "target": [0.0, 1.0],
  "source": {"dataset": "fictional-format-example", "origin": "synthetic"}
}
```

This illustrates format, not evaluation gold. The converter derives native IDs
from each original record and question and retains source families. Noul labels
are JSON booleans, with targets ordered `[false, true]`; string `"true"` is not
interchangeable with boolean `true` in the official evaluator.

Reviewed soft probability targets are preserved for **training**. Their label
is an explicitly marked diagnostic argmax and does not replace the target vector.
Heldouts require one-hot reviewed gold because upstream evaluation and
temperature fitting consume discrete labels. Soft heldout targets are refused.
Existing output directories are never overwritten, and failures do not publish
partial bundles.

## Continue with the publisher's GPU runtime

This is a data handoff, not a training job. The checkpoint uses a Qwen3.8-27B
backbone, a separate BF16 decision readout, noncausal full-attention layers and
last-position pooling. A generic causal chat recipe does not preserve that
setup. Native prediction applies its saved temperature once. See the
[checkpoint card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/README.md)
and [decision configuration](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/decision_config.json).

For an environment you provision yourself:

1. Obtain the complete pinned checkpoint, retaining shards, readout, processor,
   configuration, source and notices. Follow its [dependency metadata](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/pyproject.toml)
   in a separate Python 3.12+ CUDA environment. Inference weights alone require
   roughly 49 GiB plus working memory. Full-weight training additionally needs
   gradients, optimizer state, activations and checkpoint space.
2. Inspect `autojev-train --help` after installing the publisher source package.
   Use `--initial-checkpoint` for domain continuation, new `--run` and `--output`
   directories outside that artifact, and a new `AUTOJEV_EVENTS` log path.
   Continuation starts a fresh optimizer; it is different from `--resume`.
3. Map the native train file to `--train`, validation to `--development`, and
   calibration to `--temperature`. Preserve
   `--attention-mode noncausal_full_attention` and `--pooling last`. The supplied
   trainer performs full-weight training, not LoRA.
4. Supply complete fixed development predictions via `--reference`. The trainer
   uses these for calibration/selection gates and calls them `jev` in reports.
   An evaluation of the original local checkpoint can supply a local comparison,
   but retain its actual identity; it is not a Jev result. Paid API calls are
   unnecessary for that comparison.
5. Keep the test file out of `--public` and other periodic training checks.
   Freeze a selected model and fitted temperature, then use
   `autojev-evaluate --local --checkpoint ... --data ... --output ...` on test.
   Inspect selection results and your own domain gates before deployment.

The data/prompt contracts can be checked without weights. That does not establish
GPU-runtime compatibility, training quality or deployment readiness.

## Rent hardware if local memory is insufficient

The [AWS](../remote-training/aws.md), [Azure](../remote-training/azure.md),
[GCP](../remote-training/gcp.md), [Modal](../remote-training/modal.md) and
[RunPod](../remote-training/runpod.md) guides explain GPU job/storage primitives.
For native Perplexity, provision the publisher runtime as your own custom job and
transfer only approved data. Managed trainers, result admission and serving in
this workbench do not yet support this profile. Another model's managed image
does not add that support. Retain model/data/runtime identities and stop paid
resources explicitly.
