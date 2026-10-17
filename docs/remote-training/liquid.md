# Train Liquid models on a cloud GPU

[Provider directory](README.md) · [Model directory](../models/README.md) · [Project overview](../../README.md)

Use AWS, Azure, GCP, Modal or RunPod to train a domain model when your computer
cannot run its training profile. The application prepares the same reviewed,
frozen study for every provider. A shared launcher selects the matching native
trainer, and the returned export must pass independent admission and owner release.

| Backend | Domain training method | Data and local model guide |
|---|---|---|
| `liquid-trl` | d1-3B LoRA with its native causal readout | [d1-3B](../models/liquid.md) |
| `liquid-unsloth` | d1-3B FastVisionModel/Unsloth LoRA | [Unsloth setup](../models/liquid.md#train-with-fastvisionmodel-and-unsloth) |
| `liquid-omni` | Original d1-omni-600M decision head; frozen trunk/media encoders | [Omni](../models/liquid-omni.md) |

These routes accept text/JSON. Use the shared intake to describe the decision and
data you need, upload or enter examples, and review labels. Optional synthetic
generation uses a separate generative teacher; approved candidates remain
training-only. The native converters preserve independent real validation,
calibration and test records. d1-3B SFT requires one-hot training/validation
targets; omni retains normalized soft labels. The model guides explain each format.

The cloud image recipe is experimental. Validate its dependency, GPU, model-cache
and provider behavior before submitting paid jobs. Official API contracts and
local fixtures do not establish account access, final bills or domain quality.
Cloud training also does not remove the serving model's memory requirements:
independent admission needs a compatible local or operated serving environment.

## Build a matching training image

Use a Linux/amd64 Docker builder, sufficient disk space and the repository revision
you will use to prepare the study. The image installs a separate model profile
and caches the exact public checkpoint during construction. No domain data or
provider credentials belong in the image. Review the model license before building
or redistributing it.

From the repository root, build one selected backend:

```bash
docker build --platform linux/amd64 -f docker/models/Dockerfile.liquid --build-arg LIQUID_PROFILE=liquid-omni -t decision-liquid-omni .
docker run --rm decision-liquid-omni python -c "print(open('/app/liquid-image.json').read())"
docker run --rm --gpus all --network none decision-liquid-omni python -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name())"
```

Choose `liquid-trl` or `liquid-unsloth` instead when building a d1-3B image, and
use a distinct image tag for each profile. The TRL/omni profiles use Transformers
5.19; Unsloth uses its separate compatible Transformers 5.17 / TRL 1.13 stack.
Do not combine their dependency files into one environment. Matching Torch 2.10
and torchvision 0.25 CUDA 12.6 wheels are installed before the selected profile.

The image metadata records backend, checkpoint revision, package versions and
package-source SHA-256. Confirm that the study and image use identical source.
Pin the pushed registry image by digest rather than its mutable tag. Rebuilding
an image with changed source requires a new study and image profile.

Allow at least the checkpoint size plus working data and retained outputs. Omni's
float32 checkpoint is about 2.35 GB; d1-3B's base weights are about 6.25 GB.
Set provider disk/volume and artifact limits above the full export, adapters,
native data and reports. For a first GPU check, use short inputs, batch size 1
and one or two optimizer updates on fictional data. A successful small run checks
mechanics; use independent domain evaluation to choose a real training budget.

## Configure the provider and image capability

Start with the corresponding private [cloud profile template](../../examples/cloud/README.md)
and its provider guide below. Keep credentials in the operator/API/worker
environment. Enter your own provider keys or service identity there; the browser
selects an approved profile rather than receiving its credentials.

In that profile, set the pushed `image_digest`, matching `source_sha256`, GPU,
region, storage and limits. Explicitly declare the image's compatible backend:

```json
{
  "training_backends": ["liquid-omni"]
}
```

This fragment is an addition to a complete provider profile, not a standalone
profile. Declare only backends whose environments and cached assets the image
actually provides. Profiles without this field retain their existing Laya/Unsloth
decision-head meaning and cannot allocate Liquid training. The controller rejects
an undeclared backend before creating a cloud run. This declaration is an operator
assertion; an image digest alone does not certify its contents.

| Provider | Service and launcher | Storage and completion | Configuration and official contract |
|---|---|---|---|
| AWS | SageMaker AI training; `cloud_runner` via explicit container entrypoint/arguments | S3 input channel; `/opt/ml/model` collected into the model artifact | [Managed AWS](../managed-cloud-training.md), [AWS container contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html) |
| Azure | Azure ML command job; `azure_runner` invokes the shared launcher | Named downloaded input and uploaded output | [Managed Azure](../managed-azure-training.md), [Azure environments](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-manage-environments-v2?view=azureml-api-2) |
| GCP | CustomJob; `gcp_runner` invokes the shared launcher | Generation-bound GCS input/output; training runs on ordinary local disk | [Managed GCP](../managed-gcp-training.md), [Google custom containers](https://cloud.google.com/vertex-ai/docs/training/create-custom-container) |
| Modal | SDK GPU Sandbox; `modal_runner` invokes the shared launcher | Dedicated durable Volume; committed output and shutdown observation | [Managed Modal](../managed-modal-training.md), [Modal Sandboxes](https://modal.com/docs/guide/sandboxes) |
| RunPod | Dedicated GPU Pod; `runpod_runner` invokes the shared launcher | Network Volume output; Pod termination is separate from trainer exit | [Managed RunPod](../managed-runpod-training.md), [RunPod Pod lifecycle](https://docs.runpod.io/pods/manage-pods) |

The provider-specific identity, upload, stop, uncertain-outcome and artifact
retrieval protocols remain in those guides. No provider job is created when you
prepare a study. Account quotas, data transfer, retained storage and compute can
incur charges when you submit or retain resources.

## Prepare and submit from the application

1. Follow the [localhost setup](../../README.md#run-on-your-computer). Define your
   domain, review examples, prepare its snapshot and save release criteria.
2. Select the Liquid training backend, then **Cloud GPU**. A cloud study uses
   CUDA and a pre-cached checkpoint; training-time model downloads are disabled,
   including for providers that run without network access. Your local computer
   does not need a GPU to prepare or approve the study.
3. Under **Train on a cloud GPU**, select the provider and enter its approved
   region/environment, GPU identity and pinned training-image digest. Confirm
   data transfer approval and choose **Prepare remote training study**.
4. Retrieve the frozen inputs if operating the trainer yourself. For a managed
   run, select its matching provider profile and approve both data transfer and
   paid compute. Submit, monitor and stop using that provider's managed guide.
5. Retrieve the complete export after successful provider completion. It must
   match the original study, source, checkpoint, settings and data hashes.
   A failed or uncertain run must be reconciled before another allocation.
6. Configure the corresponding model interpreter for local admission and serving:
   `DMT_LIQUID_PYTHON`, `DMT_LIQUID_UNSLOTH_PYTHON` or `DMT_OMNI_PYTHON`.
   Follow [remote admission](../remote-admission-guide.md) to stage the export or
   use managed retrieval to queue it. Inspect the independently recomputed metrics
   and activate an eligible version. Use **Make decision** or the
   [decision API](../product-workflow.md#6-release-and-integrate).

## Run the frozen study on an operated GPU host

The same six-file archive can be used on your own provider VM or compatible
container. Retain its recorded archive and request SHA-256 values. From the
matching model environment, run the common launcher with your archive paths:

Set the Bash variables `ARCHIVE_SHA256` and `REQUEST_SHA256` to the values from
your approved remote-study record. Copy its downloaded archive to
`data/liquid-study.zip` on the host, then validate it without training:

```bash
python -m decision_workbench.training.cloud_runner --archive data/liquid-study.zip --archive-sha256 "$ARCHIVE_SHA256" --request-sha256 "$REQUEST_SHA256" --work data/liquid-check --out data/liquid-check-export --check-only
```

That checks the archive, source, four data roles, native study and settings.
Use new work/output paths for the actual run; the check's directory is preserved:

```bash
python -m decision_workbench.training.cloud_runner --archive data/liquid-study.zip --archive-sha256 "$ARCHIVE_SHA256" --request-sha256 "$REQUEST_SHA256" --work data/liquid-train --out data/liquid-export
```

The output must be new or empty. The launcher refuses model,
source, data or configuration substitution and produces a portable manifest-bound
export. Do not retrofit a standalone checkpoint with fabricated remote provenance.

The returned bundle retains only the reviewed publisher code needed by the
native model. Admission verifies those code/configuration hashes before any custom
model loading, measures validation/calibration/test predictions, performs a second
load and preserves the fitted temperatures. Supplied validation/test metrics do
not determine the local release decision. Retain the provider resource receipts
separately from the model bundle.

Sources and interface assumptions were checked on 17 October 2026. Use the
[compatibility guide](../compatibility-and-upgrades.md) when changing provider APIs,
SDKs, image packages or model revisions.
