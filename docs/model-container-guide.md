# Configure native model runtimes

<a id="linux-vega-and-semantic-model-image"></a>

[Documentation](README.md) · [Project overview](../README.md)

Native model images and process ownership are separate from the lightweight CPU application. Select the compatible model profile, install only its required runtime and qualify the chosen hardware.

## Choose a section

- [Linux Vega and semantic model image](#linux-model-image)

- [Windows model process ownership](#windows-process-ownership)

<a id="linux-model-image"></a>

## Linux Vega and semantic model image

<a id="linux-model-image--linux-vega-and-semantic-model-image"></a>

Train and serve Vega models, or run semantic data audits, in a Linux GPU container. The image supplies vegaml 0.8.0, Torch 2.10.0+cu126, Transformers 5.17.0 and NumPy 2.5.2 for Linux/amd64 CPython 3.12.15, alongside the application dependencies. Use the [CPU container](container-guide.md) for CPU workflows. Native Laya and Unsloth require separate compatible model images.

The pinned Torch profile has [dependency advisories](compatibility-and-upgrades.md#gpu-dependency-advisories). Remediate and requalify the model environment before production use; a successful image build does not establish model safety or quality.

<a id="linux-model-image--build-the-model-image"></a>

### Build the model image

```bash
docker build --platform linux/amd64 --file docker/models/Dockerfile.vega --tag domain-decision-platform:vega-local .
```

The [recipe](../docker/models/Dockerfile.vega) pins the Python base digest and rejects other architectures. It installs the [hashed Torch/Triton wheel stage](../requirements/torch-linux-amd64-py312-cu126.txt) from the official CUDA 12.6 index without dependencies, then the [complete model lock](../requirements/vega-linux-amd64-py312-cu126.txt) from PyPI. Both installs require hashes and wheels. Local project installation disables dependency resolution and build isolation; pip checks the completed environment. The model profile has 124 exact external pins, including explicit build tools.

Keep Torch and Triton downloads on the official CUDA index while generic dependencies resolve from PyPI. Hashes from one index may refuse a differently built distribution from another. Distribution hashes do not establish package safety or byte-identical images. [Official PyTorch versions](https://pytorch.org/get-started/previous-versions/), [uv index routing](https://docs.astral.sh/uv/guides/integration/pytorch/), [pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)

Regenerate the candidate using uv 0.11.23 and the exact input pins:

```bash
uv --no-config pip compile pyproject.toml requirements/vega-linux-amd64-py312-cu126.in --extra ui --extra platform --extra aws --extra azure --extra gcp --extra runpod --extra modal --extra semantic --python-version 3.12.15 --python-platform x86_64-unknown-linux-gnu --only-binary :all: --torch-backend cu126 --generate-hashes --default-index https://pypi.org/simple --output-file requirements/vega-linux-amd64-py312-cu126.txt
```

Reextract the exact `torch==2.10.0+cu126` and `triton==3.6.0` hash blocks into the wheel-stage file after regenerating; review changes and qualify the new image before use.

<a id="linux-model-image--standalone-offline-training"></a>

### Standalone offline training

Prepare all eight reviewed, reserved files using the [Vega data contract](vega-guide.md#vega-model): four positive roles and their four applicability-negative counterparts. Preserve source families, semantic/temporal reservations and independent calibration partitions. Supply an approved cached engine/backbone, a qualified retained image and an empty writable output directory. Container UID 10001 must be able to write the output. This shell example uses operator-selected absolute host paths:

```bash
docker run --rm --gpus "device=${DMT_GPU_DEVICE}" --network none --read-only \
  --tmpfs /tmp:rw,nosuid,size=1g --memory 6g --cpus 2 --pids-limit 256 \
  --cap-drop ALL --security-opt no-new-privileges \
  --env DMT_MODEL_CACHE=/cache --env HF_HOME=/tmp/hf \
  --env TRITON_CACHE_DIR=/tmp/triton --env TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor \
  --env OMP_NUM_THREADS=2 --env MKL_NUM_THREADS=2 \
  --mount "type=bind,source=${DMT_VEGA_INPUT_DIR},target=/inputs/data,readonly" \
  --mount "type=bind,source=${DMT_MODEL_CACHE_PATH},target=/cache,readonly" \
  --mount "type=bind,source=${DMT_VEGA_OUTPUT_DIR},target=/out" \
  --entrypoint python "${DMT_MODEL_IMAGE}" \
  -m decision_workbench.training.vega_runner --data /inputs/data --out /out/export \
  --device cuda --offline --epochs "${DMT_VEGA_EPOCHS}" \
  --batch-size "${DMT_VEGA_BATCH_SIZE}" --rank "${DMT_VEGA_RANK}" \
  --learning-rate "${DMT_VEGA_LEARNING_RATE}" --gate-weight 1 --resource-lock /out/gpu.lock
```

The standalone lease coordinates processes using the output lock; it does not coordinate a separate application workspace or other GPU consumers. Reserve a device for this route or use the application's shared lease. The trainer verifies the eight-file contract and writes a hash-manifest export. Load the verified manifest checkpoint. Use [independent admission](remote-training-guide.md#returned-model-admission) and owner gates before release; select resource bounds and hyperparameters using your data and hardware.

<a id="linux-model-image--verify-authenticated-container-lifecycle"></a>

### Verify authenticated container lifecycle

Build/retain the CPU browser and model images first, then supply an approved public Hub cache and a separately available local device. This qualification creates a new fictional workspace; it does not use your existing application records, provider profiles or teacher credentials:

```bash
python scripts/verify_containers.py --backend vega \
  --image domain-decision-platform:local --model-image "${DMT_MODEL_IMAGE}" \
  --model-cache "${DMT_MODEL_CACHE_PATH}" --gpu-device "${DMT_GPU_DEVICE}"
```

Set `--docker-context` explicitly when needed, such as `desktop-linux` for Docker Desktop's Linux engine or `default` for a selected Linux host. `--output` selects a private receipt/log parent; each run creates a fresh unique directory and Compose project. The worker joins an internal network; API/browser also join a dedicated entrypoint bridge for loopback access. No teacher/decision-provider/cloud profile is inherited. [Docker Compose service networks](https://docs.docker.com/reference/compose-file/networks/).

The Vega route reviews 800 positive and 240 applicability-negative fictional examples through authenticated HTTP, reserves eight coupled files in the independent worker, performs six actual GPU optimizer steps and checks the frozen export before owner release. It then probes resident inference and cross-container lease contention, API shutdown/reload, replacement of all three containers with volume persistence, and authenticated new-home cold HTTP recovery after stopping both original-home GPU services. The browser is checked for HTTP health; this command does not render the complete graphical journey. Its intentionally permissive owner policy tests mechanics and is not a domain release recommendation.

The verifier creates its own configuration and fictional data. Run a separate check for your `compose.yaml`/`compose.models.yaml` settings, JWT configuration, provider profiles and domain data. Inspect the verifier's report before proceeding; a configured command is not a completed check.

<a id="linux-model-image--queued-eviction-and-real-child-cancellation"></a>

#### Queued eviction and real-child cancellation

Add `--vega-resource-checks` to the same Vega command to exercise the supervised resource transitions. It temporarily stops only its owned worker, queues an actual Vega job and observes eviction of the API's resident model process before requesting inference. A queued owner cancellation then reopens the original released model. The worker restarts and begins a second actual training child; the verifier waits for feature extraction before requesting owner cancellation, checks inference-role denial, terminal process/lease cleanup and absence of a new registration, then reloads the original model. This option requires `--backend vega`; CPU defaults retain their existing checks.

<a id="linux-model-image--worker-parent-death-and-lease-handoff"></a>

#### Worker-parent death and lease handoff

Linux model training, resident inference, imported-runtime admission and semantic auditing pass their held resource-lease file descriptor to the direct child. Before importing model packages, the child checks the exact creating parent process/thread, arms kernel `SIGKILL` on parent-thread death, rechecks the startup race and prevents descriptor inheritance by subsequent executed children. Keeping the inherited file description open retains the lock if the parent dies before protection is armed. Normal cancellation still waits for the child before unlocking. Standalone training without application supervision retains its separate resource contract. [Linux parent-death API](https://www.man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html), [Python descriptor inheritance](https://docs.python.org/3.12/library/subprocess.html).

The kernel signal also fires when the creating thread exits while its process remains alive; it is not a process-only lifetime guarantee. Protection is for the direct child, and descendant processes, credential-changing programs, non-Linux operating systems and multi-host scheduling require separate acceptance. A dead worker's durable job is fenced until its job lease expires, then marked interrupted without automatic replay or registration. The shared device lock and the durable job lease serve different purposes; a free GPU lock does not authorize retrying an uncertain job.

Add `--vega-crash-checks` to check training-worker death as well as normal resource transitions. The verifier suspends its ordinary worker, starts a one-shot replacement and kills that replacement after feature extraction begins. The container stays alive, so this checks application child supervision separately from Docker's container cleanup.

The check verifies child termination, GPU lease release, inference refusal while durable ownership remains outstanding, an interrupted job with no new registration, and recovery of the original model. It resumes the ordinary worker before service replacement and cold recovery. Inspect the completed report and retain failure logs.

The verifier uses a PID-1 reaper and targets an ordinary worker process. PID-1 signals have special restrictions; check that the intended worker is suspended before starting a competitor. [Linux PID namespace behavior](https://www.man7.org/linux/man-pages/man7/pid_namespaces.7.html)

The cancellation and crash checks exercise local Vega lifecycle mechanics. Validate other model families, optimizer-phase crashes, concurrency, provider subprocesses, power loss and hardware/load separately. Windows has a different [ownership contract](model-container-guide.md#windows-process-ownership). Fixture results do not establish domain efficacy or enterprise deployment.

<a id="linux-model-image--configure-application-services"></a>

### Configure application services

Model weights are not baked into the image. Predownload the approved exact engine/backbone snapshots described in the [Vega guide](vega-guide.md#vega-model), validate their hashes and licenses, and mount only the selected cache read-only. No Hub token or home credential directory is needed for offline execution. Semantic profiles additionally require their pinned encoder/tokenizer assets and Linux-visible paths; follow the [semantic guide](reservation-guide.md#semantic-relatives).

The optional [model Compose override](../compose.models.yaml) selects a model image for API and worker, the same operator-selected GPU device, and a read-only Hub cache. Both services retain the shared workspace volume and GPU lease. The browser remains in the CPU application image and calls the API. [Docker GPU device configuration](https://docs.docker.com/compose/how-tos/gpu-support/). Set these operator values after qualification:

- `DMT_MODEL_IMAGE`: the retained qualified model image reference, preferably by digest.
- `DMT_MODEL_CACHE_PATH`: an absolute host path to the approved Hub cache directory.
- `DMT_GPU_DEVICE`: the same NVIDIA device ID or UUID for API and worker.
- `DMT_MODEL_RESOURCE_VOLUME`: an external volume shared by every workbench
  container using that GPU; the default name is `decision-workbench-model-resources`.

Stop existing model services before changing the lease configuration or upgrading
from workspace-only locking. Create the shared volume once, then initialize its
directory for the supplied model images' `platform` user. These
commands use your already qualified image; they do not build or download weights:

```bash
docker volume create decision-workbench-model-resources
docker compose --file compose.yaml --file compose.models.yaml run --rm --no-deps --pull never --user 0 --entrypoint python api -c "import os,pwd; u=pwd.getpwnam('platform'); os.chown('/run/model-resources', u.pw_uid, u.pw_gid); os.chmod('/run/model-resources', 0o770)"
```

If you select another volume name, create that name instead. For a custom image,
initialize permissions for its actual service identity. Do not reset permissions
or delete the volume while any participating service is running. The one-off
initialization command runs as root only to set volume permissions; the regular
API and worker retain the selected image's service user.

Start against the existing provisioned configuration using both Compose files and disable builds so the selected model image is retained:

```bash
docker compose --file compose.yaml --file compose.models.yaml up --no-build --detach --wait
```

Build the CPU/browser image separately first, as shown in the [CPU container guide](container-guide.md). The override explicitly supplies the API command because the model image defaults to the worker. Local paths are operator configuration; do not publish credentials or enterprise records in image layers. Mounting a GPU does not establish model readiness or a globally exclusive GPU reservation. The overlay sets `DMT_MODEL_RESOURCE_LOCK=/run/model-resources/gpu.lock` for both services. Separate Compose projects use the same external volume name, so their model operations share capacity while retaining separate workspace ownership. [Docker external volumes and names](https://docs.docker.com/reference/compose-file/volumes/).

On a Linux host, for native processes and containers to coordinate together, replace the
named-volume mounts with a bind mount of one protected host lock directory in
both services. Point native processes at that directory's same lock file; matching
path text in different filesystems is insufficient. Verify lock exclusion on the
selected local filesystem. Docker Desktop and mixed Windows/Linux processes need
separate coordination checks; use a dedicated device until those pass.
Keep other GPU applications
and multi-host scheduling under separate controls. See
[shared model capacity](setup.md#configuration--shared-model-capacity-on-one-host).

Use approved paired data and frozen owner criteria with the [training/release workflow](vega-guide.md#vega-model). Evaluate accuracy, calibration and coverage on operational gold before release. For managed cloud studies, check the selected account and image through [provider compatibility checks](verification.md#provider-compatibility-checks).

<a id="windows-process-ownership"></a>

## Windows model process ownership

<a id="windows-process-ownership--windows-model-process-ownership"></a>

Run Windows model workloads through the application worker so training, inference, model validation and semantic subprocesses share the GPU/CPU capacity lease. The launcher assigns workloads to a Windows Job Object before model imports and refuses work when assignment fails. Standalone CLI commands use a separate process-ownership path.

The parent owns the durable job handle. The child closes its temporary joining handle immediately. Kill-on-last-handle-close covers job members and ordinary `CreateProcess` descendants; this implementation enables neither breakaway option. Windows job assignment can conflict with an existing host job policy, so qualify the actual service host and preserve refusal. [Microsoft job-object semantics](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

<a id="windows-process-ownership--capacity-and-shutdown"></a>

### Capacity and shutdown

Windows byte-range locks do not provide Linux's inherited open-file-description behavior. Admission therefore takes the file lock and checks the bounded prior-job marker and active-process accounting before granting capacity. Invalid metadata or unavailable accounting refuses admission. An observer that retains a job handle may delay termination; positive membership still withholds capacity after parent death. [Microsoft file-lock inheritance](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex).

Normal shutdown waits for direct-child exit, closes and waits for the owned job, then releases capacity. The resident broker stops owned descendants before joining its output reader or closing pipes. Preserve this order: a descendant can retain a pipe handle after its direct parent exits. The marker is internal coordination metadata; do not edit it to force capacity while a model job may still be active.

Do not use exit status alone to confirm training completion: a forced Windows Job Object termination can report zero. Require a valid result, complete manifest, current job ownership and independent model checks. Virtual-environment launchers may add processes to the job, so inspect membership rather than assuming a fixed process count.

<a id="windows-process-ownership--verification-boundaries"></a>

### Verification boundaries

Run the maintained native tests from the repository's platform environment:

```powershell
python -m unittest discover -s tests -p test_windows_job.py -v
python -m unittest discover -s tests -v
```

Six actual Windows OS tests cover normal descendant cleanup, parent crash, death before joining, unavailable job/malformed marker refusal, retained-handle capacity fencing and launch preconditions. They use CPU sleepers and completion markers; they do not exercise model training or establish domain quality. Linux skips these six tests. Windows skips seven Linux kernel/FD/thread cases. Report applicable counts explicitly rather than presenting skipped cases as executed.

Validate runtime crashes, training-worker crashes and durable recovery separately on the target host. Other Windows versions, service identities, nested host jobs, network filesystems, power loss, broad load and provider subprocesses need their own checks. Hosted HTTP and cloud transport subprocesses do not inherit this model-launch contract.
