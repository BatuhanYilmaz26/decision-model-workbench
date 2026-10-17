# Train a decision model with AWS SageMaker AI

[Provider directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Run the repository's Laya trainer in a SageMaker AI custom GPU container, with reviewed data staged in S3 and a complete model export returned to S3. Prepare the four data splits and training image using the [shared remote-training guide](../remote-training-guide.md).

Use the commands below for direct operator submission. For application-managed approvals, durable job history and local validation of returned weights, follow [managed AWS training](../managed-cloud-training.md). The standalone script does not create the application's cloud ledger or frozen-study request.

## Prepare resources and stage data

Choose a region with the requested SageMaker training-instance quota, a private S3 bucket and ECR image. The submitter needs job creation/description/stopping and permission to pass the designated execution role. The role needs scoped S3 input/output and image/log access; add the chosen KMS/VPC access where applicable. Keep the image and data in an approved region. Set `AWS_REGION`, `DMT_BUCKET`, `DMT_RUN_ID`, `DMT_EXECUTION_ROLE_ARN` and `DMT_TRAIN_IMAGE` to operator-owned values. `DMT_TRAIN_IMAGE` is the qualified ECR digest URI.

A G5 GPU has 24 GB memory; `ml.g5.xlarge` is a starting SKU, subject to SageMaker regional support/quota. Measure the actual model study's memory rather than assuming all backbones fit. [G5 hardware](https://aws.amazon.com/ec2/instance-types/g5/)

Upload the four reviewed files to a unique, data-only prefix:

```bash
aws s3 sync data/support/ "s3://$DMT_BUCKET/decision-runs/$DMT_RUN_ID/data/"
```

## Submit through Boto3

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

## Observe, retrieve and stop

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
