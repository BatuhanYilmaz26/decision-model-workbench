# Train and use Liquid d1-3B

[Model directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Prepare reviewed domain examples on a CPU, then use the separate local TRL
or FastVisionModel/Unsloth runner to train, calibrate and export a domain model. The CPU exporter produces
the native data formats used by Liquid's official fine-tuning notebooks. It supports text
and JSON evidence with choice, yes/no and ordered-score questions. It downloads
a pinned tokenizer, prompt builder and license, about 18 MB in total. The data
export command does not download weights, start a paid job or train a model.

Use the localhost application for reviewed project data, durable training jobs,
evaluation, versioned releases and predictions. Standalone exporter/trainer
commands are also available below. The [official Unsloth notebook](https://github.com/Liquid4All/cookbook/blob/744c5b628a27adb4f219ff4a1d11e3d70259b369/finetuning/notebooks/sft_for_decision_models_with_unsloth.ipynb)
and [official TRL notebook](https://github.com/Liquid4All/cookbook/blob/744c5b628a27adb4f219ff4a1d11e3d70259b369/finetuning/notebooks/sft_for_decision_models_with_trl.ipynb)
provide the upstream training routes. Treat those as separate GPU environments;
do not add their packages to an existing Laya or Unsloth installation.

## Train and release through the localhost application

1. Follow the [root application setup](../../README.md#run-on-your-computer) and
   install the separate [Liquid model environment](#train-with-the-local-trl-runner).
   Keep the application's CPU environment and existing model environments intact.
2. Configure **DMT_LIQUID_PYTHON** for TRL, or **DMT_LIQUID_UNSLOTH_PYTHON**
   for the [separate Unsloth profile](#train-with-fastvisionmodel-and-unsloth), in the API/worker environment, then start
   `domain-platform run` from the application's environment. Personal mode opens
   without a pasted workspace credential. Alternatively, set **Liquid d1-3B
   Python executable** in Streamlit's optional local settings before starting
   its workspace. Restart the app to change those settings.
3. Describe your policy and data requirements, enter or upload examples, review
   labels, and reserve independent training/validation/calibration/test roles.
   Synthetic candidates use the existing [teacher/review workflow](../synthetic-generation-guide.md).
   Liquid SFT refuses soft training/validation targets and needs at least eight
   real calibration records. Set your owner acceptance criteria before training.
4. Under training, select **Liquid d1-3B (TRL)** or **Liquid d1-3B (Unsloth)**
   to match the installed profile, then **This computer**. Check the
   hardware/package panel. Choose the compute device and training budget. Permit
   the 6.25 GB public checkpoint download only if its pinned assets are not cached.
   Training records stay local; the download fetches weights and reviewed model code.
5. Select **Queue training**. Monitor the persistent job and its evaluation
   report. A run is registered only after parameter-update, frozen-study and
   native export/reload checks pass. Cancellation retains the job history and
   stops its supervised model process.
6. Inspect held-out results, select the eligible model version, enter a release
   reason and select **Activate selected version**. The server checks the frozen
   owner criteria and loads the verified native runtime before activation.
7. Enter evidence under the decision workflow and select **Make decision**.
   Use the [authenticated decision API](../product-workflow.md#6-release-and-integrate)
   from an application, or follow [standalone inference](#use-the-fine-tuned-export).
   An earlier eligible release can be activated to roll back.

Windows PowerShell, after creating `.venv-liquid-trl` as described below:

```powershell
$env:DMT_LIQUID_PYTHON = (Resolve-Path .\.venv-liquid-trl\Scripts\python.exe).Path
domain-platform run
```

Linux/macOS shell, after preparing its compatible environment:

```bash
export DMT_LIQUID_PYTHON="$PWD/.venv-liquid-trl/bin/python"
domain-platform run
```

For individually operated API and worker services, configure the same variable in
both processes. Each Liquid variable selects that profile's interpreter for training **and** serving.
`DMT_WORKER_PYTHON` / `DMT_MODEL_PYTHON` keep their existing behavior for other
backends. Install project source from the same checkout in each environment;
requests are bound to that source identity. Browser/API requests cannot select
an executable or replace the frozen contract.

`DMT_LIQUID_CACHE` optionally selects a private checkpoint cache. Otherwise it
uses `model-cache/liquid-hub` beside the workspace, with separate native-module,
CUDA and Triton caches. New artifacts contain only the exact approved model code;
modified or additional Python code is refused before release/recovery loads it.
For rented GPUs, use the [Liquid remote-training workflow](../remote-training/liquid.md).
It prepares frozen studies for all five providers and independently admits returned
exports. Its container recipe is experimental and needs qualification on your chosen GPU.

## Install the CPU data tool

Clone the repository using the [root quick start](../../README.md#run-on-your-computer),
then run these commands from its root. Python 3.12 is suitable for this separate
environment; no GPU, Torch or Transformers installation is needed.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv-liquid-data
.\.venv-liquid-data\Scripts\python.exe -m pip install -r requirements/liquid-data.txt
.\.venv-liquid-data\Scripts\python.exe -m pip check
```

Linux or macOS:

```bash
python3.12 -m venv .venv-liquid-data
.venv-liquid-data/bin/python -m pip install -r requirements/liquid-data.txt
.venv-liquid-data/bin/python -m pip check
```

The optional profile installs this package and Tokenizers 0.23.3. CPU export
does not establish support for model training on macOS or a particular GPU.

## Describe the task and provide examples

Start with the [fictional example specification and CSV](../../examples/liquid/README.md).
Describe your domain, allowed outcomes, edge cases and desired training coverage
in natural language. Define the questions, enter examples or upload CSV, XLSX,
TSV, JSON or JSONL using the [application's import workflow](../import-guide.md).
Review labels and source families before reserving the data.

For synthetic coverage, use a compatible local or hosted **generative teacher**
through [synthetic generation](../synthetic-generation-guide.md). Generate from
training-only seeds, review the candidates and keep their seed ancestry. A
decision model's probability response is not a generated training example.
Perform native conversion after review; do not ask the teacher to guess Liquid's
option-token aliases.

Prepare four canonical files named `train.jsonl`, `validation.jsonl`,
`calibration.jsonl` and `test.jsonl` in a private directory, such as
`data/my-domain`. Follow [four-way reservations](../training-guide.md#reserve-four-splits).
For standalone reviewed records, the shared preparation command is:

```bash
domain-decisions prepare --input data/reviewed.jsonl --out data/my-domain
```

Run this command in your installed application environment. Only use records you
have actually reviewed; a label in a file is not a quality check. The application
resets imported review flags and requires authenticated review. Calibration and
test examples must come from independent operational data, and may not become
generation seeds. Fictional templates illustrate the format, not domain quality.

The SFT exporter requires one-hot targets in **training and validation**. It
refuses soft probability targets instead of taking their largest value. Soft
calibration/test gold is retained for later probability evaluation. Use a
different compatible training objective if your training labels are distributions.

## Export native data

Windows PowerShell:

```powershell
.\.venv-liquid-data\Scripts\python.exe -m decision_workbench.training.liquid_data --data data/my-domain --out runs/my-domain-liquid-data --max-length 2048 --download
```

Linux or macOS:

```bash
.venv-liquid-data/bin/python -m decision_workbench.training.liquid_data --data data/my-domain --out runs/my-domain-liquid-data --max-length 2048 --download
```

The first run's `--download` retrieves only four public assets from
[`LiquidAI/d1-3B` at revision `051bcc4`](https://huggingface.co/LiquidAI/d1-3B/tree/051bcc464b01b9f92942b364d9586b0ef5912432).
Their SHA-256 identities are checked before parsing the tokenizer or loading the
reviewed prompt code. Later runs can omit `--download` to stay offline. Use
`--cache` to select another private cache directory. No domain records are sent
to the model publisher by this command.

Choose a new output directory for each export. A successful command prints
decision counts for all four roles and `"trained_model": false`.

| Output | Purpose |
|---|---|
| `trl/train.jsonl`, `trl/validation.jsonl` | Native `prompt` and `completion` records for TRL |
| `unsloth/train.jsonl`, `unsloth/validation.jsonl` | Native `text` records for response-only SFT |
| `canonical/*.jsonl` | Byte-preserved original records for all four roles, including gold and provenance |
| `evaluation/calibration.jsonl`, `evaluation/test.jsonl` | Single-question inference requests; not SFT datasets |
| `lineage/*.jsonl` | Source/question/group identity, role, review metadata, gold, alias mapping and token counts |
| `manifest.json` | Input/output hashes, model/notebook revisions, asset hashes, counts and token budget |

One canonical record with three questions becomes three native decisions.
Choice labels are converted to the aliases assigned by the actual tokenizer.
Yes/no questions retain their native yes/no readout; scores retain rubric digits.
Each completion is one decision token followed by the end-of-turn token.

The tool checks the complete prompt and completion, including question and option
text. It refuses truncation, reserved control tokens, unsupported top-level media
fields, known split/seed leakage and unreviewed records. Limits are 20,000 records
per input, 64 MiB per input, 100,000 total decisions and a chosen token budget of
16–32,768. A refused export does not publish a completed bundle. If disk writes
fail, remove the incomplete output after inspecting it and retry with a new name;
only a bundle with its final manifest is complete.

## Train with the local TRL runner

Use a **new Python 3.12 environment** for this model. The selected profile follows
Liquid's TRL recipe with Transformers 5.19.0, TRL 1.15.0 and PEFT 0.21.2. Its
Torch/torchvision versions match each other. CUDA SFT also needs Triton; the
profile selects the matching Windows or Linux distribution. Do not install this profile over
the application's environment or an existing Laya/Unsloth/Vega environment.

Windows PowerShell with a compatible NVIDIA CUDA installation:

```powershell
py -3.12 -m venv .venv-liquid-trl
.\.venv-liquid-trl\Scripts\python.exe -m pip install torch==2.10.0+cu126 torchvision==0.25.0+cu126 --index-url https://download.pytorch.org/whl/cu126
.\.venv-liquid-trl\Scripts\python.exe -m pip install -r requirements/liquid-trl.txt
.\.venv-liquid-trl\Scripts\python.exe -m pip check
.\.venv-liquid-trl\Scripts\python.exe -m decision_workbench.training.liquid_runner --data data/my-domain --out runs/my-domain-liquid --device cuda --rank 16 --batch-size 1 --grad-accum 4 --max-length 2048 --epochs 1 --download
```

Linux uses the same requirements and runner arguments with its environment's
`bin/python`. Install accelerator wheels matching your hardware from
[PyTorch's official installation instructions](https://pytorch.org/get-started/locally/).
Platform and accelerator support need their own runtime qualification; a working
CPU exporter does not establish Linux CUDA or Apple training support.

The first training run's `--download` permits downloading the pinned **6.25 GB
checkpoint**, its tokenizer and reviewed runtime assets. Subsequent runs omit
that flag to use the cached checkpoint offline. Weights and datasets remain
under private `data/` and `runs/` directories. Downloading weights does not send
your domain records to Hugging Face.

Weight size alone is not a VRAM requirement: training also needs activations,
adapters and optimizer memory. Batch size, sequence lengths and LoRA rank affect
memory use. Lower them if appropriate for your data, or choose a larger GPU.
Choose training duration from validation learning curves and independent domain
results. `--max-steps` is available for bounded compatibility checks, not as a
recommended training budget.

Provide all four reviewed canonical files directly to `--data`; the runner
builds its own native export. Training and validation require one-hot labels.
Reserve at least eight reviewed real calibration records; this minimum is a
mechanical gate, not evidence that calibration data is representative or
statistically sufficient for your domain.

The runner updates language-layer LoRA parameters while freezing the vision
encoder. Only the native decision token and end-of-turn token contribute to
loss. Evidence and padding are masked. It refuses a run with no finite parameter
updates, merges the adapter, unloads the training model and independently reloads
the native decision runtime. Validation probes must retain their decisions and
stay within a fixed probability tolerance of 0.005. A failed qualification is a
failed run, even if its training loss decreased.

Calibration selects per-question temperatures from a fixed grid using only the
reserved calibration role. Final validation/test metrics are computed from the
exported runtime. The test role is never used to fit temperatures or tune
training settings.

| Output | Use |
|---|---|
| `data/` | Native training data, byte-preserved canonical records and lineage manifest |
| `adapter/` | LoRA parameters; retain the pinned base model when using this separately |
| `merged/` (TRL), `checkpoint/` (Unsloth) | Native model assets and domain calibration; Unsloth retains a safetensors LoRA adapter beside the pinned base weights |
| `report.json` | Source/data identities, optimizer updates, parameter delta, package/hardware details, calibration and held-out metrics |
| `manifest.json` | File identities and schema/backend binding for the complete export |

## Train with FastVisionModel and Unsloth

Use this separate **Python 3.12 / NVIDIA CUDA** profile to train d1-3B through
Unsloth's `FastVisionModel`. It keeps the native decision readout and freezes
vision parameters while adapting the language layers. The current profile uses
16-bit base weights and retains the adapter in its saved precision. CPU training, four-bit training and image training are not
included in this route; use the TRL profile for the documented CPU preflight.

The [pinned Liquid notebook](https://github.com/Liquid4All/cookbook/blob/744c5b628a27adb4f219ff4a1d11e3d70259b369/finetuning/notebooks/sft_for_decision_models_with_unsloth.ipynb)
supplies the FastVisionModel and language-layer LoRA approach. This route keeps
the trained adapter separate from the base weights to avoid rounding its updates
into 16-bit weights during merging. This repository
selects **Unsloth/Zoo 2026.10.3, Transformers 5.17.0 and TRL 1.13.0**, a compatible
dependency set. That [Unsloth release](https://pypi.org/pypi/unsloth/2026.10.3/json) caps TRL at 1.13.0; do not copy the notebook's
no-dependency installation of TRL 1.15.0 into this profile. Keep the TRL 5.19
model environment separate. See [dependency compatibility](../compatibility-and-upgrades.md)
when qualifying another release.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv-liquid-unsloth
.\.venv-liquid-unsloth\Scripts\python.exe -m pip install torch==2.10.0+cu126 torchvision==0.25.0+cu126 --index-url https://download.pytorch.org/whl/cu126
.\.venv-liquid-unsloth\Scripts\python.exe -m pip install -r requirements/liquid-unsloth.txt
.\.venv-liquid-unsloth\Scripts\python.exe -m pip check
.\.venv-liquid-unsloth\Scripts\python.exe -m decision_workbench.training.liquid_runner --trainer unsloth --data data/my-domain --out runs/my-domain-liquid-unsloth --device cuda --rank 16 --batch-size 1 --grad-accum 4 --max-length 2048 --epochs 1 --download
```

Linux shell with compatible NVIDIA drivers and CUDA wheels:

```bash
python3.12 -m venv .venv-liquid-unsloth
.venv-liquid-unsloth/bin/python -m pip install torch==2.10.0+cu126 torchvision==0.25.0+cu126 --index-url https://download.pytorch.org/whl/cu126
.venv-liquid-unsloth/bin/python -m pip install -r requirements/liquid-unsloth.txt
.venv-liquid-unsloth/bin/python -m pip check
.venv-liquid-unsloth/bin/python -m decision_workbench.training.liquid_runner --trainer unsloth --data data/my-domain --out runs/my-domain-liquid-unsloth --device cuda --rank 16 --batch-size 1 --grad-accum 4 --max-length 2048 --epochs 1 --download
```

The platform-specific accelerator profile needs qualification on your Linux
host. Choose a token budget that fits your complete native inputs and available
VRAM; the commands are training recipes, not a guarantee that 2,048 tokens fits
every GPU. Later runs can omit `--download` and reuse the pinned private cache.

Provide the same four reviewed canonical roles as the TRL runner. The shared
converter checks native option aliases and token boundaries, then builds exact
completion masks before SFT. Only the native decision token and end-of-turn
receive loss; evidence and padding remain masked. The runner uses the ordinary
Torch AdamW optimizer rather than requiring an 8-bit optimizer, checks actual
parameter updates and exports a portable `checkpoint/` directory containing the
pinned base weights, native runtime assets and a hash-bound `adapter/`. The loader
applies the adapter before scoring, preserving its saved precision. Native reload,
reserved calibration and held-out evaluation remain mandatory before a report
and manifest are published.

For the localhost application, set the profile's interpreter before startup:

```powershell
$env:DMT_LIQUID_UNSLOTH_PYTHON = (Resolve-Path .\.venv-liquid-unsloth\Scripts\python.exe).Path
domain-platform run
```

In Bash, use `export DMT_LIQUID_UNSLOTH_PYTHON="$PWD/.venv-liquid-unsloth/bin/python"`.
Run `domain-platform` in the application's CPU environment. Streamlit also offers
**Liquid d1-3B Unsloth Python executable** in optional local settings. Select
**Liquid d1-3B (Unsloth)** when queueing. Its supervised child keeps compiled
Unsloth files in a private model cache, separate from other model environments.

Use the same release workflow after training. For standalone inference, select
the matching backend and use the exact schema you trained:

```powershell
.\.venv-liquid-unsloth\Scripts\python.exe -m decision_workbench.training.inference --backend liquid-unsloth --model runs/my-domain-liquid-unsloth/checkpoint --request data/my-domain-request.json
```

Python services can use `LiquidEngine("runs/my-domain-liquid-unsloth/checkpoint",
profile="liquid-unsloth")`. This native serving path does not import Unsloth's
training patches. Keep `dmt_liquid.json` with the export to retain the domain
schema, input budget and fitted temperatures. The trainer profile is recorded
in the metadata and is checked during registration and loading. Move the entire
`checkpoint/` directory when deploying; it does not need to fetch base weights
from the Hub. Bare `AutoModel.from_pretrained` does not apply the saved adapter
or this project's domain temperatures. Use the shared engine or API for the
fine-tuned, calibrated result. The same fixed reload tolerance applies to both
profiles; an unsuccessful qualification is never registered.

## Use the fine-tuned export

In the same model environment, call the shared inference helper with your
original decision schema:

```powershell
.\.venv-liquid-trl\Scripts\python.exe -m decision_workbench.training.inference --backend liquid-trl --model runs/my-domain-liquid/merged --request data/my-domain-request.json
```

The request has `state` and `questions` fields, as in the
[example request](../../examples/liquid/request.json). Replace its questions
with the **exact policy/schema you trained**. The engine uses the saved option
order, rejects schema changes and refuses inputs beyond the trained token budget.
It returns canonical answers, probabilities, confidence and expected scores for
rating questions. Use your validated thresholds and fallback before taking any
action in an application.

For Python integration:

```python
import json
from pathlib import Path
from decision_workbench.training.liquid_runtime import LiquidEngine

request = json.loads(Path("data/my-domain-request.json").read_text(encoding="utf-8"))
engine = LiquidEngine("runs/my-domain-liquid/merged", device="auto")
answers = engine.predict(state=request["state"], questions=request["questions"])
```

Load the engine once in your own service, then reuse it. `device="cpu"` selects
CPU inference with sufficient RAM; the float32 profile requires at least
16 GiB available RAM before loading. CPU training uses ordinary PyTorch causal-LM
loss with the same masked completions and requires at least 20 GiB available RAM.
These CPU compute paths need separate execution/performance qualification from CUDA. Do not expose model paths or unbounded requests directly to untrusted
clients. Follow [application integration](../application-blueprints.md) for
routing, authorization and fallback responsibilities.

Use this engine to apply the saved domain temperatures. Loading the export
through raw `AutoModel.system_one` gives the publisher's native uncalibrated
readout unless you separately apply the domain calibration; it does not read
this project's `dmt_liquid.json` automatically. Neither standalone export nor
this helper automatically registers a model in the application.

## Use the data in the official notebooks

Review the notebook's package installation, GPU requirements, pinned model
revision and [LFM Open License](https://huggingface.co/LiquidAI/d1-3B/blob/051bcc464b01b9f92942b364d9586b0ef5912432/LICENSE)
before running it. Keep your domain data private when using a remote notebook.
Use the same checkpoint revision recorded in the exported manifest, and preserve
its tokenizer and native runtime assets when saving the fine-tuned model.

Replace the tutorial's Banking77 preparation and prompt-conversion cells with
your exported dataset loader. Do not rerun the tutorial's split or conversion
cells afterward. In the TRL notebook:

```python
from datasets import load_dataset

native = load_dataset("json", data_files={
    "train": "my-domain-liquid-data/trl/train.jsonl",
    "validation": "my-domain-liquid-data/trl/validation.jsonl",
})
train_dataset_sft = native["train"]
eval_dataset_sft = native["validation"]
```

In the Unsloth notebook, load the corresponding text files:

```python
from datasets import load_dataset

native = load_dataset("json", data_files={
    "train": "my-domain-liquid-data/unsloth/train.jsonl",
    "validation": "my-domain-liquid-data/unsloth/validation.jsonl",
})
train_dataset = native["train"]
eval_dataset = native["validation"]
```

Match the trainer's sequence length to the export's `max_length`. Preserve
response-only loss masking and inspect that only the decision completion is
trained. Liquid's Unsloth notebook uses **FastVisionModel**, keeping the trained
d1 readout; this project's existing **FastDecisionModel/DecisionTrainer** backend
is a different adapter and must not be substituted.

Keep `canonical/calibration.jsonl` and `canonical/test.jsonl` outside training.
After training, independently reload the complete saved model through Liquid's
native API and follow [evaluation and calibration](../evaluation-guide.md).
An SFT loss curve alone does not establish calibrated decisions or application
release readiness. For the native loading/`system_one` interface, see the
[publisher's d1-3B guide](https://docs.liquid.ai/lfm/models/d1-3b).

For hosted d1 or d1-ultra, use the separate [Liquid API workflow](liquid-hosted.md).

## Remote training and other Liquid models

The [AWS, Azure, GCP, Modal and RunPod guides](../remote-training/README.md)
explain the project's existing provider workflows. A d1-3B-specific managed
runner/image and account-level training qualification are not available yet;
do not submit a Liquid study to a Laya/Vega runner. You can use a suitable
private GPU notebook environment for the official training recipe, subject to
the provider's terms and your data-transfer policy.

This data profile is for **d1-3B**, not hosted d1-ultra or d1-omni-600M. The
[Liquid model directory](https://docs.liquid.ai/lfm/models/decision-models)
describes those separate routes. Image/audio/video training, quantized serving,
Apple inference and fully managed d1-3B release remain separate compatibility
work; this text/JSON profile does not qualify them.
