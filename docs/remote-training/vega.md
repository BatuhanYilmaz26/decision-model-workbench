# Train Vega on a cloud GPU

[Provider directory](README.md) | [Vega workflow](../vega-guide.md) | [Project overview](../../README.md)

Train a domain adapter on AWS, Azure, GCP, Modal or RunPod when your computer cannot run Vega. The application freezes paired positive/applicability data and the shared cloud runner invokes the native Vega trainer. Retrieval reuses independent Vega admission before owner release. The image recipe is experimental; select and qualify the GPU, dependencies and provider account you will use.

## Prepare the domain and its paired data

1. Describe the decision, confirm the allowed outcomes and [upload or enter examples](../data-guide.md). Review the labels and reserve independent training, validation, calibration and test roles.
2. Prepare [applicability negatives](../applicability-guide.md), review them and reserve the paired snapshot. Set the owner's applicability/coverage criteria before submitting a study. At least sixty independent positive calibration families are required for the two disjoint fitting partitions; that minimum does not establish statistical adequacy.
3. Optionally [generate and review synthetic candidates](../synthetic-generation-guide.md) for training gaps. Preserve reserved real holdouts and seed lineage. The same canonical data and native Vega conversion apply on every provider.

The ZIP contains `request.json`, four `data/{role}.jsonl` positives and four `data/gate_{role}.jsonl` negatives. It has no `data/manifest.json`. The request binds both reservations, all eight file hashes, the question schema, source and pinned engine/backbone, trainer settings and applicability preflight. Keep the complete ZIP unchanged.

## Prepare a compatible image

Build on a Linux/amd64 image builder with adequate disk space, including a cloud builder if needed. A local GPU is not required for image construction. Use the current repository source and the pinned [Vega base recipe](../../docker/models/Dockerfile.vega), then derive its [cloud training image](../../docker/models/Dockerfile.vega-cloud). The base contains dependencies without pretrained weights. The derived cloud build downloads both pinned public checkpoints into its cache, so training also works where outbound network access is disabled. This increases image size and builder storage/transfer requirements.

```bash
docker build --platform linux/amd64 -f docker/models/Dockerfile.vega -t YOUR_REGISTRY/vega-base:YOUR_TAG .
docker push YOUR_REGISTRY/vega-base:YOUR_TAG
```

Read the pushed base image's exact digest from your registry, then supply that digest when building the training image:

```bash
docker build --platform linux/amd64 -f docker/models/Dockerfile.vega-cloud \
  --build-arg VEGA_IMAGE=YOUR_REGISTRY/vega-base@sha256:YOUR_BASE_DIGEST \
  -t YOUR_REGISTRY/vega-cloud:YOUR_TAG .
docker push YOUR_REGISTRY/vega-cloud:YOUR_TAG
```

The derived image retains the base's model/provider dependencies and project source. It runs as root for provider-mounted training outputs; use the separate base/application images for the web service. Its build uses the same pinned asset helper as native training and stores both checkpoints at `DMT_MODEL_CACHE=/opt/dmt/vega-hub`, then enables offline execution. Leave the study's cached-assets setting enabled. An incomplete cache must fail rather than switch versions. Managed Modal Sandboxes block outbound networking, so downloading missing weights during that job is not an alternative; prepare the complete image first. Refer to the [model container guide](../model-container-guide.md) for approved assets and the [dependency guide](../compatibility-and-upgrades.md) for profile limits/advisories.

Record the final training image digest and the source fingerprint from that image:

```bash
docker run --rm YOUR_REGISTRY/vega-cloud@sha256:YOUR_TRAINING_DIGEST \
  python -c 'from decision_workbench.training.provenance import source_fingerprint; print(source_fingerprint())'
```

That fingerprint must match the controlling application's package and the frozen study. The example requires you to replace registry, tag and digest values. Do not add private data or provider keys to image layers.

## Configure one of the five providers

Use its existing profile template and managed setup. Set `training_backends` to `["vega"]`, replace the image digest/source fingerprint, and size GPU memory, host RAM, runtime and artifact capacity for the complete backbone, engine, adapter and calibration. Legacy profiles that omit the field continue to declare Laya/Unsloth only. An explicit declaration allows dispatch; it does not prove an image or GPU is qualified.

| Provider | Configure and approve the run | Provider runtime |
|---|---|---|
| AWS | [SageMaker setup](../managed-cloud-training.md), [profile](../../examples/cloud/aws-profiles.json) | SageMaker training job, study input channel and complete model export |
| Azure | [Azure ML setup](../managed-azure-training.md), [profile](../../examples/cloud/azure-profiles.json) | Managed command job with its mounted input/output paths |
| GCP | [Custom job setup](../managed-gcp-training.md), [profile](../../examples/cloud/gcp-profiles.json) | Regional custom GPU job with generation-bound object storage |
| Modal | [Sandbox setup](../managed-modal-training.md), [profile](../../examples/cloud/modal-profiles.json) | GPU Sandbox and persistent Volume |
| RunPod | [Pod setup](../managed-runpod-training.md), [profile](../../examples/cloud/runpod-profiles.json) | GPU Pod, approved SSH runtime and retained network storage |

Each controller invokes the shared archive validator and cloud runner. It rechecks both data roles and builds a fixed `decision_workbench.training.vega_runner` command with the frozen model/backbone and options, `--study-request` and `--device cuda`. It does not pass the eight-file study to a Laya, chat-SFT or Unsloth trainer. Provider-specific identity, submission, observation, stopping and storage controls remain in the linked guides.

## Submit, retrieve and release

1. In **Training jobs**, select **Vega domain adapter**, its positive/paired negative snapshots and **Cloud GPU**. Keep **Use only cached pinned Vega/backbone assets** enabled for the prepared cloud image. Prepare a remote study with the approved provider, region, GPU and image digest. This preparation allocates no compute.
2. Select the matching operator cloud profile. Approve both study-data transfer and paid compute before creating a managed run. Follow its submit/status/recovery controls. An ambiguous submission is not permission to create another resource.
3. On success, retrieve the complete export and queue independent admission. The admission environment needs the compatible Vega serving dependencies and enough capacity. It recomputes validation/test and applicability measurements, checks uncertainty and reload equivalence, and preserves model abstention.
4. Inspect the admitted version and release it only if the frozen owner gates pass. Use the authenticated [prediction workflow](../product-workflow.md#6-release-and-integrate); a closed gate or ambiguous prediction set must reach your fallback.
5. Stop paid resources and review retained storage separately. Cancellation or local failure does not prove billing has stopped. Vega has no optimizer-state resume or distributed-training path here.

For operator-managed execution, use the unchanged [eight-file request and standalone command](../remote-admission-guide.md#vega-eight-file-studies-and-independent-admission), then stage the export and provider receipt. Physical GPU/image execution, provider access/billing and quality on your domain require your own qualification; local contract/fixture checks do not establish those outcomes.
