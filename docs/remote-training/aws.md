# Train on AWS SageMaker AI

<a id="train-a-decision-model-with-aws-sagemaker-ai"></a>

[Documentation](../README.md) · [Project overview](../../README.md)

Choose direct operator submission or the application-managed workflow below. Both require your approved account, a compatible training image and usable resources. App-managed runs additionally retain approvals, frozen study identity, status and returned-model validation.

## Choose a section

- [Submit directly through the provider](#direct-submission)

- [Manage training through the workbench](#application-managed-training)

<a id="direct-submission"></a>

## Submit directly through the provider

<a id="direct-submission--train-a-decision-model-with-aws-sagemaker-ai"></a>

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Run the repository's Laya trainer in a SageMaker AI custom GPU container, with reviewed data staged in S3 and a complete model export returned to S3. Prepare the four data splits and training image using the [shared remote-training guide](../remote-training-guide.md#study-protocol).

Use the commands below for direct operator submission. For application-managed approvals, durable job history and local validation of returned weights, follow [managed AWS training](aws.md#application-managed-training). The standalone script does not create the application's cloud ledger or frozen-study request.

<a id="direct-submission--prepare-resources-and-stage-data"></a>

### Prepare resources and stage data

Choose a region with the requested SageMaker training-instance quota, a private S3 bucket and ECR image. The submitter needs job creation/description/stopping and permission to pass the designated execution role. The role needs scoped S3 input/output and image/log access; add the chosen KMS/VPC access where applicable. Keep the image and data in an approved region. Set `AWS_REGION`, `DMT_BUCKET`, `DMT_RUN_ID`, `DMT_EXECUTION_ROLE_ARN` and `DMT_TRAIN_IMAGE` to operator-owned values. `DMT_TRAIN_IMAGE` is the qualified ECR digest URI.

A G5 GPU has 24 GB memory; `ml.g5.xlarge` is a starting SKU, subject to SageMaker regional support/quota. Measure the actual model study's memory rather than assuming all backbones fit. [G5 hardware](https://aws.amazon.com/ec2/instance-types/g5/)

Upload the four reviewed files to a unique, data-only prefix:

```bash
aws s3 sync data/support/ "s3://$DMT_BUCKET/decision-runs/$DMT_RUN_ID/data/"
```

<a id="direct-submission--submit-through-boto3"></a>

### Submit through Boto3

Install `boto3` in an operator environment, authenticate through the approved AWS credential chain and save this as your submission script. It creates **paid compute** when you run it.

```python
import os
import boto3

job_name = os.environ["DMT_RUN_ID"]
bucket = os.environ["DMT_BUCKET"]
client = boto3.client("sagemaker", region_name=os.environ["AWS_REGION"])
client.create_training_job(
    TrainingJobName=job_name,
    RoleArn=os.environ["DMT_EXECUTION_ROLE_ARN"],
    AlgorithmSpecification={
        "TrainingImage": os.environ["DMT_TRAIN_IMAGE"],
        "TrainingInputMode": "File",
        "ContainerEntrypoint": ["python"],
        "ContainerArguments": [
            "-m", "decision_workbench.training.laya_runner",
            "--data", "/opt/ml/input/data/domain",
            "--out", "/opt/ml/model",
            "--model", "convaiinnovations/laya",
            "--revision", "7b928d828b7b0e022f929d9bd2e44165aa270148",
            "--epochs", "2", "--batch-size", "2",
            "--grad-accum", "4", "--max-length", "512",
        ],
    },
    InputDataConfig=[{
        "ChannelName": "domain",
        "DataSource": {"S3DataSource": {
            "S3DataType": "S3Prefix",
            "S3Uri": f"s3://{bucket}/decision-runs/{job_name}/data/",
            "S3DataDistributionType": "FullyReplicated",
        }},
    }],
    OutputDataConfig={"S3OutputPath": f"s3://{bucket}/decision-runs/{job_name}/outputs/"},
    ResourceConfig={"InstanceType": "ml.g5.xlarge", "InstanceCount": 1, "VolumeSizeInGB": 100},
    StoppingCondition={"MaxRuntimeInSeconds": 7200},
    EnableManagedSpotTraining=False,
)
print(job_name)
```

The explicit container entrypoint/arguments prevent SageMaker's default `train` argument from reaching a CLI that does not accept it. The `domain` channel becomes `/opt/ml/input/data/domain`; the final export goes to `/opt/ml/model`. [CreateTrainingJob fields](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html), [container invocation](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html), [managed paths](https://docs.aws.amazon.com/sagemaker/latest/dg/model-train-storage-env-var-summary.html)

For Unsloth, change only the training module and add its supported `--rank`/study settings; keep the split and storage contract. Do not add managed spot retries until the actual trainer can resume optimizer/scheduler/calibration state safely. The current adapter is a fresh-run trainer.

<a id="direct-submission--observe-retrieve-and-stop"></a>

### Observe, retrieve and stop

```bash
aws sagemaker describe-training-job --training-job-name "$DMT_RUN_ID" \
  --query '{status:TrainingJobStatus,failure:FailureReason,artifact:ModelArtifacts.S3ModelArtifacts}'
```

Inspect CloudWatch logs and the failure reason for stopped/failed jobs. Wait for `Completed`, then use the returned artifact URI, rather than constructing an assumed path:

```bash
aws s3 cp "$DMT_MODEL_ARTIFACT_URI" retrieved-model.tar.gz
```

Extract into a new directory with safe archive handling and inspect the full manifest/report. Default SageMaker output is a compressed tar of `/opt/ml/model`; changing output compression changes retrieval semantics. [Output contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html)

To cancel an active run:

```bash
aws sagemaker stop-training-job --training-job-name "$DMT_RUN_ID"
```

Observe its terminal status. A completed/stopped training job releases its training compute; S3/ECR/log retention continues to cost money. Retain the experiment evidence and delete only specifically chosen temporary artifacts under the owner's retention policy. No serving endpoint is created by this recipe. Private/no-egress training requires weights packaged in an approved artifact path and an adapted local-checkpoint invocation; the Hub-ID recipe needs download access.

<a id="application-managed-training"></a>

## Manage training through the workbench

<a id="application-managed-training--managed-cloud-training-through-the-platform"></a>

Submit a frozen decision-model study to AWS SageMaker AI through the browser, authenticated API and independent worker. The controller stages inputs in S3, records the provider job and retrieves its export for local validation. Install Boto3/Botocore **1.43.113** in the control environment and prepare a compatible training image. For direct submission, use the [AWS walkthrough](aws.md#direct-submission).

<a id="application-managed-training--operator-configuration"></a>

### Operator configuration

Install the AWS extra in the API/worker environments that use this feature. GPU admission still needs the separately qualified model environment; an API container is not a training image.

```bash
python -m pip install -e '.[ui,platform,aws]'
python -m pip check
```

Start from the [illustrative operator profile](../../examples/cloud/aws-profiles.json). Replace every example resource, tenant, image digest and source hash. The zero hashes deliberately cannot approve a current study. Keep the file operator-owned outside versioned application artifacts and point `DMT_CLOUD_PROFILES_FILE` to it in the API and worker processes. In Compose, put it in the mounted operator config directory and use `/run/config/cloud-profiles.json`. Python entry points read process variables; they do not load `.env.example` automatically.

The profile binds one AWS account, commercial AWS region, same-account ECR digest, training role, S3 bucket/prefix, one GPU instance, volume and maximum runtime. Optional `vpc` contains `Subnets` and `SecurityGroupIds`; optional `kms_key_arn` binds input/output encryption and volume encryption to an approved same-account/region key. GovCloud/China, cross-account images/buckets, distributed jobs, network-isolated/offline weights and automatic spot/resume are not implemented profiles.

Provide **temporary** credentials to the worker using `<credential_prefix>_ACCESS_KEY_ID`, `<credential_prefix>_SECRET_ACCESS_KEY` and, when applicable, `<credential_prefix>_SESSION_TOKEN`. The example prefix is `DMT_CLOUD_AWS`. The selected adapter does not fall back to ambient AWS credentials or instance metadata, and ignores globally configured/per-service SDK endpoint overrides. Automatic credential refresh, IAM-role/default-chain profiles and enterprise secret-store integration remain required work. Rotate the selected environment values without changing the frozen public resource profile. API/UI requests never carry provider secrets; the launcher excludes `DMT_CLOUD_*` variables from the web process. STS checks the selected credential account before external operations. [GetCallerIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html)

The submitter needs scoped `sts:GetCallerIdentity`, `sagemaker:CreateTrainingJob`, `DescribeTrainingJob`, `StopTrainingJob`, the applicable `iam:PassRole`, and input/output S3 operations. The execution role separately needs the approved ECR, S3, logs and optional KMS/VPC permissions. Provision resources and quotas under your organization's policies; this feature does not create buckets, roles, images or networks. Review the named resources, data residency, access and tariff before owner approval. [SageMaker training roles](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-roles.html)

<a id="application-managed-training--qualify-the-training-image"></a>

### Qualify the training image

The ECR image must contain this exact `decision_workbench` package, the selected actual native Laya/Unsloth trainer dependencies and a compatible GPU runtime. Run the package hash command inside both the local source environment and the final image; set the profile's `source_sha256` to their matching value:

```bash
python -c 'from decision_workbench.training.provenance import source_fingerprint; print(source_fingerprint())'
```

The fingerprint binds package source bytes. Record the image digest and check CUDA, dependencies, checkpoint loading, peak memory, training, export/reload and archive packaging on the selected Linux GPU. A package-source change needs a new image/profile and frozen study; preserve the original provenance of existing exports. The source fingerprint alone does not authenticate the image or its build.

AWS invokes `python -m decision_workbench.training.cloud_runner` with the approved archive and request hashes. The S3 File channel mounts `study.zip` at `/opt/ml/input/data/study/study.zip`. The runner accepts exactly `request.json`, `data/manifest.json` and the four split JSONL files, verifies all bytes/source/snapshot/schema, and constructs the actual native/Unsloth command from the unchanged frozen request. Native training explicitly uses CUDA; Unsloth retains its own GPU checks. The export goes to `/opt/ml/model`. A preflight-only mode checks the same inputs/configuration without training:

```bash
python -m decision_workbench.training.cloud_runner --help
```

The runner requires `--archive`, `--archive-sha256`, `--request-sha256`, a new `--work` directory and an empty/new `--out` directory; add `--check-only` for preflight. Obtain the two hashes from the immutable study entity, not a new unreviewed data export. Training-side report/manifest qualification remains mandatory. [Container entrypoint contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html), [managed paths](https://docs.aws.amazon.com/sagemaker/latest/dg/model-train-storage-env-var-summary.html)

<a id="application-managed-training--owner-workflow"></a>

### Owner workflow

1. Follow [frozen-study preparation](../remote-training-guide.md#returned-model-admission). Select AWS and exactly the region, GPU instance and ECR digest in the operator profile. Freeze owner criteria and prepared data before remote training.
2. In **Train on a cloud GPU**, choose the frozen study and managed profile. Review the public resources and limits; explicitly approve both data transfer and paid compute. **Approve cloud run** creates a local immutable approval only.
3. **Submit approved paid training** queues the worker action. With configured AWS credentials, this uploads domain data and creates paid compute in the selected account.
4. **Observe the same provider job** checks the frozen job/account/resource/input/output identities and records status plus reported training/billable seconds. A completed local control action does not mean training finished. Poll manually, confirm the provider's terminal state and reconcile its billing; this version has no unattended watchdog.
5. **Request provider stop** first checks job identity, then calls the provider stop API. Capacity remains held until a matching terminal status is observed. Cancelling a local worker/control job does not stop provider compute.
6. Once a matching **Completed** status is observed, **Retrieve and queue independent admission** downloads the returned approved S3 object, safely extracts it, validates provenance and queues the existing `remote-import` worker. Review the admission job in Training jobs. The model stays inactive until independent runtime/held-out checks and a separate owner release pass.

The API exposes `GET /v1/cloud-profiles`, owner `POST /v1/projects/{project}/cloud-runs`, paged `GET` of that collection, scoped run `GET`, and `POST /v1/projects/{project}/cloud-runs/{run_id}/actions`. All routes derive tenant/role from the configured authentication. Creation requires `study`, `profile_id`, literal-true `data_approved` and `compute_approved`, and an 8–128-character `request_key`. Action bodies contain `action` (`submit`, `observe`, `stop`, `abandon` or `retrieve`), the current integer `revision`, and a request key. Writes recheck authentication before durable submission; queued work also checks current operator profile availability. Stale queued actions fail without calling AWS.

<a id="application-managed-training--interruption-and-spending-limits"></a>

### Interruption and spending limits

One frozen study has one logical cloud run and fixed `dmt-<run_id>` provider job name. Upload/create attempt markers are committed before the relevant external call. SDK request retries are disabled. An uncertain create, worker crash or cancellation retains the attempt and capacity; another submit is refused. Observe the same job. Never allocate a replacement solely because a request timed out. If upload alone was attempted and compute creation never started, the owner can abandon that run locally; uploaded objects still need retention/cleanup. Once compute creation was attempted, abandonment cannot waive its unresolved state.

SageMaker's optional `RetryStrategy.MaximumRetryAttempts` has a minimum of **1** in the pinned SDK; setting it to zero is invalid. The adapter omits the optional strategy and rejects an observed unapproved retry policy, retained warm-pool capacity or distributed/training-plan resources. It starts on-demand, requests one GPU instance and applies the profile maximum runtime. These controls do not establish a dollar spending cap or exact provider cessation deadline. [RetryStrategy](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_RetryStrategy.html), [CreateTrainingJob](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html)

Run-count and unresolved-capacity accounting is transactional across API/worker processes in **one local workspace**, grouped across tenant/profile aliases by provider, AWS account/GCP project number and region using the strictest configured limits. Unresolved attempts do not age out. These are local submission limits; other workspaces/accounts/direct AWS jobs, tariffs, upload/storage/log costs and provider-side retries/billing are outside the ledger. Account-wide budgets and representative load/ledger-growth measurements remain requirements.

Immutable approval and hash-chained state histories are validated during [backup/restore](../recovery-guide.md#local-backups). Recovery interrupts copied local jobs but preserves unresolved external cloud state. Restore profiles/credentials separately and observe the same job before taking any new external action. Changing/revoking a public profile prevents operations through this adapter; inspect/stop the original resource through an authorized operator path and retain evidence. Automated profile-change recovery/settlement is pending.

<a id="application-managed-training--artifact-bounds-and-admission-boundary"></a>

### Artifact bounds and admission boundary

Retrieval binds the observed S3 URI to the exact approved bucket/run/job output path, uses `ExpectedBucketOwner`, and pins a returned VersionId or conditional ETag before bounded download. It records actual downloaded SHA/size; these are transfer evidence rather than an independent signed producer attestation. Input upload requests an explicit SHA checksum, conditional create and approved encryption. [PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html), [GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)

The configured download/expanded-model ceiling is at most 64 GiB. Extraction allows at most 2,048 entries and 16 path levels, with a checked deadline scaled to the size bound, retaining a ten-minute floor and four-hour ceiling. These checks and socket timeouts do not guarantee wall-clock cancellation. Free-space admission reserves three artifact bounds plus 1 GiB. Apply retention and cleanup to partial transfers and successful staging; cleanup is not automatic.

The extractor accepts short-name regular-file/directory USTAR and compatible GNU headers. It rejects links, devices, sparse/PAX/long-name extensions, traversal, duplicate paths, excessive sizes and unsupported/executable model files. Check a returned SageMaker archive against this format before a production rollout; an unsupported archive is refused. [SageMaker output handling](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html)

Cloud history retains provider observations. Import receipts record operator attestations rather than signed provider or image-origin proofs. The importer verifies the frozen study, configuration and export, then reloads the model on held-out data. Use [provider compatibility checks](../verification.md#provider-compatibility-checks) to validate the account, returned archive, billing and resource shutdown. [API reference metadata dated 11 October](../../research/provenance/cloud-aws-sources-2026-10-11.json)

For other providers, use the managed [Azure](azure.md#application-managed-training), [GCP](gcp.md#application-managed-training), [Modal](modal.md#application-managed-training) or [RunPod](runpod.md#application-managed-training) guides. Each has its own identity, storage and shutdown contract.
