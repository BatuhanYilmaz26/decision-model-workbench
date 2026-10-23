# Use and train a Decision 3.0 model

[Model directory](README.md) · [Cloud training](../remote-training/d3.md) · [Project overview](../../README.md)

Choose a pinned D3 variant, prepare reviewed examples for your domain, adapt its
original answer-code readout and calibrate the result on independent real data.
Use the resulting version through the local application, its decision API or the
command line. The shared adapter accepts text and JSON with choice, yes/no and
ordered-score questions. Synthetic text generation needs a separate teacher.

## Choose a variant and provision its resources

Each variant has its own checkpoint revision, tokenizer/configuration hashes,
weight layout and readout dimensions. Selecting another variant creates a
different study and requires its own evaluation and calibration.

| Backend | Official checkpoint | Backbone weight download | CPU RAM startup floor | Free CUDA memory startup floor |
|---|---|---|---|---|
| `d3-edge` | [D3 Edge](https://huggingface.co/vllm-sr/d3-edge/tree/c454ee39b862248d70abd7f84195d2f54ffe6d43) | 1.19 GB | 3 GiB | 2 GiB |
| `d3-lite` | [D3 Lite](https://huggingface.co/vllm-sr/d3-lite/tree/03560c26f5199c3c04129a2ccb38960c913635e0) | 1.71 GB | 6 GiB | 3 GiB |
| `d3-nano` | [D3 Nano](https://huggingface.co/vllm-sr/d3-nano/tree/462efc366229ea268cf4eed3738b3fc4415e4817) | 4.43 GB | 11 GiB | 6 GiB |
| `d3-mini` | [D3 Mini](https://huggingface.co/vllm-sr/d3-mini/tree/bd0090dd758d32ba59ef5c749910a8e87fc4391f) | 9.08 GB | 19 GiB | 10 GiB |
| `d3-flash` | [D3 Flash](https://huggingface.co/vllm-sr/d3-flash/tree/13dc1cc8ef23caefa765907e1d764751e5629f7d) | 16.79 GB | 34 GiB | 17 GiB |
| `d3` | [D3](https://huggingface.co/vllm-sr/d3/tree/cf15549a78fc52598ddd1951f321c58372bbd64f) | 52.18 GB | 100 GiB | 50 GiB |

Download sizes come from the pinned publisher file inventories. GB means decimal
bytes; GiB means binary bytes. Memory floors are conservative application checks,
not measured peak-memory requirements or a guarantee that your context/batch fits.
Larger inputs, concurrency and other processes require additional memory. This
trainer uses one CUDA device; memory across multiple GPUs is not pooled. The
larger variants are experimental profiles and need qualification on your hardware.
[Edge's reference workflow](d3.md) describes the smallest profile in detail.

Keep storage for the checkpoint cache and each complete retained export. A new
cache and export together need roughly twice the backbone download size, plus
runtime packages, data and scratch space. Backup, restore and remote admission
need additional copies. Training checks available RAM/VRAM and storage before
large checkpoint transfers; reduce the batch/context or use a larger host when
a check refuses the request.

All six checkpoints use a 255-code FP32 readout and the native BF16 Qwen3.5
implementation. The adapter freezes the full backbone and updates that original
readout, whose width varies by model. It retains native noncausal full-attention
layers, causal Gated DeltaNet layers, left padding and option/code ordering.
Native choice permutation averaging is disabled in this reference profile. Match
those prompt/kernel settings when comparing upstream predictions. The publisher's Transformers wrapper is inference-only; this workbench method
is separate from ordinary chat SFT.

This profile uses eager native kernels. Images/video, quantization, ROCm fast
paths, vLLM and Semantic Router Engine require their own adapters and probability
qualification. Upstream support for a feature does not make it available in this
training adapter. See the [official collection](https://huggingface.co/collections/vllm-sr/decision-30)
for the publisher's runtime and benchmark descriptions, and the
[benchmark guide](../benchmarks.md) for comparison limits.

## Install the application and model environment

Start with the [root quick start](../../README.md#run-on-your-computer). Keep the
application environment separate from the Python 3.12 D3 model environment.
All listed pins use the same compatible native package profile.

Windows PowerShell, from the repository root:

```powershell
py -3.12 -m venv .venv-d3
.\.venv-d3\Scripts\python.exe -m pip install -r requirements/d3-native.txt
.\.venv-d3\Scripts\python.exe -m pip check
$env:DMT_D3_PYTHON = (Resolve-Path .\.venv-d3\Scripts\python.exe).Path
.\.venv\Scripts\python.exe -m decision_workbench.platform.cli run
```

Linux shell:

```bash
python3.12 -m venv .venv-d3
.venv-d3/bin/python -m pip install -r requirements/d3-native.txt
.venv-d3/bin/python -m pip check
export DMT_D3_PYTHON="$PWD/.venv-d3/bin/python"
.venv/bin/python -m decision_workbench.platform.cli run
```

For CUDA, install matching Torch 2.10 / torchvision 0.25 wheels from the
[PyTorch instructions](https://pytorch.org/get-started/previous-versions/) before
the remaining profile dependencies. The reference stack uses Transformers 5.17.
Keep it separate from Liquid's Transformers 5.19 environment. Linux GPU images
remain experimental and need validation on the selected host/provider.

`DMT_D3_PYTHON` applies to training and serving. Optional `DMT_D3_CACHE` selects
the public checkpoint cache; variants occupy separate pinned Hub directories
inside it. Neither a D3 API key nor a pasted workspace credential is needed for
personal localhost use. Restart services after changing their environment.

For large exports, configure finite `DMT_MODEL_LOAD_TIMEOUT_SECONDS` and
`DMT_INFERENCE_TIMEOUT_SECONDS` values appropriate to cold loading and inference
on your storage/hardware. Set `DMT_UI_MODEL_TIMEOUT_SECONDS` in the web process
to cover those deadlines plus response overhead. See [resident model settings](../setup.md#configuration)
and inspect the active version after a client timeout before retrying a release.

## Prepare your domain and train in the app

1. Describe the decision and the data you need. Define allowed outcomes and the
   evidence available at decision time. Enter examples or upload
   [CSV, XLSX, TSV, JSON or JSONL](../data-guide.md#file-import).
2. Review/correct labels. Optional [synthetic generation](../synthetic-generation-guide.md)
   proposes training candidates through a separate generative teacher. Reviewed
   candidates remain training-only; protect independent real holdouts.
3. Prepare nonempty training, validation, calibration and test roles. Use at least
   eight calibration records and choose release criteria appropriate to your
   domain's error costs. This numerical floor does not establish data quality.
4. Select the desired D3 variant under **Training backend** and choose local CPU
   or CUDA. Enable the public checkpoint download only when needed. Alternatively,
   select **Cloud GPU** and follow the [five-provider guide](../remote-training/d3.md).
5. Queue training. The study binds variant/revision, source, settings, reviewed
   data and release policy. The native converter preserves complete soft targets
   and review/group lineage. The full model is independently reloaded, calibrated
   and evaluated before registration.
6. Inspect the per-question metrics and saved gates. Activate an eligible version
   with a reason, then use **Make decision** or the
   [authenticated decision API](../product-workflow.md#6-release-and-integrate).
   Retain an eligible earlier version for rollback.

The [retrieval-evidence examples](../../examples/d3/README.md) show the common
schema and request format. They are pending synthetic illustrations and cannot
substitute for your reviewed domain training and independent real evaluation data.

## Export native data without downloading weights

Use the separate [data-only environment](../../requirements/d3-data.txt) when you
only need to validate native prompts and produce model-specific data. It needs
tokenizer/configuration assets, with no Torch, Transformers or backbone weights.
It does not execute the checkpoint's Python code.

With your own reviewed four-role study at `data/my-study`:

```bash
python -m decision_workbench.training.d3_data --backend d3-lite --data data/my-study --out data/my-d3-lite-native --cache data/model-cache/d3-hub --download --max-length 2048
```

Choose any listed backend. The output manifest names its exact model/revision;
canonical examples and lineage remain separate from native prompts/targets.
Long prompts, reserved control tokens, unsupported media and incompatible question
fields are refused rather than silently truncated or reinterpreted.

## Train and use a model from the command line

In the compatible model environment, select the same backend throughout. Replace
these paths with your reviewed study and a request matching its questions:

```bash
python -m decision_workbench.training.d3_runner --backend d3-lite --data data/my-study --out data/my-d3-lite-model --cache data/model-cache/d3-hub --device cuda --download
python -m decision_workbench.training.inference --backend d3-lite --model data/my-d3-lite-model --request data/my-request.json
```

Use `--device cpu` on a sufficiently provisioned CPU host. Omit `--download` for
a complete offline cache. Each output path must be new. Follow
[Edge's request builder](d3.md) to preserve the exported question/choice order.
The complete manifest-bound bundle is required for inference and admission.

Exports record the calibrated CPU/CUDA device and keep it for default serving.
Moving to another variant, device, kernel or runtime requires fresh evaluation;
matching selected answers does not establish calibrated probability parity.
Native loading checks publisher source hashes and ignores bytecode caches.
Application confidence is the largest outcome probability; upstream entropy-based
confidence has a different meaning. Evaluate your thresholds on the serving profile.
