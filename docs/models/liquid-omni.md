# Train and use Liquid d1-omni-600M

[Model directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Adapt Liquid's original omni decision head to reviewed domain examples, then
calibrate, release and use the exported model on your computer. This profile
accepts text and JSON with choice, yes/no and ordered-score questions. Its native
bidirectional encoder and option-marker head differ from the
[d1-3B causal SFT profile](liquid.md).

The workbench trains the original head with soft-target cross-entropy and freezes
the text trunk, vision encoder and audio encoder. This is a workbench adaptation
method based on the publisher's native implementation. Liquid's d1-3B SFT
notebooks are not an omni training recipe. Image/audio preparation, media
fine-tuning, quantized exports, MLX and managed cloud training are not available
through this adapter.

## Prepare the model environment

First install the application using the [root quick start](../../README.md#run-on-your-computer).
Create a separate Python 3.12 model environment. The float32 weights download is
about 2.35 GB, and each full exported checkpoint requires similar disk space.
Allow room for the cache, dataset exports and retained versions. CPU training is
experimental; use the CPU comparison baseline when memory or execution time is
limiting. CPU execution
requires at least 6 GiB available RAM; CUDA loading requires at least 4 GiB free
VRAM. These are startup checks, not capacity guarantees: input length, batching
and head training can require more memory. Start with batch size 1 and short inputs.

Windows PowerShell, from the repository root:

```powershell
py -3.12 -m venv .venv-omni
.\.venv-omni\Scripts\python.exe -m pip install -r requirements/liquid-omni.txt
.\.venv-omni\Scripts\python.exe -m pip check
$env:DMT_OMNI_PYTHON = (Resolve-Path .\.venv-omni\Scripts\python.exe).Path
domain-platform run
```

Linux shell:

```bash
python3.12 -m venv .venv-omni
.venv-omni/bin/python -m pip install -r requirements/liquid-omni.txt
.venv-omni/bin/python -m pip check
export DMT_OMNI_PYTHON="$PWD/.venv-omni/bin/python"
domain-platform run
```

Use the application environment's `domain-platform` launcher. You can also set
the model Python path under the app's advanced local setup. For separately run
API/worker services, configure `DMT_OMNI_PYTHON` in both processes. This variable
selects the model interpreter for training and serving without replacing other
model environments. It must contain an absolute Python executable path.

For an NVIDIA GPU, install matching Torch 2.10.0 and torchvision 0.25.0 CUDA
wheels using [PyTorch's official wheel instructions](https://pytorch.org/get-started/previous-versions/)
before installing this profile. CPU wheels work for the CPU route. Do not replace
another model's working environment. The runtime pins compatible packages and
refuses a mismatched profile. macOS/MPS, Linux GPU images and other hardware
configurations need separate qualification; the adapter does not select MPS.

## Train and release in the app

1. Start the localhost app and define your domain and allowed decisions. Use the
   [guided intake and import workflow](../product-workflow.md) to enter examples
   or upload CSV, XLSX, TSV, JSON or JSONL records.
2. Review labels and reserve independent real validation, calibration and test
   records. Reserve at least eight calibration records. Prepare a snapshot using
   the [dataset guide](../data-guide.md).
3. Optionally generate [synthetic training candidates](../synthetic-generation-guide.md)
   with a separate compatible teacher. Describe the examples you need and review
   each candidate before including it in training. Omni scores allowed answers;
   it does not generate example text. Keep held-out records out of teacher seeds.
4. Set your release criteria. Select **Liquid d1-omni-600M (native head, text/JSON)**
   and **This computer**. Inspect the detected environment and choose CPU, CUDA
   or automatic selection. Enable checkpoint downloading only if it is not cached.
5. Queue training. The worker freezes source/data/settings, reserves the shared
   model resource, updates the native head and exports the complete model. It
   independently reloads the export, fits calibration on reserved real records
   and evaluates the held-out sets before registration.
6. Inspect per-question metrics and release gates. Activate an eligible version,
   then select **Make decision** or call the
   [authenticated decision API](../product-workflow.md#6-release-and-integrate).
   Retained eligible versions can be activated for rollback.

`DMT_OMNI_CACHE` optionally sets the private Hugging Face checkpoint cache.
Otherwise it uses `model-cache/liquid-omni-hub` beside the workspace. Custom-code
caches are separate from d1-3B. First use downloads a pinned public checkpoint;
subsequent offline runs require its complete cache. No paid API key is required.

## Export native training data

Use the [canonical record format](../data-guide.md) and
[Liquid example specification and CSV](../../examples/liquid/README.md).
All four nonempty files are required:
`train.jsonl`, `validation.jsonl`, `calibration.jsonl`, `test.jsonl`.
The shared contract checks review, real held-out origin, group/state separation
and training lineage. Complete normalized soft labels are retained without
rounding. Question schemas must match across records.

In the separate [CPU data environment](../../requirements/liquid-data.txt), or
with the omni environment's Python, run:

```bash
python -m decision_workbench.training.liquid_omni_data --data data/domain --out data/omni-native --assets data/omni-assets --download-assets --max-length 2048
```

This downloads only reviewed small publisher assets, about 5 MB, and creates:

| Output | Purpose |
|---|---|
| `canonical/` | Exact original bytes for all four roles |
| `native/` | Input token IDs, option-marker positions, question type and target probabilities |
| `lineage/` | Original IDs/groups/review metadata, gold, role and native-row hash |
| `manifest.json` | Model/revision, lengths, counts and output hashes |

Native yes/no head order is `false, true`; the publisher's high-level
`probabilities` API reverses that order. The adapter maps by label explicitly.
Instructions/options have native per-question budgets even with a long overall
context. Inputs that would truncate state, policy or options are refused. Shorten
the evidence or option descriptions rather than silently losing information.

## Use the standalone trainer and export

Run this with the omni model environment's Python. Choose a new output directory
for every run; existing runs are preserved.

```bash
python -m decision_workbench.training.liquid_omni_runner --data data/domain --out data/omni-run --cache data/model-cache/liquid-omni-hub --device cpu --batch-size 1 --grad-accum 4 --max-length 2048 --epochs 1 --download
```

Choose `--device cuda` for a compatible NVIDIA GPU. `--max-steps 2` limits a
mechanics check to two optimizer updates; it is not a useful domain-quality
training prescription. Tune settings on independent validation data and retain
the real test set for final evaluation.

The result contains `checkpoint/`, `data/`, `report.json` and `manifest.json`.
The report records successful finite updates, unchanged frozen weights, fitted
temperatures, validation/calibration/test metrics and reload equivalence.
Publisher base-model temperatures are disabled for a trained export; domain
calibration is applied once. The checkpoint retains the original native model
code and license. Use the whole bundle for integrity-checked inference:

```bash
python -m decision_workbench.training.inference --backend liquid-omni --model data/omni-run --request data/request.json
```

`request.json` contains `state` and the exact training `questions` schema. Supply
complete task evidence at the workflow's decision point. The result provides
allowed-outcome probabilities and normalized decisions; it does not execute
application actions. Apply your own thresholds, error-cost policy and fallback.
Standalone training does not automatically register a model in a running workspace;
use the application training path for its registry/release workflow.

## Source and compatibility boundaries

This adapter pins `LiquidAI/d1-omni-600M` revision
`02b55d7076f15129e59ab3f94783f32c4b088674`, verifies native code/tokenizer hashes
before loading and uses safe tensor weights. The publisher supports additional
text/image/audio runtimes; those capabilities do not imply that this workbench
adapter supports media or can train every publisher format.

Sources: [Liquid model documentation](https://docs.liquid.ai/lfm/models/d1-omni-600m),
[pinned model card](https://huggingface.co/LiquidAI/d1-omni-600M/blob/02b55d7076f15129e59ab3f94783f32c4b088674/README.md),
[native decision head](https://huggingface.co/LiquidAI/d1-omni-600M/blob/02b55d7076f15129e59ab3f94783f32c4b088674/encoder.py),
[native formatting and answer mapping](https://huggingface.co/LiquidAI/d1-omni-600M/blob/02b55d7076f15129e59ab3f94783f32c4b088674/prompt.py).
Review the [pinned LFM Open License](https://huggingface.co/LiquidAI/d1-omni-600M/blob/02b55d7076f15129e59ab3f94783f32c4b088674/LICENSE)
and [third-party terms](../../THIRD_PARTY_NOTICES.md) before training,
redistribution or deployment. Source identities were checked on 17 October 2026.
