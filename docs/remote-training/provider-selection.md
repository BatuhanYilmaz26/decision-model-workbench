# Choose remote training infrastructure

Choose a GPU training provider based on your cloud accounts, identity requirements, storage controls, GPU capacity and total study cost. Use this comparison before the [provider walkthroughs](../remote-training-guide.md). Check quota, stock and regional pricing in your account before submission.

## Match the provider to the organization

| Provider | Where the trainer runs | Operator interface | Persistent outputs | Main qualification question |
|---|---|---|---|---|
| [AWS SageMaker AI](aws.md) | Custom training container | Boto3/REST `CreateTrainingJob` | S3 model artifact | Does the execution role, ECR digest and S3/KMS/network policy work in the selected region? |
| [Azure ML](azure.md) | Command job on GPU compute | CLI v2, SDK v2 or REST | Named URI-folder output | Does the selected compute identity access the exact environment and datastore, and scale down after cancellation? |
| [GCP custom training](gcp.md) | Custom container in a CustomJob | `gcloud ai custom-jobs`, REST or SDK | Cloud Storage export | Does the chosen service account have only the required storage/registry access and compatible GPU/machine quota? |
| [Modal](modal.md) | GPU Function or application-managed Sandbox | Python SDK/CLI | Managed Volume | Is image/cache compatibility qualified, and is the storage location acceptable for the data? |
| [RunPod](runpod.md) | GPU Pod | REST v2 and direct runtime access | Pod or independent network volume | Is the selected GPU/data center available, and will output survive Pod termination? |

Direct scripts let an operator submit and manage the job. The application's managed controllers add durable attempts, observation, stopping and local model validation. Choose the route that fits your operating process, then check account and image compatibility with a small study before uploading enterprise data.

Current primary contracts: [AWS job API](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html), [Azure training interfaces](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-train-model?view=azureml-api-2), [GCP custom-job instructions](https://cloud.google.com/vertex-ai/docs/training/create-custom-job), [Modal GPU configuration](https://modal.com/docs/guide/gpu), [RunPod Pod creation](https://docs.runpod.io/api-reference-v2/pods/create-a-pod).

## Size the study before renting capacity

Record the exact backbone, trainable components, precision, maximum complete prompt, number of questions, batch size, gradient accumulation and export format. Parameter count alone does not predict peak training memory. Measure model load, frozen-feature extraction, backward/optimizer steps and export separately using representative longest records. Cached-feature Vega training and encoder-LoRA training have different resource profiles.

When local VRAM is insufficient, run a small bounded qualification study on a compatible remote GPU first. Lowering batch size and using accumulation can help; changing precision, context or quantization changes the evaluated runtime and must be requalified. Increasing accumulation does not remove the memory cost of one overlong example. Refuse unsupported input lengths rather than silently truncating business evidence.

A multi-GPU VM does not pool memory automatically. Use a trainer's explicitly supported sharding/distribution path and test it. The current repository's qualified Laya/Unsloth profile does not establish distributed training. Avoid spot/retry execution until optimizer, scheduler, RNG, data position and unfinished-output recovery are implemented and exercised.

## Compare the complete cost

Use an illustrative budget equation, with current quotes supplied by the operator:

`study cost = billed compute hours × compute rate + persistent storage + transfers + image builds + retained logs/artifacts`

Include download/setup time, failed preflights and idle resources in billed compute where the provider charges them. Divide by accepted model versions or completed studies, not only training steps. Record currency, region, SKU, date, billing granularity and measured elapsed/billed time. This guide does not assert that one provider is universally cheapest.

For serving, compare ongoing infrastructure and fallback/review/error costs against hosted requests at matched quality and coverage. Remote training does not require remote serving: a verified export can return to the local machine if inference fits and the loader/runtime is qualified. Keep the complete backbone, tokenizer, head, calibration, schema and manifest rather than copying only a small adapter file.

## Storage, deadlines and final acceptance

Modal documents US durable storage for its managed Volumes, Images, Secrets and application logs, even when compute runs elsewhere. A compute-region selection is not a dataset-residency guarantee. Use an independently qualified regional storage path when requirements demand it. [Modal data residency](https://modal.com/docs/guide/data-residency), [compute and routing regions](https://modal.com/docs/guide/region-selection)

RunPod's current migration guide schedules REST v1 retirement for **15 November 2026**. Use the reviewed v2 request/response schema; a URL replacement cannot migrate v1 fields. A trainer timeout does not terminate its Pod. Network-volume persistence and Pod deletion are separate operations. [RunPod migration](https://docs.runpod.io/api-reference-v2/migrate-from-v1)

Freeze a unique input prefix, data hashes, code source, image digest and checkpoint revisions before submission. If creation times out, observe the same durable attempt; do not blindly create another paid job. Verify terminal execution separately from a cancellation acknowledgement. Retrieve a complete bounded export, independently check its manifest and held-out behavior, then obtain owner release approval. Retained storage and logs remain subject to the owner's retention policy after GPU shutdown.

A first real acceptance run should produce the provider resource ID, accepted identity/configuration, logs, measured peak memory, optimizer-update evidence, terminal/stop receipts, full export hashes, independent loader equivalence and held-out report.
