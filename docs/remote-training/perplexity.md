# Train native Perplexity Decider on a cloud GPU

[Provider directory](README.md) · [Native model and data](../models/perplexity-native.md) · [App user guide](../app-guide.md#screen-manual)

Train the pinned open `pplx-decider-v1.1-27b` for your domain using AWS,
Azure, GCP, Modal or RunPod. The same frozen study carries your reviewed data,
model identity, settings and acceptance criteria between providers. Returned
weights require independent admission before an owner can release them.

This is an **experimental native full-weight Linux/CUDA profile**. The image
recipe and provider declarations need validation on your selected account,
GPU and driver. They do not establish measured throughput or domain quality.
The [hosted Perplexity API](../models/perplexity.md) is a separate option that
does not train or download provider weights.

## Size the training and serving hosts

The publisher trainer updates the backbone and native BF16 readout with a
CPU-offloaded optimizer. It preserves noncausal full attention, native recurrent
attention and the saved global calibration temperature. See the pinned
[trainer](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/train.py)
and [optimizer](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b/blob/14c9d90137962d300f3de95633f7b6535e80c5ce/source/src/autojev/optim.py).

Allow roughly **107 GiB free VRAM on one GPU, 428 GiB available host RAM and
150 GiB free run storage**, in addition to the preinstalled 52.2 GB checkpoint.
These are startup floors, not measured peak requirements. Larger inputs and
batches can need more. Linux checks include inherited container memory limits.
Provision at least 512 GB host RAM and additional operating-system headroom.

The trainer uses **one GPU process**. The AWS, Azure and GCP choices below
allocate eight H200 GPUs per machine, although this workflow uses only the first
device. You pay for the allocated machine; its GPUs do not combine their memory
here. Modal and RunPod can request one GPU. Check quota, regional capacity and
account limits before approving compute.

| Provider | Native profile declaration | Storage and additional settings |
|---|---|---|
| AWS | `gpu: "ml.p5e.48xlarge"` or `"ml.p5en.48xlarge"` | `volume_gb: 300` or greater; one training instance |
| Azure | `gpu: "Standard_ND96isr_H200_v5"` | Dedicated Linux AML compute, zero minimum and one maximum node; verify scratch space inside the container |
| GCP | `machine_type: "a3-ultragpu-8g"`, `gpu: "NVIDIA_H200_141GB"` | `volume_gb: 300` or greater; eight accelerators and a Hyperdisk Balanced boot disk |
| Modal | `gpu: "H200"`, `memory_mb: 524288` | 512 GiB requested and capped RAM; compatible GPU Sandbox and retained Volume |
| RunPod | `gpu: "NVIDIA H200"`, `min_memory_gb: 512` | `volume_gb: 300` or greater, `disk_gb: 100` or greater; the RAM placement minimum is in GB |

The controller refuses known undersized native profiles before creating a paid
run. A provider may still reject a request for capacity, quotas or account limits.
Runtime free-memory/storage checks remain necessary.

Resource references: [AWS H200 instances](https://aws.amazon.com/ec2/instance-types/p5/),
[Azure H200 sizes](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series),
[GCP machine/accelerator and boot-disk combinations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/configure-compute),
[Modal GPUs](https://modal.com/docs/guide/gpu), [Modal memory and disk](https://modal.com/docs/guide/resources),
[RunPod v2 placement fields](https://github.com/runpod/runpod-python/blob/main/runpod/api/ctl_commands.py).

## Build the native training image

Use a Linux/amd64 build host with enough storage and an approved registry.
From a clean clone of this repository:

```bash
docker build --platform linux/amd64 \
  -f docker/models/Dockerfile.perplexity \
  -t your-registry.example/decision-perplexity:training .
```

**Building this image downloads the complete large checkpoint and CUDA runtime.**
It is separate from the lightweight CPU application installation. The
[recipe](../../docker/models/Dockerfile.perplexity) installs the pinned publisher
runtime plus data, evaluation and provider tools. It verifies the checkpoint and
assets, records source/package identities in `/app/perplexity-image.json` and
keeps training offline. Its small Git repository records public build source for
publisher logging; it contains neither domain data nor model weights.

If model access requires a Hugging Face token, use a BuildKit secret. With your
token already in `HF_TOKEN`, add `--secret id=hf_token,env=HF_TOKEN` to the build
command. See [Docker build secrets](https://docs.docker.com/build/building/secrets/).
Keep cloud credentials and domain data out of image layers and Git.

Inspect metadata without loading a model, push, then retain the immutable digest:

```bash
docker run --rm --entrypoint cat your-registry.example/decision-perplexity:training \
  /app/perplexity-image.json
docker push your-registry.example/decision-perplexity:training
docker image inspect your-registry.example/decision-perplexity:training \
  --format '{{index .RepoDigests 0}}'
```

Keep the printed `source_sha256` and `repository@sha256:...` digest. Validate CUDA
execution on your intended host before using private domain data. A successful
image build without a GPU does not qualify GPU execution.

The recipe sets `DMT_PERPLEXITY_CHECKPOINT` to its complete pinned cache snapshot.
For your own image, preserve that absolute path, compatible packages, the matching
public source fingerprint and real Git metadata. Submitted studies cannot enable
checkpoint downloads or replace the pinned model revision.

## Configure the selected provider

Copy a template to a **private operator configuration file**. Configure your own
account, region, storage and credentials using the linked walkthrough. Replace
demonstration GPU/resources with the native settings above, use your image
digest/source fingerprint and declare `"training_backends": ["perplexity-native"]`.

| Provider | Setup and submission path | Template |
|---|---|---|
| AWS | [SageMaker training API](aws.md#direct-submission), [managed app controls](aws.md#application-managed-training) | [AWS](../../examples/cloud/aws-profiles.json) |
| Azure | [Azure ML command jobs](azure.md#direct-submission), [managed app controls](azure.md#application-managed-training) | [Azure](../../examples/cloud/azure-profiles.json) |
| GCP | [Custom training jobs](gcp.md#direct-submission), [managed app controls](gcp.md#application-managed-training) | [GCP](../../examples/cloud/gcp-profiles.json) |
| Modal | [GPU Sandbox and Volume](modal.md#direct-submission), [managed app controls](modal.md#application-managed-training) | [Modal](../../examples/cloud/modal-profiles.json) |
| RunPod | [GPU Pods and network volumes](runpod.md#direct-submission), [managed app controls](runpod.md#application-managed-training) | [RunPod](../../examples/cloud/runpod-profiles.json) |

- **AWS:** publish to the ECR account/region in your profile. Grant the training
  role access to the approved input/output prefix and confirm SageMaker training
  quota for the selected H200 instance.
- **Azure:** register the image as the specified AML environment version. Verify
  the dedicated H200 compute identity and scratch storage; a VM's total storage
  does not establish free space at the container's work directory.
- **GCP:** use Artifact Registry in the approved project/region with the documented
  operator/training service accounts. Confirm H200 availability for your custom-job
  scheduling policy. The adapter uses the existing regional `customJobs` API.
- **Modal:** register the image with your App/environment and obtain its image ID.
  Use the retained Volume and confirm that a 512 GiB RAM request fits your account's
  creation limits. GPU Sandboxes can be preempted; this trainer cannot resume
  optimizer state after interruption.
- **RunPod:** use the exact H200 identifier available in the selected secure-cloud
  data center. Configure network-volume/registry access. `min_memory_gb` becomes
  v2 `gpu.minRamPerGpu`; readback must report adequate allocated host RAM. This is
  a placement filter rather than an independent RAM reservation.

Set `max_artifact_bytes: 68719476736` (64 GiB) for the returned backbone, readout,
processor and pinned publisher source. An adapter-sized cap cannot hold this
export. Retrieval reserves three times the approved cap plus 1 GiB locally:
**193 GiB for a 64 GiB cap**, beyond retained data and models.

Set an explicit runtime/cost budget. Current managed profiles allow at most four
hours, including provider-side packaging/transfer work. This does not guarantee
that your chosen epochs/data fit. The native workflow selects the final epoch
and has no optimizer-state resume. A timed-out run is not a release candidate.

Set `DMT_CLOUD_PROFILES_FILE` to your private configuration and restart workspace
services. Provider credentials belong in the server/operator environment described
by each guide, not project data or the workspace-login field. Cloud preparation
requires no local model environment or GPU.

## Prepare, submit and follow your study

1. Follow [domain intake and review](../app-guide.md#screen-manual--first-project-walkthrough).
   Use **Choice** or **Noul** questions with text/JSON evidence. Reserve real
   validation, calibration and test records before generating reviewed synthetic
   training candidates. Preserve soft training targets; heldouts need one-hot gold.
   An owner sets acceptance criteria before the first study.
2. In **Training jobs**, choose **Perplexity open v1.1**, a training snapshot and
   **Cloud GPU**. Set epochs, micro-batch, accumulation, token budget, learning
   rate and optional CPU/evaluation controls. This does not allocate compute.
3. Under **Train on a cloud GPU**, approve study-data transfer and enter the
   provider, region, GPU/instance identity and image digest from your profile.
   Choose **Prepare remote training study** and follow its preparation job.
4. Select the matching managed profile. Approve data and paid compute, choose
   **Approve cloud run**, then **Submit approved paid training**. Observe the same
   run rather than allocating another after a browser timeout.
5. Use the provider's stop/termination action when required and observe its terminal
   state. Local cancellation alone does not stop billing. Modal Volumes and RunPod
   network volumes remain after compute termination and can incur storage charges.
6. After provider success, choose **Retrieve and queue independent admission**.
   The full export is bounded, hash-checked and matched to the frozen request.
   Unapproved source, altered pinned assets and cached Python bytecode are refused.

For operator-managed submission, download the frozen study and use the selected
provider walkthrough with this native image. The common entry point is
`python -m decision_workbench.training.cloud_runner`; provider guides describe how
their wrappers receive inputs and publish exports. Retain input, request and
output checksums plus the exact provider job identity.

## Admit and use the fine-tuned model

Cloud training does not eliminate serving requirements. Independent admission and
the app's native serving route need a compatible Linux/CUDA environment with
roughly 58 GiB free VRAM and 58 GiB available host staging RAM. Run the workspace
on that host and use shared browser access if your computer cannot serve it.
Follow [native app setup](../models/perplexity-native.md#train-through-the-application).

Admission loads the returned runtime, recomputes validation/calibration/test
metrics from reserved local records and compares a second load. It retains the
trained global temperature and original training report. Provider success alone
does not activate a model.

Inspect **Models and releases** and the frozen acceptance results. An owner can
activate an eligible version or roll back. Use Text/JSON in **Decisions and audit**,
or integrate the [decision API](../api-integration-guide.md) with abstention and
fallback behavior. See [returned-model admission](../remote-training-guide.md#returned-model-admission)
for manual staging and [standalone native prediction](../models/perplexity-native.md#use-the-exported-model-for-your-domain)
for use outside the application.
