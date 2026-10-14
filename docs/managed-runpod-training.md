# Managed RunPod training

[All guides](README.md) · [Project overview](../README.md)

Train a frozen decision-model study in a RunPod v2 GPU Pod using an approved network volume. The application records submission and Pod state, then retrieves the completed export for local validation. Prepare the provider profile and owner approvals before creating the Pod.

## Prepare approved infrastructure and identity

Select an existing **STANDARD network volume** in a reviewed S3-compatible data center and an approved Secure Cloud GPU/CUDA profile. This adapter creates only a dedicated training Pod. It does not create or delete network volumes, credentials, registry accounts or clusters. Network-volume retention charges survive Pod termination. [Volume API](https://docs.runpod.io/api-reference-v2/network-volumes/get-a-network-volume), [S3 compatibility](https://docs.runpod.io/storage/s3-api)

Install the optional `runpod` dependency profile in the API/worker environment. It uses HTTPX 0.28.1 and Botocore 1.43.113 for actual SigV4 signing, without the RunPod SDK or an ambient AWS credential chain. Copy [the illustrative profile](../examples/cloud/runpod-profiles.json) to a private operator-controlled configuration file, replace its fictional resources/image and zero source hash, and set `DMT_CLOUD_PROFILES_FILE`. Qualify the image's exact package source, Linux/CUDA native Laya/Unsloth dependencies, tokenizer/model revision and representative memory before enabling paid studies. The CPU API Dockerfile is a separate image.

Supply only the selected private credential variables (the example prefix is `DMT_CLOUD_RUNPOD`):

- `DMT_CLOUD_RUNPOD_API_KEY`: RunPod REST key, scoped to approved resources where supported.
- `DMT_CLOUD_RUNPOD_S3_ACCESS_KEY_ID`: the approved `user_...` identity recorded in `account_id`.
- `DMT_CLOUD_RUNPOD_S3_SECRET_ACCESS_KEY`: separate RunPod S3 key secret.

The worker checks that both selected REST and storage credentials can access the exact approved volume and that its ID/data center/size/type match. A private image may use an approved `registry_id`, whose selected REST reference is checked. **v2 does not expose a user-whoami field in the reviewed contract**: the REST key's account membership remains operator-managed, with unique-volume accessibility as the cross-key resource check. Do not describe this as independent REST account attestation. Rotate credentials only within the same qualified account/resource scope; profile changes halt existing runs for operator recovery.

Secrets are absent from public profile projections, browser configuration, Pod arguments and model inventory. The GPU Pod receives the mounted approved network volume and no REST/S3 operator credential. It has no exposed ports, automatic SSH/Jupyter injection, cluster attachment, template expansion or global networking. Egress/base-model download, private registry access, effective IAM, volume writer isolation and supply-chain attestation still need enterprise qualification.

## Approve a frozen study and submit once

Prepare a domain study through [remote admission](remote-admission-guide.md), confirming owner criteria, exact four-role inputs, schema/source hashes and profile image/GPU/region. The owner explicitly approves data transfer and paid compute. The RunPod consent screen explains that its trainer deadline does not cap Pod billing and that stopping permanently terminates this dedicated Pod while retaining the network volume.

Submission checks the unique input/output/result prefix is absent, signs and uploads the exact frozen study ZIP (at most 256 MiB), records SHA/bytes/ETag, and checks uploaded metadata. RunPod's reviewed S3 API has **no versioning, bucket policy/ACL or object-lock support**. HEAD-before-PUT is not atomic conditional creation; the initial adapter does not claim If-None-Match enforcement. Use an operator-isolated volume/run prefix and account access policy. SHA verification refuses changed study bytes before the trainer runs. Unrelated writers can still invalidate availability or result evidence; no immutable storage promise is made. [Official limitations](https://docs.runpod.io/storage/s3-api)

The application commits immutable upload/create markers before mutations, checks a complete bounded Pod list for its name, then sends **one** `POST https://api.runpod.io/v2/pods`. The frozen request uses v2 `image`, explicit exec-form `entrypoint`/`cmd`, one exact `gpu.id`, exact CUDA version, one approved data center, container disk and `mounts.network` path. It does not silently fall back to another GPU/location/image. [Create contract](https://docs.runpod.io/api-reference-v2/pods/create-a-pod)

Pod names are not unique and v2 has no caller-selected Pod creation ID or reviewed idempotency key. Lost/malformed responses keep capacity unresolved. Before initial creation and when reconciling an unknown creation, the adapter reads at most 1,000 Pods (including cluster members), requires no next page, and requires exactly one matching name with complete frozen configuration for reconciliation. More than one match, a second page, absent unknown creation, unknown state or configuration drift is refused. The run is never replayed. A preexisting name prevents a fresh allocation; namespace/IAM isolation and actual service defaults must be qualified. Once recorded, the Pod ID cannot change or disappear from history.

## Train, package and inspect results

The dedicated image runs `decision_workbench.training.runpod_runner`. It invokes the unchanged frozen-study trainer on **ordinary local disk**, then creates a bounded regular-file USTAR archive and copies it to the approved mounted output directory. It refuses existing output/scratch locations. The last-written, fsynced `result.json` binds run, input/request/source hashes and either a sanitized failure or exact output SHA/bytes/manifest hash. This marker is **volume-writer attestation**, not a signed provider/model-quality receipt.

On Linux, the trainer runs in a new process group. A timeout or failure sends TERM, then KILL, and waits for the child. This timer covers the runner/trainer group; it excludes provisioning, image startup, packaging, network filesystem I/O and billing. Check shutdown on the target Linux image and observe Pod termination separately. Interrupted runs start fresh; optimizer resume, spot and distributed operation are not supported.

Observe uses the same Pod ID/configuration, requiring the image, commands, mounts, GPU/CUDA/data center, empty ports/environment, registry and service profile to match. Provisioning with incomplete required placement readback remains unresolved until a fully bound observation exists. `PROVISIONING`, `STARTING`, `RUNNING`, `EXITED` and `ERROR` remain locally `InProgress`; the retained provider status and result show why. A successful marker sets `output_ready`, but does not permit model retrieval or free capacity before Pod termination. Hourly cost is a provider observation in USD/hour, not a spending cap or reconciled invoice. [v2 schema](../tests/fixtures/provider-contracts/cloud-runpod-contract-2026-10-11.json)

## Terminate, observe and retrieve

Use **Terminate RunPod compute (network volume retained)** for the dedicated Pod. The worker binds the current resource, commits a termination-attempt marker and sends one `DELETE /v2/pods/{id}`. A 204 acknowledgement is persisted; it still requires later observation. The network volume is detached, not deleted. The initial adapter deliberately does not use host-local persistent Pod mounts that this operation would destroy. [Termination contract](https://docs.runpod.io/api-reference-v2/pods/terminate-a-pod)

A fully bound `TERMINATED` readback, or 404 absence of the recorded ID **after a persisted termination acknowledgement**, permits a terminal state. Successful bound result becomes `Completed`; failed result becomes `Failed`; no result becomes `Stopped`. A missing/invalid/changed marker is refused where a prior result exists. The recorded result cannot be changed or cleared. A 404 alone, lost deletion acknowledgement, local job cancellation or trainer exit does not free unresolved capacity. Lost acknowledgement plus an absent Pod needs operator investigation/recovery; this adapter supplies no automatic trusted settlement for it and never repeats that deletion attempt. Real termination/billing and stored-volume retention require independent provider acceptance.

After observed `Completed`, retrieval reads the exact approved volume/output path. It bounds metadata, response bytes and checked elapsed time, checks matching ETag and the independently computed SHA/length from the frozen result, rechecks output metadata and result after streaming, and rejects truncation/corruption/encoding changes. The storage has no version IDs or promised conditional-GET enforcement. SHA and before/after bindings detect different bytes; producer provenance remains unattested.

The importer checks the export's manifest, report, data, schema and source before independent reload and held-out evaluation. The model remains inactive until validation passes and the owner releases it. [Admission](remote-admission-guide.md)

## Recovery and operating limits

Backup/restore validates input receipt, attempt flags, monotonic termination receipt, immutable Pod identity and training-result history. Unknown external state keeps capacity indefinitely. REST/S3 credentials are restored separately; cached metadata is historical evidence. Quotas are rolling run counts and unresolved capacity shared inside one workspace/account/data-center namespace, not hard account-wide GPU or dollar caps.

Complete the [provider compatibility checks](verification.md#provider-compatibility-checks) for v2 defaults, SigV4 storage, mounted paths, the GPU image, returned model and actual charges. Monitor runs and retained data yourself; this controller has no unattended watchdog or automatic cleanup. [Interactive SSH workflow](remote-training/runpod.md), [Modal alternative](managed-modal-training.md), [API reference metadata dated 11 October](../research/provenance/cloud-runpod-sources-2026-10-11.json)
