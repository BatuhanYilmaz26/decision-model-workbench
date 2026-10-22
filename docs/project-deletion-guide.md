# Delete an archived project's database records

[All guides](README.md) · [Archive and restore](project-lifecycle-guide.md) · [Application guide](app-guide.md)

Use an owner-reviewed deletion plan to remove a project's policy, datasets,
review revisions, synthetic requests/results, model registry entries and job
records from the application database. Unreferenced dataset content is removed;
content still needed by another project is preserved. A linked schema-assistant
intake and its completed job are included.

This action cannot be undone through the application. It retains a deletion
receipt and minimal quota counters. You choose whether historical audit events
remain. Read the preview and save any external resource information you need
before applying it.

**Database deletion does not erase model files, node caches, downloads, exports,
logs, backups or provider storage.** It does not stop compute or storage charges,
wipe SQLite free pages, PostgreSQL old row versions, transaction logs or storage
snapshots, or recall bytes already downloaded. Apply your separate storage and
backup policies to those copies. Existing streams and resident processes can
retain data until they finish or shut down.

## Prepare the project

1. Use a workspace owner account. Other application roles cannot delete records
   or read deletion receipts.
2. [Archive the project](project-lifecycle-guide.md). Finish/cancel jobs, observe
   cloud compute and settle uncertain calls first. Deletion rechecks those
   conditions and waits for fresh worker heartbeats to release the project.
3. If other projects name this project as their policy parent, retain it or
   delete dependent projects first. The application refuses to break their
   ancestry and release-history references.
4. Choose the minimum time archived before deletion. The default is **30 days**;
   owners can explicitly choose **0** for immediate eligibility or up to 3,650
   days. Restoring and archiving again restarts that interval.
5. Decide whether to retain or erase historical audit events. Retained events
   can include actor identifiers and free-form release notes. Even when you
   erase them, an administrative deletion event and receipt remain.

Keep any required backups and exported audit history in appropriately protected
storage. Keeping a backup also keeps its original data; deleting the current
database records does not rewrite an earlier backup.

## Delete through the browser

Select the archived project and open **Permanently delete project data**.
Set **Minimum days archived before deletion** and **Historical audit events**,
then choose **Preview permanent deletion**.

The preview shows the project's record versions to unlink, shared versions
within your workspace, metadata/job counts, retained quota-counter count and
the eligibility time in UTC. These are logical project counts, not a promise
of freed filesystem space. The preview does not reveal whether another tenant
has matching content; shared content is protected regardless.

Save the reviewed plan if you need a private record. Once eligible, type the
full project ID, acknowledge the plan and external-copy responsibilities, then
choose **Delete project data permanently**. Confirmation is bound to that
preview's hash. Changing the settings requires a new preview.

Save the deletion receipt, then refresh the workspace. The project disappears
from the normal selector. To retrieve its receipt later, open **Deleted project
receipts** in the sidebar, choose **Find a deletion receipt**, enter its full
ID and select **Look up receipt**. Owners can also page through retained audit
events there. Saved plans and receipts use the private `.dmtdelete` extension;
keep them outside public folders.

## Use the API

Use your normal authenticated owner bearer token and replace `PROJECT_ID` with
the exact project ID. Review the inputs before sending the final request.

```http
POST /v1/projects/PROJECT_ID/deletion/preview
Content-Type: application/json
Authorization: Bearer YOUR_WORKSPACE_TOKEN

{"audit_mode":"retain","min_archived_days":30}
```

The response contains `plan`, `plan_sha256` and `eligible`. Apply that exact plan
with the same settings and a full-ID confirmation:

```http
POST /v1/projects/PROJECT_ID/deletion
Content-Type: application/json
Authorization: Bearer YOUR_WORKSPACE_TOKEN

{"audit_mode":"retain","min_archived_days":30,"plan_sha256":"YOUR_REVIEWED_SHA256","confirmation":"PROJECT_ID"}
```

Retrieve a receipt or retained audit page:

```text
GET /v1/projects/PROJECT_ID/deletion
GET /v1/projects/PROJECT_ID/deletion/audit?limit=100&after_id=0
```

Use `next_after_id` while `has_more` is true. A stale plan, unelapsed retention
interval or live dependency returns HTTP 409. Wrong confirmation returns 400;
unapproved roles return 403. Normal project routes return 404 after deletion.

If a response is lost, retrieve the receipt by project ID and compare its
`plan_sha256`. Replaying the same confirmed plan and settings returns the same
receipt; different inputs are refused. Retrying an old deleted schema-intake
request does not recreate it. A genuinely new intake needs a new request key
and remains subject to the original quota window.

## Understand what remains

Minimal accounting entries preserve rolling request limits for hosted decision
profiles, generation teachers, schema suggestions and shared cloud accounts.
They keep hashed scope identities, original reservation times and relevant
estimated/reported cost values. They contain no prompts, examples, question
schemas or model answers. They represent quota reservations and planning data,
not independently verified invoices.

The receipt keeps opaque project/artifact IDs, counts, hashes, the selected audit
mode, deletion time and the owner's actor ID. These retained administrative
records are intentional; deletion is not an anonymization guarantee.

Database changes, quota preservation and receipt publication commit together.
An error rolls back the transaction. A terminal deletion marker prevents stale
requests from writing project records again. File and provider cleanup is
separate: [shared-object retention](shared-object-retention-guide.md) can later
select unreferenced versions, while [local transfer cleanup](local-stage-cleanup-guide.md)
only handles abandoned temporary transfers and preserves final model caches.
Use [cache retention](cache-retention-guide.md) to reclaim owned final caches
after stopping the selected shared-workspace node.

## Capacity, upgrades and recovery

Previews refuse oversized inventories: up to 1,000 collection roots, 200,000
inventory rows/content identities and a 64 MiB metadata budget, with conservative
size preflights. Reaching a bound does not partially delete the project. Review
capacity with the operator instead of bypassing the preview or editing live
database tables.

Stop all services and back up before upgrading. This release uses SQLite schema
**11** and PostgreSQL workspace protocol **14**; older nodes must not remain
running. All nodes need the same application version so deletion markers and
retained quota accounting are enforced consistently.

Local backup verification checks deletion receipts, retained accounting and
deleted intake aliases. Recovery preserves those markers. Restoring an older,
pre-deletion backup can restore the original data and therefore requires its
own retention decision. For shared workspaces, database and object-store
recovery must be coordinated separately.
