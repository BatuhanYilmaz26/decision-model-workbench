# Train remotely when local VRAM is insufficient

Train your own open-weight decision model on a rented GPU when local VRAM is insufficient. The workflows below run the repository's Laya or Unsloth trainer with the same reviewed datasets and export checks used locally. Hosted Jev access is a separate API workflow; TypeSafe's [model documentation](https://docs.typesafe.ai/models) does not offer customer fine-tuning.

Choose AWS, Azure, Google Cloud, Modal or RunPod based on your infrastructure, identity and GPU needs. Each walkthrough covers data staging, job submission, output retrieval and shutdown. Before sending domain data, prepare a Linux training image and verify it with a small non-sensitive study. See [provider compatibility checks](verification.md#provider-compatibility-checks).

| Provider | Training product and control plane | Data / export storage | Choose when |
|---|---|---|---|
| [AWS](remote-training/aws.md) | SageMaker AI custom training; Boto3 `CreateTrainingJob` | S3 channel → `/opt/ml/input/data`; export → S3 | Existing AWS IAM, managed jobs and artifact governance |
| [Azure](remote-training/azure.md) | Azure ML command job; CLI v2 YAML or SDK v2 | URI-folder input and named output | Existing Azure ML workspace and enterprise identity |
| [GCP](remote-training/gcp.md) | Vertex AI-compatible custom training; `gcloud` / `CustomJob` API | Cloud Storage and local staging | Existing GCP project, service accounts and Artifact Registry |
| [Modal](remote-training/modal.md) | GPU Function recipe or managed GPU Sandbox; Modal SDK/CLI | Persistent Volume | Short programmable jobs without managing a GPU VM |
| [RunPod](remote-training/runpod.md) | GPU Pod; REST v2 plus authenticated SSH | Persistent workspace or network volume | Interactive setup and direct control of GPU/runtime |

Google Cloud documents CustomJob training under [Gemini Enterprise Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/create-custom-job). The walkthrough uses `gcloud ai custom-jobs` and the regional `aiplatform` REST API.

For application-managed approvals, job history and artifact validation, use [AWS](managed-cloud-training.md), [Azure](managed-azure-training.md), [GCP](managed-gcp-training.md), [RunPod v2](managed-runpod-training.md) or [Modal GPU Sandboxes](managed-modal-training.md). Check each provider's storage, recovery and shutdown contract when configuring the profile.

Start with [provider selection, VRAM sizing and total cost](remote-training/provider-selection.md). The [comparison dated 12 October](../research/decision-model-refresh-2026-10-12.md) lists the provider references. Vega uses a separate [eight-file operator study/admission route](remote-admission-guide.md#vega-eight-file-studies-and-independent-admission); the managed Laya/Unsloth runner does not accept it.

## Before you begin

The provider walkthroughs use Bash, Python and the selected provider's CLI or SDK. On Windows, run Bash commands in WSL or the provider's Cloud Shell. You can prepare and submit jobs without a local GPU; training runs on the remote resource.

Create the four reviewed JSONL splits, a compatible Linux training image and an approved provider account with GPU quota. Set each walkthrough's `DMT_*` variables in the shell that submits the job. Keep provider credentials in the provider's authentication configuration or secret store.

## Prepare the same domain experiment for every provider

The [worked domain adoption guide](domain-adoption-guide.md) connects these compute recipes to natural-language intake, file mapping, synthetic coverage, model evaluation and application rollout. Use the provider lifecycle matrix below to decide what to record before leaving a remote job unattended.

### Provider lifecycle and handoff matrix

| Provider | Submission interface in the walkthrough | Durable handle to retain | Where the full export comes from | Completion and shutdown evidence |
|---|---|---|---|---|
| [AWS](remote-training/aws.md) | Boto3 `create_training_job` | Training job name/ARN and region | Returned `ModelArtifacts.S3ModelArtifacts` | Successful job, verified artifact; terminal state after `StopTrainingJob` when cancelled |
| [Azure](remote-training/azure.md) | `az ml job create`; SDK v2 `jobs.create_or_update` | Job name, workspace, compute and environment identity | Named `export` output via `az ml job download` | Successful command plus complete output; cancellation followed by node scale-down observation |
| [GCP](remote-training/gcp.md) | `gcloud ai custom-jobs create`; CustomJob REST | Full regional CustomJob resource name | Recorded Cloud Storage run prefix | `JOB_STATE_SUCCEEDED` and completed transfer; terminal observation after cancellation |
| [Modal](remote-training/modal.md) | Python `train.remote`; SDK/CLI | App/function-call identity, environment and Volume | Committed run export on the Volume | Successful call plus committed files; observe stopping separately when cancelling an App |
| [RunPod](remote-training/runpod.md) | REST v2 create, then SSH trainer | Immutable Pod ID, data center and chosen volume | Complete export copied from the recorded volume/path | Trainer exit alone is insufficient; observe stop/termination and retain output before Pod deletion |

For each route, record the exact staged input hashes, resolved image and package identities, GPU, timestamps, trainer exit, manifest/report, retrieval receipt and final resource state. A lost submission response requires checking the existing job/resource before another attempt. The managed controllers implement provider-specific uncertainty handling; illustrative direct scripts do not establish durable single-attempt execution. A provider terminal state also does not remove retained storage or its charges. See [provider selection](remote-training/provider-selection.md) and [managed remote admission](remote-admission-guide.md).

1. Describe the domain, decision-time evidence, allowed outcomes, missing-evidence behavior and error costs. Obtain owner approval of the schema. Use [data intake](data-guide.md) for manual examples, CSV/XLSX mapping and review.
2. Export four reviewed files into a **data-only** directory: `train.jsonl`, `validation.jsonl`, `calibration.jsonl`, `test.jsonl`. Preserve their IDs, source-family groups, `state/questions/gold`, reviewed origin and hashes. Validation/calibration/test must be independent real records; hypothetical examples must not be relabeled as real to pass a validator.
3. Generate synthetic candidates only from reserved training seeds, review them, and retain seed lineage. The cloud GPU does not fix mislabeled or leaked data. Record dataset and schema hashes outside the job with the owner acceptance policy.
4. Choose native Laya's frozen-encoder/head training or the Unsloth encoder-LoRA path. Select an exact checkpoint revision and a qualified package/runtime image. Vega requires its own adapter/calibration pipeline; its published emotion script is not a generic Laya data loader. See [training](training-guide.md) and [new model research](../research/decision-model-expansion-2026-10-10.md).
5. Allocate one GPU initially, batch size 2, gradient accumulation 4 and a bounded token length such as 512 for a small encoder study. These are starting experiment settings, not a VRAM promise or domain-quality recommendation. Measure peak memory with representative longest inputs and include export/reload overhead. Increase capacity before expanding batch/context/backbone size. Splitting one unsupported model across several GPUs requires an implemented distributed trainer; allocating more GPUs alone does not implement it.

The adapters accept a local directory, not CSV/XLSX or cloud URIs. Validate the staged files before model download:

```bash
python -c 'from decision_workbench.training.contracts import load_splits; rows, hashes = load_splits("data/support"); print({k: len(v) for k, v in rows.items()}, hashes)'
```

Run the shared native command after substituting the provider's mounted paths:

```bash
python -m decision_workbench.training.laya_runner \
  --data /job/data --out /job/export \
  --model convaiinnovations/laya \
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
```

For the existing Unsloth route, replace the module with `decision_workbench.training.unsloth_runner`; add `--rank 16` and, for an inexpensive mechanics trial, a chosen `--max-steps` ceiling. The native runner does not accept `--rank` or `--max-steps`. Select a fresh empty output directory for every run. Both adapters enforce four splits and exported-runtime probability checks. They do **not** currently implement generic optimizer resume or automatic spot recovery. Use on-demand compute initially; interrupted runs need a fresh explicit submission and output path. [Actual adapter details](training-guide.md)

## Build and qualify a Linux training image

The root product Dockerfile packages the application; it is not a qualified CUDA training image. The [Windows dependency profiles](../requirements/README.md) contain platform-specific packages. Do not install a Windows profile into Linux. Review [GPU compatibility and dependency advisories](compatibility-and-upgrades.md) before preparing a production training environment.

Unsloth publishes `unsloth/unsloth` with GPU dependencies, Jupyter and SSH. Resolve a tested image to a digest, inspect its Python/Torch/CUDA versions and select it as a candidate base. Do not rely on a moving `latest` tag for repeatable studies. The upstream image's existence is not proof that its current contents match this project's version-specific adapter. [Official Docker installation](https://unsloth.ai/docs/get-started/install/docker)

A dedicated build directory can contain the following `Dockerfile.training`. Copy only reviewed code; keep data, credentials, `.venv`, Git internals and model caches out of the image/build context.

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /opt/domain-decisions
COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY decision_workbench/ ./decision_workbench/
RUN python -m pip install \
    'pip==26.2.1' 'setuptools==84.0.0' 'wheel==0.48.0' \
    'laya @ https://github.com/NandhaKishorM/laya/archive/1adc59f7e371deb601fcfa18a14e25db238addcc.zip' \
    'unsloth==2026.10.3' 'unsloth-zoo==2026.10.3' \
    'transformers==5.17.0' 'peft==0.21.2' 'datasets==4.8.5' \
    'safetensors==0.8.0'
RUN python -m pip install --no-deps --no-build-isolation . && python -m pip check
ENV DMT_MODEL_CACHE=/tmp/dmt-model-cache
ENTRYPOINT []
CMD ["python", "-m", "decision_workbench.training.laya_runner", "--help"]
```

This is a **candidate build recipe**, with explicit direct dependency pins for the encoder adapter's compatibility profile. It does not lock all transitive/Linux wheels or choose Torch/CUDA for every GPU. On the selected Linux GPU, verify imports, the Unsloth version guard, `pip check`, representative token budgets, a small train/export/reload run and a separate inference process. Freeze the resolved Linux dependency lock, image digest, code revision and test evidence only after those pass. If the base image has incompatible preinstalled packages, create a clean supported image rather than forcing installation. Building may be done on a cloud builder or rented host; it does not require local GPU VRAM.

```bash
docker build --build-arg BASE_IMAGE="$DMT_BASE_IMAGE_DIGEST" \
  -f Dockerfile.training -t "$DMT_TRAIN_IMAGE_TAG" .
docker push "$DMT_TRAIN_IMAGE_TAG"
```

Publish to the provider's private ECR/ACR/Artifact Registry or another approved registry. Record the resolved image digest in the job and experiment record. Managed jobs override the command with the training module. RunPod's interactive SSH route uses a separately configured upstream SSH image; the image above deliberately has a training command entry point.

## Budget, storage and interruption policy

Use the current regional SKU quote, GPU quota and provider availability before submitting. GPU-hours alone omit CPU/RAM/storage, startup/download time, registry traffic, checkpoint uploads, retained volumes, logs and egress. Record an expected maximum bill and wall-clock deadline. AWS/Azure/GCP/Modal provide job/function time limits in the recipes; RunPod requires external observation and explicit stop/delete in addition to a trainer timeout. Budget notifications are not a guaranteed hard billing cutoff.

Prefer immutable per-run input/output prefixes with least-privilege read/write identities. Separate code, domain data and secret configuration. Private/gated base weights may require a scoped token; do not put it in examples, notebook source or a container layer. Private egress/image access must be designed before uploading regulated data. Use the same real holdouts and measured quality targets when comparing providers; a faster GPU does not make one trained model more accurate by itself.

## Retrieve, qualify and host the export

Each completed adapter export includes `manifest.json`, `report.json`, safetensors weights, tokenizer/configuration and calibration. Retrieve the **entire** export, not only LoRA weights or a single safetensors file. Record the job ID, provider/region/SKU, dataset hashes, source/model/image revisions, peak memory, actual training updates, reported metrics, reload tolerance and billing receipt. A provider's `Completed` state does not substitute for inspecting the report and export manifest.

Load a retrieved native export in its qualified model environment for a separate application study:

```python
import json
from decision_workbench.training.engines import NativeEngine

with open("retrieved/data/train.jsonl", encoding="utf-8") as stream:
    questions = json.loads(next(stream))["questions"]
engine = NativeEngine("retrieved/export", device="auto")
answers = engine.predict("Please send a copy of the invoice.", questions)
```

For a merged Unsloth export use `UnslothEngine("retrieved/export")` in the matching isolated environment. These library calls do not provide HTTP authentication, resource isolation or registry admission. Verify the bundle against its retained manifest and expected data/schema/model identity before loading; do not trust an arbitrary downloaded directory as an enterprise release. Apply evaluated thresholds and serve behind a qualified application boundary. [Serving contracts](deployment-guide.md)

**Platform-managed route:** freeze a remote study before training, pass its request to the trainer and use [authenticated independent admission](remote-admission-guide.md) for the complete export. Standalone exports without that binding are not automatically admitted. The managed controllers support approval, submission, observation, stopping and retrieval; unattended watchdogs and real account execution need separate operational validation. Training does not deploy an endpoint: local serving and cloud prediction services require their own container, authentication and load checks.

To submit from the application, follow the managed guides for [AWS](managed-cloud-training.md), [Azure](managed-azure-training.md), [GCP](managed-gcp-training.md), [RunPod v2](managed-runpod-training.md) or [Modal](managed-modal-training.md). Run the [provider compatibility checks](verification.md#provider-compatibility-checks) with your selected account before a production study.
