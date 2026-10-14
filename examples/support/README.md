# Support-routing example

[Example directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

This example defines three bounded decisions: the support team, whether a refund is requested, and urgency. Use it to understand the expected input and labels before creating your own domain schema.

| File | Purpose | Next step |
|---|---|---|
| [spec.json](spec.json) | Domain description, question IDs and allowed outcomes | [Define the decision schema](../../docs/data-guide.md#define-the-decision-schema) |
| [seeds.csv](seeds.csv) | Tabular examples for upload and column mapping | [Import and review examples](../../docs/data-guide.md#supply-examples-or-upload-real-data) |
| [seeds.jsonl](seeds.jsonl) | Canonical records with state, questions, gold probabilities and metadata | [Data contract](../../docs/data-guide.md) |
| [request.json](request.json) | State and typed questions for a prediction request | [API integration](../../docs/api-integration-guide.md) or [local checkpoint prediction](../../docs/training-guide.md) |

Follow the [support-routing walkthrough](../../docs/domain-adoption-guide.md) for the full sequence. The records are fictional format examples; collect independently reviewed operational records for training and domain evaluation, and represent their actual origin accurately. The seed files are not a prequalified release dataset.

If you need more coverage, reserve the independent evaluation sets first, then use the [synthetic generation workflow](../../docs/data-guide.md#generate-and-review-synthetic-candidates) to produce and review training-only candidates.
