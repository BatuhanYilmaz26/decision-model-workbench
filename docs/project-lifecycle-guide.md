# Manage projects and policy changes

<a id="archive-or-restore-a-project"></a>

[Documentation](README.md) · [Project overview](../README.md)

Choose the operation you need: pause a project, create a successor policy, or permanently delete archived database records. These operations have different consequences; archiving does not delete data or stop upstream compute.

## Choose a section

- [Archive and restore](#archive-restore)

- [Create a policy successor](#policy-successors)

- [Delete archived project records](#database-deletion)

<a id="archive-restore"></a>

## Archive and restore

<a id="archive-restore--archive-or-restore-a-project"></a>

Archive a project when you want to pause its use without deleting its data.
An archived project accepts no new dataset changes, jobs, releases or predictions
through the application API. Its original policy, datasets, model versions,
audit history and provider usage accounting remain stored.

Archival is reversible. Restoring the project resumes access to its existing
released model; it does not train, activate a different version or repeat a paid
request. Archived projects remain in the project selector so owners can restore
them. Other projects continue operating normally.

Archival checks at most 1,000 recorded cloud runs per project, and lifecycle
history permits 10,000 transitions. Larger inventories are refused for operator
review rather than partially archived.

<a id="archive-restore--prepare-the-project"></a>

### Prepare the project

Use a workspace owner account. Editors, reviewers and inference credentials
cannot archive or restore projects.

1. Finish or cancel queued/running jobs and wait for cancellation to complete.
2. For approved cloud runs, observe their current status and stop unfinished
   compute through the provider controls. A local job finishing does not prove
   that rented compute stopped.
3. Reconcile unsettled hosted calls and settle uncertain synthetic-generation
   attempts using their existing history controls.

The transition checks these conditions transactionally and refuses unresolved
work. It makes no provider calls and does not cancel compute, delete provider
storage or stop charges itself. A cloud run approved but never submitted can
remain ready; restoring the project permits its normal approved controls again.

<a id="archive-restore--use-the-browser-application"></a>

### Use the browser application

Select the project and open **Project lifecycle** above its usual workflow.
Copy the displayed project ID into **Confirm project ID**, read and select the
acknowledgment, then choose **Archive project**.

The archived view explains why the normal screens are unavailable. Owners can
use the same confirmation to choose **Restore project**. Other roles see a
message directing them to an owner. If another owner changed the lifecycle,
refresh and confirm the current state before trying again.

<a id="archive-restore--use-the-api"></a>

### Use the API

Send your normal bearer token. Fetch the current revision first:

```text
GET /v1/projects/PROJECT_ID/lifecycle
```

A project without previous transitions reports `state: "active"` and
`revision: 0`. To archive it:

```http
PUT /v1/projects/PROJECT_ID/lifecycle
Content-Type: application/json
Authorization: Bearer YOUR_WORKSPACE_TOKEN

{"state":"archived","expected_revision":0,"confirmation":"PROJECT_ID"}
```

Replace both occurrences of `PROJECT_ID` with the same full project ID. To
restore, fetch the latest lifecycle revision and send `state: "active"` with
that revision. The response includes the new state, revision and history hash.
Stale revisions return HTTP 409; incorrect confirmation returns HTTP 400 and
unauthorized roles return HTTP 403. A different tenant cannot access the project.

Normal project API routes return HTTP 409 while archived. The project list and
its lifecycle endpoint remain available. Changes to the archived project's own records are
checked again before committing; a CPU prediction still in progress is refused
if its project was archived before its final state check. Already streamed
downloads can finish. Resident model processes retain their normal idle shutdown.

<a id="archive-restore--upgrade-and-recover"></a>

### Upgrade and recover

Back up the workspace and stop every API, worker and browser service before
upgrading. This release uses SQLite schema **11** and PostgreSQL workspace
protocol **14**. Restart all nodes with the same application version; mixed
versions are unsupported. Existing projects begin active without changing their
policy, model, dataset or release identities.

Lifecycle transitions form an immutable revision/hash chain. Local backup
verification checks it, and recovery preserves archived states while recording
the operator restore event. [PostgreSQL/shared-storage recovery](shared-artifacts-guide.md#s3-artifacts)
still needs a coordinated database/object procedure.

Use [permanent database deletion](project-lifecycle-guide.md#database-deletion) when you intend to remove archived project records. Archival itself is not deletion, anonymization, secure erasure or a retention expiry.
It does not remove backups, exports, logs, model caches, external training
volumes or data previously sent to a provider. Use your separate retention and
deletion procedures for those copies. The [local transfer cleanup](storage-maintenance-guide.md#transfer-stages)
and [shared-object cleanup](storage-maintenance-guide.md#shared-object-versions) tools also preserve
registered models belonging to archived projects.

<a id="policy-successors"></a>

## Create a policy successor

<a id="policy-successors--create-and-operate-a-policy-successor"></a>

Create a new project version when your decision rules, outcome schema or frozen acceptance criteria need to change. The successor starts from an owner-reviewed draft of the current domain, questions and optional criteria. The original project's records, models and active release remain available under its existing API routes.

Use this workflow after clarifying a policy exception, adding an outcome, or deciding that different error-cost limits are needed. If evaluation findings influenced the change, obtain fresh independent evaluation gold; a new project identifier does not make previously inspected examples unseen.

<a id="policy-successors--review-the-successor-in-the-browser"></a>

### Review the successor in the browser

1. Connect with an owner credential and select the source project. Owners use the application's `admin` role; editors and reviewers cannot create policy successors.
2. Open **Create a policy successor**, then enable **Plan a policy successor**.
3. Review the copied domain policy, data requirements and question schema. Give the successor a useful name and explicitly edit any changed instructions, question IDs or outcomes in the schema JSON.
4. Choose whether to include acceptance criteria. When the parent has a saved policy, its exact criteria fill the draft. Review accuracy, calibration, support, Vega applicability and domain error-cost gates against the successor's schema. Update cost and critical-label outcome IDs when changing outcomes; incompatible criteria are refused.
5. Record the reason for the change, approve the displayed policy/schema/criteria, and select **Create reviewed successor**.

The browser opens the new project on the first history page and confirms creation. Its policy version is the parent's version plus one. Different successors may branch from the same parent; their version numbers describe ancestry rather than a global sequence.

Acceptance criteria are saved atomically with the project when included. They remain editable through **Training jobs** until the successor's first training, hosted evaluation or remote study submission freezes them. An unchecked criteria option creates no acceptance policy; configure one before starting a study. The owner's confirmation and source schema/policy identities are retained with the successor.

Changing the parent acceptance policy invalidates a draft based on its previous version. Refresh and review the new draft before approving it. **Start a fresh successor draft** discards unsaved edits and starts a new request; it is useful when you intentionally want another branch.

<a id="policy-successors--add-and-evaluate-data-for-the-new-policy"></a>

### Add and evaluate data for the new policy

Download the successor's CSV format or use its direct-example and upload controls. Annotate and review each example against the new schema. Existing gold, review approvals, synthetic candidates, holdouts, applicability collections, taxonomy aliases, trained weights and releases are not transferred automatically.

Taxonomy-selection examples and known relatives retain their training-only restriction across project ancestry. Other prior records' split roles are not automatically inherited: establish independent evidence and reserve new validation, calibration and locked test roles explicitly. Follow [data collection](data-guide.md#data-contract), [temporal reservations](reservation-guide.md#time-boundaries), [semantic audits](reservation-guide.md#semantic-relatives) and [evaluation](evaluation-guide.md#evaluation).

Train and evaluate a new model for the successor. Keep callers on the parent project until you have accepted the successor's model and changed your application routing deliberately. Releasing a successor model activates only that successor project's endpoint; it does not redirect the parent. Retain the old endpoint for rollback and compare decisions using your domain's acceptance policy.

<a id="policy-successors--create-a-successor-through-the-api"></a>

### Create a successor through the API

Read the source project and its acceptance policy, then submit their displayed identifiers. Use one stable `request_key` for retries of the same reviewed request:

```python
import copy
import os
import uuid
import httpx

parent_id = os.environ["DMT_PARENT_PROJECT_ID"]
with httpx.Client(
    base_url=os.environ["DMT_API_URL"],
    headers={"Authorization": "Bearer " + os.environ["DMT_WORKSPACE_TOKEN"]},
    timeout=30,
) as client:
    parent_response = client.get(f"/v1/projects/{parent_id}")
    parent_response.raise_for_status()
    parent = parent_response.json()["payload"]
    policy_response = client.get(f"/v1/projects/{parent_id}/release-policy")
    policy_response.raise_for_status()
    policy = policy_response.json()["policy"]
    request = {
        "name": "Reviewed support policy v2",
        "description": parent["description"],
        "data_requirements": parent.get("data_requirements", ""),
        "questions": copy.deepcopy(parent["questions"]),
        "criteria": copy.deepcopy(policy["payload"]["criteria"]) if policy else None,
        "expected_parent_schema_sha256": parent["schema_sha256"],
        "expected_parent_policy_id": policy["id"] if policy else None,
        "note": "Owner reviewed the successor rules and data requirements",
        "request_key": uuid.uuid4().hex,
    }
    # Review and edit the request before submission. Persist its key for retries.
    response = client.post(f"/v1/projects/{parent_id}/successors", json=request)
    response.raise_for_status()
    successor = response.json()
    print(successor["id"], successor["payload"]["policy_version"])
```

The endpoint returns HTTP 201 with the project record. Repeating the same key and normalized content returns the original successor, including after the parent criteria change. Reusing the key with different content returns HTTP 409. A new request with stale source schema/policy identifiers also returns 409; refresh rather than silently substituting the new source policy.

Project creation, initial criteria and audit records commit in one local SQLite transaction. A failed transaction publishes none of them. This path supports at most 64 projects in one ancestry chain. It uses the existing single-host storage model; it does not establish distributed transaction or enterprise identity acceptance.

<a id="policy-successors--recover-both-policy-versions"></a>

### Recover both policy versions

Include both projects in your ordinary [workspace backup](recovery-guide.md#local-backups). Recovery validates the successor's retained source and initial-policy references, request checksum and ancestry before publishing a restored workspace. Later acceptance-policy revisions do not change that initial receipt. Test the parent and successor endpoints separately after recovery.

<a id="database-deletion"></a>

## Delete archived project records

<a id="database-deletion--delete-an-archived-projects-database-records"></a>

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

<a id="database-deletion--prepare-the-project"></a>

### Prepare the project

1. Use a workspace owner account. Other application roles cannot delete records
   or read deletion receipts.
2. [Archive the project](project-lifecycle-guide.md#archive-restore). Finish/cancel jobs, observe
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

<a id="database-deletion--delete-through-the-browser"></a>

### Delete through the browser

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

<a id="database-deletion--use-the-api"></a>

### Use the API

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

<a id="database-deletion--understand-what-remains"></a>

### Understand what remains

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
separate: [shared-object retention](storage-maintenance-guide.md#shared-object-versions) can later
select unreferenced versions, while [local transfer cleanup](storage-maintenance-guide.md#transfer-stages)
only handles abandoned temporary transfers and preserves final model caches.
Use [cache retention](storage-maintenance-guide.md#model-caches) to reclaim owned final caches
after stopping the selected shared-workspace node.

<a id="database-deletion--capacity-upgrades-and-recovery"></a>

### Capacity, upgrades and recovery

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
