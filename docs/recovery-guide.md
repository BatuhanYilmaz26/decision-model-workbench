# Workspace backup and recovery

[All guides](README.md) · [Project overview](../README.md)

The local platform provides operator commands to back up the SQLite workspace and all registered CPU, native Laya and Unsloth model exports. Recovery restores into a new directory and preserves immutable project/data/model history and the active release revision. It does not replay unfinished work.

This is the single-host local profile.

## Capture and retain a backup

Run from the installed application environment with filesystem access to the workspace. The output must be a **new directory outside the source workspace**. Existing targets, linked paths and source/target nesting are refused.

```bash
domain-platform backup --home data/workspace --output backups/workspace-20261010
```

The command returns JSON with `destination`, `manifest_sha256` and inventory counts. Retain the returned checksum in a separate trusted operator record. A checksum stored only beside the backup cannot establish that the backup itself was not replaced. The manifest is a checksum inventory, not a digital signature.

The command uses SQLite's online backup API, which creates a consistent database snapshot while other connections remain usable. It then copies only artifact directories referenced by that captured registry and verifies their pinned manifests, reports, data references and historical record hashes. A model registered after the snapshot is outside that backup. A missing or changed registered export causes failure instead of a successful incomplete bundle. [Python SQLite backup API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup), [SQLite online backup documentation](https://www.sqlite.org/backup.html)

The default deadline is 300 seconds. Use `--timeout 900` for a larger workspace. Deadline checks occur between SQLite page batches and file-copy/validation stages; they do not forcibly interrupt an operating-system file copy. The current format limits the inventory to 50,000 files, 500 GiB and a 16 MiB manifest. Those are refusal bounds, not tested performance guarantees.

The source API can remain running. Avoid external edits/deletion of registered artifact files during capture. A failed capture cleans its private staging directory; the completion manifest is published last. Power-loss recovery across filesystems has not been qualified.

## What is retained

| Retained | Intentionally excluded |
|---|---|
| All tenants' projects, policies and schemas | Operator credential files and secret environment configuration |
| Data contents, append/review/rejection history and current heads | Download caches and unregistered artifacts |
| [Review assignments](review-workflow.md), revision chains and referenced data versions | Browser sessions and unsaved annotation forms |
| Reserved snapshots, frozen gates, job history and audit records | Incomplete training checkpoints and private job logs |
| Registered model exports/reports and active release revisions | Service process state, runtime caches and GPU leases |

The bundle contains every tenant in the workspace and may include sensitive domain records. Store it under the enterprise's approved access controls and encrypted backup storage. This operation is a filesystem-level operator command; the tenant API does not expose a cross-tenant backup download. Reconfigure credentials and provider secrets separately after recovery.

Verification and recovery support schema **3, 4, 5, 6, 7, 8 and 9** backups. Restoration preserves incident/assignment history, explicit reservation plans, archived synthetic-generation requests/results and frozen policies while clearing copied worker heartbeats; the restarted worker must report fresh presence. Verification opens the captured database read-only and does not run migrations. Restore upgrades supported older schemas into the current workspace schema. Earlier schemas and future formats require a compatible version-specific recovery procedure.

For an authenticated encrypted local bundle, use the [encrypted recovery workflow](encrypted-recovery-guide.md). Keep the recovery key and retained receipt separate from distributed backups. The plaintext commands in this guide remain available for approved encrypted storage.

## Verify before restoring

Use the checksum retained separately when the backup was captured:

```bash
domain-platform verify-backup --backup backups/workspace-20261010 --manifest-sha256 YOUR_RETAINED_SHA256
```

Verification checks the manifest checksum, exact file inventory, every file's bytes/SHA-256, SQLite integrity, project scope, history/content hashes, dataset heads, snapshot references, frozen policies and registered model manifests. Missing or extra files, unsafe paths and altered model weights are refused. Verification does not load the GPU or establish prediction quality.

## Restore and bring services back

Choose a directory that does not exist:

```bash
domain-platform restore --backup backups/workspace-20261010 --home data/recovered-workspace --manifest-sha256 YOUR_RETAINED_SHA256
```

Restore verifies both the backup and the copied staging files, preserves immutable entities and active release identity, appends per-project `restore.workspace` audit entries and writes `restore-receipt.json`. The receipt lists copied unfinished jobs and their transitions:

| Captured status | Restored status | Operator action |
|---|---|---|
| `queued` or `running` | `interrupted` | Inspect the previous attempt; explicitly submit a new job/request key if needed |
| `cancel_requested` | `cancelled` | Cancellation remains terminal |
| Any terminal status | Unchanged | Retain the historical result |

Copied owner tokens and leases are cleared. A restored worker cannot claim those interrupted jobs or silently repeat training/teacher requests. Resume from an incomplete checkpoint is not supported by this format.

Supply an approved credential file and select the recovered home when starting the local application:

```bash
domain-platform run --home data/recovered-workspace --keys-file data/operator-keys.json
```

Use the model environment in [setup](setup.md) for native/Unsloth exports. Recovery preserves historical training configuration, including original job paths, while serving resolves registered artifacts under the current workspace. Run only one independent workspace service per GPU: capacity coordination does not extend across source and restored workspaces.

Check `/ready`, authenticated project visibility and the active release, then send a known decision request and compare its complete model/schema/revision/probability response with the trusted pre-recovery probe. Readiness alone does not verify model loading. Check the restore receipt before resubmitting any interrupted work.
