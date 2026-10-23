# Operate a shared workspace within explicit limits

Use this guide with [PostgreSQL metadata](postgresql-guide.md), [shared model artifacts](shared-artifacts-guide.md) and your organization's [authentication configuration](authentication-guide.md). The same domain, review, synthetic-data, training, release and inference workflows remain available. API and worker instances can have separate local caches while using the same database/schema, artifact repository and identity configuration.

## Queue limits and concurrent work

The application permits **four pending jobs per tenant**, counted across that tenant's projects and every connected node. Queued, running and cancellation-requested jobs occupy capacity. Completed attempts release their queue slots. A fifth pending job returns HTTP `409`; wait for completion or confirm cancellation before submitting more work.

Use an 8–128 character request key when submitting jobs. Retrying the same request with its original key returns the same job, including when the queue is full. Changing the request body requires a new key; do not create new keys automatically after an uncertain response. Teacher calls, hosted decisions and cloud runs have their own approval, accounting and [reconciliation controls](synthetic-generation-guide.md).

Workers claim durable jobs through the shared database. Only the current ownership token can commit a result. Multiple workers do not grant unlimited GPU capacity: keep the [shared model-capacity configuration](setup.md#shared-model-capacity-on-one-host) for workers sharing physical hardware. A job's queue limit, resource ownership, transport deadline and upstream account quota are separate controls.

Follow [managed cloud training](remote-training-guide.md) for AWS, Azure, GCP, Modal and RunPod. Adding application workers does not increase provider quotas or authorize extra paid compute.

## Check node readiness

Use `GET /health` for process liveness and `GET /ready` for readiness. The latter validates authentication configuration, the operational collector, required backup monitoring, the current database connection and the shared-storage binding. Its response identifies PostgreSQL or local SQLite correctly.

For shared artifacts, the background collector also checks one registered manifest's availability, preferring an active release. This uses object metadata, not a model-weight download. The response's `artifact_storage` field includes:

| Field | Meaning |
|---|---|
| `status` | The cached storage check's status |
| `metadata` | `postgresql` or `sqlite` |
| `artifacts` | `shared` or `local` |
| `probe` | `one-registered-manifest-head`, `none-no-model`, or `local-workspace` |
| `checked_at` | Timestamp of the successful check |

A workspace without a registered model reports `none-no-model`; this does **not** prove access to the configured S3, Azure Blob or GCS repository. Complete a small training/publish/cold-prediction workflow before routing application traffic to a new deployment. One successful manifest probe establishes bounded connectivity, not integrity of every artifact, free GPU capacity or domain accuracy. Cache hydration still verifies file hashes.

Database failures, a mismatched storage binding, or a failed/stale collector return HTTP `503`. Raw connection strings and storage error details are not returned to clients. Shared-storage probes run in the background, so readiness reflects the latest collector result rather than a new object-storage request on every health check. SDK request timeouts and the collector's freshness window apply.

Tune `DMT_MONITOR_INTERVAL_SECONDS` between 1 and 300 seconds; its default is 5. The collector is considered stale after the greater of 30 seconds and three intervals. Shared probes can incur object-request charges on each API node. For example, use a 30-second interval if that fits your detection requirements and request budget. The probe needs the existing object-read permission and adds no bucket-listing permission.

## Inspect failures and recovery

In the browser, select the project and open its operations/health controls. Inspect failed or interrupted jobs, missing workers, expired job leases and delayed queues. The API exposes the same scoped view at `/v1/projects/{project_id}/operations`; a tenant cannot read another tenant's project incidents.

Acknowledging an incident records the operator response; it does not repair the condition. Restore the dependency or worker, inspect the affected job, and let the collector recheck it. Transient worker/queue conditions resolve when their causes disappear. Failed-job records retain their history; submit replacement work explicitly after inspecting uncertain external operations.

Use these settings to adjust detection:

| Variable | Default | Allowed range |
|---|---:|---:|
| `DMT_WORKER_MAX_AGE_SECONDS` | 15 | 5–300 |
| `DMT_WORKER_ALERT_AFTER_SECONDS` | 60 | 1–3,600 |
| `DMT_QUEUE_ALERT_AFTER_SECONDS` | 300 | 1–86,400 |

Connect your deployment's monitoring to HTTP readiness and the authenticated project operations API. Durable incidents and health responses are available; they do not send email, Slack messages or pager notifications by themselves. Configure your preferred alerting service in your own infrastructure.

Protect the metadata database and artifact vault with [coordinated backup and recovery](shared-recovery-guide.md). Backup freshness monitoring for a personal SQLite workspace has a [separate setup](backup-monitoring-guide.md).

## Reproduce the bounded shared-workspace check

Install the application and optional PostgreSQL client. Point `DMT_TEST_POSTGRES_URL` only to a disposable database whose application test role can create and drop dedicated schemas:

```text
python -m unittest discover -s tests -p test_shared_operations.py -v
```

The checks create two API instances and two worker instances with separate caches, two tenants, two projects per tenant, and 180 reviewed fictional records per study. Sixteen concurrent submission attempts must produce four accepted jobs and four quota refusals per tenant within a 60-second local test window. They also exercise request-key replay, eight preparation jobs, two CPU training/release/prediction paths, metadata/binding faults, simulated object failure, and scoped failure/queue incident recovery.

This is a functional workload against actual PostgreSQL and local object-transport fixtures. API requests use the in-process test client. It is not an internet latency, multi-host throughput, S3 availability, cloud-account or representative-domain benchmark. No model downloads or paid provider calls are needed. Repeat with your real topology, runtime profiles, data volume and user traffic before setting production capacity or service-level objectives.
