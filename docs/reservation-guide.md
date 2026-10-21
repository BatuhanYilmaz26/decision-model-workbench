# Choose evaluation groups and time boundaries

[All guides](README.md) · [Project overview](../README.md)

Choose which reviewed source groups belong in training, validation, calibration and final testing, or define their boundaries using operational timestamps. Preview the complete assignment and approve it before the independent worker creates an immutable data snapshot.

Use automatic reservations when you do not need explicit boundaries. Grouped data and time-correlated observations need an evaluation design that reflects the intended deployment. Keep the final test set independent of model and policy selection. [scikit-learn cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html), [data-leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)

## Prepare the inputs

1. Create a domain project, import or enter examples, and complete their authenticated [review](data-guide.md).
2. Use `group_id` for the original entity or source family, such as one document, conversation, customer or device. Choose the grouping unit that matches your domain's independence requirements.
3. If using dates, supply all three [operational timestamps](temporal-data-guide.md#declare-operational-timestamps) on every real record. A declared timestamp does not prove the source history is correct.
4. Complete any required [semantic review](semantic-data-guide.md). Known semantic relatives, identical states and seed descendants must stay together even when their source identifiers differ.

Explicit reservations support up to 20,000 eligible records from 1–20 selected immutable dataset versions. Rejected records stay excluded. The application refuses incomplete reviews, duplicate IDs and conflicting labels; it does not sample or silently discard records to fit a boundary.

## Assign source groups

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

## Set date cutoffs

Choose **Date cutoffs** after inspecting fully timestamped inputs. Enter three strictly increasing ISO 8601 timestamps with an explicit timezone:

| Field | Meaning |
|---|---|
| Validation starts at | Earlier decisions are assigned to training |
| Calibration starts at | Decisions from the validation start up to this cutoff are assigned to validation |
| Test starts at | Decisions from the calibration start up to this cutoff are assigned to calibration; later decisions belong to test |

A decision exactly on a cutoff belongs to the later role. All decisions in a linked family must belong to one role. Earlier decisions and available labels must finish **strictly before** the next configured cutoff, including when the next observed decision occurs later than that cutoff. Adjust the study design or source selection if a family or delayed label crosses a boundary.

**Minimum gap before each later role (seconds)** adds an optional embargo of up to 31 days. The earlier role's latest decision or label availability, plus the gap, must precede the next boundary. For source-group assignments with complete timestamps, the boundary is the next role's first observed decision. A nonzero gap is refused for undated records.

The gap is measured in elapsed seconds, not a count of rows. This is a fixed four-role study protocol; it is not an implementation of rolling `TimeSeriesSplit`, whose `gap` parameter counts samples. [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)

## Preserve an approved reservation

The service rechecks the preview identity during submission and saves the approved plan and job atomically. A changed source version, semantic review, exposure inventory or assignment requires a new preview. Repeating the same request key and content reopens the same job rather than creating another plan.

The worker recomputes the assignment from its pinned sources before publishing a snapshot. Training, hosted studies, remote studies and recovery recheck the reservation through the shared snapshot contract. Changing the stored plan or removing its snapshot binding is refused.

Known project families retain their original roles in later preparations, including preparations that do not use an explicit plan. Matching includes record IDs, source groups, exact states and declared relatives. A new first-reservation request cannot move an old test family into training. If older studies already assigned the same family to conflicting roles, choose independent data in a new project; editing a historical snapshot is not a repair.

To add reviewed synthetic candidates later, select the existing snapshot under **Preserve holdouts from an existing snapshot when adding synthetic data**. Explicit reassignment controls are unavailable for this extension. The original real holdouts and training records remain unchanged; only reviewed synthetic training candidates can be added. See [synthetic generation](data-guide.md#generate-and-review-synthetic-candidates).

Back up and stop the API, worker and browser before upgrading. Workspace schema **10** also preserves reversible project lifecycle state alongside explicit preparation plans and approved synthetic-generation history; older applications refuse the newer schema. Do not run mixed application versions. Recovery accepts supported schema-3 through schema-10 backups and upgrades older captures without rewriting their original data or hashes. See [backup and recovery](recovery-guide.md).

## Use the API

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

These checks preserve declared relationships and time consistency. They do not establish truthful timestamps, undiscovered entity relationships, sufficient domain coverage or model quality. Inspect [data readiness](data-guide.md#inspect-readiness-before-training), [evaluation](evaluation-guide.md) and [semantic review](semantic-data-guide.md) before release.
