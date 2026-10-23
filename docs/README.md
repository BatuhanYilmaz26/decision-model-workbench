# Documentation

[Project overview and installation](../README.md#run-on-your-computer)

Choose one task. Each walkthrough includes the common steps from prerequisites to a usable result. Consult a model or provider reference only when configuring that integration.

| Your goal | Start here |
|---|---|
| Use an existing model | [Make an API call and integrate it](api-integration-guide.md) |
| Train on your domain data | [Create, train and use a domain model](product-workflow.md) |
| Generate training examples | [Generate, review and export candidates](synthetic-generation-guide.md) |

For help with a specific app control, use the [screen manual](app-guide.md). For a shared deployment, start with [application hosting](application-hosting/README.md).

<details>
<summary>Reference library</summary>

Related topics share one authoritative page. Choose its section from the page's opening menu; you do not need to read every section.

## Data, policy and evaluation

| Reference | Includes |
|---|---|
| [Domain schema](domain-schema-guide.md) | Teacher-drafted questions, category discovery and owner confirmation |
| [Data import and review](data-guide.md) | Canonical formats, file mapping, labels and reviewer assignments |
| [Evaluation reservations](reservation-guide.md) | Source groups, chronological boundaries and optional semantic review |
| [Evaluation and release criteria](evaluation-guide.md) | Metrics, calibration, error costs and critical outcomes |
| [Project lifecycle](project-lifecycle-guide.md) | Archive/restore, policy successors and permanent database deletion |
| [Worked domain example](domain-adoption-guide.md) | Adapt the general workflow to support routing |

## Models and application integration

| Reference | Includes |
|---|---|
| [Model directory](models/README.md) | Supported backends, native formats, hardware limits and model-specific guides |
| [Training reference](training-guide.md) | Compatible adapters, native training protocols and model environments |
| [Vega](vega-guide.md) | Engine/domain adaptation, applicability data, evaluation and serving |
| [Hosted policies](hosted-decisions-guide.md) | Operator profiles, user keys, evaluation, calibration, accounting and release |
| [Application blueprints](application-blueprints.md) | Add bounded decisions to existing or new applications |
| [Remote training](remote-training-guide.md) | Provider selection, frozen studies, submission and returned-model admission |
| [Provider directory](remote-training/README.md) | AWS, Azure, GCP, Modal and RunPod; direct and app-managed instructions on each provider's page |

## Deploy and operate

| Reference | Includes |
|---|---|
| [Advanced setup](setup.md) | Optional runtimes, separate services, resource coordination and external tools |
| [Credentials and organization sign-in](authentication-guide.md) | Workspace keys, enterprise tokens, OIDC and revocation |
| [Shared workspace storage](shared-artifacts-guide.md) | PostgreSQL metadata, S3, Azure Blob and GCS configuration |
| [Shared operations](shared-workspace-operations-guide.md) | Queue limits, readiness, failure monitoring and capacity boundaries |
| [Backup and recovery](recovery-guide.md) | Personal/shared capture and restore, encryption, scheduling and monitoring |
| [Storage maintenance](storage-maintenance-guide.md) | Local caches, abandoned transfers and exact remote object-version cleanup |
| [CPU containers](container-guide.md) | Browser, API and worker with persistent volumes |
| [Native model runtimes](model-container-guide.md) | Linux model images and Windows process ownership |
| [Testing and compatibility](verification.md) | Installation checks and model/provider/account qualification boundaries |
| [Compatibility and upgrades](compatibility-and-upgrades.md) | Dependency profiles, interface changes and advisories |
| [Architecture and capabilities](architecture.md) | Runtime boundaries and supported workflows |

## Concepts, research and development

[Decision-model concepts and landscape](concepts.md) · [Benchmark methodology](benchmarks.md) · [Research and primary sources](../research/README.md) · [Examples](../examples/README.md) · [Dependency profiles](../requirements/README.md) · [Helper commands](../scripts/README.md) · [Tests](../tests/README.md) · [Contributing](../CONTRIBUTING.md).

</details>

<details>
<summary>Looking for an older guide filename?</summary>

Related guides have been merged into the reference pages above. The [page-move map](page-moves.json) records former filenames and their current sections, including heading mappings. GitHub does not automatically redirect a deleted file URL; use the current link or inspect the file's Git history for its older version.

</details>
