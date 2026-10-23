# Reclaim abandoned local transfer stages

[All guides](README.md) · [Shared artifact caches](shared-artifacts-guide.md) · [Shared-object retention](shared-object-retention-guide.md)

Use these operator commands on a personal workspace or an individual shared
workspace node to remove old, inactive **owned transfer stages**. They cover
partial artifact-cache downloads and remote-export intake copies created by the
current application. They make no cloud calls and need no model download or API
credential.

The application normally removes its temporary transfer files when a transfer
finishes or fails. If its process dies, an ownership record remains beside the
partial directory. An OS lease distinguishes a live transfer from an abandoned
one; PID age or directory name alone is not treated as proof of abandonment.

## Choose the affected node

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

## Preview the exact files

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

## Apply the reviewed plan

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

## Handle an interruption

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
policies. Use [shared-object retention](shared-object-retention-guide.md) for
unreferenced S3 versions, Azure blob versions or GCS generations rather than treating a local deletion as cloud erasure.

## Run the local checks

```bash
python -m unittest discover -s tests -p test_local_stage_cleanup.py -v
python -m unittest discover -s tests -p test_shared_artifacts.py -v
python -m unittest discover -s tests -p test_remote_admission.py -v
```

The local stage suite uses tiny fictional files, actual OS leases and a producer
subprocess that is killed before cleanup. Other integration suites distinguish
their database/SDK fixtures and optional PostgreSQL requirements. No pretrained
weights or paid provider calls are needed.
