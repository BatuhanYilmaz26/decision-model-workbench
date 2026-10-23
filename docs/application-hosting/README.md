# Host the application in your cloud

[All guides](../README.md) · [Project overview](../../README.md)

Application hosting runs the browser, API and durable workers for your users. It is separate from renting a GPU for an individual training run.

| Target | Application deployment | Training jobs |
|---|---|---|
| AWS | [ECS Fargate, RDS and S3](aws.md) | [SageMaker training](../remote-training/aws.md) |
| Azure | [Container Apps, PostgreSQL, Blob and Key Vault](azure.md) | [Machine Learning jobs](../remote-training/azure.md) |

These recipes use pinned CPU control-plane images. They support CPU baseline training, hosted decision policies, data/review workflows and the existing remote-training controllers when their profiles and permissions are configured. Native model execution needs matching runtimes and resources; the supplied hosting profiles do not add a GPU. See [model environments](../setup.md#configure-model-training-and-serving).

Configure [shared-workspace operations](../shared-workspace-operations-guide.md), [organization sign-in](../enterprise-sign-in-guide.md) and [coordinated recovery](../shared-recovery-guide.md) before production adoption. Validate networking, identity, quotas, storage and model quality in your own deployment.
