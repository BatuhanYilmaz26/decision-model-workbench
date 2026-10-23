# Cloud training configuration templates

[Example directory](../README.md) · [Provider directory](../../docs/remote-training/README.md) · [Project overview](../../README.md)

These templates describe operator-approved resources for the application-managed training controllers. Replace their fictional values using the corresponding guide; they are not deployable account configurations.

| Template | Configuration guide | Direct provider walkthrough |
|---|---|---|
| [aws-profiles.json](aws-profiles.json) | [Managed AWS](../../docs/remote-training/aws.md#application-managed-training) | [SageMaker AI](../../docs/remote-training/aws.md#direct-submission) |
| [azure-profiles.json](azure-profiles.json) | [Managed Azure](../../docs/remote-training/azure.md#application-managed-training) | [Azure Machine Learning](../../docs/remote-training/azure.md#direct-submission) |
| [gcp-profiles.json](gcp-profiles.json) | [Managed GCP](../../docs/remote-training/gcp.md#application-managed-training) | [Google Cloud](../../docs/remote-training/gcp.md#direct-submission) |
| [modal-profiles.json](modal-profiles.json) | [Managed Modal](../../docs/remote-training/modal.md#application-managed-training) | [Modal Function](../../docs/remote-training/modal.md#direct-submission) |
| [runpod-profiles.json](runpod-profiles.json) | [Managed RunPod](../../docs/remote-training/runpod.md#application-managed-training) | [RunPod Pod](../../docs/remote-training/runpod.md#direct-submission) |

Confirm the account, tenant scope, region, GPU, image digest, source identity, storage and limits. Supply credentials through the guide's private operator configuration, and obtain the required data-transfer and compute approvals before submission.

Use [provider compatibility checks](../../docs/verification.md#provider-compatibility-checks) with non-sensitive examples. Verify cancellation, resource termination, retained storage and charges. Follow [remote admission](../../docs/remote-training-guide.md#returned-model-admission) before registering a returned model export.
