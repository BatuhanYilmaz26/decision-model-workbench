# Host Decision Model Workbench on AWS

[Application hosting](README.md#application-hosting) · [Project overview](../../README.md) · [AWS training jobs](../remote-training/aws.md#direct-submission)

This recipe generates a CloudFormation stack for separate API, worker and Streamlit services on ECS Fargate. It creates private RDS PostgreSQL metadata, a private versioned S3 artifact bucket, HTTPS load balancing, service roles, CloudWatch logs and basic infrastructure alarms.

**The stack creates billable resources.** Review the change set, desired task counts, database class/Multi-AZ choice and retention settings before executing it. The template generator makes no AWS calls.

## Prepare your account and network

You need:

- The [local application environment](../../README.md#run-on-your-computer), Docker with Linux/amd64 builds and AWS CLI v2 authenticated to your own account.
- Permission to create the stack's ECS, IAM, RDS, S3, load-balancer, Secrets Manager, log and alarm resources, including passing its task roles.
- An existing VPC with at least two public load-balancer subnets and two private subnets in different Availability Zones. Private tasks need outbound connectivity to ECR, Logs, Secrets Manager, S3 and the external providers you approve. Supply NAT routing or the relevant VPC endpoints; private database subnets do not require public access.
- A hostname you control and a matching ACM certificate in the deployment region. Create its DNS alias after the stack returns the load-balancer DNS name and zone ID.
- Capacity and quota for the selected Fargate and RDS sizes. Verify an available PostgreSQL 18 version with `aws rds describe-db-engine-versions --engine postgres`. The database defaults to major version `18`; you can supply an available minor version through `DatabaseVersion`.

The default services are two API tasks, one worker and one browser task. API/browser tasks receive 1 vCPU/2 GiB each; the worker receives 2 vCPU/4 GiB. Each task has 40 GiB of ephemeral storage. These are a starting CPU profile, not a capacity guarantee for large datasets or native model weights.

## Build and publish an immutable image

From the repository root, build the pinned CPU image and its [deployment extension](../../docker/deployment/Dockerfile):

```text
docker build --platform linux/amd64 -t dmt-base:release .
docker build --platform linux/amd64 --build-arg BASE_IMAGE=dmt-base:release -f docker/deployment/Dockerfile -t dmt-application:release .
```

The extension adds hash-pinned PostgreSQL/OIDC dependencies and the public RDS CA bundle. It includes no model weights. Its API and worker use the same source and compatible dependency versions. Keep native model runtimes in their documented environments; add and qualify them explicitly if you build a different application image.

Create an ECR repository with immutable tags, authenticate Docker and push your image. Replace `REGION`, `ACCOUNT` and `RELEASE` with your values:

```text
aws ecr create-repository --repository-name decision-model-workbench --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true --region REGION
aws ecr get-login-password --region REGION | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.REGION.amazonaws.com
docker tag dmt-application:release ACCOUNT.dkr.ecr.REGION.amazonaws.com/decision-model-workbench:RELEASE
docker push ACCOUNT.dkr.ecr.REGION.amazonaws.com/decision-model-workbench:RELEASE
aws ecr describe-images --repository-name decision-model-workbench --image-ids imageTag=RELEASE --region REGION --query "imageDetails[0].imageDigest" --output text
```

Use the resulting `ACCOUNT.dkr.ecr.REGION.amazonaws.com/decision-model-workbench@sha256:...` URI in the stack. The template requires a digest, so a mutable tag cannot silently replace the deployed code. [ECR authentication](https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html).

## Prepare service-private configuration

Provision your initial workspace key. Save the displayed bearer token in your password manager; the JSON file stores its hash:

```text
domain-platform provision-key --file data/config/operator-keys.json --tenant your-team --actor owner --role admin
python -c "import json; from pathlib import Path; Path('data/config/backend.json').write_text(json.dumps({'operator_keys': json.loads(Path('data/config/operator-keys.json').read_text()), 'environment': {}}), encoding='utf-8')"
aws secretsmanager create-secret --name decision-model-workbench/backend --secret-string file://data/config/backend.json --region REGION
```

Keep these files private. Record the returned secret ARN as `BackendSecretArn`.

The backend JSON supports:

| Field | Use |
|---|---|
| `operator_keys` | The complete approved workspace-key hash/tenant/actor/role inventory |
| `sso` | Optional trusted API issuer, audience, bindings and revocations from the [enterprise sign-in guide](../authentication-guide.md#organization-sign-in) |
| `decision_profiles` | Supported hosted decision profiles |
| `cloud_profiles` | Approved remote-training account/image/resource profiles |
| `taxonomy_profiles` | Optional category-discovery provider profiles |
| `gcp_credentials` | Optional private JSON credential document for the managed GCP controller |
| `semantic_profile` | Optional semantic-audit profile for an explicitly provisioned matching runtime |
| `environment` | String-valued provider/cloud/teacher/generation/schema/taxonomy/semantic settings, documented model-runtime Python paths and `DMT_MODEL_CACHE` |

Use the existing [hosted API](../hosted-decisions-guide.md), [teacher](../setup.md#configuration--configure-a-teacher) and [five-provider training](../remote-training-guide.md#study-protocol) guides for the file formats, allowed settings, credentials and approval controls. Put credential documents in their typed fields rather than redirecting profile-file paths through `environment`. Secret values are limited to Secrets Manager's 64 KiB string size.

The browser receives neither this secret nor database/provider credentials. For OIDC, create a **separate** UI JSON secret containing only `secrets_toml`, whose value is the Streamlit auth TOML described in [enterprise sign-in](../authentication-guide.md#organization-sign-in). Use the public HTTPS callback URL. Set `UiSecretArn` and `UiAuthMode=oidc`; otherwise the shared-workspace key login remains available. The API's issuer configuration and the browser's OAuth client are separate settings.

If your imported secrets use a customer-managed KMS key, supply its ARN as `SecretsKmsKeyArn` and authorize the relevant roles in the key policy. Provider credentials stay server-side. The normal backend role has artifact read/write permissions, not object deletion, bucket administration or database-administrator access.

## Generate and review the stack

```text
python -m decision_workbench.platform.aws_deployment --output data/deploy/aws-stack.json
aws cloudformation validate-template --template-body file://data/deploy/aws-stack.json --region REGION
```

For local schema checks, install `cfn-lint==1.58.0` in a separate tooling environment and run `cfn-lint --regions REGION --template data/deploy/aws-stack.json`. Keep tooling dependencies separate from the application environment.

Create `data/deploy/parameters.json` using the [parameter example](../../examples/deployment/aws-parameters.example.json). Replace every account, network, hostname, certificate and secret value. Start with `ServicesEnabled=false`: infrastructure can initialize before any application service starts. Keep this private parameter file current for later updates.

```text
aws cloudformation create-change-set --stack-name dmt-application --change-set-name initial --change-set-type CREATE --template-body file://data/deploy/aws-stack.json --parameters file://data/deploy/parameters.json --capabilities CAPABILITY_IAM --region REGION
aws cloudformation wait change-set-create-complete --stack-name dmt-application --change-set-name initial --region REGION
aws cloudformation describe-change-set --stack-name dmt-application --change-set-name initial --region REGION
```

Inspect the proposed resources and charges, then execute the reviewed change set:

```text
aws cloudformation execute-change-set --stack-name dmt-application --change-set-name initial --region REGION
aws cloudformation wait stack-create-complete --stack-name dmt-application --region REGION
aws cloudformation describe-stacks --stack-name dmt-application --region REGION --query "Stacks[0].Outputs" --output json
```

The outputs include the cluster, bootstrap/API task definitions, task security group, database identifier/endpoint, artifact bucket, service names and log group. The database password is generated in Secrets Manager and is not returned as a stack output.

## Initialize the database and shared authorization

Create `data/deploy/network.json` using the same private subnet IDs and the output `TaskSecurityGroup`:

```json
{
  "awsvpcConfiguration": {
    "subnets": ["subnet-YOUR_PRIVATE_A", "subnet-YOUR_PRIVATE_B"],
    "securityGroups": ["sg-YOUR_TASK_GROUP"],
    "assignPublicIp": "DISABLED"
  }
}
```

Run the output `BootstrapTask` once:

```text
aws ecs run-task --cluster CLUSTER --task-definition BOOTSTRAP_TASK --launch-type FARGATE --platform-version 1.4.0 --network-configuration file://data/deploy/network.json --region REGION
aws ecs wait tasks-stopped --cluster CLUSTER --tasks TASK_ARN --region REGION
aws ecs describe-tasks --cluster CLUSTER --tasks TASK_ARN --region REGION --query "tasks[0].containers[].{name:name,exitCode:exitCode,reason:reason}"
```

Require exit code `0`. The bootstrap role alone can read the RDS administrator secret. It creates the dedicated application login and owned schema, then verifies the application credential. It does not change an existing role's password implicitly. Database connections require certificate/hostname verification.

Next create `data/deploy/publish-auth.json`:

```json
{
  "containerOverrides": [{
    "name": "application",
    "command": ["python", "-m", "decision_workbench.platform.deployment", "publish-auth", "--expected-auth-revision", "0"]
  }]
}
```

Run the output `ApiTask` as a one-off task with this override:

```text
aws ecs run-task --cluster CLUSTER --task-definition API_TASK --launch-type FARGATE --platform-version 1.4.0 --network-configuration file://data/deploy/network.json --overrides file://data/deploy/publish-auth.json --region REGION
```

Wait for it to stop and require exit code `0` as above. Its private log reports the committed authorization revision/hash, never bearer tokens. API instances read this shared PostgreSQL authority, so revocation and role changes do not depend on rolling every container. Provider secrets are not stored in this authority.

## Start and use the application

Create the DNS alias for your `Hostname` using `LoadBalancerDns` and `LoadBalancerZoneId`. Verify the hostname is covered by your ACM certificate.

Change `ServicesEnabled` to `true` in the approved parameter file. Create/review/execute an `UPDATE` change set using the same template and current parameters, with a new change-set name such as `start-services`. Wait for `stack-update-complete` and for the ECS services to become stable:

```text
aws ecs wait services-stable --cluster CLUSTER --services API_SERVICE WORKER_SERVICE UI_SERVICE --region REGION
```

Open `https://YOUR_HOSTNAME`. Use the OIDC login or choose **Shared workspace**, enter that same HTTPS API URL, and use the bearer token saved earlier. Follow the [first-project walkthrough](../app-guide.md#screen-manual--first-project-walkthrough). Start with a reviewed CPU baseline study to exercise data intake, worker preparation, training, release and prediction before enabling external teachers or paid GPU jobs.

`/v1/*`, `/health`, `/ready` and API documentation routes go to the API; the remaining paths go to Streamlit. HTTP redirects to HTTPS. The browser service has no database or artifact permissions. Normal API/worker tasks cannot read the RDS administrator secret.

For AWS training, attach your approved scoped policy through `AdditionalBackendPolicyArn`. Other providers need their own configured profiles/credentials. These permissions do not authorize automatic paid jobs: the existing owner review and submission controls still apply. Fargate supplies no GPU, and this image does not contain Torch or foundation weights. Select the appropriate [remote GPU workflow](../remote-training-guide.md#study-protocol) or qualify a suitable native runtime/topology for model execution.

## Monitor, update and recover

- Use `/ready`, [project operations](../shared-workspace-operations-guide.md), ECS service events and CloudWatch logs. The stack includes API unhealthy-target and low database-storage alarms. Supply an existing SNS topic ARN through `AlarmTopicArn` if you want alarm delivery; configure and confirm its subscriptions yourself.
- Completed projects, datasets, jobs, models and releases persist in RDS/S3. Local caches and unfinished task files are ephemeral. An interrupted job needs inspection and explicit resubmission; do not replay uncertain provider operations automatically.
- Use [coordinated shared recovery](../recovery-guide.md#shared-workspace-recovery) from an approved private-network operator environment and a dedicated backup role. RDS's seven-day database backups alone do not capture model objects. Protect an independent recovery vault; normal application roles deliberately lack its administrative permissions.
- To inspect the authorization revision and pending-job count, run `ApiTask` once with the command override `python -m decision_workbench.platform.deployment status` and read its private log.
- To change workspace keys, roles, issuers or revocations, update the complete approved backend secret and run `publish-auth` with the current expected revision. Changed content with a stale revision is refused; an identical publication can be retried without creating another revision. An explicit empty `operator_keys` list with no SSO denies all keys. Local credential-file commands do not edit the cloud authority.
- Changing provider secrets/profiles or browser OIDC settings requires a new task deployment to reload them. Do not assume updating Secrets Manager refreshes running process environments/files. [AWS secret refresh behavior](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data.html).
- Before updating images, pause submissions and inspect/drain pending jobs. Keep API and worker on the same approved digest. For a strict cutover, disable services only after draining, update the image/configuration, then re-enable them. Workers begin draining on `SIGTERM`; an in-progress claim/job can continue, and ECS's 120-second stop timeout can interrupt a longer job.
- Review every update change set. The deployment circuit breaker can roll back to a previously completed ECS deployment; it does not restore a database or undo external calls. Keep the prior image digest and parameter file. Restore into a fresh database/schema and artifact scope when state recovery is needed. [Circuit-breaker behavior](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html).
- Shared authorization history is part of cloud metadata backups. After recovery, publish your current approved trust/revocation policy before exposing a restored deployment; provider and browser secrets remain separately provisioned.

Before production traffic, validate the deployed DNS/TLS chain, IAM permissions, login/role isolation, provider access/billing, model runtime, quotas, load and a cold restore. Local SDK/container checks and CloudFormation validation do not establish these account-specific conditions.

### Restore to replacement infrastructure

Create replacement infrastructure with services disabled and run the database bootstrap. When recovering an existing workspace, leave authorization publication until after the restore. From your approved private-network operator environment, follow [shared recovery](../recovery-guide.md#shared-workspace-recovery) using the replacement database and an independent target artifact scope. The database security group accepts application-task security-group members; arrange equivalent approved operator access without making RDS public.

Set the stack's `DatabaseSchema` and `ArtifactPrefix` parameters to the restored schema and returned object prefix. Review that update before starting any service; both application configuration and its S3 policy must use the same restored scope. Run the `status` task to inspect the recovered authorization revision, then publish your current approved configuration with that expected revision. Verify cold predictions, role isolation and unfinished-job state before promoting the replacement hostname. Isolate the original deployment and reconcile upstream work before permitting new requests.

## Remove a deployment deliberately

Drain and disable services, capture/verify recovery data and inspect retained resources before deleting the stack. RDS deletion protection is enabled; disable it deliberately through your approved database procedure when you intend to remove the instance. Stack deletion creates a final database snapshot and retains the artifact bucket, application database secret and log group. Imported configuration secrets are also independent of the stack. These retained resources can continue to incur charges until your approved retention/deletion policy removes them. Do not delete artifact versions or backup keys merely to make stack deletion succeed.

References: [Fargate task parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html), [execution-role permissions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_execution_IAM_role.html), [CloudFormation ECS services](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-service.html), [RDS TLS and CA bundles](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html), [RDS PostgreSQL versions](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-versions.html).
