# Train on Modal

<a id="train-a-decision-model-on-modal"></a>

[Documentation](../README.md) · [Project overview](../../README.md)

Choose direct operator submission or the application-managed workflow below. Both require your approved account, a compatible training image and usable resources. App-managed runs additionally retain approvals, frozen study identity, status and returned-model validation.

## Choose a section

- [Submit directly through the provider](#direct-submission)

- [Manage training through the workbench](#application-managed-training)

<a id="direct-submission"></a>

## Submit directly through the provider

<a id="direct-submission--train-a-decision-model-on-modal"></a>

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Run the repository's decision-model trainer in a Modal GPU Function, using a prepared container image and a persistent Volume for data and exports. Modal's [Unsloth example](https://modal.com/docs/examples/unsloth_finetune) shows the platform's fine-tuning infrastructure; the command below selects the decision-head trainer rather than the example's generative trainer.

<a id="direct-submission--stage-immutable-input-on-a-volume"></a>

### Stage immutable input on a Volume

Complete [image and four-split qualification](../remote-training-guide.md#study-protocol). Install the `modal` SDK in an operator environment, authenticate with `modal setup`, and use an approved workspace/environment. The qualified image must contain the project and its Python/CUDA dependencies and be accessible to Modal. For a private image, configure the documented registry-secret mechanism rather than making the image public.

```bash
modal volume create domain-decision-runs
modal volume put domain-decision-runs data/support/ /runs/study-001/data/
```

`study-001` is an example run ID; use a unique ID for each study and verify that all four files are directly under that run's `data/` prefix. Do not overwrite an input prefix after recording its hashes. [Volume CLI upload/download](https://modal.com/docs/cli/latest/volume)

<a id="direct-submission--define-and-invoke-the-gpu-function"></a>

### Define and invoke the GPU function

Set `DMT_TRAIN_IMAGE` in your operator environment to the qualified image digest. Save this as `modal_train.py` outside any secret/data directory:

```python
import os
import re
import modal

app = modal.App("domain-decision-training")
volume = modal.Volume.from_name("domain-decision-runs")
image = modal.Image.from_registry(os.environ["DMT_TRAIN_IMAGE"])

@app.function(image=image, gpu="A10", volumes={"/runs": volume},
              timeout=7200, retries=0)
def train(run_id: str):
    import subprocess
    import sys
    from pathlib import Path
    from decision_workbench.training.contracts import load_splits

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("Invalid run identifier")
    root = Path("/runs/runs") / run_id
    load_splits(root / "data")
    target = root / "export"
    if target.exists():
        raise ValueError("Use a fresh run/output prefix")
    try:
        subprocess.run([
            sys.executable, "-m", "decision_workbench.training.laya_runner",
            "--data", str(root / "data"), "--out", str(target),
            "--model", "convaiinnovations/laya",
            "--revision", "7b928d828b7b0e022f929d9bd2e44165aa270148",
            "--epochs", "2", "--batch-size", "2", "--grad-accum", "4",
            "--max-length", "512",
        ], check=True)
    finally:
        volume.commit()
    return {"run_id": run_id, "export": str(target)}

@app.local_entrypoint()
def main(run_id: str = "study-001"):
    print(train.remote(run_id))
```

The Volume's `/runs/study-001` directory is mounted under `/runs`, giving the container path `/runs/runs/study-001`. Check memory and availability before using the example's A10G and two-hour limit. The function disables retries because the adapter cannot resume optimizer state. Keep interrupted outputs for investigation, and release only a completed, validated export. [Existing-image API](https://modal.com/docs/guide/existing-images), [GPU configuration](https://modal.com/docs/guide/gpu), [Volumes and explicit commit](https://modal.com/docs/guide/volumes)

```bash
modal run modal_train.py --run-id study-001
```

This starts paid GPU execution. Keep the CLI attached for the first qualification. For background execution, the documented `modal run --detach` option changes lifecycle/observation; record the application/function-call identity and use the dashboard to observe completion. A dropped operator connection must not be treated as proof of cancellation.

<a id="direct-submission--retrieve-and-stop"></a>

### Retrieve and stop

After successful function completion:

```bash
modal volume get domain-decision-runs /runs/study-001/export retrieved/modal/export
```

Inspect the whole model manifest/report and verify its input hashes and reload qualification. Persist each experiment's Modal call ID, GPU, image digest and measured usage. The function does not create an HTTP serving endpoint or a platform registry entry.

Use the Modal dashboard's stop action or `modal app stop APP_ID` for an active application, then observe its stopped/terminal state. Successful job functions release their GPU compute; the persistent Volume remains until deliberately removed and may remain billable. The configured timeout limits a call, while retries would multiply allowed total time. [Timeout behavior](https://modal.com/docs/guide/timeouts), [application CLI](https://modal.com/docs/cli/latest/app)

Do not copy the upstream long-training retry example until a resumable decision trainer exists. A durable Volume preserves files; it does not by itself restore optimizer state, ensure one logical job, or prevent duplicate billing. [Long-running training example](https://modal.com/docs/examples/long-training)

<a id="direct-submission--managed-application-alternative"></a>

### Managed application alternative

For application-managed studies, use the [Modal Sandbox controller](modal.md#application-managed-training) with SDK 1.6.1. It adds owner approval, frozen inputs, durable provider identity, observation, termination and local artifact validation. When using the Function route independently, retain the call ID and persist the export on a Volume: FunctionCall outputs expire after seven days. [Job-queue contract](https://modal.com/docs/guide/job-queue)

<a id="application-managed-training"></a>

## Manage training through the workbench

<a id="application-managed-training--managed-modal-training"></a>

Run an owner-approved decision-model study in a Modal GPU Sandbox from the application. The controller uploads the frozen study, records the Sandbox identity and retrieves a completed Volume export for local validation. Use **Modal SDK 1.6.1** in the API/worker environment.

<a id="application-managed-training--prepare-an-approved-workspace-and-image"></a>

### Prepare an approved workspace and image

Install `python -m pip install -e '.[platform,modal]'` in the API/worker environment. The optional SDK is pinned separately in [modal-transport.txt](../../requirements/modal-transport.txt); this is a control dependency, not a reproducible GPU image lock. Prepare an existing deployed App and named Volume in an explicit Modal Environment. The controller looks up resources with `create_if_missing=False` and checks their exact IDs plus the authenticated Workspace name. Provision infrastructure separately under operator approval. [App lookup](https://modal.com/docs/sdk/py/latest/App#lookup), [Workspace](https://modal.com/docs/sdk/py/latest/Workspace#from_context), [Volume](https://modal.com/docs/sdk/py/latest/Volume#from_name)

Copy [the illustrative profile](../../examples/cloud/modal-profiles.json) to a private operator-controlled file, replace every fictional identity and its zero source hash, and set `DMT_CLOUD_PROFILES_FILE`. Use a qualified existing `modal_image_id` built from the approved `image_digest` with this package's source, native Laya/Unsloth dependencies and **cached exact model/tokenizer revisions**. The frozen study still checks provider, GPU, region, image digest and package source before approval. A Modal image ID is not an OCI digest: the mapping from that ID to its source digest/dependencies is an operator build attestation. The runtime does not independently recover the originating digest from the image API. Preserve build logs and qualify the actual image before enabling paid studies. [Images](https://modal.com/docs/sdk/py/latest/Image#from_id)

Provide only `DMT_CLOUD_MODAL_TOKEN_ID` and `DMT_CLOUD_MODAL_TOKEN_SECRET` for the illustrative `DMT_CLOUD_MODAL` credential prefix. Tokens remain in the API/worker, cross a private child-process pipe for SDK operations and never enter browser profiles, container command lines or exported models. Compose forwards them to API/worker only. The child uses an empty private TOML file, fixed `https://api.modal.com`, an explicit Environment and selected credentials through `Client.from_credentials`. It does not use an ambient Modal profile, CLI login, proxy or another provider credential chain. [Selected-client API](https://modal.com/docs/sdk/py/latest/Client#from_credentials)

The initial profile requests one exact reviewed GPU, a fixed CPU request/limit, memory request/limit, a reviewed region and `runtime="gvisor"`. Multi-GPU, fallback lists, spot/resume and dynamic autoscaling are unsupported. `H100!` and explicit A100 memory variants avoid the documented automatic upgrades of generic H100/A100 requests. The current GPU list calls the former A10G **A10**, although some older Volume examples still say A10G. [GPU documentation](https://modal.com/docs/guide/gpu)

**Compute region is not a complete data residency guarantee.** Region selection can change prices; **Modal managed Volumes, Images and application logs are stored in the United States**, regardless of the selected compute region. The Modal consent screen explicitly names that US-based Volume transfer. Regional storage alternatives require a different qualified storage adapter; the initial managed-Volume controller does not implement them. Control-plane routing and image/model distribution require separate enterprise review. Some Sandbox operations route through US-East. Do not describe `eu-west` as proving that every study byte remains within that region. This adapter supplies no account-wide monetary cap or independently verified provider invoice. [Regions and routing](https://modal.com/docs/guide/region-selection), [data residency](https://modal.com/docs/guide/data-residency)

<a id="application-managed-training--freeze-approve-and-submit-once"></a>

### Freeze, approve and submit once

Use [remote studies](../remote-training-guide.md#returned-model-admission) to reserve the same reviewed train/validation/calibration/test experiment and owner acceptance criteria. Synthetic candidates remain training-only. The owner must approve data transfer and paid compute before submitting through the cloud controls. The runtime sends the exact bounded study ZIP, rather than a user-supplied executable or arbitrary cloud URL.

Submission refuses existing input/output/result paths, uploads at most 256 MiB through `Volume.batch_upload(force=False)`, reads back the exact input and records SHA/bytes/Volume ID. Every mutation has a durable attempt marker. SDK upload retries are distinct from application replay; the application never restarts a submission after an uncertain upload or creation. Volume metadata is not versioned immutable object evidence. Other writers may modify it: isolate the Volume/run prefix and retain the exact input SHA guard. [Volume API](https://modal.com/docs/sdk/py/latest/Volume#batch_upload), [concurrent writers](https://modal.com/docs/guide/volumes)

The worker creates a uniquely named Sandbox `dmt-<run-id>` in the approved App, using the approved prebuilt image ID, exact command and run/source/input/plan tags. No interactive tunnel, SSH, notebook, OIDC token or operator secret is requested. Egress is blocked; `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` require cached model files in the image. This deliberately fails when a requested model is absent instead of silently downloading unqualified content. Network policy enforcement and cache/image contents still require actual provider acceptance.

In SDK 1.6.1, GPU requests use the **legacy Sandbox backend** even though the SDK defaults to its newer backend for supported CPU cases. The isolated client explicitly disables the newer selection, and the accepted job-ID shape is the reviewed legacy `sb-` plus 22 alphanumeric characters. SDK source and offline serialization are pinned; do not equate Modal's internal Sandbox version with RunPod REST v1/v2. The application uses public Modal APIs, not direct undocumented gRPC requests. [Pinned SDK wheel/source](../../research/provenance/cloud-modal-sources-2026-10-11.json)

Modal assigns the Sandbox ID. The application persists it and never replaces it. A lost creation response can be reconciled through `Sandbox.from_name` **only while the Sandbox is running**, with exact expected tags. A missing name may mean completion or failed creation; it never authorizes another allocation or frees unknown capacity. Once an ID is known, polling and termination use `Sandbox.from_id`. Names are documented as unique within an App, but name reuse after completion is not cross-time idempotency. The SDK retries some RPCs with its own per-invocation idempotency metadata; that metadata does not survive this application's crash. No new SDK creation invocation is issued for an uncertain run. [Sandbox API](https://modal.com/docs/sdk/py/latest/Sandbox)

Tags bind writer-declared run/plan/source/input identity; they do **not** independently attest actual container configuration or image provenance. Other token holders can alter tags or Volume contents. Effective workspace permissions, isolated writers, returned resource settings and paid execution must be qualified separately.

<a id="application-managed-training--run-the-frozen-trainer-and-persist-output"></a>

### Run the frozen trainer and persist output

The image entrypoint runs `decision_workbench.training.modal_runner`, which invokes the shared frozen-study runner on ordinary local disk. It checks package/input/request hashes, all four splits, schema and immutable training configuration before native Laya/Unsloth execution. The wrapper packages a bounded regular-file USTAR export, copies it to the mounted Volume, checks the copied SHA and writes a last-written fsynced result marker. Interrupted training does not implement optimizer resume.

The Sandbox lifetime covers startup, training and packaging. The Linux trainer's process-group deadline is shorter, reserving 120 seconds for packaging; that reserve does not guarantee packaging finishes. Measure startup, interruption and packaging behavior in your Sandbox, and check resource termination and actual billing separately.

Modal documents background commits and a final Volume snapshot/commit on container shutdown. This runner writes closed, fsynced files and relies on that mounted-Volume behavior rather than passing a storage token into the workload. A successful process exit without a visible result remains pending; later observation can resolve delayed visibility. Test abrupt termination, failure, commit visibility and retained-storage cleanup on the actual provider. [Volume commit behavior](https://modal.com/docs/guide/volumes)

<a id="application-managed-training--observe-terminate-and-admit"></a>

### Observe, terminate and admit

`poll()` returning `None` means still running; it cannot permit retrieval. Exit code zero plus an exact successful result marker permits `Completed`. Other bound exit codes become `Failed`; 137/143 after a recorded termination attempt become `Stopped`. A missing/changed/invalid prior result, missing known Sandbox or unsupported identity stays unresolved. Result markers are Volume-writer attestations, not provider-signed training-quality receipts.

Use **Terminate Modal Sandbox (Volume retained)** to request shutdown. The worker binds the same Sandbox, commits an attempt before calling `terminate(wait=False)` once, and persists acknowledgement where received. Acknowledgement alone does not establish shutdown. Observe the same bound ID and exit code afterwards. If the acknowledgement was lost but exit code 137/143 is observed for the bound ID, the lifecycle can resolve as stopped. Repeated stop never issues a second termination invocation; missing/unknown observations retain capacity. Volume retention charges and provider settlement need operator verification.

Each SDK operation runs in a separate private process. Controller operations have a 90-second process deadline; input uploads have 300 seconds. Output downloads use a size-aware process deadline, with a ten-minute floor and four-hour ceiling. Timeout kills/waits for the local child and records uncertainty; it does not cancel cloud compute. SDK RPC/block-download retries may occur inside that invocation. SDK block prefetching and infrastructure can exceed a single application's chunk buffer. These local process limits are not a provider SLA, guaranteed OS deadline or unattended cloud watchdog.

After `Completed`, retrieval accepts only `modal-volume://<approved-volume>/<exact-run>/outputs/model.tar.gz`. It checks regular-file metadata, bounded length, archive SHA and a stable result marker. It preserves existing local files and removes only its own partial transfer. The Volume protocol has no independent generation/ETag pin or signed producer attestation; hash checks identify transferred bytes, not who produced them.

The export goes into [independent admission](../remote-training-guide.md#returned-model-admission). The importer verifies its manifest, report, source, schema and data, reloads the model and measures held-out decisions before registering an inactive version. Release requires a separate owner action. Backup/restore preserves frozen inputs, Sandbox identity and termination history; unknown external state continues to reserve capacity.

Complete the [provider compatibility checks](../verification.md#provider-compatibility-checks) with your selected Sandbox image, Volume and account before a production study. [API reference metadata dated 11 October](../../research/provenance/cloud-modal-sources-2026-10-11.json)
