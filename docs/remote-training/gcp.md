# Train on Google Cloud

<a id="train-a-decision-model-on-google-cloud"></a>

[Documentation](../README.md) · [Project overview](../../README.md)

Choose direct operator submission or the application-managed workflow below. Both require your approved account, a compatible training image and usable resources. App-managed runs additionally retain approvals, frozen study identity, status and returned-model validation.

## Choose a section

- [Submit directly through the provider](#direct-submission)

- [Manage training through the workbench](#application-managed-training)

<a id="direct-submission"></a>

## Submit directly through the provider

<a id="direct-submission--train-a-decision-model-on-google-cloud"></a>

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Run the repository's Laya trainer on a Google Cloud GPU using a custom container. This walkthrough stages four reviewed data splits in Cloud Storage, submits a CustomJob with `gcloud ai custom-jobs`, and downloads the complete trained model export. [Google Cloud custom training](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/create-custom-job)

Use the commands below for direct operator submission. For application-managed approvals, job history, cancellation and local validation of returned weights, follow [managed GCP training](gcp.md#application-managed-training).

<a id="direct-submission--prepare-the-project-and-data"></a>

### Prepare the project and data

Prepare the data and image using the [shared remote-training guide](../remote-training-guide.md#study-protocol). You need a Google Cloud project, a GPU-capable region, a private Cloud Storage bucket, an Artifact Registry image and the Google Cloud CLI. Enable the training, storage and registry APIs. Give the submitter permission to create, inspect and cancel CustomJobs and act as the selected training service account. Give that service account the required bucket, image and logging access. Confirm regional GPU quota before submission.

Set these variables in your submission shell:

| Variable | Value |
|---|---|
| `DMT_GCP_PROJECT` | Project ID that owns the job |
| `DMT_REGION` | Region with the required GPU quota |
| `DMT_GCS_BUCKET` | Private bucket name, without `gs://` |
| `DMT_RUN_ID` | Unique run identifier used for input/output prefixes |
| `DMT_TRAIN_IMAGE` | Prepared Artifact Registry image digest URI |
| `DMT_TRAIN_SERVICE_ACCOUNT` | Training service-account email |

Authenticate and upload the four reviewed JSONL files under a new run prefix:

```bash
gcloud auth login
gcloud config set project "$DMT_GCP_PROJECT"
gcloud storage cp data/support/*.jsonl "gs://$DMT_GCS_BUCKET/decision-runs/$DMT_RUN_ID/data/"
```

The job below uses one T4 on `n1-standard-8` as an initial encoder study configuration; verify regional support and actual memory adequacy. A different accelerator may require a different supported machine type. Neither a hosted Gemini tuning endpoint nor generic text SFT implements Laya's decision head.

<a id="direct-submission--create-a-customjobspec"></a>

### Create a CustomJobSpec

Generate `gcp-job.json` using the following operator-side Python. Set `DMT_TRAIN_IMAGE` to the qualified Artifact Registry image digest and `DMT_TRAIN_SERVICE_ACCOUNT` to your approved service account email. `DMT_RUN_ID` should be a unique simple identifier; the output prefix must not already contain an export.

```python
import json
import os
from pathlib import Path

training_command = r'''set -euo pipefail
mkdir -p /tmp/dmt-data
cp /gcs/"$DMT_GCS_BUCKET"/decision-runs/"$DMT_RUN_ID"/data/*.jsonl /tmp/dmt-data/
python -m decision_workbench.training.laya_runner \
  --data /tmp/dmt-data --out /tmp/dmt-export \
  --model convaiinnovations/laya \
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
DEST=/gcs/"$DMT_GCS_BUCKET"/decision-runs/"$DMT_RUN_ID"/export
test ! -e "$DEST"
cp -R /tmp/dmt-export "$DEST"
sha256sum /tmp/dmt-export/manifest.json > "$DEST/_UPLOAD_COMPLETE"
'''
spec = {
    "serviceAccount": os.environ["DMT_TRAIN_SERVICE_ACCOUNT"],
    "scheduling": {"timeout": "7200s", "disableRetries": True},
    "workerPoolSpecs": [{
        "machineSpec": {
            "machineType": "n1-standard-8",
            "acceleratorType": "NVIDIA_TESLA_T4", "acceleratorCount": 1,
        },
        "replicaCount": 1,
        "diskSpec": {"bootDiskType": "pd-ssd", "bootDiskSizeGb": 100},
        "containerSpec": {
            "imageUri": os.environ["DMT_TRAIN_IMAGE"],
            "command": ["bash", "-lc"], "args": [training_command],
            "env": [{"name": n, "value": os.environ[n]}
                    for n in ["DMT_GCS_BUCKET", "DMT_RUN_ID"]],
        },
    }],
}
Path("gcp-job.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
```

Save this code outside the image and run it locally; it only writes the job specification. Submit it to start paid GPU training:

```bash
gcloud ai custom-jobs create --region="$DMT_REGION" \
  --display-name="$DMT_RUN_ID" --config=gcp-job.json
```

The `--config` file contains **CustomJobSpec**, not a `jobSpec` wrapper. REST submission instead uses `{"displayName": ..., "jobSpec": spec}` with an authenticated `POST` to `https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT/locations/REGION/customJobs`. Retain the returned resource name/job ID. [CLI and REST examples](https://cloud.google.com/vertex-ai/docs/training/create-custom-job), [CLI create reference](https://cloud.google.com/sdk/gcloud/reference/ai/custom-jobs/create)

Serverless training mounts accessible buckets under `/gcs/`, but those paths are not POSIX compliant. The recipe therefore copies data and trains/qualifies the export on local disk, then uploads the completed directory. The explicit final upload marker is external transfer metadata, not a model file; remove it from the downloaded export after retaining its checksum receipt, so it does not conflict with the model's exact file inventory. `AIP_MODEL_DIR` is a `gs://` URI when `baseOutputDirectory` is configured, not a directory accepted directly by this trainer. [Training code and Cloud Storage rules](https://cloud.google.com/vertex-ai/docs/training/code-requirements)

<a id="direct-submission--observe-retrieve-and-cancel"></a>

### Observe, retrieve and cancel

```bash
gcloud ai custom-jobs describe "$DMT_JOB_ID" --region="$DMT_REGION"
gcloud storage cp --recursive \
  "gs://$DMT_GCS_BUCKET/decision-runs/$DMT_RUN_ID/export" retrieved/gcp/
```

Inspect Cloud Logging and wait for `JOB_STATE_SUCCEEDED`. Download only from the recorded run prefix, retain the upload checksum and verify the export inventory/report. A failed upload or cancelled job is not a completed study even if some files exist. For Unsloth, change the command module/options while preserving local staging and split isolation.

```bash
gcloud ai custom-jobs cancel "$DMT_JOB_ID" --region="$DMT_REGION"
```

Observe a terminal cancellation/success/failure state. Training compute is job-scoped; Cloud Storage, registry and logs remain billable. Delete only explicitly selected temporary data under the retention policy. This CustomJob does not create a prediction endpoint or import the export into the local platform registry. [Cancellation reference](https://cloud.google.com/sdk/gcloud/reference/ai/custom-jobs/cancel)

<a id="application-managed-training"></a>

## Manage training through the workbench

<a id="application-managed-training--managed-gcp-training-through-the-application"></a>

Submit an approved frozen study to a Google Cloud CustomJob, monitor its state and retrieve the Cloud Storage export for local model validation. The adapter uses Google-auth and regional REST endpoints. For direct submission with `gcloud` or REST, use the [GCP walkthrough](gcp.md#direct-submission).

<a id="application-managed-training--configure-an-approved-operator-profile"></a>

### Configure an approved operator profile

Install the optional authentication/transport dependencies in the API and relevant worker environments:

```bash
python -m pip install -e '.[ui,platform,gcp]'
```

Start from [the illustrative GCP profile](../../examples/cloud/gcp-profiles.json). Replace its project number (`account_id`), project ID, regional bucket, service-account emails, image digest, tenant allowlist and package source hash. The zero hashes deliberately cannot approve a current study. Add the profile to the same `domain-cloud-profiles-v1` file used for AWS, and set `DMT_CLOUD_PROFILES_FILE` in the API/worker processes. Profile IDs must be unique across providers. These entry points read process variables; they do not load `.env.example` automatically.

This profile supports one `NVIDIA_TESLA_T4` on `n1-standard-4/8/16/32`, or eight
`NVIDIA_H200_141GB` accelerators on `a3-ultragpu-8g` with a Hyperdisk Balanced
boot disk. The latter is the declared profile for [native Perplexity](perplexity.md),
whose current trainer uses one GPU process while the allocated machine has eight.
The adapter uses standard scheduling and a timeout up to four hours. It requires
an Artifact Registry digest in the same project/region and a regional bucket
owned by that project. Check quota, scheduling availability and actual free
memory/storage. Distributed launch, spot/resume, persistent resources, VPC/PSC,
CMEK, cross-project resources and other machine/accelerator combinations remain
outside this profile. [Compute and disk combinations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/configure-compute)
· [CustomJobSpec and scheduling](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/CustomJobSpec)

Configure an operator service-account JSON key privately through `<credential_prefix>_CREDENTIALS_FILE`; the example prefix is `DMT_CLOUD_GCP`. The adapter requires the JSON project and email to match the profile, pins the standard OAuth token endpoint and refuses another Google universe. It does not select ambient operator credentials, browser credentials or an arbitrary endpoint. The key is loaded for each action, and Google-auth refreshes its scoped access token when needed. Keyless workload identity, impersonation, secret-store integration and enterprise credential lifecycle qualification remain required work. Do not commit the key or place it in a model/study export.

For Compose, place the private operator file in the mounted configuration directory and set `DMT_CLOUD_GCP_CREDENTIALS_FILE=/run/config/gcp-operator.json`. The server/worker environment anchor carries this path; the web process receives no cloud credential variables. The API Dockerfile includes cloud control dependencies and remains a CPU application image. A qualified GPU training image is a separate prerequisite.

Before mutations, the adapter checks the project ID/number and ACTIVE lifecycle through Cloud Resource Manager, then checks bucket ownership and region through Storage metadata. The operator identity needs the corresponding project/bucket read permissions, object create/get permissions, CustomJob create/list/get/cancel permissions, and permission to act as the training service account. The training identity needs approved input-object reads, output-object creation, registry access and required platform/logging access. Confirm effective IAM and regional quota separately; no blanket owner role is prescribed. [Project metadata](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/get), [bucket metadata](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/get)

<a id="application-managed-training--freeze-approve-and-submit"></a>

### Freeze, approve and submit

1. Prepare a reviewed snapshot and freeze the owner release criteria through the [remote-study workflow](../remote-training-guide.md#returned-model-admission). Choose `gcp` and exactly the region, accelerator identity and image digest from the operator profile. Machine/volume/service-account configuration comes from the owner-approved profile.
2. In the owner's training view, select the completed study and matching profile, then explicitly approve both data transfer and paid compute. API approval creates the immutable run and local capacity reservation; it allocates no remote compute.
3. Submit the approved run. The independent worker writes an upload-attempt event before conditionally creating the unique `inputs/study.zip` object (`ifGenerationMatch=0`). It verifies returned object identity, size and MD5, and retains SHA-256 plus the immutable input generation. MD5 is transport-corruption evidence; the frozen study SHA-256 remains the data identity.
4. The worker writes a create-attempt event before the single CustomJob POST. It requests one replica, the pinned image, explicit training service account, bounded running time, `disableRetries=true`, `restartJobOnWorkerRestart=false`, standard scheduling and no interactive access. It retains the provider-assigned numeric job resource name. No mutation is automatically retried.

The API routes and action bodies are shared with [managed AWS training](aws.md#application-managed-training). Owner/tenant/project authentication, literal-true approvals, current-revision checks, current profile availability and immutable resource bindings apply to both providers. Profiles are public projections without tenant allowlists or credential selectors.

Google assigns CustomJob resource names; the create API has no caller-selected job ID in the reviewed contract. A lost response therefore leaves the run unresolved. Observation lists at most two jobs using the frozen `dmt_run` label and display name, requires exactly one result with no further page, then fetches and binds its complete approved configuration. An absent, duplicate, changed or unsupported result keeps capacity reserved. Once a resource name is recorded, observation and cancellation cannot switch to another numeric job. A display name or label alone is insufficient identity evidence. [Reviewed CustomJob methods](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest), [dated machine-readable API contract](../../tests/fixtures/provider-contracts/cloud-gcp-contract-2026-10-11.json)

<a id="application-managed-training--run-the-actual-decision-trainer"></a>

### Run the actual decision trainer

The requested entrypoint is `python -m decision_workbench.training.gcp_runner`. Its arguments contain the frozen public configuration, exact archive/request hashes and input generation; they contain no operator key. The approved image must contain this exact package source, Google-auth/HTTPX and the separately qualified native Laya or Unsloth GPU dependencies.

Inside the training container only, Google-auth obtains the workload credential from the fixed compute-metadata endpoint and checks its service-account email against the approved training identity. The wrapper downloads the exact input generation to ordinary local disk, checks size/MD5/SHA, and invokes the common [six-file frozen-study runner](aws.md#application-managed-training). That runner verifies package/input/snapshot/schema identities and runs the actual supported trainer before its export/report checks. Training and reload use local disk rather than GCS FUSE. [Training code requirements](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/code-requirements)

After successful trainer/export qualification, the wrapper builds a regular-file USTAR gzip archive with bounded size/count and conditionally uploads `outputs/model.tar.gz` to the unique run prefix. It verifies the uploaded object's checksum/size/generation before returning success. A failed or uncertain upload cannot be promoted to a completed export. There is no generic optimizer-state resume or automatic replay.

Before using the wrapper with enterprise data, validate your training image, workload identity, IAM, network access and complete export with a small non-sensitive job. Follow the [provider compatibility checks](../verification.md#provider-compatibility-checks), including independent model reload and cancellation checks.

<a id="application-managed-training--observe-cancel-and-retrieve"></a>

### Observe, cancel and retrieve

Owner observation maps queued/pending/running to `InProgress`, cancelling to `Stopping`, and succeeded/failed/cancelled to `Completed`/`Failed`/`Stopped`. Other states fail conservatively and need operator investigation. The original provider state is retained with the normalized state. The running timeout does not prove a maximum queueing duration; unattended observation and overdue reconciliation remain required.

Stop first binds the original job, requests its cancellation and leaves capacity held. A local worker cancellation or successful cancel request does not prove upstream GPU shutdown. Observe a matching terminal provider state before treating compute as finished. [CustomJob cancellation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest)

Retrieval requires a previously observed matching completed job. It binds the exact approved output URI, reads bounded metadata, pins the reported object generation, downloads with size/MD5/SHA checks, and uses the shared narrow safe extractor. Composite outputs without the required MD5 metadata are refused by this initial profile. Checked transfer deadlines and socket timeouts are not a hard operating-system wall-clock deadline. Successful provider status does not replace artifact or model qualification. [Object retrieval](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get), [checksum semantics](https://docs.cloud.google.com/storage/docs/data-validation)

The retrieved directory queues `remote-import`. Its loader measures held-out metrics and reload equivalence before registering an inactive model; owner release is a separate step. Cloud history retains observation and transfer records. Import receipts are operator attestations rather than signed producer or image-origin proofs.

<a id="application-managed-training--recovery-and-cost-controls"></a>

### Recovery and cost controls

Backup/restore checks the frozen profile, study, input receipt, provider identity and state history. Recovery retains unresolved cloud runs and interrupts copied local jobs. Restore credentials separately and reconcile the original job. Changing or revoking the operator profile blocks further managed actions; recover the approved configuration before using those controls.

Quotas are transactional inside one local workspace, grouped by **provider, account/project number and region** across tenant/profile aliases, using the strictest configured limits. Unresolved attempts do not expire. This is not a global cloud-account budget or monetary ceiling. Registry/storage/log retention costs, external submissions, tariffs and real provider billing require separate controls and measurements. Object cleanup is not automatic.

Complete the [provider compatibility checks](../verification.md#provider-compatibility-checks) for the image, workload identity, job lifecycle, returned export and total cost. Monitor the run and provider budget separately; this workspace has no unattended watchdog or account-wide spending ceiling.

Use [RunPod](runpod.md#application-managed-training) or [Modal](modal.md#application-managed-training) only with their separate resource and storage contracts; their managed workflows share owner approvals and independent admission.
