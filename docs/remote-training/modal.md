# Train a decision model on Modal

[Provider directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Run the repository's decision-model trainer in a Modal GPU Function, using a prepared container image and a persistent Volume for data and exports. Modal's [Unsloth example](https://modal.com/docs/examples/unsloth_finetune) shows the platform's fine-tuning infrastructure; the command below selects the decision-head trainer rather than the example's generative trainer.

## Stage immutable input on a Volume

Complete [image and four-split qualification](../remote-training-guide.md). Install the `modal` SDK in an operator environment, authenticate with `modal setup`, and use an approved workspace/environment. The qualified image must contain the project and its Python/CUDA dependencies and be accessible to Modal. For a private image, configure the documented registry-secret mechanism rather than making the image public.

```bash
modal volume create domain-decision-runs
modal volume put domain-decision-runs data/support/ /runs/study-001/data/
```

`study-001` is an example run ID; use a unique ID for each study and verify that all four files are directly under that run's `data/` prefix. Do not overwrite an input prefix after recording its hashes. [Volume CLI upload/download](https://modal.com/docs/cli/latest/volume)

## Define and invoke the GPU function

Set `DMT_TRAIN_IMAGE` in your operator environment to the qualified image digest. Save this as `modal_train.py` outside any secret/data directory:

```python
import os
import re
import modal

app = modal.App("domain-decision-training")
volume = modal.Volume.from_name("domain-decision-runs")
image = modal.Image.from_registry(os.environ["DMT_TRAIN_IMAGE"])

@app.function(image=image, gpu="A10", volumes={"/runs": volume},
              timeout=7200, retries=0)
def train(run_id: str):
    import subprocess
    import sys
    from pathlib import Path
    from decision_workbench.training.contracts import load_splits

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("Invalid run identifier")
    root = Path("/runs/runs") / run_id
    load_splits(root / "data")
    target = root / "export"
    if target.exists():
        raise ValueError("Use a fresh run/output prefix")
    try:
        subprocess.run([
            sys.executable, "-m", "decision_workbench.training.laya_runner",
            "--data", str(root / "data"), "--out", str(target),
            "--model", "convaiinnovations/laya",
            "--revision", "7b928d828b7b0e022f929d9bd2e44165aa270148",
            "--epochs", "2", "--batch-size", "2", "--grad-accum", "4",
            "--max-length", "512",
        ], check=True)
    finally:
        volume.commit()
    return {"run_id": run_id, "export": str(target)}

@app.local_entrypoint()
def main(run_id: str = "study-001"):
    print(train.remote(run_id))
```

The Volume's `/runs/study-001` directory is mounted under `/runs`, giving the container path `/runs/runs/study-001`. Check memory and availability before using the example's A10G and two-hour limit. The function disables retries because the adapter cannot resume optimizer state. Keep interrupted outputs for investigation, and release only a completed, validated export. [Existing-image API](https://modal.com/docs/guide/existing-images), [GPU configuration](https://modal.com/docs/guide/gpu), [Volumes and explicit commit](https://modal.com/docs/guide/volumes)

```bash
modal run modal_train.py --run-id study-001
```

This starts paid GPU execution. Keep the CLI attached for the first qualification. For background execution, the documented `modal run --detach` option changes lifecycle/observation; record the application/function-call identity and use the dashboard to observe completion. A dropped operator connection must not be treated as proof of cancellation.

## Retrieve and stop

After successful function completion:

```bash
modal volume get domain-decision-runs /runs/study-001/export retrieved/modal/export
```

Inspect the whole model manifest/report and verify its input hashes and reload qualification. Persist each experiment's Modal call ID, GPU, image digest and measured usage. The function does not create an HTTP serving endpoint or a platform registry entry.

Use the Modal dashboard's stop action or `modal app stop APP_ID` for an active application, then observe its stopped/terminal state. Successful job functions release their GPU compute; the persistent Volume remains until deliberately removed and may remain billable. The configured timeout limits a call, while retries would multiply allowed total time. [Timeout behavior](https://modal.com/docs/guide/timeouts), [application CLI](https://modal.com/docs/cli/latest/app)

Do not copy the upstream long-training retry example until a resumable decision trainer exists. A durable Volume preserves files; it does not by itself restore optimizer state, ensure one logical job, or prevent duplicate billing. [Long-running training example](https://modal.com/docs/examples/long-training)

## Managed application alternative

For application-managed studies, use the [Modal Sandbox controller](../managed-modal-training.md) with SDK 1.6.1. It adds owner approval, frozen inputs, durable provider identity, observation, termination and local artifact validation. When using the Function route independently, retain the call ID and persist the export on a Volume: FunctionCall outputs expire after seven days. [Job-queue contract](https://modal.com/docs/guide/job-queue)
