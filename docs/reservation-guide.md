# Reserve independent evaluation data

<a id="choose-evaluation-groups-and-time-boundaries"></a>

[Documentation](README.md) · [Project overview](../README.md)

Prepare training, validation, calibration and locked test data. Use source groups and operational timestamps where available; semantic review is an additional optional check, not proof of independence.

## Choose a section

- [Reserve groups and roles](#reservations)

- [Chronological reservations](#time-boundaries)

- [Semantic relatives and review](#semantic-relatives)

<a id="reservations"></a>

## Reserve groups and roles

<a id="reservations--choose-evaluation-groups-and-time-boundaries"></a>

Choose which reviewed source groups belong in training, validation, calibration and final testing, or define their boundaries using operational timestamps. Preview the complete assignment and approve it before the independent worker creates an immutable data snapshot.

Use automatic reservations when you do not need explicit boundaries. Grouped data and time-correlated observations need an evaluation design that reflects the intended deployment. Keep the final test set independent of model and policy selection. [scikit-learn cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html), [data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)

<a id="reservations--prepare-the-inputs"></a>

### Prepare the inputs

1. Create a domain project, import or enter examples, and complete their authenticated [review](data-guide.md#data-contract).
2. Use `group_id` for the original entity or source family, such as one document, conversation, customer or device. Choose the grouping unit that matches your domain's independence requirements.
3. If using dates, supply all three [operational timestamps](reservation-guide.md#time-boundaries--declare-operational-timestamps) on every real record. A declared timestamp does not prove the source history is correct.
4. Complete any required [semantic review](reservation-guide.md#semantic-relatives). Known semantic relatives, identical states and seed descendants must stay together even when their source identifiers differ.

Explicit reservations support up to 20,000 eligible records from 1–20 selected immutable dataset versions. Rejected records stay excluded. The application refuses incomplete reviews, duplicate IDs and conflicting labels; it does not sample or silently discard records to fit a boundary.

<a id="reservations--assign-source-groups"></a>

### Assign source groups

In **Prepare and generate**, select your dataset versions and select **Choose holdout groups or date cutoffs yourself**. This optional workflow requires a workspace owner.

1. Select **Inspect reservation inputs**, then choose **Source groups**.
2. Assign every displayed real source group to `train`, `validation`, `calibration` or `test`. Edit the paged table and select **Save assignments for this page** before changing pages or previewing.
3. For larger assignments, download the CSV or JSON template, fill every role, upload it and select **Load assignment file**. Preserve identifiers exactly; extra, missing and duplicate assignments are refused.
4. Select **Preview explicit reservation**. Check the role counts, grouped families and any time constraints.
5. Select **I reviewed and approve these source groups, time boundaries and heldout roles**, then **Reserve train / validation / calibration / test**.

The worker saves the snapshot in the background. Select **Refresh workspace** to see the completed snapshot and inspect its role counts before generation or training.

The CSV format is:

```csv
group_id,role
device-a,train
device-b,validation
device-c,calibration
device-d,test
```

Templates contain your actual source identifiers. The CSV template is withheld when an identifier could be interpreted as a spreadsheet formula; use the JSON template or page editor instead. Assignment files are limited to 1 MiB. The JSON alternative is an object mapping each group identifier to its role.

Groups sharing the same `family_id` are linked and must receive the same role. Families containing synthetic examples or examples exposed during taxonomy discovery remain training-only. Every role needs at least one independent reviewed real family; this minimum does not establish adequate sample size or class coverage.

<a id="reservations--set-date-cutoffs"></a>

### Set date cutoffs

Choose **Date cutoffs** after inspecting fully timestamped inputs. Enter three strictly increasing ISO 8601 timestamps with an explicit timezone:

| Field | Meaning |
|---|---|
| Validation starts at | Earlier decisions are assigned to training |
| Calibration starts at | Decisions from the validation start up to this cutoff are assigned to validation |
| Test starts at | Decisions from the calibration start up to this cutoff are assigned to calibration; later decisions belong to test |

A decision exactly on a cutoff belongs to the later role. All decisions in a linked family must belong to one role. Earlier decisions and available labels must finish **strictly before** the next configured cutoff, including when the next observed decision occurs later than that cutoff. Adjust the study design or source selection if a family or delayed label crosses a boundary.

**Minimum gap before each later role (seconds)** adds an optional embargo of up to 31 days. The earlier role's latest decision or label availability, plus the gap, must precede the next boundary. For source-group assignments with complete timestamps, the boundary is the next role's first observed decision. A nonzero gap is refused for undated records.

The gap is measured in elapsed seconds, not a count of rows. This is a fixed four-role study protocol; it is not an implementation of rolling `TimeSeriesSplit`, whose `gap` parameter counts samples. [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

<a id="reservations--preserve-an-approved-reservation"></a>

### Preserve an approved reservation

The service rechecks the preview identity during submission and saves the approved plan and job atomically. A changed source version, semantic review, exposure inventory or assignment requires a new preview. Repeating the same request key and content reopens the same job rather than creating another plan.

The worker recomputes the assignment from its pinned sources before publishing a snapshot. Training, hosted studies, remote studies and recovery recheck the reservation through the shared snapshot contract. Changing the stored plan or removing its snapshot binding is refused.

Known project families retain their original roles in later preparations, including preparations that do not use an explicit plan. Matching includes record IDs, source groups, exact states and declared relatives. A new first-reservation request cannot move an old test family into training. If older studies already assigned the same family to conflicting roles, choose independent data in a new project; editing a historical snapshot is not a repair.

To add reviewed synthetic candidates later, select the existing snapshot under **Preserve holdouts from an existing snapshot when adding synthetic data**. Explicit reassignment controls are unavailable for this extension. The original real holdouts and training records remain unchanged; only reviewed synthetic training candidates can be added. See [synthetic generation](data-guide.md#data-contract--generate-and-review-synthetic-candidates).

Back up and stop the API, worker and browser before upgrading. Workspace schema **11** also preserves reversible project lifecycle state alongside explicit preparation plans and approved synthetic-generation history; older applications refuse the newer schema. Do not run mixed application versions. Recovery accepts supported schema-3 through schema-11 backups and upgrades older captures without rewriting their original data or hashes. See [backup and recovery](recovery-guide.md#local-backups).

<a id="reservations--use-the-api"></a>

### Use the API

Owners can inspect inputs with `POST /v1/projects/{project}/reservation-preview`:

```json
{"datasets": ["immutable-dataset-id"], "semantic_audit": null, "policy": null}
```

Preview a complete policy by supplying `policy`, using one of these shapes:

```json
{
  "mode": "time",
  "cutoffs": ["2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"],
  "embargo_seconds": 3600
}
```

```json
{
  "mode": "groups",
  "group_roles": {"device-a": "train", "device-b": "validation", "device-c": "calibration", "device-d": "test"},
  "embargo_seconds": 0
}
```

After reviewing the returned counts, submit a regular `prepare` job with `datasets`, optional `semantic_audit`, `split_strategy: "auto"`, your `reservation_policy`, `expected_reservation_sha256` copied from the preview's `sha256`, and `reservation_approved: true`. Use a stable request key. Explicit policies cannot be combined with `base_snapshot` or another automatic split strategy. The resulting snapshot's `reservation_plan` references its owner-approved immutable plan.

These checks preserve declared relationships and time consistency. They do not establish truthful timestamps, undiscovered entity relationships, sufficient domain coverage or model quality. Inspect [data readiness](data-guide.md#data-contract--inspect-readiness-before-training), [evaluation](evaluation-guide.md#evaluation) and [semantic review](reservation-guide.md#semantic-relatives) before release.

<a id="time-boundaries"></a>

## Chronological reservations

<a id="time-boundaries--reserve-data-using-information-available-at-decision-time"></a>

For changing customer traffic or delayed outcomes, reserve earlier decisions for training and later decisions for evaluation. Random splitting can give misleading estimates when observations are correlated over time. Information unavailable at prediction time is leakage, including future outcomes accidentally included in the input state. [scikit-learn cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html), [scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)

<a id="time-boundaries--declare-operational-timestamps"></a>

### Declare operational timestamps

Canonical JSONL accepts this optional object inside each record's `metadata`:

```json
{
  "origin": "real",
  "reviewed": true,
  "temporal": {
    "decision_at": "2026-01-01T09:00:00Z",
    "evidence_available_at": "2026-01-01T08:55:00Z",
    "label_available_at": "2026-01-02T10:00:00Z"
  }
}
```

- `decision_at` is when the operational decision was made.
- `evidence_available_at` is when the **latest information included anywhere in the state** became available. It must be at or before the decision. A historical invoice containing a later payment settlement needs an earlier state reconstruction, rather than a changed timestamp.
- `label_available_at` is when the target was known and usable for model selection or training. For fraud or payment outcomes, this may be substantially later than the original decision. This field is an operational declaration; it is separate from the authenticated annotation review time.

Supply all three fields together. Dates require ISO 8601 seconds and an explicit timezone (`Z` or an offset); comparisons normalize to UTC. A date without a timezone, an impossible date, a missing field or future input evidence is rejected. Excel cells should contain timestamp text with the timezone; the importer does not guess the timezone of a local Excel date.

The maintenance simple-table importer recognizes columns named after these three fields. In the application, enable **Map decision and availability timestamps** during CSV/TSV/XLSX mapping and select the three source columns. The API's mapping object accepts `temporal_columns` with all three field names mapped to existing column names. Canonical JSONL uploads retain the nested declaration. Upload review flags still reset and records still need authenticated label review. The manual example form does not yet expose timestamp entry; use an uploaded canonical record or mapped table for temporal studies.

Timestamps and provenance are supplied by the data owner. The application checks their consistency; it does not verify them against the original enterprise source system.

<a id="time-boundaries--reserve-chronological-roles"></a>

### Reserve chronological roles

The preparation API's `split_strategy` and the application's **Reservation strategy** support:

| Setting | Behavior |
|---|---|
| `auto` | Chronological if any real record declares timestamps; otherwise the existing grouped random reservation |
| `chronological` | Requires complete timestamps on every included real record |
| `group-random` | Permitted only when real records have no temporal declarations |

Partly dated real collections fail preparation. Explicit random splitting cannot bypass a dated collection's chronology checks. Undated collections remain supported, but the readiness report says temporal leakage has not been checked.

Grouping still joins source families, normalized identical states and known seed descendants. For chronological data, preparation additionally joins overlapping group intervals, from the earliest decision to the latest decision or label availability. Ties join the same interval. Joined intervals are ordered and divided into approximate 70/10/10/10 training, validation, calibration and test roles. Ratios are based on independent intervals, not row counts. No record is silently discarded to achieve a boundary.

Each role's decisions and available labels must end **strictly before** the next role's first decision. A long customer family or delayed label can join many intervals and leave fewer than four independent windows; preparation then fails. Collect longer history or reconstruct an explicitly reviewed study instead of splitting the family to make the check pass. Chronological reservations are independent of the random seed.

Synthetic families remain training-only. If a synthetic seed belongs to later operational traffic, forcing its real ancestor into training can violate chronology and preparation fails. Synthetic generation time is not an operational decision timestamp. Reviewed hypothetical extensions can omit temporal metadata and preserve the existing heldouts; their known seed lineage must remain within the original training set. If an extension declares an operational window, that window is checked too.

The maintenance CLI accepts `--split-strategy chronological`. Manually assigned four-file studies pass the same temporal checks when loaded by native Laya, Unsloth and remote qualification runners. A dated four-file study with random or overlapping role boundaries is refused.

<a id="time-boundaries--inspect-and-preserve-the-audit"></a>

### Inspect and preserve the audit

To choose your own source-group assignments, explicit validation/calibration/test start dates or an elapsed-time embargo, use the [reservation workflow](reservation-guide.md#reservations). Automatic interval-based reservations remain available.

New application snapshots contain a `declared-temporal-v1` audit: strategy, real timestamp coverage and each role's decision/label ranges. The preparation screen exposes this report. Dataset readiness reports coverage and incomplete-declaration blockers. Existing reservations remain immutable when adding approved synthetic candidates.

The worker rechecks the complete split contract before synthetic generation, hosted evaluation, training, remote-study creation and remote admission. Restore validation also rechecks reservations. A correctly hashed but temporally contaminated snapshot is refused before training artifacts or provider calls are created. Existing snapshots without an audit remain readable; the rows are checked when used. Old dated snapshots that violate this new contract must be reconstructed as a successor study.

This protocol is intentionally conservative: validation labels must be available before calibration traffic, and calibration labels before test traffic. It does not impose a universal business embargo or reconstruct a model's historical deployment. Feature windows, policy changes, entity independence, retention and real study adequacy still require domain review.

<a id="time-boundaries--limits-and-domain-validation"></a>

### Limits and domain validation

These checks cover declared time and existing exact/known-lineage relationships. They do not detect a paraphrase with a new family identifier. Semantic embedding similarity can find candidate paraphrases, but similarity scores require a pinned model, domain-specific thresholds and owner adjudication; they are not proof of duplication or independence. [Sentence Transformers semantic similarity](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html), [paraphrase mining](https://www.sbert.net/examples/sentence_transformer/applications/paraphrase-mining/README.html)

Combine timestamp checks with the [semantic workflow](reservation-guide.md#semantic-relatives) to review possible relatives and bind reservations. Verify declared history against operational source records; passing consistency checks does not prove timestamp truth or eliminate undocumented leakage.

<a id="semantic-relatives"></a>

## Semantic relatives and review

<a id="semantic-relatives--find-and-review-semantic-relatives-before-reservation"></a>

Different wording does not make two records independent. The platform supports a separate local embedding audit to propose related states, followed by authenticated adjudication. Its encoder does not train the decision head, generate examples or label business outcomes. It helps keep source relatives out of different evaluation roles.

Cosine similarity can propose paraphrases, but candidate selection depends on the model and threshold. Similarity alone does not establish shared source history. The project compares every pair within an explicitly bounded study; it refuses candidate overflow instead of returning a silently truncated top-k list. [Sentence Transformers paraphrase mining](https://sbert.net/examples/sentence_transformer/applications/paraphrase-mining/README.html)

<a id="semantic-relatives--operator-setup"></a>

### Operator setup

Use a model-capable worker environment with the optional `semantic` package extra or [semantic-models.txt](../requirements/semantic-models.txt). The ordinary application environment can run the API; the worker uses `DMT_SEMANTIC_PYTHON` for its separate CPU child. No GPU tensor is created.

The available runner supports standard BERT encoders with attention-mask mean pooling and L2 normalization. It loads a **local directory**, requires safetensors, disables Hub access/implicit tokens/telemetry and refuses remote-code configuration. Its child receives an allowlist of ordinary OS variables rather than provider credentials. It checks all six model/tokenizer file hashes before and after execution and checks exact installed model-library versions.

The worker gives each job a private Torch cache instead of inheriting cache locations or username variables. Include these directories in the workspace's retention policy. The setting follows the [pinned PyTorch cache implementation](https://github.com/pytorch/pytorch/blob/v2.10.0/torch/_inductor/runtime/cache_dir_utils.py).

Use [all-MiniLM-L6-v2 at revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) for the pinned profile. It produces 384-dimensional embeddings with masked mean pooling and normalization. The usual limit is 256 word pieces; this runner rejects inputs beyond the configured budget rather than truncating them. The card declares Apache-2.0; check dependency and backbone terms separately. [Pinned model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md)

Prepare an approved local snapshot using the model owner's distribution process. Inside the repository checkout, run the operator helper with the model environment:

```sh
python -m scripts.create_semantic_profile \
  --model-dir /absolute/approved/minilm-snapshot \
  --model sentence-transformers/all-MiniLM-L6-v2 \
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --tenant YOUR_TENANT --project YOUR_PROJECT_ID \
  --out data/config/semantic-profile.json
```

Replace the path and scope with your deployment's values. Repeating `--tenant`/`--project` adds explicitly approved scopes; project `*` applies to the selected tenants' projects. The helper hashes the local files and captures installed dependencies; it **does not authenticate a claimed Hub revision or download weights**. A directory path is an operator detail and is omitted from the public profile API. Keep that file outside Git.

Configure `DMT_SEMANTIC_PROFILE_FILE` in both API and worker. Configure `DMT_SEMANTIC_PYTHON` in the worker when its ordinary Python lacks the model libraries. These values are process environment settings, not automatically loaded from `.env` by Python. An unavailable/invalid configured profile fails visibly; an absent or unmatched scoped profile leaves the legacy unchecked-semantic path available with an explicit UI notice.

The helper defaults to a **required** audit, a 256-token budget, 5,000 records, 10,000 candidates and a 600-second child deadline. These are resource bounds, not adequacy recommendations. Operators can select supported bounded values. `--optional` permits legacy reservations for that scope; optional does not make them semantically qualified. The UI disables reservation when a required audit is absent or its candidate reviews are pending; the API and worker enforce the same requirement.

The Windows dependency profile uses `torch 2.10.0+cu126`, but the semantic audit runs on CPU. In a container, install the model-capable runtime and mount an approved model directory read-only before enabling the profile; the baseline image contains no model assets. Validate replacement checkpoints and CPU-wheel profiles separately. Monitor interrupted children and manage cache retention yourself; there is no fleet-wide capacity scheduler.

<a id="semantic-relatives--process-memory-and-interrupted-audits"></a>

### Process memory and interrupted audits

Managed audits require Windows or Linux. `--memory-limit-mb` sets the operator
profile's `memory_limit_mb` ceiling in MiB (256–65,536; default 16,384).
Existing profiles without the field use that default. Set an appropriate budget
for your installed encoder and runtime; the ceiling does not reserve RAM or
prove enough physical memory is available.

A new audit can inspect at most 20,000 source records across the selected
collections and base reservation, including rejected records. The API checks
stored row counts before reading the collections; the worker rechecks before
embedding. The configured `max_rows` separately bounds eligible records.
Oversized input is refused without sampling. Use smaller collections; changing
this new-job limit does not rewrite or invalidate historical audit records.

On Windows, the owned process job limits committed memory across the child and
its ordinary descendants. On Linux, an inherited `RLIMIT_AS` caps each child's
virtual address space, including mapped libraries. Existing stricter limits
are retained. These measure different resources, so the same number does not
imply equal RSS or usable tensor memory. Use container/host controls for an
aggregate service limit. [Windows job memory](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information), [Python resource limits](https://docs.python.org/3/library/resource.html#resource.RLIMIT_AS)

Limits are armed before model imports. Allocation failure, cancellation or a
deadline publishes no partial audit. Open `jobs/JOB_ID/semantic.log` within your
workspace storage, correct the input or resource profile and submit a new job.
Parent loss terminates the owned child;
the job becomes interrupted after its lease expires. Row, pair, token and time
limits still apply. A required semantic profile continues to block unchecked
reservation. Direct use of the internal runner is not a replacement for this
managed supervision path.


<a id="semantic-relatives--owner-and-reviewer-workflow"></a>

### Owner and reviewer workflow

1. Approve complete business labels on the selected immutable dataset versions. Explicitly rejected rows are excluded; pending records still block the study.
2. In **Prepare and generate**, select input dataset versions and, for a synthetic extension, the existing base snapshot.
3. Open **Find and review semantic relatives**, select a domain-reviewed cosine threshold and queue the local audit. The UI's illustrative default is not a recommended universal threshold.
4. Inspect the completed audit's record/comparison/candidate counts and paginated state pairs. Nothing is sent to a hosted provider.
5. An owner or reviewer records **related** or **independent** with a reason for every candidate. An editor can inspect and prepare reviewed data but cannot adjudicate pairs or change the threshold by creating an audit; audit creation requires an owner.
6. Select that exact reviewed audit and reserve the four roles. Preparation refuses an audit from different dataset hashes, a different base snapshot, a changed profile/source or a job that never completed successfully.

Related pairs join transitively with the existing source, exact-state and seed relationships before initial splitting. Records remain intact: neither their targets nor their states are overwritten, and independent judgments cannot override an already declared source family or exact duplicate. Different labels can still describe related counterfactuals that should stay in one role.

For a synthetic extension, the audit compares new candidates **and all original reserved states**. Existing heldouts retain their exact content. A new confirmed relationship crossing a reserved role causes refusal. Do not mark a relationship independent just to bypass this check; reject/correct the candidate or design a successor study with the domain owner.

Reviews are immutable revisions. Concurrent stale writes return a conflict and require refresh. A later review revision does not change an existing snapshot: preparation pins the exact review IDs and their content hashes. Initial report, row membership/content, profile, threshold, source, job and review bindings remain in the workspace audit history and backup.

<a id="semantic-relatives--application-and-portable-contracts"></a>

### Application and portable contracts

The worker revalidates semantic bindings before generation, local training, hosted evaluation, remote-study creation and remote admission. It reconstructs the exact audited source records and checks the reservation's annotations/content, not just record IDs. Model/profile/package changes require a new audit before future study use. This is not automatic revocation of already released inference.

Backup/restore validates the preserved report/review/source relationships without importing an embedding model or requiring the current operator configuration. A successfully restored historical study still needs current runtime/profile qualification before new work.

Reserved records carry compact `metadata.semantic_review` bindings and `semantic_links` for confirmed relatives. The shared four-file validator refuses links crossing roles or referring to missing records. These portable links preserve declared relationships during native Laya, Unsloth and remote runner input validation. Standalone maintenance JSONL remains operator-supplied data: links alone are not cryptographic proof of authenticated review. Platform provenance/input hashes bind remotely returned exports to the owner-reviewed platform reservation.

| Endpoint/action | Purpose |
|---|---|
| `GET /v1/projects/{project}/semantic-profile` | Public scoped model/file/version/resource profile; excludes local model paths and tenant inventory |
| Job kind `semantic-audit` | `datasets`, optional `base_snapshot`, and numeric `threshold` from 0.5 to 1; operator profile and package source freeze at submission |
| `GET /v1/projects/{project}/entities/semantic-audit` | Bounded audit summaries, without full row hashes or pair lists |
| `GET /v1/projects/{project}/semantic-audits/{audit}/pairs` | Paginated states and current review; `limit` 1–100, `offset` nonnegative |
| `POST /v1/projects/{project}/semantic-audits/{audit}/reviews` | `pair_id`, `decision`, `expected_revision`, `expected_hash` and `note` |
| Job kind `prepare` | Add `semantic_audit` to bind an exact fully reviewed audit |

The encoder evaluates the state text or canonical JSON serialization only; question instructions, gold labels and review notes do not enter the embedding. It does not fit a vectorizer on heldouts. Semantic adjudication can still reveal test inputs to reviewers, so control their access and preserve the distinction between independence review and model selection.

<a id="semantic-relatives--combined-positive-and-applicability-audits"></a>

### Combined positive and applicability audits

The applicability workflow uses a distinct owner-created `gate-semantic-audit` with `snapshot`, `datasets` and `threshold`. It freezes the exact positive reservation, ordered negative versions, reviewed included records, content hashes, profile, package source and threshold before the worker invokes the same bounded offline CPU encoder. Pending negative annotations block the audit; rejected records are excluded. Every included positive and negative state is compared together.

In Applicability, select the paired inputs and open **Combined Find and review semantic relatives**. Review all candidates using authenticated owner/reviewer accounts. Select the completed combined audit and reserve applicability roles. A required scoped profile refuses missing or pending audits. A positive-only audit, different collection version, changed profile/source or different positive snapshot cannot substitute for this audit. New review revisions cannot change a previously queued job's frozen review IDs and content hashes.

Positive role membership and stored snapshot content remain immutable. Confirmed new positive relationships appear only on portable derived copies. Cross-collection links live on negative copies so that the positive-only validator can still validate its four files. Negative relatives follow their positive anchors. Any transitive component joining two frozen positive roles refuses reservation. Known families, exact states, timestamps and positive/negative annotation contradictions remain independently enforced.

Vega training and admission consume those derived positive copies alongside the negative files. Calibration-family accounting includes the newly reviewed positive links and negative bridges; apparent sample counts cannot override the frozen negative test floor or the sixty positive calibration-family preflight. Eight file hashes bind the actual materialized inputs, while original snapshot hashes remain in provenance. Do not independently export the original positive snapshot and splice it into a combined study.

Cold backup/restore replays original audited records, immutable reviews and derived reservations without loading the encoder or requiring an active operator profile. New training/export work still requires current source/profile bindings. Links in standalone files retain relationships but do not independently attest authenticated platform review.

<a id="semantic-relatives--limits-of-the-result"></a>

### Limits of the result

An exhaustive comparison covers all **included** pairs, but cosine similarity can still miss relatives or rank unrelated states highly. Evaluate negation, paraphrases, languages, structured fields, long inputs and templated records using domain examples. Choose the threshold with reviewers; do not treat the default as a universal rule.

Candidate/token/row/deadline overflow refuses the study. Larger corpora and longer states need a qualified alternative rather than silent sampling or truncation. CPU batching/chunking bounds temporary pair memory, but enterprise throughput and multi-host scheduling have not been established.

Combine semantic review with the [temporal protocol](reservation-guide.md#time-boundaries) and [data workflow](data-guide.md#data-contract). Evaluate model quality separately using independently reviewed operational gold.
