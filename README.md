# Decision Model Platform

A local-first application and practical guide for building decision models for a specific domain. Define allowed outcomes, review your data, reserve independent evaluation sets, train and calibrate a model, then serve decisions through an authenticated API.

The guide also covers integrating hosted decision APIs into existing applications and training on remote GPUs when local VRAM is insufficient. Model comparisons include versioned benchmarks, source citations and cost assumptions.

## Choose your path

| Goal | Start here |
|---|---|
| Understand Jev, Laya, Vega and other decision models | [Concepts](docs/concepts.md), [model landscape](docs/model-landscape.md), [benchmarks](docs/benchmarks.md) |
| Add a hosted decision API to an existing application | [API integration](docs/api-integration-guide.md), [application blueprints](docs/application-blueprints.md) |
| Build and train your own domain model locally | [Setup](docs/setup.md), [workflow](docs/product-workflow.md), [data](docs/data-guide.md), [training](docs/training-guide.md) |
| Train without a local GPU | [Remote training](docs/remote-training-guide.md): AWS, Azure, GCP, Modal and RunPod |
| Follow a complete support-routing example | [Domain adoption guide](docs/domain-adoption-guide.md) |

## What the application supports

Persistent domain projects, reviewer assignment queues, immutable reviewed datasets, queued jobs, training-seed-only synthetic candidates, native Laya training, a CPU comparison baseline, tenant-scoped credentials and frozen release criteria. The [capability guide](docs/capabilities.md) explains these features and their practical boundaries.

Local training supports Laya, Unsloth and Vega through separate model environments. Hosted adapters and managed cloud controllers are experimental; validate provider behavior, model outputs and billing in your account before use. Review synthetic candidates and evaluate model quality on independent domain records. The application uses single-host SQLite and does not provide distributed enterprise storage or interactive enterprise SSO.

Follow [testing](docs/verification.md) and [runtime compatibility and upgrades](docs/compatibility-and-upgrades.md) to check your installation, validate provider access and plan a production rollout. The pinned GPU profiles have known dependency advisories and need remediation and runtime requalification before production use.

## Guide index

| Area | Guides |
|---|---|
| Data | [Collection and review](docs/data-guide.md), [review assignments](docs/review-workflow.md), [policy versions](docs/policy-versioning-guide.md), [temporal boundaries](docs/temporal-data-guide.md), [semantic relatives](docs/semantic-data-guide.md), [taxonomy](docs/taxonomy-guide.md), [applicability](docs/applicability-guide.md) |
| Models | [Laya and Unsloth](docs/training-guide.md), [Vega](docs/vega-guide.md), [evaluation and calibration](docs/evaluation-guide.md), [error costs and critical outcomes](docs/error-policy-guide.md) |
| APIs and remote runs | [Hosted studies](docs/hosted-decisions-guide.md), [provider selection](docs/remote-training/provider-selection.md), [remote artifact admission](docs/remote-admission-guide.md) |
| Managed training | [AWS](docs/managed-cloud-training.md), [Azure](docs/managed-azure-training.md), [GCP](docs/managed-gcp-training.md), [Modal](docs/managed-modal-training.md), [RunPod](docs/managed-runpod-training.md) |
| Operations | [Deployment](docs/deployment-guide.md), [CPU containers](docs/container-guide.md), [model containers](docs/model-container-guide.md), [Windows process ownership](docs/windows-process-guide.md), [authentication](docs/authentication-guide.md), [audit exports](docs/audit-export-guide.md), [backup and recovery](docs/recovery-guide.md), [encrypted recovery](docs/encrypted-recovery-guide.md), [scheduled backups](docs/scheduled-backups-guide.md), [backup monitoring](docs/backup-monitoring-guide.md) |
| Research | [Primary sources](research/sources.md), [10 October comparison](research/decision-model-expansion-2026-10-10.md), [12 October refresh](research/decision-model-refresh-2026-10-12.md) |

Model and benchmark references are dated **9–12 October 2026**; the [runtime and cloud API comparison](docs/compatibility-and-upgrades.md) is dated **13 October 2026**. Prices, aliases, APIs and rankings can change. Benchmark figures are attributed to their publishers; use the pinned versions and methods when comparing results.

## Repository layout

| Location | Purpose |
|---|---|
| [app.py](app.py) | Streamlit interface for projects, review, jobs and releases |
| [decision_workbench/](decision_workbench/) | Data contracts, API, persistence, workers and model adapters |
| [docs/](docs/) | Reader and operator guides |
| [research/](research/) | Cited research, dated source metadata and benchmark extracts |
| [examples/](examples/) | Fictional data and configuration templates |
| [requirements/](requirements/) | Separate dependency profiles for platform and model environments |
| [docker/models/](docker/models/) | Vega/semantic runtime image |
| [scripts/](scripts/) | Training helpers, research capture and repository checks |
| [tests/](tests/) | Behavioral tests and provider contract fixtures |

Read [the architecture](docs/architecture.md) for the component flow and [contributing](CONTRIBUTING.md) for development and documentation standards.

## License

Original project code and documentation use [Apache-2.0](LICENSE). Models, libraries, provider schemas and cited content retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). Private data, credentials, weights, generated artifacts and local planning records are excluded from Git.
