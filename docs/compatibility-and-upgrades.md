# Runtime compatibility and upgrades

Choose the dependency profile for your operating system and training backend before installation. Keep the CPU application separate from GPU training environments. A newer package can change model serialization, probability extraction or CUDA behavior even when it installs successfully.

Use compatible, validated versions. A working pinned profile does not need an upgrade merely because a newer release exists. Change it when an applicable fix, required feature or provider deprecation justifies the change, and verify the resulting behavior before replacing the accepted environment.

The version and API comparisons below are dated **14 October 2026**. Refer to the linked upstream resources when upgrading; the [source metadata](../research/runtime-sources-2026-10-14.json) records the corresponding retrieval dates and hashes. Benchmark tables retain their own dates and model versions.

## Cloud training interfaces

| Provider | Interface used by this project | Upgrade considerations and official resources |
|---|---|---|
| AWS | SageMaker AI Training Jobs; Boto3/Botocore 1.43.113 | Supply a training image, execution role, input channels and S3 output location. Preserve the `/opt/ml` container contract and handle terminal job status. [CreateTrainingJob](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html), [training containers](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html) |
| Azure | Azure Machine Learning command jobs; CLI extension v2; Machine Learning REST API 2026-09-01 | Version the job and Blob interfaces separately. An API version does not establish regional GPU availability or workspace permissions. [Command job schema](https://learn.microsoft.com/en-us/azure/machine-learning/reference-yaml-job-command?view=azureml-api-2), [job REST API](https://learn.microsoft.com/en-us/rest/api/azureml/jobs/create-or-update?view=rest-azureml-2026-09-01) |
| GCP | Regional `aiplatform.googleapis.com/v1` CustomJobs; `gcloud ai custom-jobs`; google-auth 2.62.0 | Stage data in Cloud Storage, specify the container and machine/accelerator configuration, and preserve output artifacts. The CustomJob interface remains the relevant training API despite documentation naming changes. [Custom training](https://cloud.google.com/vertex-ai/docs/training/create-custom-job), [CLI](https://cloud.google.com/sdk/gcloud/reference/ai/custom-jobs/create), [API discovery](https://aiplatform.googleapis.com/$discovery/rest?version=v1) |
| Modal | SDK 1.6.1; Sandbox and persisted Volumes; direct Function walkthrough | Mount the volume at the path expected by the runner. Confirm GPU type, timeouts, committed outputs and resource termination. [Sandbox reference](https://modal.com/docs/reference/modal.Sandbox), [GPU selection](https://modal.com/docs/guide/gpu), [Volumes](https://modal.com/docs/guide/volumes) |
| RunPod | REST API v2 at `https://api.runpod.io/v2`; S3-compatible artifact transport | Use the v2 schema. RunPod's v1 retirement is scheduled for 15 November 2026. Verify Pod termination and retained storage separately. [Migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1), [v2 schema](https://github.com/runpod/docs/blob/main/api-reference-v2/openapi.json) |

These adapters are experimental. Validate job submission, artifact retrieval, cancellation and charges in your own account using non-sensitive examples before sending enterprise data. Follow [provider compatibility checks](verification.md#provider-compatibility-checks) and the [five provider walkthroughs](remote-training-guide.md).

RunPod's current v2 schema adds optional global volumes and expresses `Mounts` through `BaseMounts`. The existing `mounts.network` and `mounts.persistent` fields remain supported. The managed adapter selects one approved network volume; it does not enable global volumes. Retain the selected mount configuration when updating a job. [Official v2 schema](https://github.com/runpod/docs/blob/main/api-reference-v2/openapi.json)

## Model runtime versions

| Component | Project profile | Upstream version on 14 October | Why upgrading requires validation |
|---|---|---|---|
| Native Laya source | 0.4.1 at `1adc59f7e371deb601fcfa18a14e25db238addcc` | 0.4.6 at `c73908d96ff73c0062c340a11bf7c54ec7d8bc4d` | The training/export adapter depends on this source revision. Recheck heads, temperatures, probabilities and reload behavior. [Pinned metadata](https://github.com/NandhaKishorM/laya/blob/1adc59f7e371deb601fcfa18a14e25db238addcc/pyproject.toml), [newer metadata](https://github.com/NandhaKishorM/laya/blob/c73908d96ff73c0062c340a11bf7c54ec7d8bc4d/pyproject.toml) |
| Unsloth and Zoo | 2026.10.3 | 2026.10.5 | The probability adapter checks exact versions and uses internal logits/temperature interfaces. Updating the package alone will be rejected. [Unsloth metadata](https://pypi.org/pypi/unsloth/json), [decision implementation](https://github.com/unslothai/unsloth/blob/main/unsloth/models/decision.py) |
| Vega | vegaml 0.8.0 | 0.9.0 | Keep the selected 0.8.0 profile until the newer library passes the adapter's training, probability, export and independent reload checks. Pin checkpoint revisions separately. [Vega package metadata](https://pypi.org/pypi/vegaml/0.9.0/json), [Vega source](https://github.com/NandhaKishorM/vegaml), [model card](https://huggingface.co/nandakishorm/vega-08b-public-intents) |
| PyTorch | 2.10.0 with CUDA 12.6 wheels | 2.14.1 | CUDA/Triton/xformers compatibility and model results require a new qualification. Select wheels from the official accelerator index. [Release profiles](https://pytorch.org/get-started/previous-versions/), [CUDA 12.6 wheels](https://download.pytorch.org/whl/cu126/torch/) |
| Transformers | 5.17.0 | 5.19.0 | Unsloth 2026.10.3 and 2026.10.5 require Transformers at or below 5.17.0, with additional excluded versions. [Unsloth 2026.10.3 constraints](https://pypi.org/pypi/unsloth/2026.10.3/json), [current constraints](https://pypi.org/pypi/unsloth/json) |
| Datasets | Windows model profile: 4.8.5 | 5.1.0 | Both Unsloth versions require Datasets below 5.0.0. Datasets 4.8.5 also caps fsspec at 2026.2.0. This creates a security upgrade conflict described below. [Datasets 4.8.5 constraints](https://pypi.org/pypi/datasets/4.8.5/json), [current Datasets](https://pypi.org/project/datasets/) |

The CPU application uses separate hash-locked profiles for Linux/amd64 CPython 3.12.15 and Windows CPython 3.13.5. Its build tooling is pip 26.2.1, setuptools 84.0.0 and wheel 0.48.0. GitHub Actions uses commit-pinned checkout v7.0.1 and setup-python v7.0.0. Select the [dependency profile](../requirements/README.md) that matches your interpreter and platform. [pip releases](https://pip.pypa.io/en/stable/news/), [checkout release](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-python release](https://github.com/actions/setup-python/releases/tag/v7.0.0)

## GPU dependency advisories

The pinned GPU profiles need dependency remediation and runtime requalification before an enterprise production rollout. Hashes and reproducible installation do not resolve known advisories.

| Profile/package | Advisory and affected operation | Upgrade boundary |
|---|---|---|
| Windows model profile: Datasets 4.8.5 | GHSA-379c-qx7v-6h59 concerns traversal in folder-builder `file_name` metadata; the reported fixed version is 5.0.1. [Upstream fix](https://github.com/huggingface/datasets/commit/f989ef9b4cc6c0039a7a82458eebca49e2b58b4b), [advisory](https://github.com/advisories/GHSA-379c-qx7v-6h59) | The fixed major version conflicts with both reviewed Unsloth versions' Datasets constraint. Do not force a dependency override and call it a supported profile. |
| Windows model profile: fsspec 2026.2.0 | GHSA-27vj-qcqg-25rc concerns template execution in `ReferenceFileSystem` when processing a supplied references document; fixed in 2026.6.0. [Maintainer advisory](https://github.com/fsspec/filesystem_spec/security/advisories/GHSA-27vj-qcqg-25rc) | Datasets 4.8.5 caps fsspec at 2026.2.0. Upgrade the compatible dependency set together. The Linux Vega lock uses fsspec 2026.9.0. |
| Both GPU profiles: Torch 2.10.0 | Dependency advisory data flags CVE-2025-3000 for `torch.jit.script` and CVE-2026-4538 for PT2 loading. The former lists 2.13.0 as fixed; the latter has no fixed version in the PyPA record. [Upstream issue](https://github.com/pytorch/pytorch/issues/149623), [PyPA PT2 record](https://github.com/pypa/advisory-database/blob/main/vulns/torch/PYSEC-2026-139.yaml) | Assess the affected operations and upstream policy before choosing a replacement Torch stack; then rerun GPU lifecycle checks. An advisory match alone does not establish exploitability through this application's routes. |

PyTorch treats model code as executable code and explains that several local-code/input reports fall outside its security vulnerability definition. Treat model sources and weights as trusted inputs, keep training isolated, and restrict access to credentials and enterprise data. These controls help constrain risk but do not certify a vulnerable dependency set. [PyTorch security policy](https://github.com/pytorch/pytorch/security/policy)

For a new deployment, start with the CPU application or a hosted decision adapter while preparing a compatible, patched GPU environment. Do not expose the historical GPU profiles to untrusted workloads. A future profile should resolve upstream dependency constraints, pass `pip check` and advisory review, and complete actual train/export/reload/evaluation on its intended hardware.

## Upgrade procedure

1. Record the currently accepted dependency lock, source/checkpoint revisions, container digest and evaluation outputs. Keep historical benchmark/model records attached to the versions that produced them.
2. Check official release notes, package constraints, deprecations and advisories. Resolve the complete dependency set for one platform; keep accelerator wheels on their intended index.
3. Install into a new environment. Run `pip check`, the repository checks and the relevant behavioral tests. Rebuild containers rather than assuming an environment lock guarantees an image build.
4. For model changes, run real training, calibration, export, independent reload and serving parity on the target hardware. Test longest inputs and frozen acceptance criteria on reserved domain data.
5. For provider changes, verify actual account submission, retrieval, interruption/cancellation, terminal state and billing. For deployment changes, measure concurrency, memory, latency, recovery and identity behavior under your expected load.

See [testing](verification.md), [evaluation](evaluation-guide.md), [model containers](model-container-guide.md) and [deployment](deployment-guide.md) for the corresponding checks.
