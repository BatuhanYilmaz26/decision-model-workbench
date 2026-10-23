# Train and use a model for your domain

<a id="build-a-domain-decision-model"></a>

[Install and start](../README.md#run-on-your-computer) · [Other workflows](README.md)

This walkthrough takes a project from your own examples to a released model and new predictions. Follow the steps here in order. Model-specific setup is needed only for the backend you choose; advanced controls are in the screen manual.

## Before you start

- Install the CPU application using the root README. Personal localhost mode needs no workspace account or key.
- Decide what evidence the model will see and which outcomes it may return. You define the policy; the workbench does not infer authoritative labels from a description.
- Bring independently reviewed operational records for evaluation. A few invented examples can demonstrate the importer, but cannot establish domain quality. The default acceptance form requires 100 real test records per question, plus outcome coverage. Check the displayed requirements before collecting data.
- Choose your execution path below. You can collect/review data before configuring a native model. Weights and model packages can require substantial disk space and RAM/VRAM.

| Path | What to prepare | When to choose it |
|---|---|---|
| CPU comparison baseline | The application installation only | Learn the workflow or compare a lexical classifier; no foundation-model weights |
| A native decision model | The selected [model's environment and assets](models/README.md) | Adapt a supported decision model to your domain; supported devices vary |
| Rented GPU | One compatible model image and one [provider profile](remote-training/README.md) | Local resources are insufficient; your account pays for compute and retained storage |
| Hosted decision API | Your key and an approved [hosted profile](hosted-decisions-guide.md) | Evaluate and release a hosted policy; this does not train provider weights |

You do not need to run the CPU baseline before choosing a native model. API-only providers cannot be locally fine-tuned through this workflow.

## 1. Describe the decision

Run `domain-platform run` from the activated application environment and open its printed localhost address. Open **Create a domain project**. Enter the domain, decision policy and data requirements in natural language, then define the questions and confirm the project.

For each question, select category, yes/no or rating outcomes and explain what evidence supports each answer. Include missing-evidence and exception rules. Keep IDs stable: a question ID such as `route` becomes part of the data/model contract. Optional teacher suggestions are drafts you must review, not a prerequisite.

For example, a document-processing policy might use a category question `route` with `process` and `review` outcomes. Replace the example with your own domain; calculations, permissions and business actions remain in application code.

**Result:** a persistent project with confirmed questions and allowed outcomes.

## 2. Collect and review examples

In **Data and review**, download the project's CSV template, upload CSV/TSV/XLSX/JSON/JSONL, or expand **Enter a few examples without a file**. A simple file for the illustrative `route` question could look like:

```csv
id,group_id,state,route
sample-1,source-1,"Required approval is missing.",review
sample-2,source-2,"All required fields and approvals are present.",process
```

These two rows are fictional format examples, not evaluation data. For your own file:

1. Select the worksheet for XLSX. Map evidence columns to **state**, source IDs/families to the matching fields, and label columns to question IDs. Multiple evidence columns can become a JSON object.
2. Map label values to the project's allowed outcomes. Choose **File source** accurately: hypothetical/synthetic for invented rows; observed operational only for actual observations.
3. Select **Validate and preview import**, inspect the normalized rows, then **Save new collection for review**. Change the file or mapping only through a new preview.
4. Select **Dataset version** and **Example to review**. Inspect the evidence and all labels, correct mistakes, choose **Approve complete labels**, then **Save reviewed version**. Reject unusable records. Imported `reviewed` flags do not bypass this step.

Use the same source family for related records from an original event/document so they cannot be split across training and evaluation. Unknown labels can be completed during review; incomplete records are not ready for preparation.

**Result:** approved, immutable dataset revisions. The app converts them into the selected backend's native format; you do not need to hand-write a native training file for the shared app workflow.

## 3. Reserve independent evaluation data

In **Prepare and generate**, select one reviewed revision per collection under **Dataset versions to prepare**. For a first study, leave **Preserve holdouts from an existing snapshot** unset.

Choose **auto** for the standard reservation: it uses chronology when the necessary real timestamps exist, otherwise groups related source families. Use explicit group/date controls only when your data needs them. Select **Reserve train / validation / calibration / test**, then follow the preparation job in **Training jobs**. Refresh and select the resulting snapshot.

| Reserved role | Used for |
|---|---|
| Train | Fitting the model; reviewed synthetic examples may extend this role |
| Validation | Comparing settings during development |
| Calibration | Fitting the exported probability policy |
| Test | Checking the final frozen acceptance criteria using independent real records |

Inspect role counts and readiness. Resolve missing classes, too few independent examples, duplicates or leakage warnings before training. Keep evaluation evidence limited to what would have been available at decision time; do not invent operational origin to pass readiness.

**Result:** a fixed study snapshot with four separate data roles.

## 4. Add reviewed training coverage

This step is optional. If you need additional training examples, use [Generate and review candidates](synthetic-generation-guide.md). Configure a generative teacher, describe the gaps, preview the outgoing request, approve its data/cost implications and review the returned candidates.

Return to **Prepare and generate**. Select the approved synthetic revision with the original data, and select the original snapshot under **Preserve holdouts from an existing snapshot when adding synthetic data**. Prepare the extended snapshot and use it for training. Existing validation, calibration and test records remain reserved; synthetic rows remain training-only.

You can skip this step when your reviewed training data is sufficient. A teacher is not required for ordinary training on existing data.

## 5. Train and inspect the export

In **Training jobs**, expand **Acceptance criteria — set before the first training run**. An owner sets meaningful quality, calibration and minimum-count requirements, then selects **Save acceptance criteria**. These criteria freeze at the first study submission. A failed result is a reason to improve data/model behavior; a changed policy or criteria requires a successor project.

### On your computer

Select **Training snapshot**, **Training backend** and **This computer**. Inspect the available-hardware report and the backend's settings, then **Queue training**. For the CPU baseline, the application environment is sufficient.

For a native model, complete only its selected model guide's environment setup first. Laya/Unsloth use the configured worker/model Python; other backends have their own interpreter/checkpoint settings. The API/browser stay in the lightweight application environment. Restart services after changing runtime configuration. Backend selection does not install packages, fetch every model automatically or make unsupported hardware compatible.

Vega additionally requires paired applicability data and its acceptance gates; open Perplexity has a separate Choice/Noul training contract. Check these before preparing those model-specific studies.

### On a rented GPU

Select cloud training and open **Train on a cloud GPU**. Use the selected model's cloud instructions and one provider profile: AWS, Azure, GCP, Modal or RunPod. Approve the frozen study-data transfer and paid compute separately. Follow submission/status/logs, cancel upstream compute when necessary, retrieve the completed export and submit it for independent admission. A prepared study alone is not a submitted training job, and local cancellation alone may not stop provider billing.

### Inspect the result

Refresh the job until it is terminal. Inspect its logs and failure reason before retrying. A successful training/admission job registers an **inactive** model version. In **Models and releases**, inspect independent test metrics, per-question/outcome results, calibration, frozen criteria and export/reload checks. Success means the pipeline completed; usefulness still depends on your domain data and requirements.

**Result:** a versioned model export with evaluation and calibration, ready for an owner release decision.

## 6. Release and integrate

In **Models and releases**, select the eligible version, enter a **Release or rollback reason**, then choose **Activate selected version**. The server checks the frozen gates and runtime/artifact availability. Selecting a dropdown value alone does not activate it. To roll back, activate a prior eligible version with a reason; the release revision increases.

In **Decisions and audit**, choose **Text** or **JSON**, enter new evidence in the structure used for training, set **Abstain below confidence** and choose **Make decision**. Inspect the answer, probabilities, `abstained` flag and release identity. Choose a threshold from your evaluation, not a universal default. Abstention and request failures need an explicit review/fallback path.

Keep **Require the active release version** enabled when you need the displayed version. Open **Release identity for integrations** to copy the project/model/revision/schema identifiers. The [runnable released-model client](../examples/platform/README.md) explains credential provisioning and a version-pinned call from your application. Personal workspace credentials are temporary; provision a scoped integration credential rather than exposing an internal launcher credential.

Keep the launcher running for local predictions. Stop with **Ctrl+C**; projects persist and reopen when you run `domain-platform run` again. Native model serving needs its configured runtime and assets after restart. Hosted releases still send future evidence to their configured provider and can incur per-call charges.

**Result:** a released model or hosted policy serving new decisions, with an integration path and rollback available.

## If a step is blocked

| Symptom | What to check |
|---|---|
| Preparation refuses records | Approval, complete labels, source-family/duplicate checks and independent real evaluation coverage |
| Native backend unavailable | The selected model's environment, assets, supported device and free RAM/VRAM; CPU baseline is an explicit alternative |
| No cloud profile or teacher | Configure that optional integration before startup; restart, then preview again |
| Release refused | Frozen criteria, test counts, runtime/artifact availability and the displayed report; do not relabel synthetic rows as real |
| Timeout or interrupted job | Inspect saved status and upstream work before retrying; a missing local response does not prove no paid work happened |

For an unfamiliar control, open the [screen manual](app-guide.md#screen-manual--when-a-control-is-unavailable). Team deployment, organization sign-in and operator recovery are in [application hosting](application-hosting/README.md#application-hosting); they are not required for personal localhost use.
