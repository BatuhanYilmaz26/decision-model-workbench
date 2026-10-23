# Train on a rented GPU and admit the result

<a id="train-remotely-when-local-vram-is-insufficient"></a>

[Documentation](README.md) · [Project overview](../README.md)

Use one compatible model image and one provider account. Prepare the study, approve data transfer and compute, submit and observe the provider job, then independently validate the returned export before release.

## Choose a section

- [Prepare and submit a frozen study](#study-protocol)

- [Choose infrastructure and estimate costs](#choosing-a-provider)

- [Validate and admit the returned model](#returned-model-admission)

<a id="study-protocol"></a>

## Prepare and submit a frozen study

<a id="study-protocol--train-remotely-when-local-vram-is-insufficient"></a>

Train your own open-weight decision model on a rented GPU when local VRAM is insufficient. The shared protocol supports compatible native trainers with the same reviewed datasets and export checks used locally. Use the [Liquid cloud guide](remote-training/liquid.md) or [D3 Edge cloud guide](remote-training/d3.md) for their separate images, native formats and admission profiles. Hosted Jev access is a separate API workflow; TypeSafe's [model documentation](https://docs.typesafe.ai/models) does not offer customer fine-tuning.

Choose AWS, Azure, Google Cloud, Modal or RunPod based on your infrastructure, identity and GPU needs. Each walkthrough covers data staging, job submission, output retrieval and shutdown. Before sending domain data, prepare a Linux training image and verify it with a small non-sensitive study. See [provider compatibility checks](verification.md#provider-compatibility-checks).

| Provider | Training product and control plane | Data / export storage | Choose when |
|---|---|---|---|
| [AWS](remote-training/aws.md#direct-submission) | SageMaker AI custom training; Boto3 `CreateTrainingJob` | S3 channel → `/opt/ml/input/data`; export → S3 | Existing AWS IAM, managed jobs and artifact governance |
| [Azure](remote-training/azure.md#direct-submission) | Azure ML command job; CLI v2 YAML or SDK v2 | URI-folder input and named output | Existing Azure ML workspace and enterprise identity |
| [GCP](remote-training/gcp.md#direct-submission) | Vertex AI-compatible custom training; `gcloud` / `CustomJob` API | Cloud Storage and local staging | Existing GCP project, service accounts and Artifact Registry |
| [Modal](remote-training/modal.md#direct-submission) | GPU Function recipe or managed GPU Sandbox; Modal SDK/CLI | Persistent Volume | Short programmable jobs without managing a GPU VM |
| [RunPod](remote-training/runpod.md#direct-submission) | GPU Pod; REST v2 plus authenticated SSH | Persistent workspace or network volume | Interactive setup and direct control of GPU/runtime |

Google Cloud documents CustomJob training under [Gemini Enterprise Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/create-custom-job). The walkthrough uses `gcloud ai custom-jobs` and the regional `aiplatform` REST API.

For application-managed approvals, job history and artifact validation, use [AWS](remote-training/aws.md#application-managed-training), [Azure](remote-training/azure.md#application-managed-training), [GCP](remote-training/gcp.md#application-managed-training), [RunPod v2](remote-training/runpod.md#application-managed-training) or [Modal GPU Sandboxes](remote-training/modal.md#application-managed-training). Check each provider's storage, recovery and shutdown contract when configuring the profile.

Start with [provider selection, VRAM sizing and total cost](remote-training-guide.md#choosing-a-provider). The [comparison dated 12 October](../research/decision-model-refresh-2026-10-12.md) lists the provider references. Vega uses a separate paired [eight-file data contract](remote-training-guide.md#returned-model-admission--vega-eight-file-studies-and-independent-admission), now accepted by the shared cloud launcher. Follow the [Vega image and five-provider guide](remote-training/vega.md) and declare that backend explicitly.

<a id="study-protocol--before-you-begin"></a>

### Before you begin

The provider walkthroughs use Bash, Python and the selected provider's CLI or SDK. On Windows, run Bash commands in WSL or the provider's Cloud Shell. You can prepare and submit jobs without a local GPU; training runs on the remote resource.

Create the four reviewed JSONL splits, a compatible Linux training image and an approved provider account with GPU quota. Set each walkthrough's `DMT_*` variables in the shell that submits the job. Keep provider credentials in the provider's authentication configuration or secret store.

<a id="study-protocol--prepare-the-same-domain-experiment-for-every-provider"></a>

### Prepare the same domain experiment for every provider

The [worked domain adoption guide](domain-adoption-guide.md) connects these compute recipes to natural-language intake, file mapping, synthetic coverage, model evaluation and application rollout. Use the provider lifecycle matrix below to decide what to record before leaving a remote job unattended.

<a id="study-protocol--provider-lifecycle-and-handoff-matrix"></a>

#### Provider lifecycle and handoff matrix

| Provider | Submission interface in the walkthrough | Durable handle to retain | Where the full export comes from | Completion and shutdown evidence |
|---|---|---|---|---|
| [AWS](remote-training/aws.md#direct-submission) | Boto3 `create_training_job` | Training job name/ARN and region | Returned `ModelArtifacts.S3ModelArtifacts` | Successful job, verified artifact; terminal state after `StopTrainingJob` when cancelled |
| [Azure](remote-training/azure.md#direct-submission) | `az ml job create`; SDK v2 `jobs.create_or_update` | Job name, workspace, compute and environment identity | Named `export` output via `az ml job download` | Successful command plus complete output; cancellation followed by node scale-down observation |
| [GCP](remote-training/gcp.md#direct-submission) | `gcloud ai custom-jobs create`; CustomJob REST | Full regional CustomJob resource name | Recorded Cloud Storage run prefix | `JOB_STATE_SUCCEEDED` and completed transfer; terminal observation after cancellation |
| [Modal](remote-training/modal.md#direct-submission) | Python `train.remote`; SDK/CLI | App/function-call identity, environment and Volume | Committed run export on the Volume | Successful call plus committed files; observe stopping separately when cancelling an App |
| [RunPod](remote-training/runpod.md#direct-submission) | REST v2 create, then SSH trainer | Immutable Pod ID, data center and chosen volume | Complete export copied from the recorded volume/path | Trainer exit alone is insufficient; observe stop/termination and retain output before Pod deletion |

For each route, record the exact staged input hashes, resolved image and package identities, GPU, timestamps, trainer exit, manifest/report, retrieval receipt and final resource state. A lost submission response requires checking the existing job/resource before another attempt. The managed controllers implement provider-specific uncertainty handling; illustrative direct scripts do not establish durable single-attempt execution. A provider terminal state also does not remove retained storage or its charges. See [provider selection](remote-training-guide.md#choosing-a-provider) and [managed remote admission](remote-training-guide.md#returned-model-admission).

1. Describe the domain, decision-time evidence, allowed outcomes, missing-evidence behavior and error costs. Obtain owner approval of the schema. Use [data intake](data-guide.md#data-contract) for manual examples, CSV/XLSX mapping and review.
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

<a id="study-protocol--build-and-qualify-a-linux-training-image"></a>

### Build and qualify a Linux training image

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

<a id="study-protocol--budget-storage-and-interruption-policy"></a>

### Budget, storage and interruption policy

Use the current regional SKU quote, GPU quota and provider availability before submitting. GPU-hours alone omit CPU/RAM/storage, startup/download time, registry traffic, checkpoint uploads, retained volumes, logs and egress. Record an expected maximum bill and wall-clock deadline. AWS/Azure/GCP/Modal provide job/function time limits in the recipes; RunPod requires external observation and explicit stop/delete in addition to a trainer timeout. Budget notifications are not a guaranteed hard billing cutoff.

Prefer immutable per-run input/output prefixes with least-privilege read/write identities. Separate code, domain data and secret configuration. Private/gated base weights may require a scoped token; do not put it in examples, notebook source or a container layer. Private egress/image access must be designed before uploading regulated data. Use the same real holdouts and measured quality targets when comparing providers; a faster GPU does not make one trained model more accurate by itself.

<a id="study-protocol--retrieve-qualify-and-host-the-export"></a>

### Retrieve, qualify and host the export

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

For a merged Unsloth export use `UnslothEngine("retrieved/export")` in the matching isolated environment. These library calls do not provide HTTP authentication, resource isolation or registry admission. Verify the bundle against its retained manifest and expected data/schema/model identity before loading; do not trust an arbitrary downloaded directory as an enterprise release. Apply evaluated thresholds and serve behind a qualified application boundary. [Serving contracts](application-hosting/README.md#native-model-serving)

**Platform-managed route:** freeze a remote study before training, pass its request to the trainer and use [authenticated independent admission](remote-training-guide.md#returned-model-admission) for the complete export. Standalone exports without that binding are not automatically admitted. The managed controllers support approval, submission, observation, stopping and retrieval; unattended watchdogs and real account execution need separate operational validation. Training does not deploy an endpoint: local serving and cloud prediction services require their own container, authentication and load checks.

To submit from the application, follow the managed guides for [AWS](remote-training/aws.md#application-managed-training), [Azure](remote-training/azure.md#application-managed-training), [GCP](remote-training/gcp.md#application-managed-training), [RunPod v2](remote-training/runpod.md#application-managed-training) or [Modal](remote-training/modal.md#application-managed-training). Run the [provider compatibility checks](verification.md#provider-compatibility-checks) with your selected account before a production study.

<a id="choosing-a-provider"></a>

## Choose infrastructure and estimate costs

<a id="choosing-a-provider--choose-remote-training-infrastructure"></a>

Choose a GPU training provider based on your cloud accounts, identity requirements, storage controls, GPU capacity and total study cost. Use this comparison before the [provider walkthroughs](remote-training-guide.md#study-protocol). Check quota, stock and regional pricing in your account before submission.

<a id="choosing-a-provider--match-the-provider-to-the-organization"></a>

### Match the provider to the organization

| Provider | Where the trainer runs | Operator interface | Persistent outputs | Main qualification question |
|---|---|---|---|---|
| [AWS SageMaker AI](remote-training/aws.md#direct-submission) | Custom training container | Boto3/REST `CreateTrainingJob` | S3 model artifact | Does the execution role, ECR digest and S3/KMS/network policy work in the selected region? |
| [Azure ML](remote-training/azure.md#direct-submission) | Command job on GPU compute | CLI v2, SDK v2 or REST | Named URI-folder output | Does the selected compute identity access the exact environment and datastore, and scale down after cancellation? |
| [GCP custom training](remote-training/gcp.md#direct-submission) | Custom container in a CustomJob | `gcloud ai custom-jobs`, REST or SDK | Cloud Storage export | Does the chosen service account have only the required storage/registry access and compatible GPU/machine quota? |
| [Modal](remote-training/modal.md#direct-submission) | GPU Function or application-managed Sandbox | Python SDK/CLI | Managed Volume | Is image/cache compatibility qualified, and is the storage location acceptable for the data? |
| [RunPod](remote-training/runpod.md#direct-submission) | GPU Pod | REST v2 and direct runtime access | Pod or independent network volume | Is the selected GPU/data center available, and will output survive Pod termination? |

Direct scripts let an operator submit and manage the job. The application's managed controllers add durable attempts, observation, stopping and local model validation. Choose the route that fits your operating process, then check account and image compatibility with a small study before uploading enterprise data.

Current primary contracts: [AWS job API](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html), [Azure training interfaces](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-train-model?view=azureml-api-2), [GCP custom-job instructions](https://cloud.google.com/vertex-ai/docs/training/create-custom-job), [Modal GPU configuration](https://modal.com/docs/guide/gpu), [RunPod Pod creation](https://docs.runpod.io/api-reference-v2/pods/create-a-pod).

<a id="choosing-a-provider--size-the-study-before-renting-capacity"></a>

### Size the study before renting capacity

Record the exact backbone, trainable components, precision, maximum complete prompt, number of questions, batch size, gradient accumulation and export format. Parameter count alone does not predict peak training memory. Measure model load, frozen-feature extraction, backward/optimizer steps and export separately using representative longest records. Cached-feature Vega training and encoder-LoRA training have different resource profiles.

When local VRAM is insufficient, run a small bounded qualification study on a compatible remote GPU first. Lowering batch size and using accumulation can help; changing precision, context or quantization changes the evaluated runtime and must be requalified. Increasing accumulation does not remove the memory cost of one overlong example. Refuse unsupported input lengths rather than silently truncating business evidence.

A multi-GPU VM does not pool memory automatically. Use a trainer's explicitly supported sharding/distribution path and test it. The current repository's qualified Laya/Unsloth profile does not establish distributed training. Avoid spot/retry execution until optimizer, scheduler, RNG, data position and unfinished-output recovery are implemented and exercised.

<a id="choosing-a-provider--compare-the-complete-cost"></a>

### Compare the complete cost

Use an illustrative budget equation, with current quotes supplied by the operator:

`study cost = billed compute hours × compute rate + persistent storage + transfers + image builds + retained logs/artifacts`

Include download/setup time, failed preflights and idle resources in billed compute where the provider charges them. Divide by accepted model versions or completed studies, not only training steps. Record currency, region, SKU, date, billing granularity and measured elapsed/billed time. This guide does not assert that one provider is universally cheapest.

For serving, compare ongoing infrastructure and fallback/review/error costs against hosted requests at matched quality and coverage. Remote training does not require remote serving: a verified export can return to the local machine if inference fits and the loader/runtime is qualified. Keep the complete backbone, tokenizer, head, calibration, schema and manifest rather than copying only a small adapter file.

<a id="choosing-a-provider--storage-deadlines-and-final-acceptance"></a>

### Storage, deadlines and final acceptance

Modal documents US durable storage for its managed Volumes, Images, Secrets and application logs, even when compute runs elsewhere. A compute-region selection is not a dataset-residency guarantee. Use an independently qualified regional storage path when requirements demand it. [Modal data residency](https://modal.com/docs/guide/data-residency), [compute and routing regions](https://modal.com/docs/guide/region-selection)

RunPod's current migration guide schedules REST v1 retirement for **15 November 2026**. Use the reviewed v2 request/response schema; a URL replacement cannot migrate v1 fields. A trainer timeout does not terminate its Pod. Network-volume persistence and Pod deletion are separate operations. [RunPod migration](https://docs.runpod.io/api-reference-v2/migrate-from-v1)

Freeze a unique input prefix, data hashes, code source, image digest and checkpoint revisions before submission. If creation times out, observe the same durable attempt; do not blindly create another paid job. Verify terminal execution separately from a cancellation acknowledgement. Retrieve a complete bounded export, independently check its manifest and held-out behavior, then obtain owner release approval. Retained storage and logs remain subject to the owner's retention policy after GPU shutdown.

A first real acceptance run should produce the provider resource ID, accepted identity/configuration, logs, measured peak memory, optimizer-update evidence, terminal/stop receipts, full export hashes, independent loader equivalence and held-out report.

<a id="returned-model-admission"></a>

## Validate and admit the returned model

<a id="returned-model-admission--bring-a-remotely-trained-decision-model-into-the-platform"></a>

Bring a remotely trained model into the local platform using **frozen study → operator staging → runtime validation → owner release**. Native Laya and Unsloth use four data files; Vega uses eight, including applicability negatives. Choose a [provider workflow](remote-training-guide.md#study-protocol) to train the frozen inputs. Managed controllers accept compatible Laya, Unsloth, Vega, Liquid, D3 and native Perplexity studies with matching declared images. Vega retains its paired data protocol; follow its [cloud setup](remote-training/vega.md) before managed launch.

Use this workflow **before training**. A pre-existing checkpoint without the frozen study request cannot enter this route by relabeling it or changing its report. Local training remains available through the normal worker. The current transfer route requires an authenticated operator with filesystem access to the workspace; a browser upload of multi-gigabyte weights is not implemented.

Liquid d1-3B TRL/Unsloth and omni use the [Liquid remote-training workflow](remote-training/liquid.md). Their returned native model code is accepted only at its reviewed hashes and approved configuration; arbitrary uploaded Python remains prohibited. Configure the matching isolated model interpreter before admission. [D3 Edge cloud exports](remote-training/d3.md) use `DMT_D3_PYTHON`, pinned native source and the calibrated CUDA device; bytecode caches are rejected.

[Native Perplexity cloud exports](remote-training/perplexity.md) use
`DMT_PERPLEXITY_PYTHON` for independent admission and CUDA serving. They retain
the complete backbone/readout, pinned publisher source and global temperature.
Provision the serving host's RAM/VRAM and artifact storage before retrieval;
cloud training alone does not provide an inference endpoint.

<a id="returned-model-admission--1-freeze-an-owner-approved-remote-study"></a>

### 1. Freeze an owner-approved remote study

For Vega, follow [the eight-file route below](#returned-model-admission--vega-eight-file-studies-and-independent-admission). Its request, trainer settings and uncertainty measurements differ from the native Laya/Unsloth examples in this section.

Create the project, review data, prepare its immutable four-role snapshot and save release/data-sufficiency criteria. In **Training jobs → Train on a cloud GPU**, select native Laya or Unsloth, model revision and training settings; record the approved provider, region/environment, GPU/instance identity and qualified image digest. Confirm that the provider may receive the domain data, then choose **Prepare remote training study**.

The same action is available through the authenticated API:

```json
{
  "kind": "remote-study",
  "request_key": "support-study-001",
  "config": {
    "snapshot": "YOUR_PREPARED_SNAPSHOT_ID",
    "backend": "laya-native",
    "model": "convaiinnovations/laya",
    "revision": "7b928d828b7b0e022f929d9bd2e44165aa270148",
    "epochs": 1,
    "batch_size": 2,
    "grad_accum": 4,
    "max_length": 256,
    "provider": "aws",
    "region": "us-east-1",
    "gpu": "ml.g5.xlarge",
    "image_digest": "YOUR_REGISTRY/trainer@sha256:YOUR_64_CHARACTER_DIGEST"
  }
}
```

POST this JSON to `/v1/projects/{project}/jobs`. Substitute actual IDs and a complete image digest. These settings illustrate a bounded encoder experiment; they do not establish domain quality, current GPU availability or a sufficient budget. An admin must request preparation. The first local training **or remote-study submission** permanently freezes that project's acceptance criteria, including when the job later fails or is cancelled. Use a successor project to revise the policy.

An ordinary worker handles this preparation without loading GPU packages. It verifies the snapshot and class sufficiency, writes the exact four JSONL files and seals `request.json` plus a deterministic ZIP. The request binds tenant/project, snapshot/schema, owner-policy ID/hash, selected configuration, package Python-source fingerprint and actual file-byte hashes. The fingerprint covers `decision_workbench/**/*.py`; it is not an image signature or a full dependency lock. Matching hashes/report fields establish declared-profile consistency; they do not cryptographically prove that a remote trainer executed those bytes or avoided holdout exposure. Controlled provider execution, attestations and audit remain required for that assurance.

<a id="returned-model-admission--2-download-and-train-the-frozen-inputs"></a>

### 2. Download and train the frozen inputs

After preparation succeeds, retrieve the study in the browser or GET `/v1/projects/{project}/remote-studies/{study}/download` with an admin credential. The study ID is the preparation job ID. Keep `request.json` unchanged, preserve all four files and retain their hashes outside the training resource. A recovered workspace can reconstruct the exact ZIP from its immutable database; a corrupted existing archive is refused.

Use the matching packaged source and qualified Linux image. A bare image tag, the old local package version number or a moving Git branch does not establish a matching source profile. Image provenance remains operator-attested until a provider adapter/attestation verifier exists. Freeze and test a separate Linux dependency profile; the executed Windows CUDA profile is not a portable lock.

Add the frozen request to the training command:

```bash
python -m decision_workbench.training.laya_runner \
  --data /job/study/data --out /job/export \
  --model convaiinnovations/laya \
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --epochs 1 --batch-size 2 --grad-accum 4 --max-length 256 \
  --study-request /job/study/request.json
```

Use the **settings actually recorded in your request**, rather than replacing them with this example. For Unsloth select `decision_workbench.training.unsloth_runner` and its recorded rank/full-precision/step settings. Native Laya rejects Unsloth-only options. The trainer checks frozen input hashes, source fingerprint and backend/configuration before downloading/loading the model. Reports record `source_sha256` and `remote_study_sha256`; both are required for admission.

For provider recipes that stage only a `data/` prefix, copy the unchanged request into `data/request.json` and transfer it alongside the four files. Add `--study-request` using the actual mount path:

| Provider | Additional request transfer and flag |
|---|---|
| AWS | Include it in the S3 input channel; `--study-request /opt/ml/input/data/domain/request.json` |
| Azure | Include it in the URI-folder input; `--study-request ${{inputs.domain}}/request.json` |
| GCP | Download/copy `data/request.json` separately from the recipe's `*.jsonl` staging; `--study-request /tmp/dmt-data/request.json` |
| Modal | Include it in the run's Volume data prefix; `--study-request /runs/runs/RUN_ID/data/request.json` |
| RunPod | SCP `request.json` separately from `*.jsonl`; `--study-request /workspace/RUN_ID/data/request.json` |

`load_splits` consumes the four named JSONL files; adding this request file does not change their bytes or split roles. Provider job metadata, secrets and transfer receipts remain separate from training examples. Observe the provider until terminal completion and retrieve the entire model export. Stop paid resources according to its guide even if training/admission fails.

<a id="returned-model-admission--3-retain-the-export-checksum-and-provider-receipt"></a>

### 3. Retain the export checksum and provider receipt

Retain the SHA-256 of the **source export's** `manifest.json` before copying or extracting it. Verify transfer against that checksum. The current importer accepts an extracted directory, avoiding automatic untrusted archive extraction; the provider's download/extraction procedure must preserve files and reject unsafe archive paths.

Save a separate receipt using exactly these fields:

```json
{
  "provider": "aws",
  "provider_job_id": "YOUR_ACTUAL_TRAINING_JOB_ID",
  "region": "us-east-1",
  "gpu": "ml.g5.xlarge",
  "image_digest": "YOUR_REGISTRY/trainer@sha256:YOUR_64_CHARACTER_DIGEST",
  "status": "succeeded",
  "manifest_sha256": "YOUR_RETAINED_EXPORT_MANIFEST_SHA256"
}
```

Provider/region/GPU/image must match the frozen study. Keep the real provider logs, measured resource usage and billing receipt in the operator's experiment record. This JSON is explicitly **operator-attested**: the platform does not query a provider to prove the job ran, verify its bill or establish a signed supply-chain attestation. Do not present a locally fabricated receipt as cloud acceptance.

<a id="returned-model-admission--4-stage-and-queue-admission-as-an-authenticated-operator"></a>

### 4. Stage and queue admission as an authenticated operator

Run from the platform environment on the workspace host. Set `DMT_OPERATOR_TOKEN` to a valid admin credential through your operator's secret mechanism. Use the same trusted key/JWT mappings as the API; credentials stay out of command arguments and receipt files. For a PostgreSQL workspace, supply its `DMT_DATABASE_URL`, matching `--database-schema` (or `DMT_DATABASE_SCHEMA`) and shared artifact settings; follow [separate-node admission](shared-artifacts-guide.md#s3-artifacts--retrieve-and-admit-remote-training-results-on-separate-nodes). `--home` then selects this operator node's local cache, not a new SQLite workspace.

```bash
domain-platform import-remote \
  --home data/workspace --keys-file /operator/workspace-keys.json \
  --project PROJECT_ID --study STUDY_ID \
  --export retrieved/export --manifest-sha256 RETAINED_SHA256 \
  --receipt provider-receipt.json --request-key support-import-001
```

For enterprise access tokens use the matching trusted `--sso-file` configuration instead of an unrelated key file. Staging checks the initial identity and reauthenticates before durable submission. A server-scoped project, prepared study and frozen owner policy are mandatory. The CLI queues work and prints the job ID; it does not activate a model.

The input directory must be private and stable while copied. The importer rejects linked/nonregular content, unapproved executable/pickle formats, extra/missing/modified files, wrong schema/backend/model/data/source/request identity and mismatched receipts. Staging is bounded to 2,048 files and 64 GiB. Its checked transfer deadline scales with the size bound, from ten minutes up to four hours. Manifest JSON is capped at 1 MiB and report JSON at 16 MiB before parsing. It copies into a fresh private workspace location, then verifies the copy. Failed staging removes that partial copy. Identical idempotency keys/receipts return the same job; changed requests conflict.

Inspect the current job before retrying an interrupted command, using the same idempotency key. A successful queued intake remains durable if the operator disconnects; an uncertain submission retains its input when the commit cannot be ruled out. Incoming copies and private logs require operator retention/cleanup. With shared artifacts configured, a different worker can restore the verified intake; personal SQLite intake stays on its workspace host. Browser weight transfers are not supported.

<a id="returned-model-admission--5-qualify-review-and-release"></a>

### 5. Qualify, review and release

Run the worker with the compatible model interpreter configured. Admission acquires the same GPU lease as local training, pauses resident inference through the existing scheduler, launches a bounded offline model subprocess and preserves private logs. Native Laya/Unsloth loaders reject executable model configuration; Liquid, D3 and native Perplexity accept only their hash-verified source and approved native configurations. Qualification loads the actual export, measures **all reserved validation/calibration/test records**, then loads it again and checks validation probabilities and decisions at the fixed backend tolerance. It does not refit calibration or change training/holdout membership.

The supplied validation/test metrics are replaced by measured local metrics. The original report is retained as `training_report.json`; the resulting portable bundle receives a new exact manifest. Registration preserves both source and admitted checksums, study/job identities, frozen policy and provider attestation. Failed/cancelled or expired-owner jobs cannot register an eligible model. An admission failure retains the incoming source for operator investigation and removes its unregistered model copy.

A successfully admitted model is **inactive**. Review its metrics and use the existing owner release action; frozen acceptance gates, full artifact verification, serving preload and atomic release revision still apply. Its API uses the same typed answers, probability validation, authentication and rollback path as local models. A permissive mechanical-fixture policy is not enterprise-quality acceptance.

Generic optimizer resume, automatic provider retries and signed provider attestations are not supplied by admission. Shared storage still requires the identity, retention and coordinated recovery controls described in its guide.

For application-managed submission and artifact retrieval, follow [AWS training](remote-training/aws.md#application-managed-training) or [GCP training](remote-training/gcp.md#application-managed-training). Both hand the export to this independent validation and release workflow. Check the selected account and image using [provider compatibility checks](verification.md#provider-compatibility-checks).

[Managed Azure training](remote-training/azure.md#application-managed-training) also retrieves the named job's Blob export and queues the same validation and owner-release workflow.

For managed RunPod studies, follow the [v2 Pod workflow](remote-training/runpod.md#application-managed-training). Output-ready/EXITED/ERROR is not terminal controller evidence: require bound termination, preserve unknown creation/deletion outcomes, and keep network-volume result/receipt identities through recovery. Volume content remains unversioned writer attestation; independent model admission and owner release still apply.

<a id="returned-model-admission--vega-eight-file-studies-and-independent-admission"></a>

### Vega eight-file studies and independent admission

Vega uses `domain-remote-vega-study-v1`. Start with the [Vega guide](vega-guide.md#vega-model), authenticated positive and negative review, paired reservations and explicit owner applicability limits. In Training jobs select Vega and its paired negative snapshot, then prepare a remote study using the approved provider/resource/image metadata. Preparation creates no compute and performs no data upload. Missing negatives or a required combined semantic audit refuse preparation. The exact pinned engine/backbone, sixty-family preflight, all eight input hashes and frozen owner policy are retained. With a [combined semantic reservation](reservation-guide.md#semantic-relatives--combined-positive-and-applicability-audits), both local and operator studies consume the derived positive copies and paired negatives; preserve that complete archive rather than substituting the original positive-only export.

The downloaded ZIP contains `request.json`, four positive `data/{role}.jsonl` files and four negative `data/gate_{role}.jsonl` files. The eight-file format has no four-file `data/manifest.json`. Preserve every byte. The shared cloud runner validates this paired archive and dispatches the Vega trainer; managed launch additionally requires a profile declaring `training_backends: ["vega"]`. Use an independently qualified Vega environment and the unchanged request; do not pass `--local-study-request`, which is reserved for a supervised local application worker.

For a study prepared with one epoch, batch sixteen, rank four, six maximum optimizer steps and offline assets, the matching command is:

```bash
python -m decision_workbench.training.vega_runner \
  --data /job/study/data --out /job/export \
  --study-request /job/study/request.json \
  --epochs 1 --batch-size 16 --rank 4 --max-steps 6 --offline
```

Use every setting recorded in your actual request, including nondefault learning rate and gate weight. Offline mode requires both complete pinned assets already cached; choose it before freezing the study. Normal standalone execution owns its resource lease. Model/input/options/source drift refuses before model imports. No gradient-accumulation or native encoder options apply to this profile. The default engine/backbone identity is the separately qualified pair documented in the Vega guide.

Retrieve the complete engine, backbone, domain adapter, calibration, metadata and report, retain the manifest checksum, and use the operator import command above with the matching provider receipt. A job receipt remains operator-attested; source hashes cannot prove remote execution or holdout nonexposure. The importer verifies positive and negative provenance, stages bounded ordinary files and queues independent admission. Browser weight upload is still unavailable.

The admission worker owns the shared GPU lease and loads the staged export in offline mode. It remeasures validation/calibration/test decisions, positive accepted fractions and per-question negative gate opening using the exported adapter threshold. The supplied applicability metrics are replaced. A second load compares positive and negative probabilities, decisions, abstention, conformal sets, adapter selection, unbound/upstream refusal and gate values. It retains `training_report.json` and seals a new manifest whose report binds the original source export and frozen study. Admission measures the existing calibration; it does not refit on the locked test.

Owner release still enforces both ordinary quality and positive-coverage/negative-applicability limits. The registered version replays its authenticated reservations and admission identities during release and recovery. Qualification of this route is separate from actual cloud/image execution and representative operational model quality.

For incomplete private intake copies left by a stopped operator process, use [owned local transfer cleanup](storage-maintenance-guide.md#transfer-stages). Completed intake IDs and recorded admission sources are preserved by that tool.
