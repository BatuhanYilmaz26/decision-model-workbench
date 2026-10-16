# Prepare domain data for Liquid d1-3B

[Model directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Use the CPU exporter to turn reviewed domain examples into the native data
formats used by Liquid's official d1-3B fine-tuning notebooks. It supports text
and JSON evidence with choice, yes/no and ordered-score questions. It downloads
a pinned tokenizer, prompt builder and license, about 18 MB in total. It does
not download weights, start a paid job or train a model.

The exporter is a standalone data-preparation tool. Liquid training, calibration,
registry admission and serving are not yet integrated into the application's
training menu. The [official Unsloth notebook](https://github.com/Liquid4All/cookbook/blob/744c5b628a27adb4f219ff4a1d11e3d70259b369/finetuning/notebooks/sft_for_decision_models_with_unsloth.ipynb)
and [official TRL notebook](https://github.com/Liquid4All/cookbook/blob/744c5b628a27adb4f219ff4a1d11e3d70259b369/finetuning/notebooks/sft_for_decision_models_with_trl.ipynb)
provide the upstream training routes. Treat those as separate GPU environments;
do not add their packages to an existing Laya or Unsloth installation.

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

## Remote training and other Liquid models

The [AWS, Azure, GCP, Modal and RunPod guides](../remote-training/README.md)
explain the project's existing provider workflows. A d1-3B-specific managed
runner/image and account-level training qualification are not available yet;
do not submit this data bundle to a Laya/Vega runner. You can use a suitable
private GPU notebook environment for the official training recipe, subject to
the provider's terms and your data-transfer policy.

This data profile is for **d1-3B**, not hosted d1-ultra or d1-omni-600M. The
[Liquid model directory](https://docs.liquid.ai/lfm/models/decision-models)
describes those separate routes. Image/audio/video training, quantized serving,
Apple inference and fully managed d1-3B release remain separate compatibility
work; this CPU exporter does not qualify them.
