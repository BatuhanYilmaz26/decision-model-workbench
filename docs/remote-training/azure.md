# Train a decision model with Azure Machine Learning

[Provider directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Run a decision-model trainer as an Azure Machine Learning command job. This walkthrough stages four reviewed data splits, submits a GPU job and retrieves the complete export. Prepare the data and container using the [shared remote-training guide](../remote-training-guide.md).

Use this route for direct CLI or SDK submission. For application-managed approvals, job history, cancellation and Blob retrieval into local model validation, follow [managed Azure training](../managed-azure-training.md).

## Workspace, identity and GPU compute

Use an existing approved Azure ML workspace with its storage and registry access. Install the Azure CLI `ml` extension, sign in and configure the target subscription/resource group/workspace. The submitter needs job/compute/environment permissions. The compute identity needs appropriate data and private-image access; validate the chosen network and secret configuration before uploading enterprise records.

```bash
az extension add --name ml
az login
az account set --subscription "$DMT_SUBSCRIPTION_ID"
az configure --defaults group="$DMT_RESOURCE_GROUP" workspace="$DMT_WORKSPACE"
az ml compute list-sizes --location "$DMT_REGION"
```

Select an available GPU SKU with approved quota and enough measured VRAM. `DMT_GPU_SIZE` names that SKU; it must not be a CPU-only VM. This creates a cluster that can scale down to zero:

```bash
az ml compute create --name decision-gpu --type amlcompute \
  --size "$DMT_GPU_SIZE" --min-instances 0 --max-instances 1
az ml environment create --name decision-trainer --version 1 --image "$DMT_TRAIN_IMAGE"
```

The environment references the qualified image digest in ACR or another approved registry. Choose a new environment version for changed dependencies, and adjust the job accordingly. For a stronger idle shutdown policy, the SDK's `AmlCompute` supports `idle_time_before_scale_down`; verify its configured value. [Compute provisioning](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-create-attach-compute-cluster?view=azureml-api-2), [compute CLI](https://learn.microsoft.com/en-us/cli/azure/ml/compute?view=azure-cli-latest), [environment CLI](https://learn.microsoft.com/en-us/cli/azure/ml/environment?view=azure-cli-latest)

## Define and submit a command job

Save `azure-job.yml` beside a data-only `data/support/` containing the four reviewed JSONL files. CLI v2 uploads local input data to workspace storage; alternatively use a registered data version or approved datastore URI. The image already contains the project package, so the job does not upload the entire checkout.

```yaml
$schema: https://azuremlschemas.azureedge.net/latest/commandJob.schema.json
type: command
display_name: domain-laya-study
experiment_name: domain-decisions
compute: azureml:decision-gpu
environment: azureml:decision-trainer:1
inputs:
  domain:
    type: uri_folder
    path: ./data/support
    mode: download
outputs:
  export:
    type: uri_folder
    mode: upload
limits:
  timeout: 7200
command: >-
  python -m decision_workbench.training.laya_runner
  --data ${{inputs.domain}} --out ${{outputs.export}}
  --model convaiinnovations/laya
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148
  --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
```

```bash
az ml job create --file azure-job.yml --query name --output tsv
```

Retain the returned job name as `DMT_JOB_NAME`. `${{inputs.domain}}` and `${{outputs.export}}` are Azure ML substitutions; shell environment variables written inside YAML are not automatically expanded. Output `upload` mode stages local files and uploads them at job completion. The shared adapter refuses a pre-populated export directory. [Command-job schema](https://learn.microsoft.com/en-us/azure/machine-learning/reference-yaml-job-command?view=azureml-api-2), [training through CLI/SDK/REST](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-train-model?view=azureml-api-2)

For SDK v2 users, the equivalent primitives are `MLClient`, `command`, `Input(type="uri_folder", ...)`, `Output(type="uri_folder", ...)` and `client.jobs.create_or_update(job)`. The YAML path above provides the complete job definition without requiring a second generated template. For Unsloth, replace the module and add only that runner's supported options. Start without spot/retry execution: this project's trainer does not resume optimizer state.

## Observe, retrieve and cancel

```bash
az ml job show --name "$DMT_JOB_NAME"
az ml job stream --name "$DMT_JOB_NAME"
az ml job download --name "$DMT_JOB_NAME" --all --download-path retrieved/azure
```

Check terminal status and download the `export` named output, including manifest/report/calibration/tokenizer files. `stream` ends when the job terminates; an uploaded partial directory from a failed job is not a qualified model. Log the resolved environment/image and data asset identity. [Job-management CLI](https://learn.microsoft.com/en-us/cli/azure/ml/job?view=azure-cli-latest)

```bash
az ml job cancel --name "$DMT_JOB_NAME"
```

Wait for cancellation and verify that cluster nodes scale down. Workspace storage, registry, logs and retained data remain billable. Inspect retained prefixes before deleting any temporary output. Job cancellation does not delete data, and archiving a job does not establish GPU shutdown. No managed online endpoint is created by this training job; local or Azure serving is a separate qualification.
