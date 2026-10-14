# Guided browser components

[Package directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

These components support the [Streamlit application](../../app.py), which connects to the authenticated API. Launch it using [local setup](../../docs/setup.md); these files do not run as separate applications.

| User task | Guide |
|---|---|
| Describe a domain, define questions and upload or enter examples | [Data preparation](../../docs/data-guide.md) |
| Review assigned annotation work | [Review workflow](../../docs/review-workflow.md) |
| Add reviewed synthetic candidates | [Synthetic generation](../../docs/data-guide.md#generate-and-review-synthetic-candidates) |
| Set quality limits and asymmetric error costs | [Error policy](../../docs/error-policy-guide.md) |
| Inspect semantic relatives or proposed categories | [Semantic review](../../docs/semantic-data-guide.md), [taxonomy](../../docs/taxonomy-guide.md) |
| Change a domain policy | [Policy successors](../../docs/policy-versioning-guide.md) |
| Prepare Vega applicability data | [Vega workflow](../../docs/vega-guide.md), [applicability data](../../docs/applicability-guide.md) |
| Download a verified project audit | [Audit exports](../../docs/audit-export-guide.md) |

The API enforces the domain and authorization contracts. Use [architecture](../../docs/architecture.md) and [testing](../../docs/verification.md) when changing browser behavior.
