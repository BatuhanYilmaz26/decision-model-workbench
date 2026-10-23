# Back up and recover a workspace

<a id="workspace-backup-and-recovery"></a>

[Documentation](README.md) · [Project overview](../README.md)

Choose your storage topology first. Personal SQLite backups and PostgreSQL/object-store backups are separate commands. Keep retained checksums, encryption keys and recovery storage independent of the live workspace.

## Choose a section

- [Personal SQLite backup and restore](#local-backups)

- [Encrypted local backups](#encrypted-backups)

- [Scheduling and verified retention](#scheduled-backups)

- [Backup freshness and failure monitoring](#backup-monitoring)

- [PostgreSQL and shared-object recovery](#shared-workspace-recovery)

<a id="local-backups"></a>

## Personal SQLite backup and restore

<a id="local-backups--workspace-backup-and-recovery"></a>

The local platform provides operator commands to back up the SQLite workspace and its registered model exports. Recovery restores into a new directory and preserves immutable project/data/model history and the active release revision. It does not replay unfinished work.

For PostgreSQL with shared S3, Azure Blob or GCS artifacts, use [shared-workspace backup and restore](recovery-guide.md#shared-workspace-recovery). The local commands below capture SQLite workspaces only.

This is the single-host local profile.

<a id="local-backups--capture-and-retain-a-backup"></a>

### Capture and retain a backup

Run from the installed application environment with filesystem access to the workspace. The output must be a **new directory outside the source workspace**. Existing targets, linked paths and source/target nesting are refused.

```bash
domain-platform backup --home data/workspace --output backups/workspace-20261010
```

The command returns JSON with `destination`, `manifest_sha256` and inventory counts. Retain the returned checksum in a separate trusted operator record. A checksum stored only beside the backup cannot establish that the backup itself was not replaced. The manifest is a checksum inventory, not a digital signature.

The command uses SQLite's online backup API, which creates a consistent database snapshot while other connections remain usable. It then copies only artifact directories referenced by that captured registry and verifies their pinned manifests, reports, data references and historical record hashes. A model registered after the snapshot is outside that backup. A missing or changed registered export causes failure instead of a successful incomplete bundle. [Python SQLite backup API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup), [SQLite online backup documentation](https://www.sqlite.org/backup.html)

The default deadline is 300 seconds. Use `--timeout 900` for a larger workspace. Deadline checks occur between SQLite page batches and file-copy/validation stages; they do not forcibly interrupt an operating-system file copy. The current format limits the inventory to 50,000 files, 500 GiB and a 16 MiB manifest. Those are refusal bounds, not tested performance guarantees.

The source API can remain running. Avoid external edits/deletion of registered artifact files during capture. A failed capture cleans its private staging directory; the completion manifest is published last. Power-loss recovery across filesystems has not been qualified.

<a id="local-backups--what-is-retained"></a>

### What is retained

| Retained | Intentionally excluded |
|---|---|
| All tenants' projects, policies and schemas | Operator credential files and secret environment configuration |
| Data contents, append/review/rejection history and current heads | Download caches and unregistered artifacts |
| [Review assignments](data-guide.md#assigned-review), revision chains and referenced data versions | Browser sessions and unsaved annotation forms |
| Reserved snapshots, frozen gates, job history and audit records | Incomplete training checkpoints and private job logs |
| Registered model exports/reports and active release revisions | Service process state, runtime caches and GPU leases |

The bundle contains every tenant in the workspace and may include sensitive domain records. Store it under the enterprise's approved access controls and encrypted backup storage. This operation is a filesystem-level operator command; the tenant API does not expose a cross-tenant backup download. Reconfigure credentials and provider secrets separately after recovery.

Verification and recovery support schema **3, 4, 5, 6, 7, 8, 9, 10 and 11** backups. Restoration preserves incident/assignment history, explicit reservation plans, archived synthetic-generation requests/results and frozen policies while clearing copied worker heartbeats; the restarted worker must report fresh presence. Verification opens the captured database read-only and does not run migrations. Restore upgrades supported older schemas into the current workspace schema. Earlier schemas and future formats require a compatible version-specific recovery procedure.

For an authenticated encrypted local bundle, use the [encrypted recovery workflow](recovery-guide.md#encrypted-backups). Keep the recovery key and retained receipt separate from distributed backups. The plaintext commands in this guide remain available for approved encrypted storage.

<a id="local-backups--verify-before-restoring"></a>

### Verify before restoring

Use the checksum retained separately when the backup was captured:

```bash
domain-platform verify-backup --backup backups/workspace-20261010 --manifest-sha256 YOUR_RETAINED_SHA256
```

Verification checks the manifest checksum, exact file inventory, every file's bytes/SHA-256, SQLite integrity, project scope, history/content hashes, dataset heads, snapshot references, frozen policies and registered model manifests. Missing or extra files, unsafe paths and altered model weights are refused. Verification does not load the GPU or establish prediction quality.

<a id="local-backups--restore-and-bring-services-back"></a>

### Restore and bring services back

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

Use the matching model environment in [setup](setup.md#configuration) for native exports. Recovery preserves historical training configuration, including original job paths, while serving resolves registered artifacts under the current workspace. Source and restored services sharing a GPU must use the same [model-capacity lock](setup.md#configuration--shared-model-capacity-on-one-host). Before probing the restored native release, stop model serving/training in the source workspace or let its resident model unload. While the source owns that capacity, a restored native prediction returns resource unavailable rather than loading a competing model. CPU baseline predictions do not require this handoff.

Check `/ready`, authenticated project visibility and the active release, then send a known decision request and compare its complete model/schema/revision/probability response with the trusted pre-recovery probe. Readiness alone does not verify model loading. Check the restore receipt before resubmitting any interrupted work.

<a id="encrypted-backups"></a>

## Encrypted local backups

<a id="encrypted-backups--encrypt-and-recover-a-local-workspace"></a>

Capture a consistent workspace and its registered model exports into an encrypted bundle, verify it using a separately retained receipt, and recover into a new directory. These are filesystem-level operator commands: the tenant API does not expose all-tenant backup downloads.

The encrypted workflow preserves the existing [backup scope, validation and interrupted-job rules](recovery-guide.md#local-backups). It includes private domain records from every tenant in the workspace. Registered model data are checked against the captured registry; credentials, caches and incomplete training checkpoints remain outside recovery.

<a id="encrypted-backups--prepare-private-storage-and-a-recovery-key"></a>

### Prepare private storage and a recovery key

Install the CPU [platform dependency profile](../requirements/README.md). It includes Cryptography 50.0.2. Choose approved local storage with sufficient free space and access restricted to the backup operator. Temporary plaintext is required: use an encrypted volume when that is required by your data policy.

Generate a new key file:

```bash
domain-platform create-backup-key --output .internal/backup-key.dmtkey
```

The command creates a versioned binary file containing a randomly generated 256-bit secret and prints only its path and key identifier. It refuses to overwrite an existing file. POSIX creation mode is `0600`; Windows operators must configure appropriate NTFS permissions because POSIX mode does not establish Windows ACLs. [Windows access-control management](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls)

Keep the key in approved secret storage, with a separate recoverable copy. Do not store it alongside a distributed backup or substitute a password/text file. Losing the key makes its backups unrecoverable. Create a new key file for future captures when rotating; retain each older key for the backups that still require it. Enterprise KMS integration and automatic key rotation are separate deployment work.

<a id="encrypted-backups--capture-an-encrypted-backup"></a>

### Capture an encrypted backup

Choose a destination that does not exist and is outside the source workspace:

```bash
domain-platform backup-encrypted --home data/workspace --key-file .internal/backup-key.dmtkey --output backups/domain-20261014 --timeout 900
```

Use a new destination for every capture. The source API may stay running: SQLite supplies a consistent captured database, and only model exports referenced by that snapshot are copied. Avoid external modifications or deletion of registered exports during capture.

The completed directory contains:

| File | Purpose |
|---|---|
| `payload.dmtenc` | Encrypted captured manifest and file contents |
| `encrypted-backup.json` | Format, key identifier, ciphertext size/checksum, captured-manifest checksum and capture time |

The command returns `encrypted_manifest_sha256`, `key_id`, `payload_bytes` and the destination. Retain that returned checksum and key identifier separately from the bundle, under approved access controls. A checksum copied from the bundle itself does not establish trusted provenance. The metadata exposes size, capture time and hashes; filenames and domain contents are encrypted.

Copy both files when moving a bundle. Missing/extra entries or altered ciphertext are refused. Completion metadata is published last. A caught publication failure cleans the new destination; existing directories are never overwritten.

<a id="encrypted-backups--verify-before-recovery"></a>

### Verify before recovery

Create a private scratch-parent directory on approved storage outside the backup. Then supply the checksum retained at capture:

```bash
domain-platform verify-encrypted-backup --backup backups/domain-20261014 --key-file .internal/backup-key.dmtkey --encrypted-manifest-sha256 YOUR_RETAINED_SHA256 --scratch-parent .internal/recovery-scratch --timeout 900
```

Verification checks the retained metadata identity, key identity, complete authenticated stream and exact ciphertext checksum. Only then does it interpret plaintext file records. It refuses unsafe paths, excessive sizes, missing/trailing contents or file-hash mismatches and performs the existing read-only database/registry/model validation. A successful result contains `verified: true`, the key identifier and captured summary. It does not load a GPU or establish prediction quality.

Temporary payload/file copies are removed on ordinary success or caught failure. A killed process or power loss can leave scratch directories; inspect and remove abandoned `.dmt-*` directories after confirming no recovery process still uses them. Directory removal does not guarantee secure erasure or remove filesystem snapshots.

<a id="encrypted-backups--restore-into-a-fresh-workspace"></a>

### Restore into a fresh workspace

Use the corresponding key and separately retained checksum:

```bash
domain-platform restore-encrypted --backup backups/domain-20261014 --key-file .internal/backup-key.dmtkey --encrypted-manifest-sha256 YOUR_RETAINED_SHA256 --home data/recovered-workspace --timeout 900
```

Authentication and workspace validation must succeed before the destination is published. The command preserves active release identity, data/review history and frozen gates; copied pending/running jobs become interrupted and cannot silently replay teacher or training calls. Supported schema-3/4/5/6/7 backups use the current recovery path. The result includes the ordinary restore receipt plus the encrypted manifest checksum and key identifier.

Reconfigure trusted credentials/provider secrets, start the API and worker against the recovered home, check `/ready`, and compare known authenticated decisions with your pre-backup probe. Follow the [recovery startup checklist](recovery-guide.md#local-backups--restore-and-bring-services-back). Keep the original workspace until you have accepted the recovered service.

<a id="encrypted-backups--capacity-and-encryption-format"></a>

### Capacity and encryption format

The format retains the existing limits of 50,000 captured files, 500 GiB of file contents and a 16 MiB captured manifest. These are refusal bounds, not scale or recovery-time guarantees. Allow approximately twice the captured payload size in free space for capture/verification staging and three times for restore staging and the recovered copy. The source and completed backup consume additional space. Uncompressed model exports can dominate these requirements.

The deadline is checked between bounded encryption/decryption/copy stages and before publication; an operating-system file copy or existing workspace-validation call is not forcibly interrupted. Measure capture and recovery time on your actual storage and model exports.

The project-specific v1 envelope uses HKDF-SHA256 to derive a fresh AES-256 key from the private root key and a random per-backup salt. One-MiB records use unique 96-bit counter nonces and full 128-bit authentication tags; associated data binds the header, position and record framing. A required authenticated footer binds count, total length and payload hash. Reordering, truncation and trailing records are refused. Keep a compatible application version to read this format. [Cryptography AES-GCM documentation](https://cryptography.io/en/stable/hazmat/primitives/aead/), [HKDF documentation](https://cryptography.io/en/stable/hazmat/primitives/key-derivation-functions/)

For interval captures and verified retention, use the [scheduled backup service](recovery-guide.md#scheduled-backups). Offsite replication, external alerts, enterprise key custody and power-loss/large-scale acceptance must be configured and qualified for your deployment. The [source metadata](../research/provenance/encrypted-recovery-sources-2026-10-14.json) records the primary interfaces used here.

<a id="scheduled-backups"></a>

## Scheduling and verified retention

<a id="scheduled-backups--schedule-encrypted-backups-and-retain-verified-copies"></a>

Run encrypted captures on a fixed interval, retain their trusted receipts in a separate catalog, and prune older owned bundles after verifying the copies you will keep. This is a single-host operator service; it does not invoke training, teacher or provider jobs.

<a id="scheduled-backups--configure-private-storage"></a>

### Configure private storage

Install the [CPU platform profile](../requirements/README.md) and generate a [recovery key](recovery-guide.md#encrypted-backups--prepare-private-storage-and-a-recovery-key). Use an existing workspace, a **new dedicated vault**, a **new catalog** and a **new policy file**:

```bash
domain-platform configure-backups --home data/workspace --vault backups/scheduled --catalog .internal/backups.sqlite3 --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey --interval-seconds 86400 --retry-seconds 300 --keep-last 7 --timeout 900
```

The command returns the policy identifier and checksum. It refuses existing targets, invalid bounds and paths that put the vault, catalog, policy or key inside the workspace, or the control files/key inside the vault. Keep the key, policy and catalog in approved private storage separate from distributed encrypted bundles. Restrict Windows NTFS permissions; POSIX creation modes do not establish Windows ACLs.

| Setting | Meaning and supported range |
|---|---|
| `--interval-seconds` | Delay after a successful capture; integer 10–31,536,000; default 86,400 |
| `--retry-seconds` | Delay after a failed/interrupted capture; integer 10 through the capture interval; default 300 |
| `--keep-last` | Newest successful copies retained by a pruning operation; 1–1,000; default 7 |
| `--timeout` | Per-capture/per-bundle verification deadline; greater than zero through 86,400 seconds; default 900 |
| `--auto-prune` | Optional explicit authorization to prune older owned bundles after a new successful capture |

Choose an interval appropriate to capture duration and storage capacity. A policy is immutable. To change its workspace, schedule, retention settings or key, stop its service and configure a replacement using new policy/catalog/vault paths. Older policies and receipts remain usable for their retained copies. Keep older recovery keys while those copies are needed.

<a id="scheduled-backups--run-once-or-continuously"></a>

### Run once or continuously

Check the configuration with one due-run invocation:

```bash
domain-platform backup-run --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey
```

The first capture runs immediately. Later invocations return `captured: false` and `next_at` until the interval or retry delay elapses. A successful capture returns its run ID, destination and durable receipt, including `encrypted_manifest_sha256`. Retain these receipts separately; they are the trust anchors for [verification and restore](recovery-guide.md#encrypted-backups--verify-before-recovery).

Start the persistent operator service:

```bash
domain-platform backup-service --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey
```

The service writes JSON results to standard output. It serializes capture/pruning through a catalog operation lock and permits one persistent backup service per workspace. An overlapping service or operation is refused. Ctrl+C requests a graceful stop after the active operation; forced termination is handled on the next startup. Use your operating system's approved process supervisor for restart and log management. This command does not install a Windows service or systemd unit.

Missed intervals produce one fresh capture, not a burst of historical catch-up copies. The schedule uses the host's wall clock; keep that clock synchronized. A backward clock change can postpone the next due time. Captures can run while the source API remains available, but avoid external changes/deletion of registered exports during capture.

<a id="scheduled-backups--inspect-receipts-and-failures"></a>

### Inspect receipts and failures

```bash
domain-platform backups-status --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey
```

Status includes policy settings, catalog head, run states/receipts and the current retention plan. The separate SQLite catalog contains authenticated, chained immutable events. It records the prepared receipt **before publication**, then records completion after the bundle is published.

When a capture process dies, the next due-run invocation first reconciles its unfinished record while holding the operation lock. A complete bundle with a durable prepared receipt is fully verified against that receipt before it is accepted. An unpublished capture becomes interrupted; a changed/invalid returned bundle becomes failed. The service never invents a trusted receipt by hashing an untracked bundle. It does not replay copied training/teacher jobs.

Protect and independently back up the catalog and policy as well as the key. For SQLite catalog copies, use a consistent online backup or stop all catalog operations before copying the database and its required journal files. Catalog authentication detects changed events, sequence gaps and invalid transitions; it is not an external anti-rollback or WORM guarantee. Reverting a catalog can leave newer vault entries unmanaged, which blocks pruning. [SQLite online backup](https://www.sqlite.org/backup.html)

Service errors are reported locally as JSON; capture/deletion transitions remain in the private catalog. External alert delivery and enterprise telemetry require deployment integration. Sensitive temporary plaintext uses the [encrypted recovery storage rules](recovery-guide.md#encrypted-backups--capacity-and-encryption-format).

Use the [signed backup health monitor](recovery-guide.md#backup-monitoring) for publisher heartbeats, capture freshness/failure checks, scoped application incidents and optional readiness requirements. The API receives public verification assets; recovery and signing private keys stay with the operator.

<a id="scheduled-backups--review-and-apply-retention"></a>

### Review and apply retention

Pruning is disabled unless you explicitly authorize it. The default policy provides a plan through `backups-status`:

- `keep` lists the newest successful run IDs.
- `remove` lists older owned bundles and interrupted deletion attempts.
- `unmanaged_entries` lists content the service will preserve and refuse to prune around.
- `plan_sha256` binds the policy, catalog head and exact candidates.

After reviewing that plan, apply its current checksum:

```bash
domain-platform prune-backups --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey --expected-plan-sha256 YOUR_REVIEWED_PLAN_SHA256
```

A capture or other catalog change invalidates an older plan. Every kept bundle is authenticated and workspace-validated before any deletion. Complete removal candidates are also verified. The command only unlinks the two known encrypted bundle files under catalog-owned run directories; it never recursively removes a directory. Extra, changed or linked files are preserved and cause refusal.

If deletion is interrupted, inspect status again. A new plan can resume only the recorded operation's remaining files whose hashes match its original durable receipt. Keepers are verified again. No last retained copy is removed, and unrelated vault directories or failed/untracked captures are not automatically deleted.

For automatic retention, include `--auto-prune` when creating the policy. That signed setting authorizes the same verification/deletion rules after each successful capture. Without it, scheduled captures continue accumulating until an operator applies a plan. Verification can dominate pruning time for large models; measure it before selecting a frequency.

<a id="scheduled-backups--capacity-and-deployment-checks"></a>

### Capacity and deployment checks

The local catalog limits are 100,000 events, 512 MiB of catalog database and 10,000 vault entries. A capture reserves space for its begin/prepared/completed events before it starts; retention checks event capacity before deletion. Archive and replace the policy/catalog before reaching these bounds. The service does not erase old receipts to reclaim catalog capacity.

Use local filesystems with working OS file locks and SQLite durability. Distributed/NFS scheduling, cloud replication, enterprise key custody, external alerts, power-loss behavior and large-scale recovery require separate qualification. Verify your restart supervisor, resource use, due/failure behavior and recovery objectives on the intended host. [Python SQLite transactions](https://docs.python.org/3.13/library/sqlite3.html), [Windows file locking](https://docs.python.org/3.13/library/msvcrt.html), [POSIX file locking](https://docs.python.org/3.12/library/fcntl.html)

[Primary source metadata](../research/provenance/scheduled-backup-sources-2026-10-14.json) records the interfaces used by the scheduler and catalog.

<a id="backup-monitoring"></a>

## Backup freshness and failure monitoring

<a id="backup-monitoring--monitor-backup-freshness-and-failures"></a>

Publish a signed summary from the authenticated backup catalog, then show backup health and scoped incidents in the application. The API verifies a public key; it does not receive the backup decryption key, signing private key or catalog receipts.

Use this with the [scheduled backup service](recovery-guide.md#scheduled-backups). Signatures establish the configured publisher's identity and protect the summary against modification. They do not establish backup quality or replace complete encrypted verification and recovery drills. [Ed25519 signing and verification](https://cryptography.io/en/stable/hazmat/primitives/asymmetric/ed25519/)

<a id="backup-monitoring--create-a-separate-monitoring-key"></a>

### Create a separate monitoring key

Choose new private signing and public verification files:

```bash
domain-platform create-backup-monitor-key --private-file .internal/backup-monitor.dmtkey --public-file .internal/api-monitor/backup-monitor.dmtpub
```

The signing key uses a separate versioned binary format and cannot substitute for a recovery decryption key. Keep it with the backup operator. The API-visible monitoring directory should contain only public verification assets, its monitor profile and health snapshot. Restrict signing/recovery key permissions and keep those private files out of API/container mounts.

<a id="backup-monitoring--configure-the-api-trust-profile"></a>

### Configure the API trust profile

Bind the profile to the existing backup policy and set freshness limits:

```bash
domain-platform configure-backup-monitor --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey --public-key-file .internal/api-monitor/backup-monitor.dmtpub --health-file .internal/api-monitor/.dmt-backup-health.json --output .internal/api-monitor/.dmt-backup-monitor.json --max-snapshot-age-seconds 120 --max-backup-age-seconds 90000
```

The factory refuses invalid bounds, existing profile targets and snapshot/profile paths that overlap protected control files or the source workspace/vault. Shared-directory public-key/snapshot references are relative to the profile, supporting directory mounts with atomic snapshot replacement.

Set the API environment variable and restart it:

```text
DMT_BACKUP_MONITOR_PROFILE=.internal/api-monitor/.dmt-backup-monitor.json
```

The default profile is optional: backup faults open incidents while `/ready` remains available if its other dependencies are healthy. Include `--required` when generating the profile to make unhealthy backup monitoring cause readiness HTTP 503. Use this only when your ingress/deployment policy should stop routing traffic on backup-monitor faults. An unreadable required profile fails closed. With no profile configured, the application reports monitoring as unconfigured and does not assume backups are healthy.

<a id="backup-monitoring--publish-and-maintain-heartbeats"></a>

### Publish and maintain heartbeats

Publish one summary to validate the setup:

```bash
domain-platform publish-backup-health --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey --monitor-key-file .internal/backup-monitor.dmtkey --health-file .internal/api-monitor/.dmt-backup-health.json
```

For ongoing monitoring, start the backup service with its separate signing key and snapshot destination:

```bash
domain-platform backup-service --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey --monitor-key-file .internal/backup-monitor.dmtkey --health-file .internal/api-monitor/.dmt-backup-health.json --health-interval-seconds 30
```

The independent heartbeat thread refreshes the summary during long captures and idle intervals. Signing and file replacement are serialized. The interval is 1–60 seconds; default 30. Choose `max-snapshot-age-seconds` above that interval plus the API collector interval and expected storage delays. A one-off `backup-run` also accepts the signing/snapshot flags, but it does not maintain a continuous heartbeat.

The summary binds the policy identifier and canonical workspace home. The API verifies that binding against its workspace. Run publisher and API with the same workspace path representation, including container mount paths; a snapshot for another workspace/policy is refused. After recovering into a different home, configure a new backup policy and monitor profile for that recovered workspace.

<a id="backup-monitoring--read-health-and-incidents"></a>

### Read health and incidents

Owners and editors can open **Workspace health and alerts**. The scoped operations endpoint also returns a safe `collector.backup` summary:

```text
GET /v1/projects/{project}/operations
```

| State | Operator action |
|---|---|
| `no_success` | Run and inspect the first capture |
| `snapshot_stale` | Recover the health publisher or its private storage |
| `backup_stale` | Inspect scheduling, retries and capture duration |
| `capture_failed` | Inspect the private catalog and latest attempt |
| `capture_overdue` | Inspect the still-running capture and deadline |
| `backup_missing` | Inspect missing/changed bundle identity, inventory or size |
| `retention_attention` | Review unmanaged content or interrupted retention |
| `unavailable` | Inspect profile, public key, signature, publisher/catalog and host clock |
| `healthy` | Continue periodic verification and recovery drills |

The collector opens a transient `backup_unhealthy` incident in each current project and resolves it after observing healthy signed evidence. Processing is bounded to 500 projects per collection pass. Tenant credentials cannot access another tenant's incident routes. Summaries omit policy/key identifiers, paths, catalog heads, receipts and domain contents. Acknowledging an incident does not repair its cause; removing the monitor profile does not silently resolve an existing fault.

Health measures recorded capture freshness, attempt state and basic availability/manifest identity/size of the latest retained bundle. It does not continually decrypt every model file or prove current payload integrity. Use [complete encrypted verification](recovery-guide.md#encrypted-backups--verify-before-recovery) and known-decision recovery probes on your planned schedule. Host clocks must be synchronized. Signatures and freshness bounds are not a persistent anti-replay/WORM witness across restarts.

Local incidents and JSON service output are available now. External notifications, central telemetry, enterprise key custody, cloud supervision and workload-scale acceptance require separate integration; no messages are sent by this monitor.

<a id="backup-monitoring--upgrade-and-container-setup"></a>

### Upgrade and container setup

Back up and stop all API/browser/worker services before upgrading. Schema **7** protects backup-alert semantics; older applications supporting only schema 6 refuse the upgraded workspace. Do not mix versions or lower the schema number manually. Recovery supports schema-3/4/5/6/7 backups.

The Compose API accepts `DMT_BACKUP_MONITOR_PROFILE`. Mount the public-only monitoring directory read-only so atomic host file replacement remains visible. Run the backup operator separately with approved private mounts. Both processes need matching workspace paths for the binding; configure policies inside the intended namespace. Qualify the complete container/supervisor setup before relying on its alerts.

[Primary source metadata](../research/provenance/backup-monitor-sources-2026-10-14.json) records the signing interfaces used here.

<a id="shared-workspace-recovery"></a>

## PostgreSQL and shared-object recovery

<a id="shared-workspace-recovery--back-up-and-restore-a-shared-workspace"></a>

Use these operator commands when your workspace uses [PostgreSQL metadata](shared-artifacts-guide.md#postgresql-metadata) and shared [S3 artifacts](shared-artifacts-guide.md#s3-artifacts), [Azure Blob artifacts](shared-artifacts-guide.md#azure-blob) or [Google Cloud artifacts](shared-artifacts-guide.md#google-cloud-storage). A backup captures the application tables from one consistent database view and copies every referenced model and incoming training artifact into an independent recovery prefix. Restore creates a new workspace with the original data, model references and release history.

For a personal SQLite workspace, use the [local recovery guide](recovery-guide.md#local-backups) instead. The encrypted and scheduled SQLite backup commands do not capture shared PostgreSQL/object-store workspaces.

<a id="shared-workspace-recovery--before-you-start"></a>

### Before you start

- Install the application, `requirements/postgres.txt` and your chosen transport's optional profile: `requirements/aws-sdk.txt`, `requirements/azure-artifacts.txt` or `requirements/gcp-artifacts.txt`.
- Configure `DMT_DATABASE_URL` and one live artifact transport using the matching guide below. Keep credentials in its SDK credential chain or secret manager.
- Choose independent versioned recovery storage. Source and recovery prefixes must not contain each other. Live, backup and restore-target stores must use the same transport for server-side copies.
- Give the operator access to inspect versioning, read source metadata/versions and create recovery/target objects. Include your encryption-key permissions where required. The copying identity must be authorized for both locations; normal application identities need no cleanup permission.
- Choose private local storage for the metadata bundle and retain its checksum separately. Treat the bundle as sensitive data from **every tenant** in the workspace. It is plaintext; protect it with your storage encryption and access policy.

Standard AWS S3 supports regional server-side copies. Custom S3-compatible services must use the same endpoint and support the required versioning/copy APIs; qualify your service before relying on it. These commands do not transfer model weights through the operator's computer.

Select one row; the capture, verify and restore commands are the same for all three transports:

| Transport | Live configuration | Recovery configuration | Restored prefix variable | Storage and permissions |
|---|---|---|---|---|
| S3 | `DMT_ARTIFACT_S3_*` | `DMT_BACKUP_S3_*` | `DMT_ARTIFACT_S3_PREFIX` | [S3 setup](shared-artifacts-guide.md#s3-artifacts); enable bucket versioning and allow server-side/multipart copies |
| Azure Blob | `DMT_ARTIFACT_AZURE_*` | `DMT_BACKUP_AZURE_*` | `DMT_ARTIFACT_AZURE_PREFIX` | [Azure live/vault settings](shared-artifacts-guide.md#azure-blob--back-up-and-restore-shared-state); use independent containers in the same account |
| GCS | `DMT_ARTIFACT_GCS_*` | `DMT_BACKUP_GCS_*` | `DMT_ARTIFACT_GCS_PREFIX` | [GCS live/vault settings](shared-artifacts-guide.md#google-cloud-storage--back-up-and-restore); independent buckets are supported |

Leave other transports' settings unset. Set the selected row's `BUCKET`/`CONTAINER`, location and identity settings as described in its guide. Configuration prefixes in the table denote a group of variables, not literal environment-variable names.

<a id="shared-workspace-recovery--capture-a-backup"></a>

### Capture a backup

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

<a id="shared-workspace-recovery--verify-retained-recovery-data"></a>

### Verify retained recovery data

Keep the same selected recovery configuration (`DMT_BACKUP_S3_*`, `DMT_BACKUP_AZURE_*` or `DMT_BACKUP_GCS_*`):

```text
domain-platform verify-shared-backup --backup backups/shared-2026-10-22 --manifest-sha256 YOUR_RETAINED_SHA256 --timeout 3600
```

Verification checks the local metadata checksum, table inventory, workspace identity, counts, artifact references, manifest content and availability of the exact copied object versions. S3 blob checks use version metadata; they do not download and hash every weight file. Normal cache hydration verifies each file's content hash before model use. Perform a restore drill to test your permissions, model runtime and serving behavior together.

<a id="shared-workspace-recovery--restore-into-a-new-workspace"></a>

### Restore into a new workspace

1. Stop or isolate the original deployment before promoting a replacement. Reconcile external teacher requests and cloud jobs; restoration cannot stop work already running in provider accounts.
2. Provision an empty, dedicated PostgreSQL schema with the normal application role. Keep the same compatible application version for the restore.
3. Set `DMT_DATABASE_URL` to the **target** database. Point the selected live configuration at an independent target bucket/container and prefix; keep the matching backup configuration pointing to the recovery vault. The restore command creates a unique artifact subprefix under your target prefix. For example, S3 uses `DMT_ARTIFACT_S3_*` for the target and `DMT_BACKUP_S3_*` for the retained vault; Azure and GCS use their own table row.
4. Run:

```text
domain-platform restore-shared --backup backups/shared-2026-10-22 --manifest-sha256 YOUR_RETAINED_SHA256 --home data/recovered-node --database-schema recovered_decisions --timeout 3600
```

The home must be new. Existing domain records in the target schema are refused. Referenced artifacts are copied first; metadata is then imported transactionally. A corrupt or incomplete metadata stream rolls back the import. A failed restore may leave bootstrap tables, copied objects or an imported target schema if final local publication fails. Inspect the result before retrying; do not overwrite an existing workspace to hide an incomplete attempt.

5. Use the returned `artifact_storage.prefix` as the selected transport's **restored prefix variable** from the table. Use its target database/schema and the returned home. Reprovision workspace keys, organization sign-in, provider credentials and other operator configuration separately.
6. Start the API/worker/browser using your normal [shared-workspace setup](setup.md#configuration--personal-and-shared-workspaces). Check a representative prediction and its release version, data/review history, tenant permissions and readiness before routing traffic to it.

The recovered workspace keeps its original workspace UUID, immutable references and usage history. Worker heartbeats are cleared. Queued/running jobs become interrupted; cancellation requests become cancelled. None are automatically replayed. Inspect upstream state before explicitly submitting replacement work. The restore adds audit events and records its result in `shared-restore.json` in the new home.

<a id="shared-workspace-recovery--recovery-boundaries-and-retention"></a>

### Recovery boundaries and retention

This is an application-level logical backup, not PostgreSQL physical backup or point-in-time recovery. It does not capture database roles, infrastructure, secret files, environment variables or unrelated bucket contents. User-supplied records can themselves contain sensitive values. Apply your managed database backup/PITR, key custody and offsite policies separately.

Every registered model and remote-import input reference is retained, including references from terminal jobs. Source object cleanup cannot delete independent recovery copies. Bucket lifecycle rules, version deletion or lost encryption keys can still invalidate a backup; protect the vault and verify it periodically.

Current bounds are 16 GiB of logical metadata, 8 MiB per JSONL line, ten million rows, 250,000 distinct objects and a 64 MiB completion manifest. Individual model bundles retain the [shared-artifact limits](shared-artifacts-guide.md#s3-artifacts). The operator timeout is positive and at most 24 hours; individual SDK requests also have transport timeouts. Server-side copies incur storage/request charges and may incur transfer charges. SDK contract tests do not establish paid-account behavior or deployed cloud-storage acceptance.

Implementation references: [PostgreSQL repeatable-read snapshots](https://www.postgresql.org/docs/18/transaction-iso.html#XACT-REPEATABLE-READ), [Psycopg server-side cursors](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#server-side-cursors), [Boto3 managed object copying](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/copy.html).
