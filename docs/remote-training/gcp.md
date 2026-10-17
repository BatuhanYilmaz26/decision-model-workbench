# Train a decision model on Google Cloud

[Provider directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

For Liquid d1-3B or omni, use the [Liquid model-specific cloud workflow](liquid.md) with this provider's compatible image and admission path.

Run the repository's Laya trainer on a Google Cloud GPU using a custom container. This walkthrough stages four reviewed data splits in Cloud Storage, submits a CustomJob with `gcloud ai custom-jobs`, and downloads the complete trained model export. [Google Cloud custom training](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/training/create-custom-job)

Use the commands below for direct operator submission. For application-managed approvals, job history, cancellation and local validation of returned weights, follow [managed GCP training](../managed-gcp-training.md).

## Prepare the project and data

Prepare the data and image using the [shared remote-training guide](../remote-training-guide.md). You need a Google Cloud project, a GPU-capable region, a private Cloud Storage bucket, an Artifact Registry image and the Google Cloud CLI. Enable the training, storage and registry APIs. Give the submitter permission to create, inspect and cancel CustomJobs and act as the selected training service account. Give that service account the required bucket, image and logging access. Confirm regional GPU quota before submission.

Set these variables in your submission shell:

| Variable | Value |
|---|---|
| `DMT_GCP_PROJECT` | Project ID that owns the job |
| `DMT_REGION` | Region with the required GPU quota |
| `DMT_GCS_BUCKET` | Private bucket name, without `gs://` |
| `DMT_RUN_ID` | Unique run identifier used for input/output prefixes |
| `DMT_TRAIN_IMAGE` | Prepared Artifact Registry image digest URI |
| `DMT_TRAIN_SERVICE_ACCOUNT` | Training service-account email |

Authenticate and upload the four reviewed JSONL files under a new run prefix:

```bash
gcloud auth login
gcloud config set project "$DMT_GCP_PROJECT"
gcloud storage cp data/support/*.jsonl "gs://$DMT_GCS_BUCKET/decision-runs/$DMT_RUN_ID/data/"
```

The job below uses one T4 on `n1-standard-8` as an initial encoder study configuration; verify regional support and actual memory adequacy. A different accelerator may require a different supported machine type. Neither a hosted Gemini tuning endpoint nor generic text SFT implements Laya's decision head.

## Create a CustomJobSpec

Generate `gcp-job.json` using the following operator-side Python. Set `DMT_TRAIN_IMAGE` to the qualified Artifact Registry image digest and `DMT_TRAIN_SERVICE_ACCOUNT` to your approved service account email. `DMT_RUN_ID` should be a unique simple identifier; the output prefix must not already contain an export.

```python
import json
import os
from pathlib import Path

training_command = r'''set -euo pipefail
mkdir -p /tmp/dmt-data
cp /gcs/"$DMT_GCS_BUCKET"/decision-runs/"$DMT_RUN_ID"/data/*.jsonl /tmp/dmt-data/
python -m decision_workbench.training.laya_runner \
  --data /tmp/dmt-data --out /tmp/dmt-export \
  --model convaiinnovations/laya \
  --revision 7b928d828b7b0e022f929d9bd2e44165aa270148 \
  --epochs 2 --batch-size 2 --grad-accum 4 --max-length 512
DEST=/gcs/"$DMT_GCS_BUCKET"/decision-runs/"$DMT_RUN_ID"/export
test ! -e "$DEST"
cp -R /tmp/dmt-export "$DEST"
sha256sum /tmp/dmt-export/manifest.json > "$DEST/_UPLOAD_COMPLETE"
'''
spec = {
    "serviceAccount": os.environ["DMT_TRAIN_SERVICE_ACCOUNT"],
    "scheduling": {"timeout": "7200s", "disableRetries": True},
    "workerPoolSpecs": [{
        "machineSpec": {
            "machineType": "n1-standard-8",
            "acceleratorType": "NVIDIA_TESLA_T4", "acceleratorCount": 1,
        },
        "replicaCount": 1,
        "diskSpec": {"bootDiskType": "pd-ssd", "bootDiskSizeGb": 100},
        "containerSpec": {
            "imageUri": os.environ["DMT_TRAIN_IMAGE"],
            "command": ["bash", "-lc"], "args": [training_command],
            "env": [{"name": n, "value": os.environ[n]}
                    for n in ["DMT_GCS_BUCKET", "DMT_RUN_ID"]],
        },
    }],
}
Path("gcp-job.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
```

Save this code outside the image and run it locally; it only writes the job specification. Submit it to start paid GPU training:

```bash
gcloud ai custom-jobs create --region="$DMT_REGION" \
  --display-name="$DMT_RUN_ID" --config=gcp-job.json
```

The `--config` file contains **CustomJobSpec**, not a `jobSpec` wrapper. REST submission instead uses `{"displayName": ..., "jobSpec": spec}` with an authenticated `POST` to `https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT/locations/REGION/customJobs`. Retain the returned resource name/job ID. [CLI and REST examples](https://cloud.google.com/vertex-ai/docs/training/create-custom-job), [CLI create reference](https://cloud.google.com/sdk/gcloud/reference/ai/custom-jobs/create)

Serverless training mounts accessible buckets under `/gcs/`, but those paths are not POSIX compliant. The recipe therefore copies data and trains/qualifies the export on local disk, then uploads the completed directory. The explicit final upload marker is external transfer metadata, not a model file; remove it from the downloaded export after retaining its checksum receipt, so it does not conflict with the model's exact file inventory. `AIP_MODEL_DIR` is a `gs://` URI when `baseOutputDirectory` is configured, not a directory accepted directly by this trainer. [Training code and Cloud Storage rules](https://cloud.google.com/vertex-ai/docs/training/code-requirements)

## Observe, retrieve and cancel

```bash
gcloud ai custom-jobs describe "$DMT_JOB_ID" --region="$DMT_REGION"
gcloud storage cp --recursive \
  "gs://$DMT_GCS_BUCKET/decision-runs/$DMT_RUN_ID/export" retrieved/gcp/
```

Inspect Cloud Logging and wait for `JOB_STATE_SUCCEEDED`. Download only from the recorded run prefix, retain the upload checksum and verify the export inventory/report. A failed upload or cancelled job is not a completed study even if some files exist. For Unsloth, change the command module/options while preserving local staging and split isolation.

```bash
gcloud ai custom-jobs cancel "$DMT_JOB_ID" --region="$DMT_REGION"
```

Observe a terminal cancellation/success/failure state. Training compute is job-scoped; Cloud Storage, registry and logs remain billable. Delete only explicitly selected temporary data under the retention policy. This CustomJob does not create a prediction endpoint or import the export into the local platform registry. [Cancellation reference](https://cloud.google.com/sdk/gcloud/reference/ai/custom-jobs/cancel)
