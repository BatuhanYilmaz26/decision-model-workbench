# Prepare, train and use Perplexity's open Decider

[Hosted API](perplexity.md) · [Model directory](README.md) · [Project overview](../../README.md)

Convert reviewed domain examples into Perplexity's native training/evaluation
format on a CPU, without downloading model weights. The output preserves the
four data roles, source lineage and file checksums.

This profile targets `perplexity-ai/pplx-decider-v1.1-27b`, revision
`14c9d90137962d300f3de95633f7b6535e80c5ce`. It is separate from hosted v1.2.
The CPU exporter connects to an **experimental Linux/CUDA native profile** for
application or standalone training, calibrated releases and prediction. Its
[five-provider cloud workflow](../remote-training/perplexity.md) uses a separate
experimental image, H200 resource declarations and independent admission.
Use the [hosted workflow](perplexity.md) for provider-hosted Perplexity decisions.

## Prepare compatible reviewed records

Use text/JSON evidence with **Choice** or **Noul** questions. Describe the
allowed outcomes and keep their order fixed. The publisher's inference formatter
also represents Score questions, but its supplied training/evaluation path
accepts Choice and Noul only. This exporter refuses Score, media and unsupported
fields instead of silently changing them. See the pinned
[formatter](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/model.py),
[trainer](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/train.py)
and [evaluator](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/evaluate.py).

1. Follow the [app walkthrough](../app-guide.md#screen-manual--first-project-walkthrough) to
   describe your domain, define outcomes and enter or upload examples. The app
   accepts CSV, XLSX, TSV, JSON and JSONL. Review and approve the records.
2. Reserve independent real validation, calibration and test data. Optional
   [synthetic generation](../app-guide.md#screen-manual--generate-and-review-training-candidates)
   uses a separate teacher. Review the candidates and extend only training,
   preserving the original holdouts.
3. In **Prepare and generate**, select the **Reserved data snapshot** and expand
   **Download reserved study data**. Approve the download, prepare it, then save
   **Download reserved canonical study**. Extract the ZIP privately with all four
   JSONL files together.

Alternatively, supply reviewed canonical files using the [data contract](../data-guide.md#data-contract).
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
See [setup](../setup.md#configuration) for activation troubleshooting. The
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
| `native/validation.jsonl` | Development evaluation and reload comparison |
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

## Train with the native Linux launcher

The checkpoint uses a Qwen3.8-27B
backbone, a separate BF16 decision readout, noncausal full-attention layers and
last-position pooling. A generic causal chat recipe does not preserve that
setup. Native prediction applies its saved temperature once. See the
[checkpoint card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/README.md)
and [decision configuration](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/decision_config.json).

### Check resources before installing the model runtime

The native launcher invokes the publisher's **full-weight** trainer with a fresh
CPU-offloaded optimizer. It is not LoRA or readout-only adaptation. Its logger
imports Linux `fcntl`; run on Linux, including a suitably provisioned WSL2 VM or
Linux GPU host. Use Python 3.12 and Git, and run from this cloned repository.
For WSL2, keep the native run and checkpoint directories on its Linux filesystem
so the publisher's file locks and selected-checkpoint symlink behave consistently.

The initial checkpoint download is about **52.2 GB**. The launcher checks for
roughly 107 GiB of free GPU memory, 428 GiB of available host RAM for FP32
optimizer states, and 150 GiB of free run storage in addition to the existing
checkpoint. These are conservative startup floors, not a guarantee that every
batch/token budget fits. Provision extra memory and disk headroom; 512 GiB host
RAM and a GPU with more than 107 GiB free are a starting point for sizing, not a
measured training configuration. Replicated multi-GPU training does not pool
device memory, and this launcher accepts one GPU process only. Use a smaller
model or the hosted API when these resources are impractical. The memory needs
follow the publisher's [FP32 CPU optimizer](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/optim.py)
and [native trainer](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/train.py).

Create a separate environment on that host:

```bash
python3.12 -m venv .venv-perplexity-native
source .venv-perplexity-native/bin/activate
python -m pip install -r requirements/perplexity-native.txt
python -m pip check
```

The [native profile](../../requirements/perplexity-native.txt) follows the
publisher's Torch 2.14 / Transformers 5.17 pins and adds the CPU data tools.
It is a direct dependency profile, not a complete transitive lock or a qualified
GPU image. Use a CUDA-compatible PyTorch distribution and driver; consult the
[official installation selector](https://pytorch.org/get-started/locally/).
The native GPU environment and training throughput still need hardware validation.

### Obtain the pinned checkpoint explicitly

Skip this step if you already have the complete pinned checkpoint. This command
downloads large model weights into your chosen cache, after a free-space check:

```bash
python -m decision_workbench.training.perplexity_checkpoint \
  --cache data/perplexity-model-cache --download-weights
```

Retain the printed absolute `checkpoint` path. The command checks the source,
processor and all twelve weight-file identities against the pinned publisher
manifest/Hub metadata. It uses your configured Hugging Face access if needed;
keep access tokens in the environment or Hugging Face's credential store.
No weight download happens from the CPU data-export command or native trainer.

To fetch only source/config/tokenizer files, use
`python -m decision_workbench.training.perplexity_checkpoint --assets data/perplexity-publisher --download-assets`.
That roughly 21 MB asset directory is **not** a complete model checkpoint.
Downloaded publisher code is hash-checked before execution; the native loader
isolates its package imports and ignores existing Python bytecode caches.

### Run full-weight domain continuation

Use the original four reviewed canonical files from the earlier download.
Replace the checkpoint path below with the value printed by the previous command:

```bash
python -m decision_workbench.training.perplexity_runner \
  --data data/perplexity-canonical \
  --checkpoint /absolute/path/to/pinned/checkpoint \
  --out data/perplexity-domain-run \
  --epochs 1 --batch-size 1 --grad-accum 4 \
  --max-length 2048 --learning-rate 0.000002
```

The launcher validates and converts all four roles before loading a model,
evaluates the original local checkpoint on validation, then uses those saved
predictions as the trainer's fixed comparison. It makes no paid API calls.
Upstream logs call the comparison `jev`; the exported report identifies it as
the **original local Perplexity checkpoint**, not a Jev measurement.

It retains noncausal full-attention behavior, the original recurrent layers,
last pooling and the native BF16 readout. Training uses a fixed final-epoch
checkpoint, not development-selected weights or an interrupted-run resume.
Validation curves and separate calibration data remain available, while test is
never passed into periodic training evaluation. `--grad-accum` sets effective
batch size to batch size times accumulation; a larger `--max-length` increases
memory use. `--cpu-threads` and `--eval-every` control host threads and evaluation
frequency. Do not run competing model jobs on the same hardware.

Watch the printed phase and `baseline.log`, `training.log`, `events.jsonl` and
`study/summary.json` inside the new run directory. Existing runs are refused.
On failure or interruption, retain the logs for diagnosis and choose a new run;
the launcher does not offer optimizer-state resume. A failed run without a
complete export manifest is not a usable domain export.

### Use the exported model for your domain

Successful completion produces `export/` with the checkpoint, pinned publisher
source/licenses, domain schema, metrics and a file manifest. The launcher checks
that both backbone and readout tensor payloads changed, reloads the export and
compares validation probabilities with the publisher's saved results. The saved
global temperature is applied once. Test metrics are computed after the final
weights and calibration are fixed. These checks do not establish representative
domain quality; inspect the per-question results and action costs yourself.

Keep the printed `manifest_sha256` separately and retain the **whole** export
directory, not only its weight files. Put new evidence in a JSON request such as:

```json
{"state": {"required_fields_present": false}}
```

The export supplies its frozen questions. Run native prediction with the saved
manifest hash and your request file:

```bash
python -m decision_workbench.training.perplexity_predict \
  --export data/perplexity-domain-run/export \
  --manifest-sha YOUR_SAVED_MANIFEST_SHA256 \
  --request data/new-evidence.json --device cuda --threshold 0.8
```

Prediction is offline and returns typed answers, complete probabilities and
abstention flags. Route abstained results to your fallback; the threshold does
not retrain the model. CPU inference is available on a Linux host with roughly
112 GiB of available RAM plus workload headroom, because the publisher loads CPU
weights in FP32. GPU inference has a lower startup floor than training, roughly
58 GiB free device memory and 58 GiB available host RAM for the publisher's CPU
staging before moving the model to CUDA. These guards are not latency benchmarks.

The native workflow has source, integrity and fictional orchestration checks. Its
complete Torch 2.14 training and inference paths remain experimental until
exercised on compatible hardware. Managed cloud admission remains separate;
the hosted app path is available without native model hardware.

## Train through the application

Run the workspace API/worker on the Linux host with the required resources.
Your browser can be on another computer. A Windows-hosted worker cannot launch
a Linux model interpreter simply by entering a WSL path.

Install the separate native environment and obtain the pinned checkpoint as
described above. In the **CPU application environment**, set the model paths
before starting the workspace:

```bash
export DMT_PERPLEXITY_PYTHON="$PWD/.venv-perplexity-native/bin/python"
export DMT_PERPLEXITY_CHECKPOINT="/absolute/path/to/pinned/checkpoint"
export DMT_PERPLEXITY_TRAINING_TIMEOUT_SECONDS=86400
export DMT_MODEL_LOAD_TIMEOUT_SECONDS=900
export DMT_UI_MODEL_TIMEOUT_SECONDS=1200
domain-platform run
```

Replace the checkpoint path with the actual directory printed by the explicit
download command. The interpreter and checkpoint are operator configuration;
HTTP job requests cannot choose executable or checkpoint paths. Direct personal
Streamlit startup also offers optional Perplexity path fields before starting
the workspace. Neither path entry installs packages or downloads weights.
For a shared workspace, configure the API/worker environment and connect with
your normal workspace credential. A Perplexity API key is unnecessary here.

1. Define a Choice/Noul project, enter or upload examples, and complete review.
   Optional reviewed synthetic data can extend training only. Use adjudicated
   discrete gold for all heldouts; do not round soft targets into evaluation gold.
2. Reserve the four roles. The native trainer needs at least eight real
   calibration records; the owner's release policy may require much more data.
   The app defaults to 100 real test records per question and additional coverage
   checks. Collect adequate data instead of lowering gates merely to release.
3. An owner saves acceptance criteria before the first study. In **Training
   jobs**, choose **Perplexity open v1.1 (native CUDA, experimental)** and
   **This computer**. Here, the computer is the workspace host, not the browser.
4. Use **Check available hardware and model packages** to inspect the configured
   training/serving interpreter. The app checks Linux, versions, available RAM
   and free GPU memory. Inspection is a snapshot, not a model integrity or
   hardware qualification certificate; the child checks resources and all pinned
   checkpoint identities again. Restart services after configuration changes.
5. Set epochs, micro-batch size, gradient accumulation, complete input token
   budget and learning rate. The advanced controls set CPU threads and the
   evaluation interval. Settings, source, schema and all four data hashes are
   frozen in the queued study. Unsupported Score questions disable this route.
6. Choose **Queue training**. The worker owns the GPU lease, records progress
   and retains private logs. It runs the publisher's full-weight stage and
   compares its saved validation predictions with the exported runtime. No
   provider API request or model download occurs. Refresh to see completion.
7. In **Models and releases**, inspect metrics, the local-reference identity,
   fitted global temperature and limitations. An owner can activate an eligible
   version and later reactivate an earlier eligible version to roll back.
8. In **Decisions and audit**, choose **Text** or **JSON** evidence format. JSON
   preserves an object/array/string as structured input rather than sending its
   serialization as plain text. Use your domain's original evidence structure,
   select an abstention threshold and **Make decision**. Route abstentions to
   your fallback. Inference-only users have the same evidence-format choice.

The native job deadline defaults to 24 hours; operators can set 600–604,800
seconds using `DMT_PERPLEXITY_TRAINING_TIMEOUT_SECONDS`. A timeout or cancellation
stops the owned model child and produces no releasable partial version. This
profile does not resume optimizer state; retain logs and submit a fresh study
after resolving a failure. Closing the browser does not cancel the job.

The load/UI timeouts above allow large model verification and cold loading.
Choose finite values appropriate to your host and gateway. Application serving
uses CUDA to match training and reload; standalone CPU inference remains an explicit
CLI choice. Standalone exports are not automatically registered app versions.
Native managed cloud studies and external-export admission are unavailable;
use the custom-job guidance below when operating rented hardware yourself.

## Use the publisher commands directly

If you prefer to operate its trainer yourself:

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

The trainer also requires Git provenance and its `source/uv.lock`. Retain the
complete pinned source tree rather than copying `train.py` alone. Its event
logger requires Linux. Source/data checks do not establish GPU compatibility,
domain quality or deployment readiness.

## Rent hardware if local memory is insufficient

The [AWS](../remote-training/aws.md#direct-submission), [Azure](../remote-training/azure.md#direct-submission),
[GCP](../remote-training/gcp.md#direct-submission), [Modal](../remote-training/modal.md#direct-submission) and
[RunPod](../remote-training/runpod.md#direct-submission) guides explain GPU job/storage primitives.
For native Perplexity, follow the [five-provider native cloud workflow](../remote-training/perplexity.md)
for the compatible image, H200 resource declarations, study submission and
independent returned-model admission. Transfer only approved data. An image for
another model does not supply this runtime. Retain model/data/runtime identities
and stop paid resources explicitly.
