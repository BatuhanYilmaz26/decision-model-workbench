# Back up and restore a shared workspace

Use these operator commands when your workspace uses [PostgreSQL metadata](postgresql-guide.md) and shared [S3 artifacts](shared-artifacts-guide.md), [Azure Blob artifacts](azure-artifacts-guide.md) or [Google Cloud artifacts](gcs-artifacts-guide.md). A backup captures the application tables from one consistent database view and copies every referenced model and incoming training artifact into an independent recovery prefix. Restore creates a new workspace with the original data, model references and release history.

For a personal SQLite workspace, use the [local recovery guide](recovery-guide.md) instead. The encrypted and scheduled SQLite backup commands do not capture shared PostgreSQL/object-store workspaces.

## Before you start

- Install the application, `requirements/postgres.txt` and your chosen transport's optional profile: `requirements/aws-sdk.txt`, `requirements/azure-artifacts.txt` or `requirements/gcp-artifacts.txt`.
- Configure `DMT_DATABASE_URL` and one live artifact transport using the matching guide below. Keep credentials in its SDK credential chain or secret manager.
- Choose independent versioned recovery storage. Source and recovery prefixes must not contain each other. Live, backup and restore-target stores must use the same transport for server-side copies.
- Give the operator access to inspect versioning, read source metadata/versions and create recovery/target objects. Include your encryption-key permissions where required. The copying identity must be authorized for both locations; normal application identities need no cleanup permission.
- Choose private local storage for the metadata bundle and retain its checksum separately. Treat the bundle as sensitive data from **every tenant** in the workspace. It is plaintext; protect it with your storage encryption and access policy.

Standard AWS S3 supports regional server-side copies. Custom S3-compatible services must use the same endpoint and support the required versioning/copy APIs; qualify your service before relying on it. These commands do not transfer model weights through the operator's computer.

Select one row; the capture, verify and restore commands are the same for all three transports:

| Transport | Live configuration | Recovery configuration | Restored prefix variable | Storage and permissions |
|---|---|---|---|---|
| S3 | `DMT_ARTIFACT_S3_*` | `DMT_BACKUP_S3_*` | `DMT_ARTIFACT_S3_PREFIX` | [S3 setup](shared-artifacts-guide.md); enable bucket versioning and allow server-side/multipart copies |
| Azure Blob | `DMT_ARTIFACT_AZURE_*` | `DMT_BACKUP_AZURE_*` | `DMT_ARTIFACT_AZURE_PREFIX` | [Azure live/vault settings](azure-artifacts-guide.md#back-up-and-restore-shared-state); use independent containers in the same account |
| GCS | `DMT_ARTIFACT_GCS_*` | `DMT_BACKUP_GCS_*` | `DMT_ARTIFACT_GCS_PREFIX` | [GCS live/vault settings](gcs-artifacts-guide.md#back-up-and-restore); independent buckets are supported |

Leave other transports' settings unset. Set the selected row's `BUCKET`/`CONTAINER`, location and identity settings as described in its guide. Configuration prefixes in the table denote a group of variables, not literal environment-variable names.

## Capture a backup

Set the recovery location in addition to your existing source configuration. For S3, in PowerShell:

```powershell
$env:DMT_BACKUP_S3_BUCKET = "your-recovery-bucket"
$env:DMT_BACKUP_S3_PREFIX = "decision-workbench-recovery"
$env:DMT_BACKUP_S3_REGION = "us-east-1"

domain-platform backup-shared --home data/shared-node --database-schema decision_workbench --output backups/shared-2026-10-22 --timeout 3600
```

In Bash or Zsh, use `export DMT_BACKUP_S3_BUCKET=your-recovery-bucket` and the equivalent assignments for the other variables. An optional `DMT_BACKUP_S3_ENDPOINT` selects a compatible service; it is independent of `DMT_ARTIFACT_S3_ENDPOINT`.

The output directory must be new and outside the source workspace. The command returns `manifest_sha256`, `snapshot`, object count and the recovery storage descriptor. Save the complete result in protected operator records and retain the checksum independently of the backup directory.

The directory contains:

| File | Contents |
|---|---|
| `shared-metadata.jsonl` | Logical snapshot of all application tables, including datasets, reviews, jobs, audit history and releases |
| `shared-backup.json` | Completion manifest binding the database snapshot to exact recovery object versions |

Model files remain in the configured object storage under a unique snapshot subprefix. **Both the metadata directory and those object versions are necessary for recovery.** Keep the base recovery configuration so the commands can locate that snapshot again.

Ordinary data edits can continue during capture. Shared artifact publication, release operations and artifact maintenance wait behind the backup's coordination lock. The manifest is published only after object copies finish. A failed capture can leave an incomplete snapshot prefix in the recovery bucket; it has no completed local bundle. Inspect and remove only that operation's prefix according to your retention policy, then retry with a new output directory. This command does not automatically prune backup objects.

## Verify retained recovery data

Keep the same selected recovery configuration (`DMT_BACKUP_S3_*`, `DMT_BACKUP_AZURE_*` or `DMT_BACKUP_GCS_*`):

```text
domain-platform verify-shared-backup --backup backups/shared-2026-10-22 --manifest-sha256 YOUR_RETAINED_SHA256 --timeout 3600
```

Verification checks the local metadata checksum, table inventory, workspace identity, counts, artifact references, manifest content and availability of the exact copied object versions. S3 blob checks use version metadata; they do not download and hash every weight file. Normal cache hydration verifies each file's content hash before model use. Perform a restore drill to test your permissions, model runtime and serving behavior together.

## Restore into a new workspace

1. Stop or isolate the original deployment before promoting a replacement. Reconcile external teacher requests and cloud jobs; restoration cannot stop work already running in provider accounts.
2. Provision an empty, dedicated PostgreSQL schema with the normal application role. Keep the same compatible application version for the restore.
3. Set `DMT_DATABASE_URL` to the **target** database. Point the selected live configuration at an independent target bucket/container and prefix; keep the matching backup configuration pointing to the recovery vault. The restore command creates a unique artifact subprefix under your target prefix. For example, S3 uses `DMT_ARTIFACT_S3_*` for the target and `DMT_BACKUP_S3_*` for the retained vault; Azure and GCS use their own table row.
4. Run:

```text
domain-platform restore-shared --backup backups/shared-2026-10-22 --manifest-sha256 YOUR_RETAINED_SHA256 --home data/recovered-node --database-schema recovered_decisions --timeout 3600
```

The home must be new. Existing domain records in the target schema are refused. Referenced artifacts are copied first; metadata is then imported transactionally. A corrupt or incomplete metadata stream rolls back the import. A failed restore may leave bootstrap tables, copied objects or an imported target schema if final local publication fails. Inspect the result before retrying; do not overwrite an existing workspace to hide an incomplete attempt.

5. Use the returned `artifact_storage.prefix` as the selected transport's **restored prefix variable** from the table. Use its target database/schema and the returned home. Reprovision workspace keys, organization sign-in, provider credentials and other operator configuration separately.
6. Start the API/worker/browser using your normal [shared-workspace setup](setup.md#personal-and-shared-workspaces). Check a representative prediction and its release version, data/review history, tenant permissions and readiness before routing traffic to it.

The recovered workspace keeps its original workspace UUID, immutable references and usage history. Worker heartbeats are cleared. Queued/running jobs become interrupted; cancellation requests become cancelled. None are automatically replayed. Inspect upstream state before explicitly submitting replacement work. The restore adds audit events and records its result in `shared-restore.json` in the new home.

## Recovery boundaries and retention

This is an application-level logical backup, not PostgreSQL physical backup or point-in-time recovery. It does not capture database roles, infrastructure, secret files, environment variables or unrelated bucket contents. User-supplied records can themselves contain sensitive values. Apply your managed database backup/PITR, key custody and offsite policies separately.

Every registered model and remote-import input reference is retained, including references from terminal jobs. Source object cleanup cannot delete independent recovery copies. Bucket lifecycle rules, version deletion or lost encryption keys can still invalidate a backup; protect the vault and verify it periodically.

Current bounds are 16 GiB of logical metadata, 8 MiB per JSONL line, ten million rows, 250,000 distinct objects and a 64 MiB completion manifest. Individual model bundles retain the [shared-artifact limits](shared-artifacts-guide.md). The operator timeout is positive and at most 24 hours; individual SDK requests also have transport timeouts. Server-side copies incur storage/request charges and may incur transfer charges. SDK contract tests do not establish paid-account behavior or deployed cloud-storage acceptance.

Implementation references: [PostgreSQL repeatable-read snapshots](https://www.postgresql.org/docs/18/transaction-iso.html#XACT-REPEATABLE-READ), [Psycopg server-side cursors](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#server-side-cursors), [Boto3 managed object copying](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/copy.html).
