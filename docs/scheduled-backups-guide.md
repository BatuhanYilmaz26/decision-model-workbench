# Schedule encrypted backups and retain verified copies

Run encrypted captures on a fixed interval, retain their trusted receipts in a separate catalog, and prune older owned bundles after verifying the copies you will keep. This is a single-host operator service; it does not invoke training, teacher or provider jobs.

## Configure private storage

Install the [CPU platform profile](../requirements/README.md) and generate a [recovery key](encrypted-recovery-guide.md#prepare-private-storage-and-a-recovery-key). Use an existing workspace, a **new dedicated vault**, a **new catalog** and a **new policy file**:

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

## Run once or continuously

Check the configuration with one due-run invocation:

```bash
domain-platform backup-run --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey
```

The first capture runs immediately. Later invocations return `captured: false` and `next_at` until the interval or retry delay elapses. A successful capture returns its run ID, destination and durable receipt, including `encrypted_manifest_sha256`. Retain these receipts separately; they are the trust anchors for [verification and restore](encrypted-recovery-guide.md#verify-before-recovery).

Start the persistent operator service:

```bash
domain-platform backup-service --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey
```

The service writes JSON results to standard output. It serializes capture/pruning through a catalog operation lock and permits one persistent backup service per workspace. An overlapping service or operation is refused. Ctrl+C requests a graceful stop after the active operation; forced termination is handled on the next startup. Use your operating system's approved process supervisor for restart and log management. This command does not install a Windows service or systemd unit.

Missed intervals produce one fresh capture, not a burst of historical catch-up copies. The schedule uses the host's wall clock; keep that clock synchronized. A backward clock change can postpone the next due time. Captures can run while the source API remains available, but avoid external changes/deletion of registered exports during capture.

## Inspect receipts and failures

```bash
domain-platform backups-status --config .internal/daily.dmtplan --key-file .internal/backup-key.dmtkey
```

Status includes policy settings, catalog head, run states/receipts and the current retention plan. The separate SQLite catalog contains authenticated, chained immutable events. It records the prepared receipt **before publication**, then records completion after the bundle is published.

When a capture process dies, the next due-run invocation first reconciles its unfinished record while holding the operation lock. A complete bundle with a durable prepared receipt is fully verified against that receipt before it is accepted. An unpublished capture becomes interrupted; a changed/invalid returned bundle becomes failed. The service never invents a trusted receipt by hashing an untracked bundle. It does not replay copied training/teacher jobs.

Protect and independently back up the catalog and policy as well as the key. For SQLite catalog copies, use a consistent online backup or stop all catalog operations before copying the database and its required journal files. Catalog authentication detects changed events, sequence gaps and invalid transitions; it is not an external anti-rollback or WORM guarantee. Reverting a catalog can leave newer vault entries unmanaged, which blocks pruning. [SQLite online backup](https://www.sqlite.org/backup.html)

Service errors are reported locally as JSON; capture/deletion transitions remain in the private catalog. External alert delivery and enterprise telemetry require deployment integration. Sensitive temporary plaintext uses the [encrypted recovery storage rules](encrypted-recovery-guide.md#capacity-and-encryption-format).

Use the [signed backup health monitor](backup-monitoring-guide.md) for publisher heartbeats, capture freshness/failure checks, scoped application incidents and optional readiness requirements. The API receives public verification assets; recovery and signing private keys stay with the operator.

## Review and apply retention

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

## Capacity and deployment checks

The local catalog limits are 100,000 events, 512 MiB of catalog database and 10,000 vault entries. A capture reserves space for its begin/prepared/completed events before it starts; retention checks event capacity before deletion. Archive and replace the policy/catalog before reaching these bounds. The service does not erase old receipts to reclaim catalog capacity.

Use local filesystems with working OS file locks and SQLite durability. Distributed/NFS scheduling, cloud replication, enterprise key custody, external alerts, power-loss behavior and large-scale recovery require separate qualification. Verify your restart supervisor, resource use, due/failure behavior and recovery objectives on the intended host. [Python SQLite transactions](https://docs.python.org/3.13/library/sqlite3.html), [Windows file locking](https://docs.python.org/3.13/library/msvcrt.html), [POSIX file locking](https://docs.python.org/3.12/library/fcntl.html)

[Primary source metadata](../research/provenance/scheduled-backup-sources-2026-10-14.json) records the interfaces used by the scheduler and catalog.
