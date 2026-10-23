# Reclaim storage deliberately

[Documentation](README.md) · [Project overview](../README.md)

Local model caches, temporary transfer stages and remote object versions have separate ownership and retention rules. Choose one operation below and inspect its preview before applying a plan. Local deletion does not establish cloud erasure.

## Choose a section

- [Inactive model caches](#model-caches)

- [Abandoned local transfers](#transfer-stages)

- [Unreferenced shared object versions](#shared-object-versions)

<a id="model-caches"></a>

## Inactive model caches

<a id="model-caches--reclaim-inactive-model-caches-on-a-workspace-node"></a>

Use cache cleanup to free disk space on a PostgreSQL workspace node using shared
S3, Azure Blob or GCS artifacts without
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

<a id="model-caches--before-you-start"></a>

### Before you start

- Use an operator environment with the [PostgreSQL and shared-storage setup](shared-artifacts-guide.md#s3-artifacts).
  Supply its existing `DMT_DATABASE_URL`, database schema and selected storage configuration.
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
  [shared backup and recovery](recovery-guide.md#shared-workspace-recovery) or repair a damaged shared object store.

New verified publications and downloads record their ownership and last-use
time outside the model directory. Older registered models can be selected only
after their complete local files match the registered immutable shared manifest.
Unmarked directories with no registry entry are preserved for manual review.

<a id="model-caches--preview-and-apply-a-retention-policy"></a>

### Preview and apply a retention policy

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
`s3:GetObject`. Azure and GCS use their configured transports' version metadata;
see [Azure permissions](shared-artifacts-guide.md#azure-blob) or [GCS permissions](shared-artifacts-guide.md#google-cloud-storage).
These checks can incur storage request charges. They are
availability checks, not a fresh cryptographic validation of every remote blob;
normal cache restoration verifies the downloaded content hashes. External bucket
changes after preview can still affect later restoration.

A stale plan, new release, changed intake or changed cache requires a new preview.
Released model caches remain pinned even when their project is archived. Release
a different model or delete the project's database records before reclaiming
that entry. This operation never changes a release itself.

<a id="model-caches--recover-from-an-interrupted-cleanup"></a>

### Recover from an interrupted cleanup

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

<a id="model-caches--use-the-model-again"></a>

### Use the model again

Restart the services with the same configuration. Registry entries, evaluations,
release history and shared objects remain. A small missing cache is verified and
restored when the application needs it. Large models still require the explicit
[cache-warming command](shared-artifacts-guide.md#s3-artifacts--train-release-and-warm-a-cache)
on each serving node; an API request does not silently download an unlimited
checkpoint. Warming updates the cache's last-use record.

After [project database deletion](project-lifecycle-guide.md#database-deletion), an owned orphan
cache can be reclaimed using its pinned local manifest even if the corresponding
remote orphan objects were already removed. Older unmarked orphans remain
outside automatic cleanup. Shared objects have their own
[retention workflow](storage-maintenance-guide.md#shared-object-versions).

This workflow does not remove Hugging Face caches, virtual environments, job
logs, backups, model environments or arbitrary user files. It is not secure
media erasure. Use [temporary-transfer cleanup](storage-maintenance-guide.md#transfer-stages) for
abandoned downloads and intake staging directories.

<a id="transfer-stages"></a>

## Abandoned local transfers

<a id="transfer-stages--reclaim-abandoned-local-transfer-stages"></a>

Use these operator commands on a personal workspace or an individual shared
workspace node to remove old, inactive **owned transfer stages**. They cover
partial artifact-cache downloads and remote-export intake copies created by the
current application. They make no cloud calls and need no model download or API
credential.

The application normally removes its temporary transfer files when a transfer
finishes or fails. If its process dies, an ownership record remains beside the
partial directory. An OS lease distinguishes a live transfer from an abandoned
one; PID age or directory name alone is not treated as proof of abandonment.

<a id="transfer-stages--choose-the-affected-node"></a>

### Choose the affected node

Use the node's actual workspace/cache directory as `--home`, for example
`data/local-workspace`, `data/api-cache` or `data/worker-cache`. It must contain
the workbench's SQLite or PostgreSQL binding marker. A PostgreSQL node can be
inspected without connecting to its database. Run under an operator account
permitted to read and remove that node's temporary files.

Use an ordinary local filesystem with working OS locks. Linked workspace paths,
symlinks/junctions and special files are refused. Network-filesystem locking and
platform access controls require deployment qualification.

Only owned stages directly under `artifacts/` and `incoming/` are considered.
The tool preserves registered UUID model directories, final intake directories,
Hugging Face/model-package caches, uploaded data, jobs and unmarked legacy
temporary directories. It does not evict a usable registered model or erase
project data.

<a id="transfer-stages--preview-the-exact-files"></a>

### Preview the exact files

From the installed platform environment:

```bash
domain-platform preview-local-stage-cleanup --home data/local-workspace --min-age-seconds 86400
```

The JSON result shows candidate directories and exact file metadata, logical
bytes, active stages, unmanaged entries and `plan_sha256`. Save large output to
a private file under `data/` for review. Paths can reveal deployment information;
keep the output and receipts out of the public repository.

The default retention interval is one day after the stage's latest recorded file
or directory modification. A live lease always excludes a stage, regardless of
age. Use a different nonnegative interval when your policy requires it. An
explicit `--min-age-seconds 0` is useful for reviewing remaining owned files after
an interrupted cleanup; it still cannot select an actively leased transfer.

Limits bound the number of records, directory entries and files inspected.
Malformed/oversized ownership records, replaced directories and linked content
refuse cleanup. Unmarked entries are counted and preserved; older application
versions did not provide the ownership evidence needed to remove them safely.

<a id="transfer-stages--apply-the-reviewed-plan"></a>

### Apply the reviewed plan

Use the same node, directory and retention interval:

```bash
domain-platform apply-local-stage-cleanup --home data/local-workspace --min-age-seconds 86400 --plan-sha256 YOUR_REVIEWED_PLAN_SHA256
```

The command recomputes the plan before deleting anything, then reacquires each
candidate's lease and checks its ownership and filesystem identity. A stale plan
is refused. Files are unlinked from the reviewed inventory and empty directories
are removed individually; deletion does not recursively follow unknown content.
Other active transfers and registered caches can remain in use.

A private receipt is written under the node's `.dmt-stage-cleanups/` directory
before removal begins. Successful output includes its operation ID and completed
stage count. Sizes are logical file sizes, not a guarantee of immediately free
filesystem blocks.

<a id="transfer-stages--handle-an-interruption"></a>

### Handle an interruption

If permissions, sharing conflicts or unexpected changes stop cleanup, inspect
the private receipt named in the error. Already removed files are not restored;
the receipt records a partial operation and remaining ownership records are
retained. Correct the underlying issue, run a **new preview** of the remaining
owned stages, then apply that newly reviewed hash. A zero retention interval can
be selected explicitly when retrying freshly modified remnants.

Do not manufacture ownership records for an unmarked directory or replace its
record with a model directory name. For legacy/unmanaged content, stop the
relevant services and follow a separately reviewed filesystem procedure. This
tool intentionally supplies no blanket recursive-delete command.

Local stage receipts are operational records, not tamper-proof enterprise audit
storage. Retain them separately when needed; the registered workspace backup
does not include these temporary-file receipts. Project/data deletion, registered
model-cache eviction and coordinated metadata/object recovery remain separate
policies. Use [shared-object retention](storage-maintenance-guide.md#shared-object-versions) for
unreferenced S3 versions, Azure blob versions or GCS generations rather than treating a local deletion as cloud erasure.

<a id="transfer-stages--run-the-local-checks"></a>

### Run the local checks

```bash
python -m unittest discover -s tests -p test_local_stage_cleanup.py -v
python -m unittest discover -s tests -p test_shared_artifacts.py -v
python -m unittest discover -s tests -p test_remote_admission.py -v
```

The local stage suite uses tiny fictional files, actual OS leases and a producer
subprocess that is killed before cleanup. Other integration suites distinguish
their database/SDK fixtures and optional PostgreSQL requirements. No pretrained
weights or paid provider calls are needed.

<a id="shared-object-versions"></a>

## Unreferenced shared object versions

<a id="shared-object-versions--clean-up-unreferenced-shared-object-versions"></a>

Use an operator preview/apply workflow to remove old S3 or Azure blob versions, or GCS generations, that are
not needed by the current workspace's model registry or recorded remote imports.
The collector retains every registered model, including inactive versions, and
every recorded admission input, including failed or cancelled jobs. It also
preserves young manifests and the older deduplicated blobs they reference.

This command does not delete projects, dataset history, registered models, local
caches or cloud training volumes. It does not enumerate all historical object
versions. Treat those as separate retention policies rather than assuming this
collector erases all copies of domain data.

<a id="shared-object-versions--prepare-the-workspace-and-operator-identity"></a>

### Prepare the workspace and operator identity

For Azure, configure [versioned Blob Storage and its dedicated cleanup identity](shared-artifacts-guide.md#azure-blob)
before using the preview/apply commands below. Azure deletion obeys soft-delete,
legal-hold and immutability policies; it is not proof of immediate physical erasure
or removal of storage charges.

For GCS, configure the [versioned bucket and dedicated operator permissions](shared-artifacts-guide.md#google-cloud-storage).
Inventory and deletion pin exact generations; cleanup also covers old upload staging
objects. Soft-delete retention remains separate. The commands below use the selected
workspace transport; the following prerequisites are specific to S3.

Use the [PostgreSQL/shared S3 configuration](shared-artifacts-guide.md#s3-artifacts) with
**S3 bucket versioning Enabled**. New cleanup plans refuse unversioned or suspended
buckets. Deletion targets exact non-null version IDs, with an ETag condition,
instead of deleting whichever replacement happens to occupy the same key.
[S3 version deletion](https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html).

This release uses PostgreSQL workspace protocol **14**; personal SQLite uses
schema **11**. Stop all API and worker nodes before upgrading them
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

<a id="shared-object-versions--preview-the-candidates"></a>

### Preview the candidates

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

<a id="shared-object-versions--apply-the-reviewed-plan"></a>

### Apply the reviewed plan

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

<a id="shared-object-versions--reconcile-an-interrupted-cleanup"></a>

### Reconcile an interrupted cleanup

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

<a id="shared-object-versions--retention-and-recovery-boundaries"></a>

### Retention and recovery boundaries

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
