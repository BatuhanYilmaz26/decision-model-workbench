# Import and review domain data

<a id="describe-a-domain-and-build-trustworthy-training-data"></a>

[Documentation](README.md) · [Project overview](../README.md)

Define your evidence and outcomes, import or enter examples, then review them. The shared data contract is converted by the selected model adapter; imported approval flags never bypass authenticated review.

## Choose a section

- [Domain data and quality](#data-contract)

- [File import and column mapping](#file-import)

- [Reviewer assignments](#assigned-review)

<a id="data-contract"></a>

## Domain data and quality

<a id="data-contract--describe-a-domain-and-build-trustworthy-training-data"></a>

Turn a domain description and example records into reviewed training data. Define the question contract, import or enter examples, reserve independent evaluation sets, then generate additional training candidates where coverage is missing. The `state/questions/gold` format follows [Laya's training implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/train.py) and [Unsloth's dataset builder](https://github.com/unslothai/unsloth/blob/main/unsloth/models/decision.py).

<a id="data-contract--describe-the-problem-in-natural-language"></a>

### Describe the problem in natural language

Before producing labels, specify the deployed input, allowed outcomes, written policy, missing-evidence behavior, error costs, fallback, languages, lengths and rare cases. Identify which examples may be used and which must remain private.

“Route invoices by review queue using policy version 3; identify duplicates from supplied history; do not authorize payment” defines a task. “Understand finance” does not.

The product UI separately saves the domain/policy description and the requested data coverage, then offers a structured schema builder. Requested data coverage is included in the generation context. Its advanced import and maintenance CLI use explicit JSON schemas. They do not infer final business policy silently.

<a id="data-contract--define-the-decision-schema"></a>

### Define the decision schema

The [support specification](../examples/support/spec.json) demonstrates choice, yes/no and ordinal score questions. Use stable question ids and explicit descriptions.

Provide an `other`, `unknown` or `manual_review` choice where appropriate. A yes/no question cannot produce a separate missing-evidence category; add an evidence-presence question or use a choice schema.

For incompatible combinations, apply deterministic constraints after prediction or predict a joint outcome. Several questions in one request do not automatically enforce cross-field consistency.

<a id="data-contract--supply-examples-or-upload-real-data"></a>

### Supply examples or upload real data

Follow [preview and import](data-guide.md#file-import) for file upload, mapping and collection append. Validate the full batch, inspect its normalized preview and save it for review. Changed files, worksheets, mappings or collection versions require a fresh preview.

Use the [review assignment workflow](data-guide.md#assigned-review) to distribute examples to reviewers, track drafts/completion and preserve reassignment history.

| Format | Expected contents |
|---|---|
| CSV / TSV | Preview and map input/ID/source-family/label columns; optional label-value aliases |
| XLSX | Select a worksheet, preview its header/data, then map columns; cover sheets may be skipped |
| JSON | Array of records; unknown labels may be absent in imported unreviewed drafts |
| JSONL | One record per line; complete reviewed targets are required for training |

XLSX import reads cached cell values. Export formulas to values first; missing caches are not labels. Legacy XLS, PDF, DOCX and images need separate extraction and human verification.

The UI previews ten rows, lists worksheets and shows missing-cell/value samples. Select one input column for text or several columns to preserve their field names as JSON. Map a unique ID and a source-family column where available. Generated IDs are stable for the same file, worksheet and source row. Choose only fields available at decision time; future outcomes and annotation columns must not become model evidence.

Label columns can use different names from question IDs. Map source values such as `Billing dept` to a category key or `Yes` to `true`. Choice targets use allowed keys, noul targets use true/false and score targets use zero-based indices. Missing columns or blank label cells remain unknown; invalid supplied labels stop the entire import with a source-row error. A canonical-column option preserves existing `state/questions/gold/metadata` records. The project CSV template remains useful.

Use **Enter a few examples without a file** for individual text/JSON records or a batch of up to 50 examples. Create a collection or append to an existing one. File imports can also append an accepted batch. IDs must be unique; duplicates, invalid rows or capacity failures roll back the batch. Each collection holds at most 20,000 rows, and an unsuccessful save retains the draft form input.

Manual batches ignore entirely empty placeholders. Rows with an ID, source family, supplied outcome or operational-source selection require input evidence; incomplete rows are reported instead of silently dropped. See [batch entry](data-guide.md#file-import--enter-examples-without-a-file) for spreadsheet paste instructions.

Hypothetical examples default to synthetic origin. Identify observed operational records explicitly. Imported labels and review flags do not approve a record: reviewers choose draft, approve or reject and record a reason. Partial annotations can be saved, but approval needs normalized targets for every question. Pending records block preparation.

Drafts, appends and reviews create immutable versions. New preparations exclude rejected records and retain their reviewer, reason and dataset hash. Reopening a rejection creates another revision; an existing snapshot keeps its original membership.

Each API upload is limited to 10 MiB and 20,000 rows. Tables have at most 256 columns; XLSX expansion is limited to 50 MiB and 4096 archive entries. Preview strings are bounded and value lists are sampled. Prepared canonical training files may combine multiple accepted datasets and are not incorrectly capped by the per-upload row limit. Realistic enterprise load remains a separate acceptance gate.

For probabilistic labels, use canonical JSONL:

```json
{
  "id": "case-001",
  "group_id": "original-ticket-001",
  "state": "I do not want a refund. Please send the invoice.",
  "questions": {
    "refund": {"type": "noul", "instructions": "Does the writer explicitly request a refund?"}
  },
  "gold": {
    "refund": {"probabilities": {"false": 1.0, "true": 0.0}}
  },
  "metadata": {"origin": "real", "reviewed": true}
}
```

Choice probabilities use category keys; score probabilities use `"0", "1", ...`. All allowed options appear and sum to one. Hard labels become one-hot distributions. Soft targets can describe adjudicator disagreement or teacher distributions, but provenance must remain visible. Teacher confidence is not human ground truth.

Assign the same `group_id` to versions, chunks, translations and paraphrases of one source/customer family. The simple importer defaults missing groups to row identity; replace this where rows are related.

<a id="data-contract--inspect-readiness-before-training"></a>

### Inspect readiness before training

The [reviewed semantic audit](reservation-guide.md#semantic-relatives) is a separate owner-controlled job. A pinned local CPU encoder proposes related state pairs, authenticated adjudication binds the exact input versions and related records stay together. Required scoped profiles block unchecked reservations. The readiness report itself remains a format/count/exact-link diagnostic, not an embedding audit or proof of independence.

**Analyze data readiness** reports missing/reviewed labels, class counts and probability-target mass, sparse-class warnings, input lengths, exact duplicate/conflicting states and linked real-family counts. Partial annotations can reveal conflicts on shared known questions. Known semantic links between included records join transitively with source, state and seed links, so related records do not inflate the independent-family count. A partial collection view cannot resolve relationships to records outside that view; preparation validates the complete study and refuses missing or cross-role relatives. These checks cannot prove undocumented independence, semantic decontamination or temporal safety.

An owner sets minimum reviewed training/calibration examples per predominant outcome alongside quality gates before the first training submission. The submission freezes the complete policy. Workers check the actual reserved splits and refuse insufficient counts before creating artifacts or loading models. Defaults are five training examples and one calibration example per outcome; these are configurable mechanical floors, not universal sample-size recommendations. Counts use the predominant probability target, matching classification evaluation. Model versions record observed split counts for inspection. Frozen criteria cannot be relaxed after a failed run; collect more approved data or create a successor study.

The five-example warning in the dataset report is collection guidance, separate from these owner-defined gates. Representative enterprise gold, business costs, uncertainty and subgroup adequacy still need domain-owner qualification.

<a id="data-contract--reserve-real-evaluation-data-before-generation"></a>

### Reserve real evaluation data before generation

For an actual study, first reserve time- or entity-separated validation, calibration and locked test sets. Generate only from approved training seeds and training policy examples. Do not send test examples to the teacher.

Automatic preparation produces deterministic approximate 70/10/10/10 group splits over real-only families. Owners can also [assign groups or date cutoffs explicitly](reservation-guide.md#reservations), preview counts and confirm a source-bound plan. Synthetic families and any included real seed ancestors go into training. Groups and normalized exact-state duplicates are joined transitively. All included labels must be approved. Explicitly rejected records are excluded; pending records still block preparation. Later preparations cannot move known project families into different roles.

Undated records retain these grouped random reservations. Dated operational studies support chronological roles and declared evidence/label availability checks through intake, preparation, training and restore. See the [temporal data guide](reservation-guide.md#time-boundaries) for timestamp fields, CSV/XLSX mapping, role ordering and conservative interval joins. Semantic near-duplicates, undocumented lineage and domain adequacy remain separate checks. Inspect manifests and class counts.

At least four independent real groups are required by the tool. Four groups make a demonstration, not a defensible benchmark. The bundled 12 examples are handwritten fictional fixtures that exercise file handling only. Their `real` origin flag simulates imported operational data for the demonstration; it is not evidence of real enterprise traffic.

<a id="data-contract--generate-and-review-synthetic-candidates"></a>

### Generate and review synthetic candidates

Use a **generative chat teacher**, local or hosted. A non-generative decision model cannot write new examples. Follow the [synthetic-generation guide](synthetic-generation-guide.md) to configure the teacher, preview the exact request, approve transfer and charges, inspect saved outcomes and retry explicitly.

Provide policy, schema and a coverage matrix: common/rare labels, no-match cases, negation, contradictory evidence, lengths, languages and policy boundaries. Generate counterfactual pairs where one relevant fact changes the label; keep the pair in one family.

The companion requests 1–50 candidates, validates the schema, records teacher identity and seed ids, and marks every result unreviewed. Invalid output is rejected rather than silently accepting a partial batch. Provider JSON-output support is required.

In the product, uploaded review flags are reset; authenticated browser review records the reviewer and creates an immutable revision. The standalone preparation CLI accepts documented review flags for maintenance use. Review state and label independently. Use the authenticated rejection decision to exclude invalid examples while preserving their history; do not remove a row by editing the database. Correct unsupported facts, discard copied or identifying text, inspect class distribution and deduplicate. Set `metadata.reviewed=true` only after this work. Retain prompt/policy version and reviewer decisions in the enterprise audit system.

The recorded teacher id is user-supplied, not a verified immutable checkpoint. For reproducibility, serve a pinned model and archive its configuration. Generated labels remain candidates even when the probability is 1.0.

<a id="data-contract--preserve-the-contract"></a>

### Preserve the contract

Export four JSONL datasets and the hash manifest. Archive specification, policy version, grouping, source access rules and teacher/data licenses with the model release.

A changed rubric is a data/model version change. Recalibrate and reevaluate after material changes to policy, labels, language, runtime or quantization. Synthetic test labels do not establish real-world reliability.

<a id="data-contract--collection-revision-contracts"></a>

### Collection revision contracts

The local store uses schema version 9. Supported upgrades retain existing dataset hashes, review histories and pinned references. Appends add only new membership/content and one compact revision; reviews still add one changed record. Reading an older version does not expose rows appended later. Appending requires the current immutable version and its SHA-256; concurrent stale writes return a conflict and require refresh. Assignment revisions coordinate review work separately from dataset revisions; see [review assignments and recovery](data-guide.md#assigned-review).

- `POST /v1/projects/{project}/examples` creates a collection from 1–50 manual examples.
- `POST /v1/projects/{project}/datasets/{dataset}/examples` accepts `examples` and `expected_hash`, appending a manual batch.
- `POST /v1/projects/{project}/datasets` accepts the file/mapping plus optional multipart `dataset` and `expected_hash` together. Both manual and file additions require subsequent authenticated review.
- `POST /v1/projects/{project}/datasets/{dataset}/reviews` accepts `status` (`pending`, `approved`, `rejected`), `approved`, record/gold/hash/note. The approved flag must agree with the status; callers using the previous approved-only contract remain supported.

Dataset quality reports count rejected rows separately and compute label, class, conflict and readiness diagnostics on eligible rows. They report timestamp coverage; partly dated real data block chronological preparation. A report describes the selected dataset version and does not invalidate an existing model. Assign annotation work, verify timestamps against source systems and apply your enterprise retention policy separately. Use the [semantic workflow](reservation-guide.md#semantic-relatives) to review possible relatives; similarity checks cannot prove that every source relationship has been found.

For missing choice categories, use [reviewed taxonomy discovery](domain-schema-guide.md#category-discovery) before a successor study. The examples used for taxonomy selection and their known relatives become training-only across project ancestry; proposals and provider assignments do not automatically become gold.

For asymmetric mistakes and rare critical outcomes, configure a [frozen domain error policy](evaluation-guide.md#error-cost-policy) before training. It supplements accuracy/calibration gates with explicit error costs and class-specific limits.

<a id="file-import"></a>

## File import and column mapping

<a id="file-import--preview-and-import-your-examples"></a>

Import examples into a new collection or append them to an existing one. The application validates the entire file before saving and previews its first ten normalized records. Every imported record starts pending review, including records with complete labels or an approval flag.

<a id="file-import--before-you-start"></a>

### Before you start

Follow [local setup](setup.md#configuration), open your domain project and define its questions and allowed outcomes. Owners and editors can import; reviewers annotate saved examples through the [review workflow](data-guide.md#assigned-review).

Use CSV, TSV, XLSX, JSON or JSONL. The project CSV template uses your question names. Canonical JSON/JSONL preserves `state/questions/gold/metadata`, including probability targets; see [the record format](data-guide.md#data-contract--supply-examples-or-upload-real-data). JSON is an array of records; JSONL has one record per nonblank line.

<a id="file-import--import-a-file"></a>

### Import a file

1. Under **Collection for file import**, choose **Create a new collection** or an existing collection. Appending preserves its records and historical versions.
2. Upload your file. For XLSX, select the worksheet with your records; a blank cover sheet can be skipped. This selection also applies to canonical record columns.
3. For a table, enable **Use canonical state/questions/gold record columns** for canonical records. Otherwise select input evidence columns, optional IDs and source families, labels and label-value mappings. Several input columns become JSON with their original field names.
4. Choose observed operational or synthetic/hypothetical origin. Map decision/evidence/label availability timestamps where present. Include only information available at decision time as evidence.
5. Click **Validate and preview import**. Inspect normalized inputs, IDs, source families, outcomes, unknown-outcome counts and destination. Appends show the resulting collection size. Nothing is saved yet.
6. Correct errors and preview again. Validation checks the whole batch, including rows beyond the sample. Changing the file, mapping, source, worksheet or destination invalidates the preview. Another writer advancing the collection also requires a new preview.
7. Click **Save new collection for review** or **Append file to collection**. The uploader resets after a successful save. Open the collection below and approve, correct or reject records before preparation.

The preview displays each target's highest-probability outcome; canonical soft-label distributions are preserved in full when saved. Evidence is shortened to 500 characters only for display. Previewing checks format and append eligibility, not label correctness, source independence or model quality.

<a id="file-import--enter-examples-without-a-file"></a>

### Enter examples without a file

Open **Enter a few examples without a file**. Choose one text/JSON example or **A batch of examples** of up to 50 records. Unknown outcomes can be completed during review. Hypothetical examples default to synthetic origin. Choose a new or existing collection. Failed saves retain your draft input.

For a spreadsheet batch, copy cell values without the header row and paste into the first table cell. Match the displayed column order: evidence, ID, source family, source, then the outcome columns. Use the allowed outcome keys; leave unknown outcomes blank. Entirely empty placeholder rows are ignored. A row with an ID, source family, outcome or operational source needs evidence and is rejected if that evidence is blank. A successful save clears the table for the next batch; an error preserves it for correction.

<a id="file-import--resolve-import-errors"></a>

### Resolve import errors

| Situation | Action |
|---|---|
| Blank cover worksheet | Select the sheet with unique headers and data. |
| Unknown label | Map it to an allowed outcome, correct the source, or leave the label blank for later review. |
| Duplicate ID | Give distinct examples unique IDs. Correct an existing record through review instead of importing it again. |
| Collection changed | Refresh its latest version and preview again. Previews do not reserve or lock versions. |
| Invalid CSV or UTF-8 | Export a consistently quoted UTF-8 CSV/TSV. Quoted newlines are accepted; unmatched quotes are rejected. |
| Repeated JSON keys or nonfinite numbers | Correct the JSON. Duplicate names, `NaN` and `Infinity` are rejected. |
| Blank formula cells | Export formulas as values. XLSX reads cached values without calculating formulas. |

Invalid rows, duplicate IDs or exceeded capacity prevent the entire batch from being published. Missing labels are allowed in pending records; approval requires complete targets for every question.

Limits are 10 MiB and 20,000 rows per upload, 256 table columns, 64 KB of serialized evidence per record and 20,000 records per collection. XLSX expansion is bounded to 50 MiB and 4,096 archive entries. See [Python CSV rules](https://docs.python.org/3/library/csv.html#csv.Dialect.strict) and [openpyxl read-only behavior](https://openpyxl.readthedocs.io/en/stable/optimized.html#read-only-mode) for the underlying formats. These limits do not establish performance for every deployment.

<a id="file-import--use-the-api"></a>

### Use the API

Authenticate with an owner/editor credential using [the authentication guide](authentication-guide.md#workspace-credentials). These routes accept multipart form data containing `file`.

| Route | Optional fields and result |
|---|---|
| `POST /v1/projects/{project}/imports/inspect` | `worksheet`; returns raw table headers, sampled values and sheet choices for CSV/TSV/XLSX mapping. |
| `POST /v1/projects/{project}/imports/preview` | `mapping` JSON object, `worksheet`, and paired `dataset`/`expected_hash` for appends. Returns `preview_sha256`, counts, warnings and normalized sample records. Creates no dataset or audit event. |
| `POST /v1/projects/{project}/datasets` | Same fields plus optional `preview_sha256`. When supplied, it must match this actor, project, file, mapping and destination version. The complete batch is revalidated; appends also check the current version inside their transaction. |

The browser always previews before saving. Existing API callers may omit `preview_sha256`; full validation and append checks still apply. The hash is a consistency check, not a credential or signed authorization token.

For mapped XLSX, separate `worksheet` must match `mapping.worksheet`. For canonical XLSX, send `worksheet` directly. Other formats reject worksheet selection. Mapping must be a JSON object of at most 32,000 characters. Appends need both the immutable dataset version ID and expected SHA-256. Conflicts return HTTP 409; invalid data returns HTTP 400. Invalid form-field lengths can return HTTP 422.

Continue with [review](data-guide.md#assigned-review), [independent reservations](reservation-guide.md#reservations), [synthetic generation](synthetic-generation-guide.md) and [data readiness](data-guide.md#data-contract--inspect-readiness-before-training).

<a id="assigned-review"></a>

## Reviewer assignments

<a id="assigned-review--assign-and-review-domain-examples"></a>

Use a persistent review queue to distribute annotation work and record who corrected, approved or rejected each example. Workspace owners assign records; the assigned reviewer saves labels through the same immutable dataset workflow used for unassigned examples.

<a id="assigned-review--prepare-reviewer-access"></a>

### Prepare reviewer access

Create a workspace credential with role `reviewer`, or configure a trusted enterprise access-token mapping to that role. Workspace owners use role `admin`. Use the reviewer's exact **actor ID** from the trusted identity configuration. The sidebar displays the authenticated actor ID. [Credential setup](authentication-guide.md#workspace-credentials)

An assignment does not create a user, issue credentials or grant project access. The reviewer must already authenticate into the same tenant. Owners may review assigned work themselves; their identity and review note remain in the history. Other reviewers cannot save an example while it is assigned to someone else.

<a id="assigned-review--assign-work-in-the-browser"></a>

### Assign work in the browser

1. Open the project and its data tab. Choose the latest dataset version.
2. Select an example. For a specific record, enter its ID in **Find example by ID**.
3. Open **Assign review work**. Select **Assign or reassign**, enter the reviewer actor ID and explain the assignment.
4. Select **Save assignment**. The work appears in **Review work queue** without changing the example's labels, approval or dataset hash.

Owners can reassign active or completed records for another review. Choose **Cancel assignment** to cancel currently assigned work. Every change retains the previous assignment; cancellation preserves all dataset and review history.

<a id="assigned-review--complete-assigned-work"></a>

### Complete assigned work

1. Sign in as the reviewer and open the project.
2. Expand **Review work queue**, with **Only my assignments** selected. Use the page control to browse additional tasks.
3. Select a task and choose **Open assigned example**. The application opens the collection's current version and finds the record, including records outside the first example page.
4. Read the decision-time evidence and written policy. Correct each label; choose a draft, approval or rejection and supply a review note.
5. Select **Save reviewed version**. A draft keeps the assignment open; approval or rejection completes it. Each save creates an immutable dataset revision.
6. Select **Show assignment history** to inspect assignment, draft, completion and cancellation events, including actor, note and referenced data version.

Reassignment and review are checked in the same database transaction as the label revision. If someone changes the task or collection while you are editing, refresh before saving again. A conflict never silently overwrites another review. Revoked credentials or changed roles are checked by the API on the next request.

<a id="assigned-review--assign-a-batch-through-the-api"></a>

### Assign a batch through the API

The assignment endpoint accepts 1–100 distinct record IDs per request. Supply the current dataset SHA-256 and each record's current assignment revision; use `0` only when it has no assignment history. The request succeeds atomically or changes nothing.

Read current assignments with:

```text
GET /v1/projects/{project}/datasets/{dataset}/review-assignments?limit=100&offset=0
GET /v1/projects/{project}/datasets/{dataset}/review-assignments?record_id={record_id}&limit=1
```

Submit this body to `POST /v1/projects/{project}/datasets/{dataset}/review-assignments` using an owner's bearer credential. Replace the illustrative record IDs, SHA-256 and actor ID with values from your workspace:

```json
{
  "targets": [
    {"record_id": "example-1", "expected_revision": 0},
    {"record_id": "example-2", "expected_revision": 3}
  ],
  "assignee": "annotator",
  "expected_hash": "DATASET_SHA256",
  "note": "Review rare routing cases against policy version 3"
}
```

The `assignee` field is required: use an actor ID to assign work, or explicit `assignee: null` to cancel currently assigned targets. Omitting the field is rejected. A reviewer cannot call this owner-only endpoint. List endpoints accept 1–200 items per page and an integer offset from 0 to 2,147,483,647. The project queue and paginated record history are available through:

```text
GET /v1/projects/{project}/review-assignments?mine=true&status=assigned&limit=50&offset=0
GET /v1/projects/{project}/datasets/{dataset}/review-assignments/history?record_id={record_id}&limit=20&offset=0
```

To save assigned labels through `POST /v1/projects/{project}/datasets/{dataset}/reviews`, include the current `assignment_revision` alongside `record_id`, `gold`, `approved`, `status`, `expected_hash` and `note`. A draft advances the assignment revision, so fetch the new value before the next save. Continue to use the project's complete [gold-label format](data-guide.md#data-contract) for approval. Unassigned reviews support the existing request format without an assignment revision.

Queue pages show current workflow state; they are not frozen dataset snapshots. Historical dataset views exclude assignments for records appended after that version. Existing reserved training, calibration and test records retain their original labels and hashes.

<a id="assigned-review--upgrade-and-recover-a-workspace"></a>

### Upgrade and recover a workspace

Back up the workspace, then stop the API, browser and worker services before installing and reopening the updated application. Do not run mixed application versions against one workspace: the schema compatibility check occurs at startup. Schema version **7** retains assignment revision uniqueness, frozen domain error gates and backup-alert semantics; the migration preserves dataset hashes, labels and registered artifacts. Older application versions supporting only schemas through 6 refuse to open the upgraded workspace. Reverting requires a compatible backup, rather than manually lowering the version number.

Backups include assignment history and its exact dataset/record references. Restore validates assignment hashes, revision chains and record bindings before publishing a recovered workspace. Existing schema-3/4/5/6 backups can be restored and upgraded. Follow [backup and recovery](recovery-guide.md#local-backups).

Assignments currently apply to positive domain-example collections. Vega applicability-negative collections use their separate [review workflow](vega-guide.md#applicability-data). Assignment is work coordination, rather than a separate record-visibility permission: authorized project reviewers retain their existing read access. Creating an assignment does not reopen an approved record; explicitly save a pending draft when approval needs to change. Assignments do not establish label correctness or replace independent operational evaluation.
