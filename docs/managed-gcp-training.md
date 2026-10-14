# Managed GCP training through the application

[All guides](README.md) · [Project overview](../README.md)

Submit an approved frozen study to a Google Cloud CustomJob, monitor its state and retrieve the Cloud Storage export for local model validation. The adapter uses Google-auth and regional REST endpoints. For direct submission with `gcloud` or REST, use the [GCP walkthrough](remote-training/gcp.md).

## Configure an approved operator profile

Install the optional authentication/transport dependencies in the API and relevant worker environments:

```bash
python -m pip install -e '.[ui,platform,gcp]'
```

Start from [the illustrative GCP profile](../examples/cloud/gcp-profiles.json). Replace its project number (`account_id`), project ID, regional bucket, service-account emails, image digest, tenant allowlist and package source hash. The zero hashes deliberately cannot approve a current study. Add the profile to the same `domain-cloud-profiles-v1` file used for AWS, and set `DMT_CLOUD_PROFILES_FILE` in the API/worker processes. Profile IDs must be unique across providers. These entry points read process variables; they do not load `.env.example` automatically.

This profile supports one `NVIDIA_TESLA_T4` on `n1-standard-4/8/16/32`, an SSD boot volume, on-demand scheduling and a running timeout up to four hours. It requires an Artifact Registry digest in the same project/region and a regional bucket owned by that project. Check regional quota and measure memory with your image and longest inputs. Distributed jobs, spot/resume, persistent resources, VPC/PSC, CMEK, cross-project resources and other accelerators are outside this adapter's supported profile. [CustomJobSpec and scheduling](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/CustomJobSpec)

Configure an operator service-account JSON key privately through `<credential_prefix>_CREDENTIALS_FILE`; the example prefix is `DMT_CLOUD_GCP`. The adapter requires the JSON project and email to match the profile, pins the standard OAuth token endpoint and refuses another Google universe. It does not select ambient operator credentials, browser credentials or an arbitrary endpoint. The key is loaded for each action, and Google-auth refreshes its scoped access token when needed. Keyless workload identity, impersonation, secret-store integration and enterprise credential lifecycle qualification remain required work. Do not commit the key or place it in a model/study export.

For Compose, place the private operator file in the mounted configuration directory and set `DMT_CLOUD_GCP_CREDENTIALS_FILE=/run/config/gcp-operator.json`. The server/worker environment anchor carries this path; the web process receives no cloud credential variables. The API Dockerfile includes cloud control dependencies and remains a CPU application image. A qualified GPU training image is a separate prerequisite.

Before mutations, the adapter checks the project ID/number and ACTIVE lifecycle through Cloud Resource Manager, then checks bucket ownership and region through Storage metadata. The operator identity needs the corresponding project/bucket read permissions, object create/get permissions, CustomJob create/list/get/cancel permissions, and permission to act as the training service account. The training identity needs approved input-object reads, output-object creation, registry access and required platform/logging access. Confirm effective IAM and regional quota separately; no blanket owner role is prescribed. [Project metadata](https://docs.cloud.google.com/resource-manager/reference/rest/v1/projects/get), [bucket metadata](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/get)

## Freeze, approve and submit

1. Prepare a reviewed snapshot and freeze the owner release criteria through the [remote-study workflow](remote-admission-guide.md). Choose `gcp` and exactly the region, accelerator identity and image digest from the operator profile. Machine/volume/service-account configuration comes from the owner-approved profile.
2. In the owner's training view, select the completed study and matching profile, then explicitly approve both data transfer and paid compute. API approval creates the immutable run and local capacity reservation; it allocates no remote compute.
3. Submit the approved run. The independent worker writes an upload-attempt event before conditionally creating the unique `inputs/study.zip` object (`ifGenerationMatch=0`). It verifies returned object identity, size and MD5, and retains SHA-256 plus the immutable input generation. MD5 is transport-corruption evidence; the frozen study SHA-256 remains the data identity.
4. The worker writes a create-attempt event before the single CustomJob POST. It requests one replica, the pinned image, explicit training service account, bounded running time, `disableRetries=true`, `restartJobOnWorkerRestart=false`, standard scheduling and no interactive access. It retains the provider-assigned numeric job resource name. No mutation is automatically retried.

The API routes and action bodies are shared with [managed AWS training](managed-cloud-training.md). Owner/tenant/project authentication, literal-true approvals, current-revision checks, current profile availability and immutable resource bindings apply to both providers. Profiles are public projections without tenant allowlists or credential selectors.

Google assigns CustomJob resource names; the create API has no caller-selected job ID in the reviewed contract. A lost response therefore leaves the run unresolved. Observation lists at most two jobs using the frozen `dmt_run` label and display name, requires exactly one result with no further page, then fetches and binds its complete approved configuration. An absent, duplicate, changed or unsupported result keeps capacity reserved. Once a resource name is recorded, observation and cancellation cannot switch to another numeric job. A display name or label alone is insufficient identity evidence. [Reviewed CustomJob methods](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest), [dated machine-readable API contract](../tests/fixtures/provider-contracts/cloud-gcp-contract-2026-10-11.json)

## Run the actual decision trainer

The requested entrypoint is `python -m decision_workbench.training.gcp_runner`. Its arguments contain the frozen public configuration, exact archive/request hashes and input generation; they contain no operator key. The approved image must contain this exact package source, Google-auth/HTTPX and the separately qualified native Laya or Unsloth GPU dependencies.

Inside the training container only, Google-auth obtains the workload credential from the fixed compute-metadata endpoint and checks its service-account email against the approved training identity. The wrapper downloads the exact input generation to ordinary local disk, checks size/MD5/SHA, and invokes the common [six-file frozen-study runner](managed-cloud-training.md). That runner verifies package/input/snapshot/schema identities and runs the actual supported trainer before its export/report checks. Training and reload use local disk rather than GCS FUSE. [Training code requirements](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/code-requirements)

After successful trainer/export qualification, the wrapper builds a regular-file USTAR gzip archive with bounded size/count and conditionally uploads `outputs/model.tar.gz` to the unique run prefix. It verifies the uploaded object's checksum/size/generation before returning success. A failed or uncertain upload cannot be promoted to a completed export. There is no generic optimizer-state resume or automatic replay.

Before using the wrapper with enterprise data, validate your training image, workload identity, IAM, network access and complete export with a small non-sensitive job. Follow the [provider compatibility checks](verification.md#provider-compatibility-checks), including independent model reload and cancellation checks.

## Observe, cancel and retrieve

Owner observation maps queued/pending/running to `InProgress`, cancelling to `Stopping`, and succeeded/failed/cancelled to `Completed`/`Failed`/`Stopped`. Other states fail conservatively and need operator investigation. The original provider state is retained with the normalized state. The running timeout does not prove a maximum queueing duration; unattended observation and overdue reconciliation remain required.

Stop first binds the original job, requests its cancellation and leaves capacity held. A local worker cancellation or successful cancel request does not prove upstream GPU shutdown. Observe a matching terminal provider state before treating compute as finished. [CustomJob cancellation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest)

Retrieval requires a previously observed matching completed job. It binds the exact approved output URI, reads bounded metadata, pins the reported object generation, downloads with size/MD5/SHA checks, and uses the shared narrow safe extractor. Composite outputs without the required MD5 metadata are refused by this initial profile. Checked transfer deadlines and socket timeouts are not a hard operating-system wall-clock deadline. Successful provider status does not replace artifact or model qualification. [Object retrieval](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get), [checksum semantics](https://docs.cloud.google.com/storage/docs/data-validation)

The retrieved directory queues `remote-import`. Its loader measures held-out metrics and reload equivalence before registering an inactive model; owner release is a separate step. Cloud history retains observation and transfer records. Import receipts are operator attestations rather than signed producer or image-origin proofs.

## Recovery and cost controls

Backup/restore checks the frozen profile, study, input receipt, provider identity and state history. Recovery retains unresolved cloud runs and interrupts copied local jobs. Restore credentials separately and reconcile the original job. Changing or revoking the operator profile blocks further managed actions; recover the approved configuration before using those controls.

Quotas are transactional inside one local workspace, grouped by **provider, account/project number and region** across tenant/profile aliases, using the strictest configured limits. Unresolved attempts do not expire. This is not a global cloud-account budget or monetary ceiling. Registry/storage/log retention costs, external submissions, tariffs and real provider billing require separate controls and measurements. Object cleanup is not automatic.

Complete the [provider compatibility checks](verification.md#provider-compatibility-checks) for the image, workload identity, job lifecycle, returned export and total cost. Monitor the run and provider budget separately; this workspace has no unattended watchdog or account-wide spending ceiling.

Use [RunPod](managed-runpod-training.md) or [Modal](managed-modal-training.md) only with their separate resource and storage contracts; their managed workflows share owner approvals and independent admission.
