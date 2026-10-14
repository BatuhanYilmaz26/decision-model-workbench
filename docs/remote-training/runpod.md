# Train a decision model on RunPod

Create a GPU Pod through RunPod REST v2, connect over SSH and run the decision-model trainer. This route provides interactive control over the container and export. For application-managed approvals, frozen studies and job history, follow [managed RunPod training](../managed-runpod-training.md).

RunPod REST v1 is deprecated and the current docs schedule retirement on **15 November 2026**. The examples below use REST v2 request/response shapes and the new host; replacing only the URL of a v1 payload is insufficient. [Official migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1)

## Choose an image, GPU and persistence

Follow the [shared data/runtime protocol](../remote-training-guide.md). Select an approved Secure Cloud region/data center, available GPU type and explicit budget. Record the exact GPU type identifier from RunPod's current catalog. Avoid assuming that a display label, model size or prior hourly quote is a valid API ID or a present tariff.

For interactive setup, resolve the official `unsloth/unsloth` SSH-capable image to a digest as `DMT_RUNPOD_IMAGE_DIGEST`; verify its startup, dependency versions and user configuration. The documented image uses user `unsloth`, SSH on port 2222 and `SSH_KEY` for public-key configuration. A custom training-only image needs its own SSH configuration; do not expose a port and assume a daemon exists. [Unsloth Docker/SSH instructions](https://unsloth.ai/docs/get-started/install/docker)

The container disk is disposable on stop/restart. Pod volume storage defaults to `/workspace`, survives stops, and is deleted with the Pod. A separately managed network volume survives Pod deletion; it must be compatible with the selected data center. Treat persistence as a choice, not an automatic backup. [Storage comparison](https://docs.runpod.io/pods/storage/types)

## Provision using the REST API

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

## Transfer data and train through authenticated SSH

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

## Retrieve and release GPU capacity

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

The [application-managed adapter](../managed-runpod-training.md) uses a dedicated noninteractive image, an existing network volume and frozen-byte admission. This interactive procedure itself does not create a managed inference endpoint, automatic optimizer resume or a local registry import. Record those boundaries with the experiment and use the shared export/serving qualification before release.

The v2 Pod response exposes lifecycle `status`, `runtime.ports`, `mounts` and deconstructed container commands. Use the actual Connect information for a custom image port; do not copy v1 `portMappings`/`desiredStatus` assumptions. The sample supplies Unsloth image-specific `SSH_KEY` explicitly and leaves v2 account-key SSH injection disabled. Private images require an approved v2 `registry` credential reference. The [11 October schema metadata](../../research/provenance/cloud-runpod-sources-2026-10-11.json) preserves the earlier contract; [runtime compatibility](../compatibility-and-upgrades.md) describes the compatible mount extension in the current schema. Neither schema establishes live account or service behavior.
