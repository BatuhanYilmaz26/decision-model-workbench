# Cloud training configuration templates

[Example directory](../README.md) · [Provider directory](../../docs/remote-training/README.md) · [Project overview](../../README.md)

These templates describe operator-approved resources for the application-managed training controllers. Replace their fictional values using the corresponding guide; they are not deployable account configurations.

| Template | Configuration guide | Direct provider walkthrough |
|---|---|---|
| [aws-profiles.json](aws-profiles.json) | [Managed AWS](../../docs/managed-cloud-training.md) | [SageMaker AI](../../docs/remote-training/aws.md) |
| [azure-profiles.json](azure-profiles.json) | [Managed Azure](../../docs/managed-azure-training.md) | [Azure Machine Learning](../../docs/remote-training/azure.md) |
| [gcp-profiles.json](gcp-profiles.json) | [Managed GCP](../../docs/managed-gcp-training.md) | [Google Cloud](../../docs/remote-training/gcp.md) |
| [modal-profiles.json](modal-profiles.json) | [Managed Modal](../../docs/managed-modal-training.md) | [Modal Function](../../docs/remote-training/modal.md) |
| [runpod-profiles.json](runpod-profiles.json) | [Managed RunPod](../../docs/managed-runpod-training.md) | [RunPod Pod](../../docs/remote-training/runpod.md) |

Confirm the account, tenant scope, region, GPU, image digest, source identity, storage and limits. Supply credentials through the guide's private operator configuration, and obtain the required data-transfer and compute approvals before submission.

Use [provider compatibility checks](../../docs/verification.md#provider-compatibility-checks) with non-sensitive examples. Verify cancellation, resource termination, retained storage and charges. Follow [remote admission](../../docs/remote-admission-guide.md) before registering a returned model export.
