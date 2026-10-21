# Clean up unreferenced shared object versions

[All guides](README.md) · [Shared artifact setup](shared-artifacts-guide.md) · [PostgreSQL setup](postgresql-guide.md)

Use an operator preview/apply workflow to remove old S3 object versions that are
not needed by the current workspace's model registry or recorded remote imports.
The collector retains every registered model, including inactive versions, and
every recorded admission input, including failed or cancelled jobs. It also
preserves young manifests and the older deduplicated blobs they reference.

This command does not delete projects, dataset history, registered models, local
caches or cloud training volumes. It does not enumerate all historical S3
versions. Treat those as separate retention policies rather than assuming this
collector erases all copies of domain data.

## Prepare the workspace and operator identity

Use the [PostgreSQL/shared S3 configuration](shared-artifacts-guide.md) with
**S3 bucket versioning Enabled**. New cleanup plans refuse unversioned or suspended
buckets. Deletion targets exact non-null version IDs, with an ETag condition,
instead of deleting whichever replacement happens to occupy the same key.
[S3 version deletion](https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html).

This release uses PostgreSQL workspace protocol **11**; personal SQLite remains
on its existing protocol. Stop all API and worker nodes before upgrading them
to the same application revision. Do not run older publisher nodes alongside
the collector. Take your approved metadata/object backup before maintenance.

Give a dedicated operator identity the required scoped permissions:

- `s3:GetBucketVersioning` and `s3:ListBucket` for the selected bucket/prefix.
- `s3:GetObject` to inspect immutable manifests and current candidate metadata.
- `s3:GetObjectVersion` and `s3:DeleteObjectVersion` for selected object versions.

Normal API/worker identities do not need version-deletion permission. Do not add
it to browser credentials. Keep the configured database/schema, bucket, endpoint
and prefix identical to the workspace's storage binding. Supply credentials
through the AWS SDK identity chain and `DMT_DATABASE_URL` through your operator
secret mechanism, as in the shared-artifact guide.

Listing, metadata reads and deletion use your storage account and can incur
provider charges. Inspect the candidate count and choose a maintenance window.
Only current objects inside this workspace's UUID namespace are considered;
other workspaces and unknown key formats are preserved.
[S3 listing and permissions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html).

## Preview the candidates

Run from the installed platform environment:

```bash
domain-platform preview-storage-cleanup --home data/operator-cache --database-schema decision_workbench --min-age-seconds 86400 --max-objects 10000
```

The JSON result includes candidate keys, immutable version IDs, sizes and ages,
the protected/unknown object counts, and `plan_sha256`. Keep the output private;
redirect it to a file under `data/` when reviewing a large plan. The default
minimum age is one day. Allowed ages range from one hour to ten years; the
listing limit ranges from 1 to 100,000 current objects. The bounded manifest-read
and persisted-plan limits may require a smaller inventory.

Preview does not delete objects. Missing or corrupt referenced manifests,
missing protected blobs, incomplete listing, unknown candidate versions and
inventory limits refuse cleanup. Unknown key formats are reported and retained.
Inspect unexpected content instead of broadening the tool into a bucket wipe.

## Apply the reviewed plan

Wait until training, evaluation, retrieval and admission jobs are neither queued
nor running. Model reads and ordinary workspace operations can continue. Reuse
the same age and inventory limit used for preview:

```bash
domain-platform apply-storage-cleanup --home data/operator-cache --database-schema decision_workbench --min-age-seconds 86400 --max-objects 10000 --plan-sha256 YOUR_REVIEWED_PLAN_SHA256
```

The operator recomputes the inventory and reference set before starting. A stale
plan is refused; preview again after inspecting the change. Publication guards
coordinate all participating nodes, and the database records the complete
version-specific plan before the first delete. New publication submissions can
return a temporary busy response while this guard is held; no compute is
launched by the cleanup command.

Each selected version is deleted conditionally, then checked by its exact version
ID. Progress is persisted. Successful completion records an operator audit event
and allows publication again. The reported byte count describes selected
versions, not a provider billing or immediately available disk-space measurement.
Deleting a current version can expose an older version at that key; unenumerated
historical versions are not silently purged.

Each operation also retains its immutable plan in metadata. After a later cleanup
has replaced the current status, inspect a previous plan and its audit events with
`domain-platform storage-cleanup-status --home data/operator-cache --database-schema decision_workbench --operation-id YOUR_PREVIOUS_OPERATION_ID`.
Keep these records under your audit/backup retention policy.

## Reconcile an interrupted cleanup

Inspect the durable operation:

```bash
domain-platform storage-cleanup-status --home data/operator-cache --database-schema decision_workbench
```

A pending/running record alone does not prove a process is alive. If cleanup
exits, times out or loses its database connection, publication remains blocked
until the recorded plan is reconciled. Retained registered-model reads remain
available when their storage and runtime are otherwise healthy. Do not delete
the maintenance row or bypass the block.

After correcting permissions/connectivity, resume the exact operation and hash
shown in status:

```bash
domain-platform resume-storage-cleanup --home data/operator-cache --database-schema decision_workbench --operation-id YOUR_OPERATION_ID --plan-sha256 YOUR_RECORDED_PLAN_SHA256
```

The shared guard prevents two operators from executing the same cleanup together.
A resumed operation reconciles already-absent selected versions and continues
from recorded progress. A delayed delete still targets its original immutable
version, so a later replacement is not selected by that request. Access-denied
responses are not treated as proof of absence. Changing registered references
during maintenance refuses continuation and requires investigation.

## Retention and recovery boundaries

Keep database/object recovery coordinated. Do not collect against an older
restored database until you have reconciled the object and backup retention
policy. Objects referenced only by external backups are not discoverable from
the current workspace's registry. Never apply an indiscriminate age-only bucket
lifecycle policy to active manifests or blobs. Configure incomplete multipart
upload expiry separately, and review historical-version retention with your
backup policy.

Local staging/cache cleanup, project/data deletion and coordinated recovery are
separate controls; this guide supplies no claim that they have run. For local
implementation checks, install the PostgreSQL/AWS clients, set
`DMT_TEST_POSTGRES_URL` to a disposable database, and run:

```bash
python -m unittest discover -s tests -p test_artifact_retention.py -v
python -m unittest discover -s tests -p test_shared_artifacts.py -v
```

Tests use real PostgreSQL plus a fictional versioned transport and installed SDK
response fixtures. They do not delete objects in a real account or establish
production storage scale. The locking contract uses PostgreSQL
[session advisory locks](https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS)
outside short metadata transactions.
