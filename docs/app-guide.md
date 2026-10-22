# Use the Streamlit application

[Project overview](../README.md) · [Install and start](setup.md) · [All guides](README.md)

Use the browser application to define a decision, collect and review examples,
reserve evaluation data, train or evaluate a model, release an eligible version
and make predictions. Projects, reviewed versions and jobs are saved by the
workspace service. The sidebar's **Complete app user guide** opens this manual;
short explanations beside the controls link to the relevant step.

Start the app with the [installation instructions](../README.md#run-on-your-computer),
then open the localhost address printed in the terminal, normally
`http://127.0.0.1:8501`. Keep that terminal running. You can use the CPU baseline
without a GPU, model download, cloud account or workspace credential. Other
models and external services have separate requirements below.

## Choose a workflow

| Your goal | Start with | What you get |
|---|---|---|
| Learn the local workflow | [First project walkthrough](#first-project-walkthrough) and the CPU comparison baseline | A reviewed dataset, evaluated local model version and, if eligible, an active release |
| Train your own domain model | [Define questions](#create-a-project), [review examples](#add-and-review-examples), [reserve data](#reserve-data-and-generate-candidates), then [choose a compatible backend](#train-on-this-computer) | A separately evaluated version of your trained model |
| Use an existing hosted model | [Configure a provider profile](hosted-decisions-guide.md), then [evaluate its API](#evaluate-a-hosted-decision-api) against your reserved data | A calibrated hosted policy that can be released without training provider weights |
| Train without enough local VRAM | [Cloud GPU training](#train-on-a-cloud-gpu) | A frozen study and, for supported configured profiles, a managed training run and independently admitted model |
| Expand a small training set | [Synthetic candidate generation](#generate-and-review-training-candidates), after reserving real evaluation data | Candidates to review and add to training; generation does not release or retrain a model |
| Use a released model | [Make decisions](#make-decisions-and-export-audit-history) or [connect your application](api-integration-guide.md) | Answers, probabilities and abstention flags for new evidence |

### Terms used in the app

| Term | Meaning |
|---|---|
| Project | Your domain, decision policy and allowed questions/outcomes |
| Evidence or state | Information available when the decision must be made; text or structured data |
| Gold labels | Reviewed expected outcomes used for training or evaluation |
| Collection and revision | A set of examples and a saved version of that set; an edit creates a new revision |
| Reserved snapshot | Fixed train, validation, calibration and test roles prepared from reviewed revisions |
| Job or study | Saved work such as preparation, generation, training or hosted evaluation; follow its status and result |
| Model version | An evaluated local artifact or hosted policy; registration alone does not make it active |
| Release | The version currently used for project predictions; an owner activates or rolls back a release |

## Find the right screen

| Screen or control | What you do there |
|---|---|
| [Permanent project deletion](project-deletion-guide.md) | Owners preview and confirm archived database cleanup, choose audit retention and retrieve private receipts |
| [Project lifecycle](project-lifecycle-guide.md) | Owners pause or restore a project, with explicit confirmation and active-work checks |
| [Connection and computer setup](#start-or-connect-to-a-workspace) | Start personal mode, connect to a shared API and inspect model environments |
| [Create a domain project](#create-a-project) | Describe the policy, define outcomes or review teacher-suggested questions |
| [Data and review](#add-and-review-examples) | Enter/import examples, map columns, review labels, assign work and discover categories |
| [Prepare and generate](#reserve-data-and-generate-candidates) | Reserve the four data roles, review semantic relatives and generate training candidates |
| [Training jobs](#train-or-evaluate-a-model) | Set acceptance criteria, train locally, evaluate a hosted API, manage cloud studies and follow jobs |
| [Models and releases](#inspect-results-release-and-roll-back) | Inspect metrics and provenance, activate a version or roll back |
| [Decisions and audit](#make-decisions-and-export-audit-history) | Test the active release and capture/download audit history |
| [Applicability](#prepare-vega-applicability-data) | Prepare the additional negative examples required by Vega |
| [Create a policy successor](#change-an-existing-policy) | Start a separately reviewed policy version without changing an existing release |

## First project walkthrough

1. Follow the [root quick start](../README.md#run-on-your-computer). Personal
   localhost mode needs no workspace key, cloud account or GPU.
2. Open **Create a domain project**. Add a question, describe its decision rule
   and outcomes, enter your domain/data requirements, confirm and create.
3. In **Data and review**, download the project's CSV template or enter examples.
   Use real operational records for evaluation. Imported and manually entered
   records need authenticated review even when their labels are already filled.
4. Approve the eligible records, run **Analyze data readiness**, then open
   **Prepare and generate**. Select one reviewed revision per collection and
   reserve train, validation, calibration and test roles.
5. In **Training jobs**, have an owner save acceptance criteria before the first
   study. Choose **CPU comparison baseline** and **This computer** to try the
   local data/training workflow without downloading model weights. Choose a
   supported native model, hosted API or cloud GPU path when appropriate.
6. Refresh the workspace until the job finishes. Inspect the registered version
   in **Models and releases**. An owner activates it only if it meets the frozen
   release criteria.
7. In **Decisions and audit**, enter new evidence and choose **Make decision**.
   Inspect the answers and abstention flags before connecting a business action.

To add synthetic examples between reservation and training, use
[Generate training candidates](#generate-and-review-training-candidates) with a
configured teacher. Review the resulting collection, then select its reviewed
revision in **Prepare and generate** and select the original reservation in
**Preserve holdouts from an existing snapshot when adding synthetic data**.
Prepare the extended snapshot and select that new snapshot for training.
Validation, calibration and test records remain reserved; generation never
automatically approves candidates or retrains a released model.

A few examples are useful for learning intake and review. They are not enough
to establish domain quality or necessarily satisfy training/release coverage.
The default acceptance form requires 100 real test records per question; class
coverage and other limits also apply. Collect adequate independent data rather
than lowering gates merely to obtain a release. Optional workflows below can be
introduced as the project needs them.

## Start or connect to a workspace

`domain-platform run` normally opens its personal workspace automatically. If
you start a loopback-bound Streamlit server directly, choose **On this computer**
and **Start local workspace**. Keep the launcher/server running while you need
its API and worker. Closing a browser tab is different from stopping the server.

For a team-operated service, choose **Shared workspace**, expand **Connect to an
existing workspace**, enter the **Workspace API URL** and **Workspace credential**
provided by your operator, then **Connect**. A provider's API key is not a
workspace credential. Personal mode shares its local owner identity among
browsers reaching that server; do not expose it through a public proxy.

Before starting a direct personal workspace, **Optional local model and synthetic
generation settings** accepts existing model-environment Python executables and
an optional teacher endpoint/model/key. **Teacher response format** selects
JSON object or JSON Schema output. **Teacher token limit parameter** and
**Omit teacher temperature** handle teachers with different Chat Completions
parameters. Keep **Use configured default** to preserve environment settings;
see [teacher setup](setup.md#configure-a-teacher). Leave model paths empty for
the CPU baseline. These fields do not install packages or weights. Restart the app
to change settings after services have started. Launcher-based configuration and
provider secrets are described in [setup](setup.md).

Owners can expand **Computer, model environment and provider setup** and choose
**Check available hardware and model packages**. This inspects configured
interpreters without downloading weights. Detected CUDA or an installed package
does not establish enough free memory or compatibility with every backend.
Use the linked model guides for dependency, weight-storage and hardware needs.
**Cloud GPU and hosted API setup** links to provider configuration guides; merely
selecting a guide does not provision a service.

### Sidebar, refresh and access

Use **Project** to switch domains. Project and dataset history page controls are
zero-based: page 0 is the first page. Projects load 100 per page, dataset versions
50; individual example pages also show 50 records. Use **Refresh workspace** after
worker jobs, reviews or external changes. **Sign out** clears the browser session;
it does not delete projects or cancel jobs. Stop local services with the launcher
terminal's Ctrl+C. Keep the workspace data directory for the next session.

| Workspace role | Main responsibilities |
|---|---|
| Owner (`admin`) | Configure acceptance, approve external data/compute, review, manage releases and access owner operations |
| Editor | Create projects, enter/import data and queue permitted preparation/local-training jobs; request review and owner approval where needed |
| Reviewer | Review assigned records and permitted semantic/category proposals; cannot release a model |
| Inference | Submit evidence to an active release through the prediction view |

Personal mode provides owner access. Shared permissions are configured by the
operator; the browser has no general account-management screen. See
[authentication](authentication-guide.md). Controls may be hidden or disabled
for your role even when described in this guide.

## Create a project

Under **Create a domain project**, give each question a stable identifier and
choose its **Answer format**:

| Format | Enter in the question form | Labels used later |
|---|---|---|
| Choose one category (`choice`) | One `category_key \| description` per line | Your category keys |
| Yes or no (`noul`) | A clear yes/no decision rule; no outcomes needed | `false`, `true` |
| Ordered rating (`score`) | One level description per line, in order | Zero-based indices: `0`, `1`, `2`, … |

Choose **Add or update question** for every question. Inspect the draft table;
use **Remove a draft question** and **Remove question** to discard one. Advanced
users can replace the draft through **Advanced: import an existing JSON schema**.

Fill **Project name**, the domain/policy description and **Describe the data you
need**. State the available evidence, exceptions, missing-information behavior,
rare outcomes and languages. Confirm the schema/policy and choose **Create
project**. Creation does not collect data or start a model run. The new project
appears in the sidebar. [Decision and data formats](data-guide.md)

Leave **Policy successor of** unset for an independent project. Owners branching
an existing policy can use the [reviewed successor workflow](#change-an-existing-policy)
to inspect the copied policy and criteria before creating the new version.

### Optional teacher-suggested questions

An owner can enable **Suggest decision questions with a teacher**. Describe the
domain, inspect the configured teacher, approve sending the brief and any charges,
then request suggestions. This sends the approved brief, not project datasets.
Use the saved suggestion selector and refresh control to follow its job. Review
the proposed policy/questions, edit them, confirm each outcome and choose
**Create project from reviewed suggestions**. Reopen a confirmed project rather
than creating another copy. A teacher draft is not approved policy or labelled
training data. [Schema assistant](schema-assistant-guide.md)

### Change an existing policy

Owners use **Create a policy successor → Plan a policy successor** above the tabs.
Review the copied description, questions and optionally selected acceptance
criteria. Name the successor, give a reason, explicitly approve it and create.
It receives a new project/endpoint and starts without data, models or a release.
The original remains available. Review labels and reserve evaluation data for the
new policy; do not rewrite an old artifact or loosen locked criteria after seeing
its test results. [Policy successors](policy-versioning-guide.md)

## Add and review examples

Open **Data and review**. **Download this project's CSV format** gives you the
current question columns; the collapsible question JSON shows the full schema.

### Enter examples without a file

Expand **Enter a few examples without a file**. Choose a new collection or append
to an existing one. For **One example**, enter evidence, optionally mark it as
JSON, provide an ID/source family and select known outcomes. Unknown outcomes
can be labelled during review. Source defaults to hypothetical/synthetic; choose
observed operational data only for actual observations. Use **Save example for
review**.

**A batch of examples** provides an editable table for up to 50 rows. Add evidence,
source families and outcomes, then **Save batch for review**. Entirely empty rows
are ignored. All new records remain pending review. A source family groups records
from the same document, customer or original event; keep related examples together
so they cannot leak between training and evaluation.

### Upload, map and preview a file

Upload CSV, TSV, XLSX, JSON or JSONL. Select the XLSX worksheet when applicable.
Choose canonical `state/questions/gold` records or map ordinary columns into
evidence, IDs, source families and each question's labels. Multiple evidence
columns become JSON with their names preserved. Map file label values to the
project's allowed outcomes; missing labels may remain unknown. Set **File source**
to synthetic/hypothetical for invented examples, and to observed operational
records only for actual observations. Optional timestamp
mapping distinguishes decision time, evidence availability and label availability.

Choose **Validate and preview import**. Inspect the normalized preview and
validation feedback before **Save new collection for review** or **Append file to
collection**. The first-ten-record preview abbreviates evidence; saving preserves
the full accepted content. A changed file or mapping needs a new preview.
Appending creates a new immutable revision. Imported approval flags do not replace
authenticated review. [File and column mapping](import-guide.md)

### Review records and assign work

Select **Dataset version**, use the example page control or **Find example by ID**,
then select **Example to review**. Check the actual evidence and every outcome.
An authorized owner/reviewer corrects labels and chooses **Save draft labels**,
**Approve complete labels** or **Reject and exclude from future preparation**.
Enter a review note and choose **Save reviewed version**. Drafts stay unapproved.
Advanced soft targets must remain valid
complete probability distributions; do not round them merely to fit a backend.

Historical revisions are inspectable. Choose **Open latest review version** to
make a further change. After a stale-edit conflict, refresh and review the current
record before submitting again. **Analyze data readiness** reports missing labels,
class coverage, preparation blockers and warnings; it does not prove that your
examples represent production.

**Review work queue** lets reviewers filter to their assignments and **Open assigned
example**. Owners can **Assign review work**, supply an exact trusted reviewer
actor ID, assign/reassign or cancel an assignment, and inspect assignment history.
Assignment itself does not approve labels. [Review workflow](review-workflow.md)

### Discover or change categories

Enable **Open taxonomy workspace** for a selected dataset. Select the choice
question and source examples on the displayed page. Propose categories manually,
or have an owner select a configured TypeLLM profile and explicitly approve its
data transfer and billable discovery request.

Review every candidate as **add**, **alias** or **reject**, with a canonical ID or
alias target and reason. Once no candidates remain pending, an owner confirms
the taxonomy and creates a successor project. It does not silently relabel current
records or change a released schema. Uncertain calls have an owner settlement
form; verify both local and provider completion before using it.
[Taxonomy discovery](taxonomy-guide.md)

## Reserve data and generate candidates

In **Prepare and generate**, select one reviewed revision per collection in
**Dataset versions to prepare**. Unreviewed or duplicate records prevent
preparation; rejected records are excluded. The four roles have different uses:

| Role | Purpose |
|---|---|
| Train | Fit the model; reviewed synthetic candidates may extend this role |
| Validation | Inspect development performance without using the locked test for tuning |
| Calibration | Fit the exported runtime's probability calibration |
| Test | Check the frozen acceptance criteria using independent real examples |

For a first reservation, leave **Preserve holdouts from an existing snapshot**
unset. **auto** uses chronological roles when the real records declare the
required timestamps, otherwise grouped random roles. **chronological** enforces
availability/time boundaries; **group-random** keeps known source families together.
Use **Reserve train / validation / calibration / test**, then follow the preparation
job in **Training jobs**. Select the resulting **Reserved data snapshot** to inspect
counts and any temporal audit. [Reservations](reservation-guide.md)

### Download reserved study data

With a **Reserved data snapshot** selected, expand **Download reserved study
data**. Confirm **I approve downloading this snapshot's evidence and reviewed
labels**, choose **Prepare reserved study download**, then **Download reserved
canonical study**. Owners, editors and reviewers can prepare this download.

The ZIP contains `train.jsonl`, `validation.jsonl`, `calibration.jsonl`,
`test.jsonl` and a checksum manifest. Extract them together for a compatible
standalone workflow, such as [Perplexity native preparation](models/perplexity-native.md).
The download preserves roles and reviewed labels; it does not train a model or
start cloud compute. Store it privately because it includes evidence and gold
labels. The archive is limited to 64 MiB. If integrity/reservation checks fail,
inspect the preparation result instead of editing the saved snapshot. Preparing
records an audit event; it does not prove that the browser saved the file.

### Choose source groups or date cutoffs

Owners can enable **Choose holdout groups or date cutoffs yourself**, then
**Inspect reservation inputs**. Choose **Source groups** or **Date cutoffs**.
Group mode supports paged assignments and downloadable CSV/JSON assignment
templates; load a completed file or save page assignments. Assign every eligible
group and retain forced training roles. Date mode sets the temporal boundaries
and optional gap between roles.

Use **Preview explicit reservation**, inspect counts and exclusions, then confirm
the source groups/time boundaries. Finally use the main reservation button.
Previewing and confirming do not themselves create the prepared snapshot. Existing
reserved families cannot be reassigned to improve a test score.

### Review semantic relatives

If your operator configured a semantic encoder profile, **Find and review semantic
relatives** appears. An owner queues an audit for the selected revisions and
optional base snapshot. Select the completed audit, page through proposed pairs,
inspect both states, mark each **related** or **independent**, give a reason and
save the review. A required audit blocks reservation until all pairs are reviewed.
The encoder's similarity score proposes relationships; it does not establish
semantic independence. [Semantic review](semantic-data-guide.md)

### Generate and review training candidates

After reserving real evaluation data, use **Generate training candidates**. A
separate local or hosted generative teacher must be configured. An owner describes
coverage gaps, chooses a candidate count and the number of approved training seeds,
then **Preview synthetic request**. Inspect the displayed policy, actual seeds,
teacher limits and estimated costs. Approve both data transfer and any charges
before **Approve and queue synthetic generation**.

Select the **Saved generation request** and refresh it to follow its job, response
metadata and resulting candidate dataset. Review that dataset in **Data and review**.
Then return here, select the reviewed revisions and preserve the original snapshot's
holdouts when extending training. Generation does not approve labels, train a model
or update an active release.

Use **Prepare a separate new generation batch** for another coverage request.
Cancellation stops local transport but may not stop upstream processing or charges.
For an unknown outcome, inspect the teacher and local transport before confirming
the checked outcome. **Prepare an explicit retry**, when offered, creates a new
separately approved attempt; it is not automatic or guaranteed free.
[Generation, budgets and recovery](synthetic-generation-guide.md)

## Train or evaluate a model

Open **Training jobs** after a snapshot exists. All three paths below use the
owner's acceptance criteria and produce a separately evaluated model or policy.

### Set acceptance criteria

Expand **Acceptance criteria — set before the first training run**. An owner sets
minimum accuracy/macro-F1, maximum calibration error, minimum real test counts and
training/calibration examples per outcome. Guided tables or advanced JSON add
asymmetric error costs and critical-label gates. Configure only one editor's
current draft, then **Save acceptance criteria**. Switching editors discards
unsaved changes. Enable the additional Vega criteria before the first study if
you plan to use Vega.

The first training/hosted-study submission freezes these criteria. Inspect failures
and improve data/model behavior; changed policy or gates require a successor.
[Error costs and critical outcomes](error-policy-guide.md)

### Train on this computer

Choose **Training snapshot**, **Training backend** and **This computer**. Configure
the displayed backend settings, inspect capability warnings and choose **Queue
training**.

| Backend | Requirements and detailed controls |
|---|---|
| CPU comparison baseline | Built-in lexical comparison model; no pretrained weight download. It is not decision-head fine-tuning. |
| Laya / Unsloth | Separate compatible model environment, pinned encoder/checkpoint and backend-specific training settings. [Training guide](training-guide.md) |
| Vega | Paired positive/applicability reservations and Vega acceptance gates. [Vega guide](vega-guide.md) |
| Liquid d1-3B TRL / Unsloth | Separate runtime profiles, supported compute device, LoRA/batch/length/step settings and explicit checkpoint download choice. [Liquid guide](models/liquid.md) |
| Liquid omni | Native text/JSON decision-head adaptation and its own resource requirements. [Omni guide](models/liquid-omni.md) |
| D3 family | Variant-specific native readout, memory/storage/context settings; larger runtime profiles remain experimental. [Family guide](models/d3-family.md) |
| Perplexity open v1.1 | Experimental Linux/CUDA full-weight profile with a preinstalled checkpoint and large RAM/VRAM requirements. [Native app setup](models/perplexity-native.md#train-through-the-application); separate from hosted Perplexity. |

The controls vary by backend. Start with its documented settings and use the
validation role to compare changes:

| Training control, when shown | What it changes |
|---|---|
| Model/checkpoint and revision | The exact base artifact. Keep the supported profile's immutable revision; a different model needs its own compatibility and evaluation. |
| Compute device | Selects a supported CPU/CUDA route. Automatic selection does not pool memory across devices. |
| Epochs | Passes through training examples; more passes can overfit. |
| Examples/decisions per micro-batch | Work processed together; larger batches usually need more memory. |
| Gradient accumulation | Combines micro-batches before an update without the same increase in per-batch memory. |
| Complete input token budget | Limits the rendered input, including the question and options. It is not a character count or evidence-only limit. |
| Learning rate | Size of parameter updates; use the selected trainer's supported range. |
| CPU threads / evaluation interval | Native Perplexity host thread budget and the number of optimizer updates between development/calibration checks. Its final-epoch checkpoint is not selected using test data. |
| Adapter/LoRA rank | Capacity of a compatible adapter; native-readout training has a different parameterization. |
| Maximum steps/updates/attempts | Caps work according to that trainer's displayed label. A value of 0 uses the configured epochs. |
| Offline or checkpoint download choice | Controls whether missing pinned assets may be fetched. Check storage requirements first; model downloads do not upload training records. |

Automatic device selection applies only to that backend's supported devices.
Other model paths need their documented packages, cached weights or explicit
download permission and enough disk/RAM/VRAM. An available queue button does not
establish domain quality or universal hardware support. Long inputs are subject
to the selected model's limits; do not silently remove evidence to obtain a pass.

For a shared workspace, **This computer** means the workspace's training host,
which can differ from your browser's computer. Native Perplexity checkpoint and
interpreter paths are configured by an owner before startup, not in the job
request. Cloud study preparation does not require a local GPU or model environment;
follow the [native cloud guide](remote-training/perplexity.md) for compatible
images, provider resources and independent admission.

### Evaluate a hosted decision API

An owner expands **Evaluate a hosted decision model**, selects an approved profile,
approves sending study data and future inference states, then **Queue hosted
evaluation**. Operator configuration supplies the user's server-side provider key;
do not put it in the workspace-login field. The available connectors include
TypeSafe, OpenRouter, Perplexity, Cloudflare and Liquid.

The worker sends evidence/questions, fits calibration locally and registers an
inactive hosted policy. It does not fine-tune provider weights. Activation means
future predictions also send evidence upstream. Perplexity evaluates question
groups jointly; its returned model name is an echo, not an immutable weight pin.
**Show provider call history** lists attempts. Only settle an uncertain call after
checking the required local/provider completion evidence; settlement does not
retry it. [Hosted setup and operations](hosted-decisions-guide.md)

### Train on a cloud GPU

An owner selects a compatible backend and **Cloud GPU**, then opens **Train on a
cloud GPU**. Approve study-data transfer, choose AWS, Azure, GCP, Modal or RunPod,
and enter the approved region/environment, GPU identity and qualified image digest.
**Prepare remote training study** freezes a request; it allocates no paid compute.
When ready, use its study download, or the managed controls when a compatible
operator profile is configured.

For managed execution, select the frozen study/profile and approve data and paid
compute. **Approve cloud run** records approval; **Submit approved paid training**
starts the provider submission workflow. Follow **Observe the same provider job**.
Use **Abandon before compute submission** only before creation, or the displayed
provider stop/termination action for an existing run. A local job cancellation
does not stop cloud billing. RunPod termination retains its network volume;
Modal termination retains its Volume. Storage and other resource charges can
continue after compute stops.

For a completed run, **Retrieve and queue independent admission** validates the
returned export locally. It remains inactive until admission and owner release.
Vega keeps its eight-file study and requires an image profile declaring its backend.
Follow the [Vega cloud guide](remote-training/vega.md),
[provider directory](remote-training/README.md), [returned-model admission](remote-admission-guide.md)
and the selected model guide for exact supported combinations.

### Follow jobs and operational alerts

The job table shows job kind, status, progress and errors. Expand saved result
JSON for the resulting snapshot, dataset, model or study ID. Use **Refresh
workspace** to see changes. **Cancel …** requests cancellation of a queued/running
local job; it does not undo committed data or prove upstream termination.
After a browser timeout, refresh and inspect the job before resubmitting.

Editors/owners can expand **Workspace health and alerts** to inspect worker
heartbeat, queued work and incidents. Owners can acknowledge alerts, add action
notes and resolve eligible incidents; some recover automatically when their
underlying condition clears. **Show alert history** displays prior actions.
Acknowledgement does not repair the cause, and a worker heartbeat does not prove
GPU/provider readiness. [Monitoring and operations](deployment-guide.md)

## Inspect results, release and roll back

In **Models and releases**, inspect the active model/release revision and select
an **Evaluated model version**. Read the per-question test table, **Confusion
matrices and class coverage**, **Full evaluation, provenance and export report**,
domain error-cost/critical-outcome results and any training/calibration outcome
counts. Higher accuracy/F1 and lower calibration error are useful only for the
recorded evaluation data; review its coverage and limitations too.

A completed training/evaluation job does not activate its output. An owner enters
a **Release or rollback reason** and chooses **Activate selected version**. The
server checks frozen gates, artifacts and runtime/profile availability. Selecting
an earlier eligible version and activating it is rollback; the release revision
increases. Hosted profiles must still match their evaluated contract. Choosing a
version in the dropdown alone does not change predictions.
[Evaluation](evaluation-guide.md) · [Release integration](product-workflow.md#6-release-and-integrate)

## Make decisions and export audit history

Choose **Text** or **JSON** under **Evidence format**. Text sends exactly what
you type. JSON parses an object, array or JSON string, preserving structured
evidence for the released model. Match the evidence structure used in reviewed
examples. Malformed JSON, duplicate keys, empty values, JSON numbers/booleans/null and oversized
inputs are refused; they are not silently sent as text. Inference-only users
have the same choice in their prediction screen.

In **Decisions and audit**, enter evidence for the active release and choose
**Abstain below confidence** before **Make decision**. The slider changes the
request's abstention cutoff, not model weights or the frozen acceptance policy.
Inspect each question's answer, probabilities and `abstained` value plus the model
and release identity. Abstention or a request failure must go to your application's
review/fallback path. Confidence is not a correctness guarantee or permission to
perform a consequential action.

Both views enable **Require the active release version** by default. The API rejects a changed release before execution; a request already started may finish with its captured version. Open **Release identity for integrations** to copy the project/model/revision/schema values for the [runnable API client](../examples/platform/README.md). Clear the checkbox only when you intentionally want current-release behavior without this precondition.

Inference-role users get a simpler **Input evidence → Decide** view with a fixed
0.8 cutoff and the same Text/JSON input choice. Use the
[decision API](api-integration-guide.md) to supply application idempotency keys
or automate predictions within your own workflow.
Repeated browser predictions are fresh calls and may incur hosted charges; the
browser does not expose an inference request-key field. Keep secrets and unnecessary
personal information out of evidence.

### Capture and download audit windows

Owners see the recent audit table and can enable **Open audit exports**. Enter an
after-event identifier (`0` for the beginning, or the prior window's `last_id`),
optionally use a previously frozen end identifier, describe the purpose, approve
the identity/note download and **Capture audit window**.

Select the frozen export, **Prepare verified audit download**, then **Download
verified audit window**. Inspect its manifest and page through the frozen events
when reviewing a captured window. Retain its manifest checksum separately for later
verification. Use history pages for older captures. These exports are audit
records, not training data exports or workspace backups.
[Audit export guide](audit-export-guide.md)

## Prepare Vega applicability data

The **Applicability** tab is an additional Vega workflow, not a prerequisite for
other backends. Collect actual cases where particular project questions should
not apply. Provide source evidence, its family, the inapplicable questions and a
reason, then confirm that it is observed data. Alternatively download its separate
CSV format and import an applicability file. These records carry applicability
judgments, not invented business-outcome labels.

Review the negative records, select their reviewed versions and the matching
**Frozen positive snapshot for applicability**, then **Reserve applicability
train / validation / calibration / test**. Complete any required combined semantic
review. Enable owner Vega acceptance criteria before the first study, then select
the paired applicability snapshot when training Vega in **Training jobs**.
Use **Download current applicability revision** to export the selected negative
collection separately from a reserved study.
**Prepare eight-file Vega study download** also provides reserved positive and
negative roles for a standalone study. [Applicability guide](applicability-guide.md)

## When a control is unavailable

| What you see | What to do next |
|---|---|
| No project or missing tabs | Create/select a project. Inference-only users receive the prediction view. |
| Save/approval/release disabled | Check the signed-in role and required confirmation fields. Request the appropriate owner/reviewer action. |
| Reservation disabled or rejected | Review all included records, use one revision per collection, complete required semantic reviews and explicit preview approval, and inspect readiness feedback. |
| No training snapshot | Wait for the preparation job, refresh, then choose its saved snapshot. |
| Training unavailable | Save owner criteria, provide required class/role coverage and install/select the compatible runtime. For Vega, pair reviewed applicability data. |
| No teacher/provider/cloud profiles | Configure them through the linked operator guide and restart services. Workspace login does not create provider accounts or keys. |
| A job stays queued | Keep the worker running and inspect Workspace health and alerts. Refreshing the page does not start a missing worker. |
| Timeout or unknown external outcome | Inspect the saved job/call and provider state. Do not assume no data was sent or automatically create another billable attempt. |
| Stale edit, assignment or release conflict | Refresh, inspect the latest version/revision and review your action again. |
| No active model or release refused | Inspect the frozen criteria, test counts/metrics, export/runtime/profile availability and error message. Training success alone is insufficient. |
| Shared model needs cache warming | Ask the operator to [warm the selected export on each serving node](shared-artifacts-guide.md#train-release-and-warm-a-cache), then retry release or prediction. Personal local mode does not require S3. |
| Shared-workspace node is low on disk space | Ask the operator to [review inactive cache retention](cache-retention-guide.md). It runs from the operator terminal while that node's API and worker are stopped; the browser cannot apply it to its own running server. |
| Decision abstains or fails | Use the fallback. Evaluate evidence coverage and the deployed profile before changing thresholds or collecting further training data. |

## Operator tasks outside the browser

The UI links to setup instructions; it does not configure every external service.
Operators use the guides below for credentials, infrastructure and maintenance:

- [Installation, persistence and teacher settings](setup.md), [authentication](authentication-guide.md)
  and [dependency profiles](../requirements/README.md).
- [Hosted provider keys/profiles](hosted-decisions-guide.md), [cloud training profiles](remote-training/README.md)
  and [semantic encoder configuration](semantic-data-guide.md).
- [Backup and restore](recovery-guide.md), [encrypted recovery](encrypted-recovery-guide.md),
  [scheduled backups](scheduled-backups-guide.md) and [backup monitoring](backup-monitoring-guide.md).
- [Shared PostgreSQL/S3 backup and restore](shared-recovery-guide.md): operator commands for coordinated snapshots and recovery into a fresh workspace.
- [API integration](api-integration-guide.md), [deployment](deployment-guide.md)
  and [current capabilities and limits](capabilities.md).

Model and cloud support is profile-specific. A guide link, available dropdown or
successful fixture test is not proof of production quality, provider billing
behavior or compatibility with every machine.

## Organization sign-in

An operator can enable [OIDC sign-in](enterprise-sign-in-guide.md) for a shared deployment. Select **Sign in with your organization**, complete the identity-provider redirect and use the workspace role shown in the sidebar. No workspace credential needs copying. If the API refuses an expired, revoked or unmapped token, use **Sign out and choose an account** and sign in again. Account/role changes clear the previous session's drafts. Personal localhost mode still uses the ordinary local entry screen.
