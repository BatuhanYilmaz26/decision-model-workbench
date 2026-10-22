# Platform services

[Package directory](../README.md) · [All guides](../../docs/README.md) · [Project overview](../../README.md)

These modules provide the persistent local application and authenticated decision endpoint. Use the [setup guide](../../docs/setup.md) to launch the services; individual source modules are not standalone browser applications.

| Entry point | Responsibility | Guide |
|---|---|---|
| [cli.py](cli.py) | Service launch and operator commands | [Local setup](../../docs/setup.md) |
| [local.py](local.py) | Owned personal API/worker startup, authentication and shutdown | [Personal workspace](../../docs/setup.md#personal-and-shared-workspaces) |
| [capabilities.py](capabilities.py) | Bounded hardware/package inspection in configured model interpreters | [Model setup](../../docs/setup.md#configure-model-training-and-serving) |
| [api.py](api.py) | Authenticated project, data, job, release and decision routes | [Workflow](../../docs/product-workflow.md), [authentication](../../docs/authentication-guide.md) |
| [imports.py](imports.py) | Shared whole-file validation and content-bound import/append previews | [Import workflow](../../docs/import-guide.md) |
| [worker.py](worker.py) | Process queued preparation, synthetic and training jobs | [Data workflow](../../docs/data-guide.md), [training](../../docs/training-guide.md) |
| [liquid_training.py](liquid_training.py), [model_environment.py](model_environment.py) | Bind Liquid jobs and releases to frozen inputs and a separate model environment | [Liquid application workflow](../../docs/models/liquid.md#train-and-release-through-the-localhost-application) |
| [generation.py](generation.py) | Inspect approved synthetic requests and reconcile unknown outcomes | [Synthetic generation](../../docs/synthetic-generation-guide.md) |
| [store.py](store.py) | SQLite or PostgreSQL state and immutable history | [Architecture](../../docs/architecture.md), [recovery](../../docs/recovery-guide.md) |
| [storage_health.py](storage_health.py) | Bounded metadata/binding and shared-manifest connectivity checks | [Shared operations](../../docs/shared-workspace-operations-guide.md) |

For other capabilities, follow [hosted decisions](../../docs/hosted-decisions-guide.md), [managed cloud training](../../docs/remote-training/README.md), [reviewer assignments](../../docs/review-workflow.md), [policy successors](../../docs/policy-versioning-guide.md) and [audit exports](../../docs/audit-export-guide.md).

Use [encrypted recovery](../../docs/encrypted-recovery-guide.md), [scheduled backups](../../docs/scheduled-backups-guide.md) and [backup monitoring](../../docs/backup-monitoring-guide.md) for local operator workflows. For configured PostgreSQL/S3 deployments, use [shared recovery](../../docs/shared-recovery-guide.md). The [capability guide](../../docs/capabilities.md) explains deployment limits.
