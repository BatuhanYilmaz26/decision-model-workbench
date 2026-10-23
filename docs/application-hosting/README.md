# Host the application in your cloud

[All guides](../README.md) · [Project overview](../../README.md)

Application hosting runs the browser, API and durable workers for your users. It is separate from renting a GPU for an individual training run.

| Target | Application deployment | Training jobs |
|---|---|---|
| AWS | [ECS Fargate, RDS and S3](aws.md) | [SageMaker training](../remote-training/aws.md) |

The AWS recipe uses the pinned CPU control-plane image. It supports CPU baseline training, hosted decision policies, data/review workflows and the existing remote-training controllers when their profiles and permissions are configured. Native model execution needs the matching runtime and resources; Fargate does not supply a GPU. See [model environments](../setup.md#configure-model-training-and-serving).

Configure [shared-workspace operations](../shared-workspace-operations-guide.md), [organization sign-in](../enterprise-sign-in-guide.md) and [coordinated recovery](../shared-recovery-guide.md) before production adoption. Validate networking, identity, quotas, storage and model quality in your own deployment.
