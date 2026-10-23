# Remote GPU provider directory

[All guides](../README.md) · [Project overview](../../README.md)

Train a decision model on a rented GPU when local memory is insufficient. Start with the [shared data and container protocol](../remote-training-guide.md#study-protocol), then use [provider selection](../remote-training-guide.md#choosing-a-provider) to compare GPU sizing, identity, storage and total cost.

For d1-3B TRL/Unsloth or d1-omni-600M, follow the [Liquid cloud workflow](liquid.md)
for native formats, image compatibility, cached checkpoints and independent admission.
For the six D3 variants, use the separate [D3 cloud workflow](d3.md) and its compatible native image.
For open Perplexity v1.1, follow [native full-weight cloud training](perplexity.md)
for the H200/RAM requirements, experimental image and returned-model admission.

For Vega, follow the [paired-data cloud workflow](vega.md); its native trainer and image declaration preserve the extra applicability files.

| Provider | Submission and application-managed workflow | Configuration template |
|---|---|---|
| AWS | [SageMaker AI](aws.md) | [AWS profile](../../examples/cloud/aws-profiles.json) |
| Azure | [Machine Learning](azure.md) | [Azure profile](../../examples/cloud/azure-profiles.json) |
| Google Cloud | [Custom GPU jobs](gcp.md) | [GCP profile](../../examples/cloud/gcp-profiles.json) |
| Modal | [GPU Functions and managed Sandboxes](modal.md) | [Modal profile](../../examples/cloud/modal-profiles.json) |
| RunPod | [GPU Pods](runpod.md) | [RunPod profile](../../examples/cloud/runpod-profiles.json) |

Direct walkthroughs use your operator environment. Managed workflows add the platform's owner approvals, frozen input identity, job history and returned-artifact validation. Both need an approved account, usable quota, a compatible image and independent domain evaluation. Submitting compute or retaining provider storage can incur charges.

Before release, complete [provider compatibility checks](../verification.md#provider-compatibility-checks), retrieve the full export and follow [remote model admission](../remote-training-guide.md#returned-model-admission). Check [runtime compatibility](../compatibility-and-upgrades.md) when changing an API, SDK or training profile.
