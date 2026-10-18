# Guided browser components

[Package directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

These components support the [Streamlit application](../../app.py). Its [onboarding](onboarding.py) opens a personal loopback workspace or connects to an authenticated shared API. Launch it using [local setup](../../docs/setup.md); these files do not run as separate applications.

Use the [complete application manual](../../docs/app-guide.md) for every screen and optional workflow. [Contextual guidance](guidance.py) links controls and tabs to the matching manual section without changing their data or permissions.

| User task | Guide |
|---|---|
| Describe a domain, define questions and upload or enter examples | [Data preparation](../../docs/data-guide.md) |
| Request and review teacher-suggested questions | [Schema assistant](../../docs/schema-assistant-guide.md) |
| Review assigned annotation work | [Review workflow](../../docs/review-workflow.md) |
| Add reviewed synthetic candidates | [Synthetic generation](../../docs/synthetic-generation-guide.md) |
| Set quality limits and asymmetric error costs | [Error policy](../../docs/error-policy-guide.md) |
| Inspect semantic relatives or proposed categories | [Semantic review](../../docs/semantic-data-guide.md), [taxonomy](../../docs/taxonomy-guide.md) |
| Choose evaluation groups or time boundaries | [Explicit reservations](../../docs/reservation-guide.md) |
| Change a domain policy | [Policy successors](../../docs/policy-versioning-guide.md) |
| Prepare Vega applicability data | [Vega workflow](../../docs/vega-guide.md), [applicability data](../../docs/applicability-guide.md) |
| Download a verified project audit | [Audit exports](../../docs/audit-export-guide.md) |
| Submit text or structured evidence to a release | [Prediction input](prediction_input.py), [decision walkthrough](../../docs/app-guide.md#make-decisions-and-export-audit-history) |

The API enforces the domain and authorization contracts. Use [architecture](../../docs/architecture.md) and [testing](../../docs/verification.md) when changing browser behavior.
