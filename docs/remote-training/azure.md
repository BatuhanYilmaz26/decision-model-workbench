# Train on Azure Machine Learning

<a id="train-a-decision-model-with-azure-machine-learning"></a>

[Documentation](../README.md) · [Project overview](../../README.md)

Choose direct operator submission or the application-managed workflow below. Both require your approved account, a compatible training image and usable resources. App-managed runs additionally retain approvals, frozen study identity, status and returned-model validation.

## Choose a section

- [Submit directly through the provider](#direct-submission)

- [Manage training through the workbench](#application-managed-training)

<a id="direct-submission"></a>

## Submit directly through the provider

<a id="direct-submission--train-a-decision-model-with-azure-machine-learning"></a>

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Run a decision-model trainer as an Azure Machine Learning command job. This walkthrough stages four reviewed data splits, submits a GPU job and retrieves the complete export. Prepare the data and container using the [shared remote-training guide](../remote-training-guide.md#study-protocol).

Use this route for direct CLI or SDK submission. For application-managed approvals, job history, cancellation and Blob retrieval into local model validation, follow [managed Azure training](azure.md#application-managed-training).

<a id="direct-submission--workspace-identity-and-gpu-compute"></a>

### Workspace, identity and GPU compute

Use an existing approved Azure ML workspace with its storage and registry access. Install the Azure CLI `ml` extension, sign in and configure the target subscription/resource group/workspace. The submitter needs job/compute/environment permissions. The compute identity needs appropriate data and private-image access; validate the chosen network and secret configuration before uploading enterprise records.

```bash
az extension add --name ml
az login
az account set --subscription "$DMT_SUBSCRIPTION_ID"
az configure --defaults group="$DMT_RESOURCE_GROUP" workspace="$DMT_WORKSPACE"
az ml compute list-sizes --location "$DMT_REGION"
```

Select an available GPU SKU with approved quota and enough measured VRAM. `DMT_GPU_SIZE` names that SKU; it must not be a CPU-only VM. This creates a cluster that can scale down to zero:

```bash
az ml compute create --name decision-gpu --type amlcompute \
  --size "$DMT_GPU_SIZE" --min-instances 0 --max-instances 1
az ml environment create --name decision-trainer --version 1 --image "$DMT_TRAIN_IMAGE"
```

The environment references the qualified image digest in ACR or another approved registry. Choose a new environment version for changed dependencies, and adjust the job accordingly. For a stronger idle shutdown policy, the SDK's `AmlCompute` supports `idle_time_before_scale_down`; verify its configured value. [Compute provisioning](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-create-attach-compute-cluster?view=azureml-api-2), [compute CLI](https://learn.microsoft.com/en-us/cli/azure/ml/compute?view=azure-cli-latest), [environment CLI](https://learn.microsoft.com/en-us/cli/azure/ml/environment?view=azure-cli-latest)

<a id="direct-submission--define-and-submit-a-command-job"></a>

### Define and submit a command job

Save `azure-job.yml` beside a data-only `data/support/` containing the four reviewed JSONL files. CLI v2 uploads local input data to workspace storage; alternatively use a registered data version or approved datastore URI. The image already contains the project package, so the job does not upload the entire checkout.

```yaml
$schema: https://azuremlschemas.azureedge.net/latest/commandJob.schema.json
type: command
display_name: domain-laya-study
experiment_name: domain-decisions
compute: azureml:decision-gpu
environment: azureml:decision-trainer:1
inputs:
  domain:
    type: uri_folder
    path: ./data/support
    mode: download
outputs:
  export:
    type: uri_folder
    mode: upload
limits:
  timeout: 7200
command: >-
  python -m decision_workbench.training.laya_runner
  --data ${{inputs.domain}} --out ${{outputs.export}}
  --model convaiinnovations/laya
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148
  --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
```

```bash
az ml job create --file azure-job.yml --query name --output tsv
```

Retain the returned job name as `DMT_JOB_NAME`. `${{inputs.domain}}` and `${{outputs.export}}` are Azure ML substitutions; shell environment variables written inside YAML are not automatically expanded. Output `upload` mode stages local files and uploads them at job completion. The shared adapter refuses a pre-populated export directory. [Command-job schema](https://learn.microsoft.com/en-us/azure/machine-learning/reference-yaml-job-command?view=azureml-api-2), [training through CLI/SDK/REST](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-train-model?view=azureml-api-2)

For SDK v2 users, the equivalent primitives are `MLClient`, `command`, `Input(type="uri_folder", ...)`, `Output(type="uri_folder", ...)` and `client.jobs.create_or_update(job)`. The YAML path above provides the complete job definition without requiring a second generated template. For Unsloth, replace the module and add only that runner's supported options. Start without spot/retry execution: this project's trainer does not resume optimizer state.

<a id="direct-submission--observe-retrieve-and-cancel"></a>

### Observe, retrieve and cancel

```bash
az ml job show --name "$DMT_JOB_NAME"
az ml job stream --name "$DMT_JOB_NAME"
az ml job download --name "$DMT_JOB_NAME" --all --download-path retrieved/azure
```

Check terminal status and download the `export` named output, including manifest/report/calibration/tokenizer files. `stream` ends when the job terminates; an uploaded partial directory from a failed job is not a qualified model. Log the resolved environment/image and data asset identity. [Job-management CLI](https://learn.microsoft.com/en-us/cli/azure/ml/job?view=azure-cli-latest)

```bash
az ml job cancel --name "$DMT_JOB_NAME"
```

Wait for cancellation and verify that cluster nodes scale down. Workspace storage, registry, logs and retained data remain billable. Inspect retained prefixes before deleting any temporary output. Job cancellation does not delete data, and archiving a job does not establish GPU shutdown. No managed online endpoint is created by this training job; local or Azure serving is a separate qualification.

<a id="application-managed-training"></a>

## Manage training through the workbench

<a id="application-managed-training--managed-azure-training-through-the-application"></a>

Submit an owner-approved decision-model study to Azure Machine Learning from the application, monitor the job and retrieve its Blob export for local validation. The adapter targets Azure ML **2026-09-01** ARM and Blob **2026-04-06** APIs. For direct CLI or SDK submission, use the [Azure walkthrough](azure.md#direct-submission).

<a id="application-managed-training--prepare-the-approved-infrastructure"></a>

### Prepare the approved infrastructure

Use an existing approved Azure ML workspace, storage account/container, identity-based Blob datastore, ACR image environment and GPU compute cluster. This adapter does not create the workspace, registry, storage, environment or cluster. Those deployment/IAM/network/image prerequisites need independent operator qualification before domain-data transfer. [Cluster setup](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-create-attach-compute-cluster?view=azureml-api-2), [managed identities](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-use-managed-identities)

The first profile supports one dedicated Linux `Standard_NC4as_T4_v3`, `Standard_NC8as_T4_v3` or `Standard_NC16as_T4_v3` node. These approved sizes each have one T4 with 16 GB advertised accelerator memory; measured usable VRAM, driver/CUDA and trainer limits still need image qualification. [Microsoft size specifications](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series)

For [native Perplexity training](perplexity.md), the adapter also
accepts `Standard_ND96isr_H200_v5`. This dedicated node has eight H200 GPUs and
1,850 GiB host RAM; the current native trainer uses only one GPU process. Verify
the image, quota and free container scratch space before submitting.
[H200 specifications](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series)

It requires a cluster minimum of zero, maximum of one, explicitly disabled public SSH and the exact configured idle scale-down duration (1–300 seconds). Workspace and compute use their approved system-assigned managed identities. The environment version must reference the approved ACR image digest directly, with no automatic rebuild, build context or Conda overlay. The GPU image contains this exact package source and the separately qualified native Laya/Unsloth dependencies; the CPU API Dockerfile is a different image. Regional availability, quota, measured VRAM and image ABI remain real-provider acceptance tasks.

The workspace, compute and storage account must match the profile region and subscription/resource group. The workspace storage/registry IDs and datastore account/container must match the approved resources. Datastore credentials must be `None`, its protocol HTTPS, endpoint `core.windows.net`, and service data access must use the workspace system-assigned identity. The approved storage account disables public Blob access and requires TLS 1.2. Profile identifiers describe resources; they are not storage keys or SAS tokens. [Identity-based datastore access](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-read-write-data-v2?view=azureml-api-2)

This profile supports Azure public-cloud endpoints, dedicated single-node AmlCompute and system-assigned identities. It does not support sovereign clouds, user-assigned workload identities, VNet/private compute, serverless or distributed jobs, spot/resume, custom Docker resource arguments or additional accelerator profiles. Check effective IAM, networking and image compatibility in your workspace before transferring domain data.

<a id="application-managed-training--configure-credentials-and-resource-limits"></a>

### Configure credentials and resource limits

Install the selected transport extra in the API and relevant worker environment:

```bash
python -m pip install -e '.[ui,platform,azure]'
```

Copy [the illustrative Azure profile](../../examples/cloud/azure-profiles.json) into the operator-owned `domain-cloud-profiles-v1` file alongside other approved providers. Replace every fictional identity/resource, tenant allowlist and zero image/source hash. A zero source hash deliberately cannot approve a current study. Set `DMT_CLOUD_PROFILES_FILE` in the API/worker processes; entry points read process variables rather than automatically loading `.env.example`.

`account_id` is the subscription UUID; `tenant_id` is the Entra directory UUID; `operator_application_id` is the selected application/client UUID. `workspace_principal_id` and `compute_principal_id` identify the managed identities. Store the application's credential only in `<credential_prefix>_CLIENT_SECRET`, such as `DMT_CLOUD_AZURE_CLIENT_SECRET`. API/UI requests and model exports contain no provider secret. Compose passes the credential to the server/worker environment, not the browser. Integrate this configuration with your enterprise secret store and rotation process.

The transport requests scoped client-credentials tokens from the fixed directory-specific Microsoft OAuth endpoint, independently for ARM and Storage. It refreshes expired cached tokens within an action and selects the current secret on each new action. Ambient CLI credentials, SDK endpoint overrides, interactive authentication and account-key/SAS fallback are not used. HTTP redirects and automatic request/token retries are disabled. [Client-credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)

The operator application needs approved subscription/resource inspection, ML job read/write/cancel and private input/output Blob access. The workspace/compute identities need appropriate datastore, image and platform access. Assign effective least-privilege roles through the enterprise administrator and validate them on real resources; a broad Owner role is not a prerequisite prescribed by the project. Client-secret mode is an implemented starting route, not completion of enterprise workload-identity support.

Limits include a 60–14,400-second running timeout, 1–100 locally reserved runs per rolling day, 1–4 unresolved local runs and a bounded artifact size up to 64 GiB. Shared local quotas group tenant/profile aliases by provider, subscription and region. They do not cover other workspaces/direct submissions or enforce a global monetary budget.

<a id="application-managed-training--freeze-approve-and-submit"></a>

### Freeze, approve and submit

1. Use the [frozen-study workflow](../remote-training-guide.md#returned-model-admission) to reserve reviewed train/validation/calibration/test data and owner criteria. Select `azure` and the exact region, GPU SKU and image digest from the profile.
2. Select the completed study and managed profile in the owner training view. Explicitly approve both data transfer and paid compute. Approval creates a durable local reservation and allocates no remote training resource.
3. Submit the approved run. Before mutations, the worker verifies the enabled subscription/tenant, workspace identities, GPU/scaling configuration, environment image/version, private regional storage and identity-based datastore.
4. It commits an upload-attempt marker, refuses an existing/inaccessible output object, and creates the unique input BlockBlob with `If-None-Match: *` and Content-MD5. It verifies the streamed SHA-256, returned MD5/ETag and retains any reported Blob version.
5. It commits a create-attempt marker, checks that the fixed `dmt-<run_id>` ARM job does not exist, and issues exactly one job PUT. A 201 creation response is accepted only after full identity/configuration binding. A 200 update response, timeout or other uncertainty remains unresolved and is never automatically replayed.

Azure uses a create-or-update endpoint. The adapter sends `If-None-Match: *` and refuses a preexisting resource, but the published job schema does not promise atomic conditional creation against other writers. Restrict the job namespace through IAM and operator ownership, and test concurrent-create behavior in your workspace before relying on it. [Published job API](https://learn.microsoft.com/en-us/rest/api/azureml/jobs/create-or-update?view=rest-azureml-2026-09-01), [dated request contract](../../tests/fixtures/provider-contracts/cloud-azure-contract-2026-10-11.json)

A lost response is reconciled with GET of the same named job. Observation requires its resource/name, command, image-environment version, compute, single replica, runtime limit, managed identity and exact input/output bindings to match the frozen plan. It rejects unapproved code/components, distribution, queue/resource overrides, environment variables and interactive/custom services. Standard Studio/Tracking links are recognized as metadata and never followed. Duration comparisons accept equivalent ISO values such as `PT7200S` and `PT2H`.

The cloud API routes, explicit approval/action bodies, tenant/project authorization, current-revision fencing and immutable histories are shared with [AWS](aws.md#application-managed-training) and [GCP](gcp.md#application-managed-training). Public resource projections omit credential selectors and tenant allowlists. A changed/revoked public profile blocks further managed operations and requires controlled operator recovery.

<a id="application-managed-training--train-on-ordinary-local-disk"></a>

### Train on ordinary local disk

The command invokes `python -m decision_workbench.training.azure_runner` with the AML-downloaded `uri_file` input and named `uri_file` Upload output. The wrapper passes the exact archive/request hashes to the common six-file frozen-study runner, which validates source, schema, snapshot, split bytes and supported trainer settings before running native Laya or Unsloth. It trains and qualifies its export on ordinary local disk, then packages bounded regular USTAR files into the named output. AML collects/uploads that file. [Input/output delivery modes](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-manage-inputs-outputs-pipeline?view=azureml-api-2)

The input receipt preserves ETag/version evidence, but AML's datastore input reads the declared unique path; this adapter does not promise a generation-pinned AML input read. The exact frozen SHA-256 check refuses changed bytes before training. Protect the unique prefix from unrelated writers. Provider upload/collection behavior, default readback fields, retry behavior, mounted output paths and container permissions need actual Azure qualification. The trainer starts fresh and does not implement generic optimizer-state resume.

The shared packager bounds both expanded files and compressed output, refuses nonregular files and removes only an incomplete/newly created archive on failure. A preexisting output is preserved and refused. This fixture-verified packaging contract does not qualify an actual Linux/Azure image or returned model. The Azure wrapper tests substitute the trainer; they do not represent GPU execution.

<a id="application-managed-training--observe-cancel-and-retrieve"></a>

### Observe, cancel and retrieve

Queued/preparing/running/finalizing provider states map to `InProgress`; cancellation request maps to `Stopping`; completed/failed/canceled map to `Completed`/`Failed`/`Stopped`. Other states require investigation and keep local capacity held. A successful provider status is still subject to exact artifact and independent model qualification.

Cancellation binds the named job before POST and accepts the documented 200/202 response as a request acknowledgement. It never follows an arbitrary Location header, and it keeps the run unresolved until observation of the same terminal job. Cancelling a local control worker does not cancel Azure compute. [Cancel contract](https://learn.microsoft.com/en-us/rest/api/azureml/jobs/cancel?view=rest-azureml-2026-09-01)

Retrieval binds the approved account/container/run output URI, requires bounded BlockBlob metadata, and pins the reported Blob version or ETag with conditional GET. It checks actual size, optional provider MD5 and recorded SHA-256; no signed producer attestation is implied. Transfer/extraction have checked deadlines, not a guaranteed operating-system wall-clock deadline. The safe extractor rejects linked/extended/traversal/executable content. [Blob retrieval](https://learn.microsoft.com/en-us/rest/api/storageservices/get-blob)

The existing independent `remote-import` worker must reload the real model and measure held-out metrics before inactive registration. Owner release remains separate. Fixture/fabricated weights are never loaded or served. Cloud control observations remain in cloud history; imported receipts keep their operator-attestation semantics.

<a id="application-managed-training--cluster-shutdown-and-recovery"></a>

### Cluster shutdown and recovery

A terminal job does not prove that the existing cluster has zero nodes. Observation retains current/target node counts and allocation state as readback evidence. Idle nodes can remain billable until scale-down; other submissions, cluster/storage/registry/log retention and related resources are outside this job's lifecycle. Verify actual zero-node behavior and provider billing separately. This adapter does not delete the cluster or enterprise data.

Backup/restore validates the frozen study/profile, immutable input receipt, named job identity and history chain. Interrupted local jobs do not free unknown upstream compute capacity. Restore credentials separately and observe the same resource; automated safe profile-change recovery, overdue watchdogs and monetary reconciliation remain requirements.

See the [API reference metadata dated 11 October](../../research/provenance/cloud-azure-sources-2026-10-11.json) for source versions and [provider compatibility checks](../verification.md#provider-compatibility-checks) before production use.
