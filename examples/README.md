# Examples and configuration templates

[Project overview](../README.md) · [All guides](../docs/README.md)

Use these fictional records to understand the data format and adapt the templates to your own deployment. They do not establish model quality or provide ready-to-use cloud credentials.

| Example | Use it with |
|---|---|
| [Support-routing data and request](support/README.md) | [Data preparation](../docs/data-guide.md) and the [domain walkthrough](../docs/domain-adoption-guide.md) |
| [Liquid operation-selection specification and CSV](liquid/README.md) | [Native d1-3B data preparation](../docs/models/liquid.md); fictional pending examples, not evaluation gold |
| [D3 retrieval-evidence specification and CSV](d3/README.md) | [Native D3 Edge training](../docs/models/d3.md); pending synthetic illustrations for answer/search/review decisions |
| [Released-model Python client](platform/README.md) | [Local or deployed API integration](../docs/api-integration-guide.md); release preconditions, user-owned inference keys and fallback handling |
| [Hosted decision profiles](hosted-profiles.json) | [Hosted studies](../docs/hosted-decisions-guide.md), including [Liquid d1/d1-ultra](../docs/models/liquid-hosted.md) |
| [Perplexity request and Python client](perplexity/README.md) | [Full/Flash key setup and evaluation](../docs/models/perplexity.md); a bounded direct call with your own key |
| [Category-discovery profiles](taxonomy-profiles.json) | [Reviewed taxonomy discovery](../docs/taxonomy-guide.md) |
| [Five cloud training profiles](cloud/README.md) | The corresponding [remote training workflow](../docs/remote-training/README.md) |

For your own domain, describe the policy and data needs in the application, define its questions, then enter examples or upload CSV/XLSX/TSV/JSON/JSONL. Use [column mapping and review](../docs/data-guide.md#supply-examples-or-upload-real-data) before reserving a study. Imported approval flags are not trusted.

To add synthetic coverage, configure a teacher and follow [generate and review synthetic candidates](../docs/data-guide.md#generate-and-review-synthetic-candidates). Keep real evaluation data independent and review generated labels before adding them to training.

Replace fictional account, tenant and resource values using the linked guide. Keep secrets and actual domain records in your private configuration and workspace; avoid editing public fixtures to contain enterprise data.
