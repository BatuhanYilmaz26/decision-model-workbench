# Reclaim inactive model caches on a workspace node

[All guides](README.md) · [Shared artifacts](shared-artifacts-guide.md) · [Temporary transfers](local-stage-cleanup-guide.md)

Use cache cleanup to free disk space on a PostgreSQL/S3 workspace node without
deleting its model registry or shared objects. Stop that node's services, preview
the eligible directories, then apply the exact reviewed plan. Restart the node
afterward. Other nodes keep their own caches.
Predictions on those nodes can continue using already available caches; new
releases, publications and remote-intake submissions may wait or return a busy
response while maintenance holds the workspace's shared-storage guard.

The command preserves caches for currently released models, pending remote
intakes, recent entries and directories whose ownership cannot be established.
It refuses to run while the node's API, worker, cache readers or model processes
hold their application leases. It does not evict models from a local-only SQLite
workspace, where the filesystem may contain the only model copy.

## Before you start

- Use an operator environment with the [PostgreSQL and shared-storage setup](shared-artifacts-guide.md).
  Supply its existing `DMT_DATABASE_URL`, database schema and S3 configuration.
  The command uses operator database/storage access, not a browser workspace key.
- Stop the API, worker and cache warmers using the selected local workspace.
  Wait for model subprocesses to exit. Stop any custom scripts reading its files.
- Upgrade participating services together. Older application versions and custom
  scripts that bypass cache-use leases cannot be detected reliably. Embedded
  readers must hold `decision_workbench.platform.cache_usage.use(home)` while
  consuming cache files, not just while obtaining a path.
  This release advances the PostgreSQL workspace protocol to **14** so older
  versions refuse to reopen the upgraded database. Stop all old nodes before
  upgrading; already-running old processes cannot acquire the new file leases.
- Keep appropriate database and object backups. Cache eviction does not replace
  [backup and recovery](recovery-guide.md) or repair a damaged shared object store.

New verified publications and downloads record their ownership and last-use
time outside the model directory. Older registered models can be selected only
after their complete local files match the registered immutable shared manifest.
Unmarked directories with no registry entry are preserved for manual review.

## Preview and apply a retention policy

The default policy selects inactive caches unused or unmodified for at least
86,400 seconds. This is an explicit operator command, not an automatic scheduler.
Use your actual node workspace path and configured schema:

```bash
domain-platform preview-cache-cleanup --home data/api-cache --database-schema decision_workbench --min-idle-seconds 86400
```

Review `caches`, `bytes`, `preserved_entries`, the candidate identities and
`plan_sha256`. The preview includes local filesystem paths and private model
identifiers. Keep it in protected operator storage.

Apply with the same retention settings and the returned SHA-256:

```bash
domain-platform apply-cache-cleanup --home data/api-cache --database-schema decision_workbench --min-idle-seconds 86400 --plan-sha256 YOUR_REVIEWED_SHA256
```

Both commands default to a 300-second inventory budget. Set `--timeout` from
1 to 3,600 seconds if a large local cache needs a different budget. A plan refuses
more than 10,000 entries per model/reference inventory, 100,000 total selected filesystem
entries or 32 MiB of plan metadata. Refusals do not partially apply a new plan.

The application checks local file hashes against the pinned manifest. Before
evicting a registered model, it also reads the remote manifest and checks that
each recovery blob exists with the expected size. S3 uses
[`HeadObject`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html),
which returns metadata without downloading model weights and requires
`s3:GetObject`. These checks can incur storage request charges. They are
availability checks, not a fresh cryptographic validation of every remote blob;
normal cache restoration verifies the downloaded content hashes. External bucket
changes after preview can still affect later restoration.

A stale plan, new release, changed intake or changed cache requires a new preview.
Released model caches remain pinned even when their project is archived. Release
a different model or delete the project's database records before reclaiming
that entry. This operation never changes a release itself.

## Recover from an interrupted cleanup

Selected directories are first renamed into operation-specific quarantine
directories within the same cache parent. The operation then unlinks only the
reviewed ordinary files. Receipts remain under the workspace's private
`.dmt-cache-cleanups` directory.

If cleanup reports a partial operation, retain its operation ID and original
plan hash, then run:

```bash
domain-platform resume-cache-cleanup --home data/api-cache --database-schema decision_workbench --operation-id YOUR_OPERATION_ID --plan-sha256 YOUR_REVIEWED_SHA256
```

Resume checks ownership and removes only unchanged remnants in that operation's
quarantine. A newly warmed cache at the original path is preserved. Caches not
yet quarantined are left alone and need a fresh preview. Inspect `completed_caches`
and `not_started` in the result; completion of a resumed operation does not mean
every original candidate was removed. Unexpected or modified quarantine content
is refused for operator investigation.

Do not remove lock files to bypass a busy refusal. Stop the owning service or
allow it to exit normally. OS leases release on process death; maintenance can
discard abandoned lease records without trusting a process ID.

## Use the model again

Restart the services with the same configuration. Registry entries, evaluations,
release history and shared objects remain. A small missing cache is verified and
restored when the application needs it. Large models still require the explicit
[cache-warming command](shared-artifacts-guide.md#train-release-and-warm-a-cache)
on each serving node; an API request does not silently download an unlimited
checkpoint. Warming updates the cache's last-use record.

After [project database deletion](project-deletion-guide.md), an owned orphan
cache can be reclaimed using its pinned local manifest even if the corresponding
remote orphan objects were already removed. Older unmarked orphans remain
outside automatic cleanup. Shared objects have their own
[retention workflow](shared-object-retention-guide.md).

This workflow does not remove Hugging Face caches, virtual environments, job
logs, backups, model environments or arbitrary user files. It is not secure
media erasure. Use [temporary-transfer cleanup](local-stage-cleanup-guide.md) for
abandoned downloads and intake staging directories.
