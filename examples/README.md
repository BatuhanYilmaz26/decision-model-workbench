# Examples and configuration templates

[Project overview](../README.md) · [All guides](../docs/README.md)

Use these fictional records to understand the data format and adapt the templates to your own deployment. They do not establish model quality or provide ready-to-use cloud credentials.

| Example | Use it with |
|---|---|
| [Support-routing data and request](support/README.md) | [Data preparation](../docs/data-guide.md) and the [domain walkthrough](../docs/domain-adoption-guide.md) |
| [Hosted decision profiles](hosted-profiles.json) | [Hosted decision studies](../docs/hosted-decisions-guide.md) |
| [Category-discovery profiles](taxonomy-profiles.json) | [Reviewed taxonomy discovery](../docs/taxonomy-guide.md) |
| [Five cloud training profiles](cloud/README.md) | The corresponding [remote training workflow](../docs/remote-training/README.md) |

For your own domain, describe the policy and data needs in the application, define its questions, then enter examples or upload CSV/XLSX/TSV/JSON/JSONL. Use [column mapping and review](../docs/data-guide.md#supply-examples-or-upload-real-data) before reserving a study. Imported approval flags are not trusted.

To add synthetic coverage, configure a teacher and follow [generate and review synthetic candidates](../docs/data-guide.md#generate-and-review-synthetic-candidates). Keep real evaluation data independent and review generated labels before adding them to training.

Replace fictional account, tenant and resource values using the linked guide. Keep secrets and actual domain records in your private configuration and workspace; avoid editing public fixtures to contain enterprise data.
