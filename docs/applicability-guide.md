# Review and reserve applicability evidence

[All guides](README.md) · [Project overview](../README.md)

Applicability examples say that a specific domain question should not be applied to the supplied evidence. They are separate from labelled business examples. A weather bulletin can be inapplicable to an invoice category question; assigning it an invented invoice label or uniform target would change the training objective.

The application's **Applicability** tab supports intake, authenticated review, frozen reservations and an eight-file export for the [Vega trainer](vega-guide.md). The local worker and operator admission paths consume those paired inputs; owner acceptance controls release and resident serving. Laya, Unsloth and the CPU baseline use their separate positive-data contracts.

## Collect and review source evidence

1. Create the domain project and confirm its question IDs. Reserve the reviewed positive decision examples using the existing data workflow.
2. Open Applicability. Enter observed source evidence, its original family, the question IDs that should not apply, and a reason. Editors/owners can also upload CSV, TSV, XLSX, JSON or JSONL, into a new or existing collection.
3. For tables, map the ID, family, evidence, question IDs and reason columns; choose the actual XLSX worksheet. The question column is a JSON array such as `["category"]`. Optional JSON metadata preserves declared time/source relationships. The project-specific CSV template is an illustration and needs replacement with real records.
4. An owner/reviewer checks each record, corrects the inapplicable question IDs/reason, adds a review note, and explicitly approves, rejects or returns it to pending. Every incoming approval/reviewer field is reset by the server. Uploaded flags cannot approve records.

Input shape for JSON/JSONL:

```json
{
  "id": "original-source-id",
  "group_id": "original-document-or-customer-family",
  "state": "Replace with observed evidence outside the domain question",
  "negative_for": ["category"],
  "reason": "Explain why the supplied facts do not support this question",
  "metadata": {"origin": "real"}
}
```

This is a format illustration, not operational data. The current Vega profile accepts real applicability records only. The origin claim and reviewer approval do not establish source truth or model quality. Preserve access rights, source provenance and actual operational timestamps. Declared relationships belong in `metadata.semantic_links` and `metadata.seed_ids`; complete timestamps use `metadata.temporal` from the [temporal guide](temporal-data-guide.md).

## Reserve alongside positive holdouts

Select the already frozen positive snapshot and one reviewed applicability revision per collection. Submit **Reserve applicability train / validation / calibration / test** and inspect its durable job in Training jobs. Pending records stop reservation; rejected records are excluded with their reviewer/note/hash provenance.

The worker keeps original families, identical states, declared semantic relationships and common seed ancestry together. A negative related to positive training data remains in training. A relationship connecting different frozen positive roles refuses the study; positive holdouts are never moved to accommodate negatives. Identical evidence cannot be both a positive labelled example and a negative for that question.

Undated free families use deterministic grouped reservations. With fully declared timestamps, negatives follow the positive role time boundaries; future labels or missing timestamps refuse the study. Every question needs negative evidence in each role. These checks cover declared relationships and times, not undisclosed semantic leakage or truthful timestamps.

For a configured semantic profile, use **Combined Find and review semantic relatives** in Applicability. Queue an audit over the positive snapshot and selected negative revisions, review every candidate, select the completed audit, then reserve. A required profile blocks unchecked reservation. Positive-only audits do not cover negatives, and relationships bridging frozen roles refuse the study. See the [combined semantic workflow](semantic-data-guide.md#combined-positive-and-applicability-audits).

Choose **Prepare eight-file Vega study download**, then download the ZIP. It contains `train.jsonl`, `validation.jsonl`, `calibration.jsonl`, `test.jsonl`, their four `gate_` counterparts, and a manifest with file/row/byte hashes plus both snapshot identities. Download is authenticated and rechecks access before returning the archive. The uncompressed study limit is 64 MiB. This portable input export is distinct from a provider-approved remote training study.

The trainer additionally requires at least sixty positive calibration families for disjoint probability/conformal fitting. With the default 70/10/10/10 grouped reservation, collecting around six hundred independent positive families is a starting count constraint, not a sample adequacy recommendation. Check actual role/family/class counts before training. Independent operational gold and domain-owner acceptance remain necessary.

## Revision and access contracts

Collections use immutable, content-addressed rows and compact append/review revisions. Old versions retain their previous content. Append and review require the current version/hash; stale writes return a conflict. Review identity comes from the authenticated principal. Applicability collections are tenant/project scoped and cannot be supplied to positive-data endpoints. Inference credentials cannot read these records or archives. Local backup/restore verifies both kinds of collection and reproduces the reserved negatives from their reviewed sources.

API routes:

- `POST /v1/projects/{project}/gate-examples`: create 1–50 manual records.
- `POST /v1/projects/{project}/gate-datasets`: bounded file upload, optional explicit mapping and append version/hash.
- `POST /v1/projects/{project}/gate-datasets/{dataset}/examples`: append manual records with `expected_hash`.
- `GET /v1/projects/{project}/gate-datasets/{dataset}/rows`: paged records and current head.
- `POST /v1/projects/{project}/gate-datasets/{dataset}/reviews`: `record_id`, `negative_for`, `reason`, `status`, `expected_hash` and review `note`.
- `POST /v1/projects/{project}/jobs`: `kind="gate-prepare"`, configuration containing positive `snapshot` and applicability `datasets`, plus an idempotency key.
- Job kind `gate-semantic-audit`: the same `snapshot` and `datasets`, with a numeric cosine `threshold`; owner only. Add the completed `semantic_audit` to `gate-prepare` to freeze its exact pair reviews.
- `GET /v1/projects/{project}/gate-snapshots/{snapshot}/download`: verified eight-file input ZIP.
