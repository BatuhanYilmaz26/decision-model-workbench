# Monitor backup freshness and failures

Publish a signed summary from the authenticated backup catalog, then show backup health and scoped incidents in the application. The API verifies a public key; it does not receive the backup decryption key, signing private key or catalog receipts.

Use this with the [scheduled backup service](scheduled-backups-guide.md). Signatures establish the configured publisher's identity and protect the summary against modification. They do not establish backup quality or replace complete encrypted verification and recovery drills. [Ed25519 signing and verification](https://cryptography.io/en/stable/hazmat/primitives/asymmetric/ed25519/)

## Create a separate monitoring key

Choose new private signing and public verification files:

```bash
domain-platform create-backup-monitor-key --private-file .internal/backup-monitor.dmtkey --public-file .internal/api-monitor/backup-monitor.dmtpub
```

The signing key uses a separate versioned binary format and cannot substitute for a recovery decryption key. Keep it with the backup operator. The API-visible monitoring directory should contain only public verification assets, its monitor profile and health snapshot. Restrict signing/recovery key permissions and keep those private files out of API/container mounts.

## Configure the API trust profile

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

## Publish and maintain heartbeats

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

## Read health and incidents

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

Health measures recorded capture freshness, attempt state and basic availability/manifest identity/size of the latest retained bundle. It does not continually decrypt every model file or prove current payload integrity. Use [complete encrypted verification](encrypted-recovery-guide.md#verify-before-recovery) and known-decision recovery probes on your planned schedule. Host clocks must be synchronized. Signatures and freshness bounds are not a persistent anti-replay/WORM witness across restarts.

Local incidents and JSON service output are available now. External notifications, central telemetry, enterprise key custody, cloud supervision and workload-scale acceptance require separate integration; no messages are sent by this monitor.

## Upgrade and container setup

Back up and stop all API/browser/worker services before upgrading. Schema **7** protects backup-alert semantics; older applications supporting only schema 6 refuse the upgraded workspace. Do not mix versions or lower the schema number manually. Recovery supports schema-3/4/5/6/7 backups.

The Compose API accepts `DMT_BACKUP_MONITOR_PROFILE`. Mount the public-only monitoring directory read-only so atomic host file replacement remains visible. Run the backup operator separately with approved private mounts. Both processes need matching workspace paths for the binding; configure policies inside the intended namespace. Qualify the complete container/supervisor setup before relying on its alerts.

[Primary source metadata](../research/provenance/backup-monitor-sources-2026-10-14.json) records the signing interfaces used here.
