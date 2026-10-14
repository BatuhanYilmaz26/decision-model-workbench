# Describe a domain and build trustworthy training data

[All guides](README.md) · [Project overview](../README.md)

Turn a domain description and example records into reviewed training data. Define the question contract, import or enter examples, reserve independent evaluation sets, then generate additional training candidates where coverage is missing. The `state/questions/gold` format follows [Laya's training implementation](https://github.com/NandhaKishorM/laya/blob/main/laya/train.py) and [Unsloth's dataset builder](https://github.com/unslothai/unsloth/blob/main/unsloth/models/decision.py).

## Describe the problem in natural language

Before producing labels, specify the deployed input, allowed outcomes, written policy, missing-evidence behavior, error costs, fallback, languages, lengths and rare cases. Identify which examples may be used and which must remain private.

“Route invoices by review queue using policy version 3; identify duplicates from supplied history; do not authorize payment” defines a task. “Understand finance” does not.

The product UI separately saves the domain/policy description and the requested data coverage, then offers a structured schema builder. Requested data coverage is included in the generation context. Its advanced import and maintenance CLI use explicit JSON schemas. They do not infer final business policy silently.

## Define the decision schema

The [support specification](../examples/support/spec.json) demonstrates choice, yes/no and ordinal score questions. Use stable question ids and explicit descriptions.

Provide an `other`, `unknown` or `manual_review` choice where appropriate. A yes/no question cannot produce a separate missing-evidence category; add an evidence-presence question or use a choice schema.

For incompatible combinations, apply deterministic constraints after prediction or predict a joint outcome. Several questions in one request do not automatically enforce cross-field consistency.

## Supply examples or upload real data

Use the [review assignment workflow](review-workflow.md) to distribute examples to reviewers, track drafts/completion and preserve reassignment history.

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

## Inspect readiness before training

The [reviewed semantic audit](semantic-data-guide.md) is a separate owner-controlled job. A pinned local CPU encoder proposes related state pairs, authenticated adjudication binds the exact input versions and related records stay together. Required scoped profiles block unchecked reservations. The readiness report itself remains a format/count/exact-link diagnostic, not an embedding audit or proof of independence.

**Analyze data readiness** reports missing/reviewed labels, class counts and probability-target mass, sparse-class warnings, input lengths, exact duplicate/conflicting states and linked real-family counts. Partial annotations can reveal conflicts on shared known questions. Distinct group IDs do not turn identical states or known seed descendants into independent holdouts. These checks cannot prove undocumented independence, semantic decontamination or temporal safety.

An owner sets minimum reviewed training/calibration examples per predominant outcome alongside quality gates before the first training submission. The submission freezes the complete policy. Workers check the actual reserved splits and refuse insufficient counts before creating artifacts or loading models. Defaults are five training examples and one calibration example per outcome; these are configurable mechanical floors, not universal sample-size recommendations. Counts use the predominant probability target, matching classification evaluation. Model versions record observed split counts for inspection. Frozen criteria cannot be relaxed after a failed run; collect more approved data or create a successor study.

The five-example warning in the dataset report is collection guidance, separate from these owner-defined gates. Representative enterprise gold, business costs, uncertainty and subgroup adequacy still need domain-owner qualification.

## Reserve real evaluation data before generation

For an actual study, first reserve time- or entity-separated validation, calibration and locked test sets. Generate only from approved training seeds and training policy examples. Do not send test examples to the teacher.

The companion produces deterministic approximate 70/10/10/10 group splits over real-only families. Synthetic families and any included real seed ancestors go into training. Groups and normalized exact-state duplicates are joined transitively. All included labels must be approved. Explicitly rejected records are excluded; pending records still block preparation.

Undated records retain these grouped random reservations. Dated operational studies support chronological roles and declared evidence/label availability checks through intake, preparation, training and restore. See the [temporal data guide](temporal-data-guide.md) for timestamp fields, CSV/XLSX mapping, role ordering and conservative interval joins. Semantic near-duplicates, undocumented lineage and domain adequacy remain separate checks. Inspect manifests and class counts.

At least four independent real groups are required by the tool. Four groups make a demonstration, not a defensible benchmark. The bundled 12 examples are handwritten fictional fixtures that exercise file handling only. Their `real` origin flag simulates imported operational data for the demonstration; it is not evidence of real enterprise traffic.

## Generate and review synthetic candidates

Use a **generative chat teacher**, local or hosted. A non-generative decision model cannot write new examples.

Provide policy, schema and a coverage matrix: common/rare labels, no-match cases, negation, contradictory evidence, lengths, languages and policy boundaries. Generate counterfactual pairs where one relevant fact changes the label; keep the pair in one family.

The companion requests 1–50 candidates, validates the schema, records teacher identity and seed ids, and marks every result unreviewed. Invalid output is rejected rather than silently accepting a partial batch. Provider JSON-output support is required.

In the product, uploaded review flags are reset; authenticated browser review records the reviewer and creates an immutable revision. The standalone preparation CLI accepts documented review flags for maintenance use. Review state and label independently. Use the authenticated rejection decision to exclude invalid examples while preserving their history; do not remove a row by editing the database. Correct unsupported facts, discard copied or identifying text, inspect class distribution and deduplicate. Set `metadata.reviewed=true` only after this work. Retain prompt/policy version and reviewer decisions in the enterprise audit system.

The recorded teacher id is user-supplied, not a verified immutable checkpoint. For reproducibility, serve a pinned model and archive its configuration. Generated labels remain candidates even when the probability is 1.0.

## Preserve the contract

Export four JSONL datasets and the hash manifest. Archive specification, policy version, grouping, source access rules and teacher/data licenses with the model release.

A changed rubric is a data/model version change. Recalibrate and reevaluate after material changes to policy, labels, language, runtime or quantization. Synthetic test labels do not establish real-world reliability.

## Collection revision contracts

The local store uses schema version 7. Upgrades from versions 1–6 retain existing dataset hashes, review histories and pinned references. Appends add only new membership/content and one compact revision; reviews still add one changed record. Reading an older version does not expose rows appended later. Appending requires the current immutable version and its SHA-256; concurrent stale writes return a conflict and require refresh. Assignment revisions coordinate review work separately from dataset revisions; see [review assignments and recovery](review-workflow.md).

- `POST /v1/projects/{project}/examples` creates a collection from 1–50 manual examples.
- `POST /v1/projects/{project}/datasets/{dataset}/examples` accepts `examples` and `expected_hash`, appending a manual batch.
- `POST /v1/projects/{project}/datasets` accepts the file/mapping plus optional multipart `dataset` and `expected_hash` together. Both manual and file additions require subsequent authenticated review.
- `POST /v1/projects/{project}/datasets/{dataset}/reviews` accepts `status` (`pending`, `approved`, `rejected`), `approved`, record/gold/hash/note. The approved flag must agree with the status; callers using the previous approved-only contract remain supported.

Dataset quality reports count rejected rows separately and compute label, class, conflict and readiness diagnostics on eligible rows. They report timestamp coverage; partly dated real data block chronological preparation. A report describes the selected dataset version and does not invalidate an existing model. Assign annotation work, verify timestamps against source systems and apply your enterprise retention policy separately. Use the [semantic workflow](semantic-data-guide.md) to review possible relatives; similarity checks cannot prove that every source relationship has been found.

For missing choice categories, use [reviewed taxonomy discovery](taxonomy-guide.md) before a successor study. The examples used for taxonomy selection and their known relatives become training-only across project ancestry; proposals and provider assignments do not automatically become gold.

For asymmetric mistakes and rare critical outcomes, configure a [frozen domain error policy](error-policy-guide.md) before training. It supplements accuracy/calibration gates with explicit error costs and class-specific limits.
