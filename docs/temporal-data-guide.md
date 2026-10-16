# Reserve data using information available at decision time

[All guides](README.md) · [Project overview](../README.md)

For changing customer traffic or delayed outcomes, reserve earlier decisions for training and later decisions for evaluation. Random splitting can give misleading estimates when observations are correlated over time. Information unavailable at prediction time is leakage, including future outcomes accidentally included in the input state. [scikit-learn cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html), [scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)

## Declare operational timestamps

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

## Reserve chronological roles

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

## Inspect and preserve the audit

To choose your own source-group assignments, explicit validation/calibration/test start dates or an elapsed-time embargo, use the [reservation workflow](reservation-guide.md). Automatic interval-based reservations remain available.

New application snapshots contain a `declared-temporal-v1` audit: strategy, real timestamp coverage and each role's decision/label ranges. The preparation screen exposes this report. Dataset readiness reports coverage and incomplete-declaration blockers. Existing reservations remain immutable when adding approved synthetic candidates.

The worker rechecks the complete split contract before synthetic generation, hosted evaluation, training, remote-study creation and remote admission. Restore validation also rechecks reservations. A correctly hashed but temporally contaminated snapshot is refused before training artifacts or provider calls are created. Existing snapshots without an audit remain readable; the rows are checked when used. Old dated snapshots that violate this new contract must be reconstructed as a successor study.

This protocol is intentionally conservative: validation labels must be available before calibration traffic, and calibration labels before test traffic. It does not impose a universal business embargo or reconstruct a model's historical deployment. Feature windows, policy changes, entity independence, retention and real study adequacy still require domain review.

## Limits and domain validation

These checks cover declared time and existing exact/known-lineage relationships. They do not detect a paraphrase with a new family identifier. Semantic embedding similarity can find candidate paraphrases, but similarity scores require a pinned model, domain-specific thresholds and owner adjudication; they are not proof of duplication or independence. [Sentence Transformers semantic similarity](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html), [paraphrase mining](https://www.sbert.net/examples/sentence_transformer/applications/paraphrase-mining/README.html)

Combine timestamp checks with the [semantic workflow](semantic-data-guide.md) to review possible relatives and bind reservations. Verify declared history against operational source records; passing consistency checks does not prove timestamp truth or eliminate undocumented leakage.
