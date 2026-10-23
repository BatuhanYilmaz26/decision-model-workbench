# Train on RunPod

<a id="train-a-decision-model-on-runpod"></a>

[Documentation](../README.md) · [Project overview](../../README.md)

Choose direct operator submission or the application-managed workflow below. Both require your approved account, a compatible training image and usable resources. App-managed runs additionally retain approvals, frozen study identity, status and returned-model validation.

## Choose a section

- [Submit directly through the provider](#direct-submission)

- [Manage training through the workbench](#application-managed-training)

<a id="direct-submission"></a>

## Submit directly through the provider

<a id="direct-submission--train-a-decision-model-on-runpod"></a>

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Create a GPU Pod through RunPod REST v2, connect over SSH and run the decision-model trainer. This route provides interactive control over the container and export. For application-managed approvals, frozen studies and job history, follow [managed RunPod training](runpod.md#application-managed-training).

RunPod REST v1 is deprecated and the current docs schedule retirement on **15 November 2026**. The examples below use REST v2 request/response shapes and the new host; replacing only the URL of a v1 payload is insufficient. [Official migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1)

<a id="direct-submission--choose-an-image-gpu-and-persistence"></a>

### Choose an image, GPU and persistence

Follow the [shared data/runtime protocol](../remote-training-guide.md#study-protocol). Select an approved Secure Cloud region/data center, available GPU type and explicit budget. Record the exact GPU type identifier from RunPod's current catalog. Avoid assuming that a display label, model size or prior hourly quote is a valid API ID or a present tariff.

For interactive setup, resolve the official `unsloth/unsloth` SSH-capable image to a digest as `DMT_RUNPOD_IMAGE_DIGEST`; verify its startup, dependency versions and user configuration. The documented image uses user `unsloth`, SSH on port 2222 and `SSH_KEY` for public-key configuration. A custom training-only image needs its own SSH configuration; do not expose a port and assume a daemon exists. [Unsloth Docker/SSH instructions](https://unsloth.ai/docs/get-started/install/docker)

The container disk is disposable on stop/restart. Pod volume storage defaults to `/workspace`, survives stops, and is deleted with the Pod. A separately managed network volume survives Pod deletion; it must be compatible with the selected data center. Treat persistence as a choice, not an automatic backup. [Storage comparison](https://docs.runpod.io/pods/storage/types)

<a id="direct-submission--provision-using-the-rest-api"></a>

### Provision using the REST API

Set the following in your operator environment: `RUNPOD_API_KEY`, `DMT_RUNPOD_IMAGE_DIGEST`, `DMT_RUNPOD_GPU_TYPE_ID`, `DMT_RUN_ID`, `DMT_SSH_PUBLIC_KEY_PATH`. The key path contains only your **public** SSH key; never send the private key to the provider. If location restrictions apply, add the verified `dataCenterIds` or `countryCodes` from the API reference.

```python
import os
from pathlib import Path
import httpx

response = httpx.post(
    "https://api.runpod.io/v2/pods",
    headers={"Authorization": "Bearer " + os.environ["RUNPOD_API_KEY"]},
    json={
        "name": os.environ["DMT_RUN_ID"],
        "cloud": "SECURE",
        "gpu": {"id": os.environ["DMT_RUNPOD_GPU_TYPE_ID"], "count": 1},
        "image": os.environ["DMT_RUNPOD_IMAGE_DIGEST"],
        "disk": 40, "mounts": {"persistent": {"size": 100, "path": "/workspace"}},
        "globalNetworking": False, "ports": ["2222/tcp"],
        "startSsh": False, "startJupyter": False,
        "env": {"SSH_KEY": Path(os.environ["DMT_SSH_PUBLIC_KEY_PATH"]).read_text()},
    },
    timeout=30.0, follow_redirects=False,
)
response.raise_for_status()
print(response.json()["id"])
```

This creates paid compute. Record the returned Pod ID, accepted image/GPU/location and the live price. Private images also need an approved registry credential reference. Inspect status through `GET https://api.runpod.io/v2/pods/POD_ID` or the dashboard; wait for startup and confirm that the public SSH key is configured. [Create Pod contract](https://docs.runpod.io/api-reference-v2/pods/create-a-pod)

The example deliberately leaves the image's startup entrypoint intact, allowing its SSH service to start. If you choose a different image, use its actual user/port/key mechanism. For a network-volume configuration, use v2 `mounts.network: [{"volumeId": "approved-id", "path": "/workspace"}]` and compatible `dataCenterIds` instead of assuming the default Pod volume is retained independently.

<a id="direct-submission--transfer-data-and-train-through-authenticated-ssh"></a>

### Transfer data and train through authenticated SSH

Use the Pod's **actual** public IP and mapped host port from its Connect page; the host port is not necessarily 2222. Native public-IP SSH supports file transfer; a provider proxy SSH endpoint may not support SCP. Confirm the server host key before transferring enterprise records. [RunPod SSH modes](https://docs.runpod.io/pods/configuration/use-ssh)

Create a small `cloud-code/` staging directory locally with `pyproject.toml`, `README.md`, `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `decision_workbench/` and the necessary pinned Linux dependency information. The license files are required by the package metadata. Do not copy `.env`, `.venv`, Git internals, credential directories or the full private workspace. With `DMT_SSH_KEY`, `DMT_POD_IP` and `DMT_SSH_PORT` set to your key path and connection details:

```bash
ssh -i "$DMT_SSH_KEY" -p "$DMT_SSH_PORT" "unsloth@$DMT_POD_IP" \
  'mkdir -p /workspace/study-001/data /workspace/study-001/code'
scp -i "$DMT_SSH_KEY" -P "$DMT_SSH_PORT" data/support/*.jsonl \
  "unsloth@$DMT_POD_IP:/workspace/study-001/data/"
scp -r -i "$DMT_SSH_KEY" -P "$DMT_SSH_PORT" cloud-code/. \
  "unsloth@$DMT_POD_IP:/workspace/study-001/code/"
ssh -i "$DMT_SSH_KEY" -p "$DMT_SSH_PORT" "unsloth@$DMT_POD_IP"
```

Inside the Pod, install the reviewed project into the image's selected Python environment. Follow the shared Linux dependency qualification; its exact native Laya source pin is:

```bash
python -m pip install 'laya @ https://github.com/NandhaKishorM/laya/archive/1adc59f7e371deb601fcfa18a14e25db238addcc.zip'
python -m pip install --no-deps /workspace/study-001/code
python -m pip check
nvidia-smi
```

Check that the pinned package/backbone/encoder profile is compatible before model download or training. The official image may contain a newer Unsloth than this adapter supports; a current image name is not qualification. Reconcile the package pins or build the selected Linux profile rather than ignoring the guard. Run native Laya after that preflight:

```bash
timeout --signal=TERM --kill-after=30s 7200 \
  python -m decision_workbench.training.laya_runner \
  --data /workspace/study-001/data --out /workspace/study-001/export \
  --model convaiinnovations/laya \
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
```

Use a persistent terminal session if you disconnect, capture exit status and private logs, and inspect `report.json`/`manifest.json` after success. The command timeout limits the trainer; **it does not stop Pod billing**. Keep an operator-side deadline/watchdog and stop the Pod even after a failed preflight or download. The platform does not yet supply that provider watchdog. For Unsloth, use the matching module/pins/options and the same four files.

<a id="direct-submission--retrieve-and-release-gpu-capacity"></a>

### Retrieve and release GPU capacity

From your operator machine, copy the complete export before deleting the Pod:

```bash
scp -r -i "$DMT_SSH_KEY" -P "$DMT_SSH_PORT" \
  "unsloth@$DMT_POD_IP:/workspace/study-001/export" retrieved/runpod/
```

Verify the report and retained checksums. For an active Pod, the documented REST operations are `POST /v2/pods/{id}/action` with `{"action":"stop"}` and `DELETE /v2/pods/{id}`:

```python
import os
import httpx

pod_id = os.environ["DMT_POD_ID"]
response = httpx.post(
    f"https://api.runpod.io/v2/pods/{pod_id}/action",
    json={"action": "stop"},
    headers={"Authorization": "Bearer " + os.environ["RUNPOD_API_KEY"]},
    timeout=30.0,
)
response.raise_for_status()
```

Observe the stopped state rather than assuming the API request alone released capacity. Follow the supported lifecycle for your volume profile; if stopping is unsupported, retrieve the export and terminate the Pod while preserving an independent network volume. Delete only the chosen Pod after export verification; a Pod's own volume is lost on termination. Network volumes and retained storage can continue to incur charges. [Lifecycle API](https://docs.runpod.io/api-reference-v2/pods/trigger-a-pod-state-transition), [termination API](https://docs.runpod.io/api-reference-v2/pods/terminate-a-pod)

The [application-managed adapter](runpod.md#application-managed-training) uses a dedicated noninteractive image, an existing network volume and frozen-byte admission. This interactive procedure itself does not create a managed inference endpoint, automatic optimizer resume or a local registry import. Record those boundaries with the experiment and use the shared export/serving qualification before release.

The v2 Pod response exposes lifecycle `status`, `runtime.ports`, `mounts` and deconstructed container commands. Use the actual Connect information for a custom image port; do not copy v1 `portMappings`/`desiredStatus` assumptions. The sample supplies Unsloth image-specific `SSH_KEY` explicitly and leaves v2 account-key SSH injection disabled. Private images require an approved v2 `registry` credential reference. The [11 October schema metadata](../../research/provenance/cloud-runpod-sources-2026-10-11.json) preserves the earlier contract; [runtime compatibility](../compatibility-and-upgrades.md) describes the compatible mount extension in the current schema. Neither schema establishes live account or service behavior.

<a id="application-managed-training"></a>

## Manage training through the workbench

<a id="application-managed-training--managed-runpod-training"></a>

Train a frozen decision-model study in a RunPod v2 GPU Pod using an approved network volume. The application records submission and Pod state, then retrieves the completed export for local validation. Prepare the provider profile and owner approvals before creating the Pod.

<a id="application-managed-training--prepare-approved-infrastructure-and-identity"></a>

### Prepare approved infrastructure and identity

Select an existing **STANDARD network volume** in a reviewed S3-compatible data center and an approved Secure Cloud GPU/CUDA profile. This adapter creates only a dedicated training Pod. It does not create or delete network volumes, credentials, registry accounts or clusters. Network-volume retention charges survive Pod termination. [Volume API](https://docs.runpod.io/api-reference-v2/network-volumes/get-a-network-volume), [S3 compatibility](https://docs.runpod.io/storage/s3-api)

Install the optional `runpod` dependency profile in the API/worker environment. It uses HTTPX 0.28.1 and Botocore 1.43.113 for actual SigV4 signing, without the RunPod SDK or an ambient AWS credential chain. Copy [the illustrative profile](../../examples/cloud/runpod-profiles.json) to a private operator-controlled configuration file, replace its fictional resources/image and zero source hash, and set `DMT_CLOUD_PROFILES_FILE`. Qualify the image's exact package source, Linux/CUDA native Laya/Unsloth dependencies, tokenizer/model revision and representative memory before enabling paid studies. The CPU API Dockerfile is a separate image.

Supply only the selected private credential variables (the example prefix is `DMT_CLOUD_RUNPOD`):

- `DMT_CLOUD_RUNPOD_API_KEY`: RunPod REST key, scoped to approved resources where supported.
- `DMT_CLOUD_RUNPOD_S3_ACCESS_KEY_ID`: the approved `user_...` identity recorded in `account_id`.
- `DMT_CLOUD_RUNPOD_S3_SECRET_ACCESS_KEY`: separate RunPod S3 key secret.

The worker checks that both selected REST and storage credentials can access the exact approved volume and that its ID/data center/size/type match. A private image may use an approved `registry_id`, whose selected REST reference is checked. **v2 does not expose a user-whoami field in the reviewed contract**: the REST key's account membership remains operator-managed, with unique-volume accessibility as the cross-key resource check. Do not describe this as independent REST account attestation. Rotate credentials only within the same qualified account/resource scope; profile changes halt existing runs for operator recovery.

Secrets are absent from public profile projections, browser configuration, Pod arguments and model inventory. The GPU Pod receives the mounted approved network volume and no REST/S3 operator credential. It has no exposed ports, automatic SSH/Jupyter injection, cluster attachment, template expansion or global networking. Egress/base-model download, private registry access, effective IAM, volume writer isolation and supply-chain attestation still need enterprise qualification.

<a id="application-managed-training--approve-a-frozen-study-and-submit-once"></a>

### Approve a frozen study and submit once

Prepare a domain study through [remote admission](../remote-training-guide.md#returned-model-admission), confirming owner criteria, exact four-role inputs, schema/source hashes and profile image/GPU/region. The owner explicitly approves data transfer and paid compute. The RunPod consent screen explains that its trainer deadline does not cap Pod billing and that stopping permanently terminates this dedicated Pod while retaining the network volume.

Submission checks the unique input/output/result prefix is absent, signs and uploads the exact frozen study ZIP (at most 256 MiB), records SHA/bytes/ETag, and checks uploaded metadata. RunPod's reviewed S3 API has **no versioning, bucket policy/ACL or object-lock support**. HEAD-before-PUT is not atomic conditional creation; the initial adapter does not claim If-None-Match enforcement. Use an operator-isolated volume/run prefix and account access policy. SHA verification refuses changed study bytes before the trainer runs. Unrelated writers can still invalidate availability or result evidence; no immutable storage promise is made. [Official limitations](https://docs.runpod.io/storage/s3-api)

The application commits immutable upload/create markers before mutations, checks a complete bounded Pod list for its name, then sends **one** `POST https://api.runpod.io/v2/pods`. The frozen request uses v2 `image`, explicit exec-form `entrypoint`/`cmd`, one exact `gpu.id`, exact CUDA version, one approved data center, container disk and `mounts.network` path. It does not silently fall back to another GPU/location/image. [Create contract](https://docs.runpod.io/api-reference-v2/pods/create-a-pod)

Pod names are not unique and v2 has no caller-selected Pod creation ID or reviewed idempotency key. Lost/malformed responses keep capacity unresolved. Before initial creation and when reconciling an unknown creation, the adapter reads at most 1,000 Pods (including cluster members), requires no next page, and requires exactly one matching name with complete frozen configuration for reconciliation. More than one match, a second page, absent unknown creation, unknown state or configuration drift is refused. The run is never replayed. A preexisting name prevents a fresh allocation; namespace/IAM isolation and actual service defaults must be qualified. Once recorded, the Pod ID cannot change or disappear from history.

<a id="application-managed-training--train-package-and-inspect-results"></a>

### Train, package and inspect results

The dedicated image runs `decision_workbench.training.runpod_runner`. It invokes the unchanged frozen-study trainer on **ordinary local disk**, then creates a bounded regular-file USTAR archive and copies it to the approved mounted output directory. It refuses existing output/scratch locations. The last-written, fsynced `result.json` binds run, input/request/source hashes and either a sanitized failure or exact output SHA/bytes/manifest hash. This marker is **volume-writer attestation**, not a signed provider/model-quality receipt.

On Linux, the trainer runs in a new process group. A timeout or failure sends TERM, then KILL, and waits for the child. This timer covers the runner/trainer group; it excludes provisioning, image startup, packaging, network filesystem I/O and billing. Check shutdown on the target Linux image and observe Pod termination separately. Interrupted runs start fresh; optimizer resume, spot and distributed operation are not supported.

Observe uses the same Pod ID/configuration, requiring the image, commands, mounts, GPU/CUDA/data center, empty ports/environment, registry and service profile to match. Provisioning with incomplete required placement readback remains unresolved until a fully bound observation exists. `PROVISIONING`, `STARTING`, `RUNNING`, `EXITED` and `ERROR` remain locally `InProgress`; the retained provider status and result show why. A successful marker sets `output_ready`, but does not permit model retrieval or free capacity before Pod termination. Hourly cost is a provider observation in USD/hour, not a spending cap or reconciled invoice. [v2 schema](../../tests/fixtures/provider-contracts/cloud-runpod-contract-2026-10-11.json)

<a id="application-managed-training--terminate-observe-and-retrieve"></a>

### Terminate, observe and retrieve

Use **Terminate RunPod compute (network volume retained)** for the dedicated Pod. The worker binds the current resource, commits a termination-attempt marker and sends one `DELETE /v2/pods/{id}`. A 204 acknowledgement is persisted; it still requires later observation. The network volume is detached, not deleted. The initial adapter deliberately does not use host-local persistent Pod mounts that this operation would destroy. [Termination contract](https://docs.runpod.io/api-reference-v2/pods/terminate-a-pod)

A fully bound `TERMINATED` readback, or 404 absence of the recorded ID **after a persisted termination acknowledgement**, permits a terminal state. Successful bound result becomes `Completed`; failed result becomes `Failed`; no result becomes `Stopped`. A missing/invalid/changed marker is refused where a prior result exists. The recorded result cannot be changed or cleared. A 404 alone, lost deletion acknowledgement, local job cancellation or trainer exit does not free unresolved capacity. Lost acknowledgement plus an absent Pod needs operator investigation/recovery; this adapter supplies no automatic trusted settlement for it and never repeats that deletion attempt. Real termination/billing and stored-volume retention require independent provider acceptance.

After observed `Completed`, retrieval reads the exact approved volume/output path. It bounds metadata, response bytes and checked elapsed time, checks matching ETag and the independently computed SHA/length from the frozen result, rechecks output metadata and result after streaming, and rejects truncation/corruption/encoding changes. The storage has no version IDs or promised conditional-GET enforcement. SHA and before/after bindings detect different bytes; producer provenance remains unattested.

The importer checks the export's manifest, report, data, schema and source before independent reload and held-out evaluation. The model remains inactive until validation passes and the owner releases it. [Admission](../remote-training-guide.md#returned-model-admission)

<a id="application-managed-training--recovery-and-operating-limits"></a>

### Recovery and operating limits

Backup/restore validates input receipt, attempt flags, monotonic termination receipt, immutable Pod identity and training-result history. Unknown external state keeps capacity indefinitely. REST/S3 credentials are restored separately; cached metadata is historical evidence. Quotas are rolling run counts and unresolved capacity shared inside one workspace/account/data-center namespace, not hard account-wide GPU or dollar caps.

Complete the [provider compatibility checks](../verification.md#provider-compatibility-checks) for v2 defaults, SigV4 storage, mounted paths, the GPU image, returned model and actual charges. Monitor runs and retained data yourself; this controller has no unattended watchdog or automatic cleanup. [Interactive SSH workflow](runpod.md#direct-submission), [Modal alternative](modal.md#application-managed-training), [API reference metadata dated 11 October](../../research/provenance/cloud-runpod-sources-2026-10-11.json)
