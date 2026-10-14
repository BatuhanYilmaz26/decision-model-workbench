# Assign and review domain examples

Use a persistent review queue to distribute annotation work and record who corrected, approved or rejected each example. Workspace owners assign records; the assigned reviewer saves labels through the same immutable dataset workflow used for unassigned examples.

## Prepare reviewer access

Create a workspace credential with role `reviewer`, or configure a trusted enterprise access-token mapping to that role. Workspace owners use role `admin`. Use the reviewer's exact **actor ID** from the trusted identity configuration. The sidebar displays the authenticated actor ID. [Credential setup](authentication-guide.md)

An assignment does not create a user, issue credentials or grant project access. The reviewer must already authenticate into the same tenant. Owners may review assigned work themselves; their identity and review note remain in the history. Other reviewers cannot save an example while it is assigned to someone else.

## Assign work in the browser

1. Open the project and its data tab. Choose the latest dataset version.
2. Select an example. For a specific record, enter its ID in **Find example by ID**.
3. Open **Assign review work**. Select **Assign or reassign**, enter the reviewer actor ID and explain the assignment.
4. Select **Save assignment**. The work appears in **Review work queue** without changing the example's labels, approval or dataset hash.

Owners can reassign active or completed records for another review. Choose **Cancel assignment** to cancel currently assigned work. Every change retains the previous assignment; cancellation preserves all dataset and review history.

## Complete assigned work

1. Sign in as the reviewer and open the project.
2. Expand **Review work queue**, with **Only my assignments** selected. Use the page control to browse additional tasks.
3. Select a task and choose **Open assigned example**. The application opens the collection's current version and finds the record, including records outside the first example page.
4. Read the decision-time evidence and written policy. Correct each label; choose a draft, approval or rejection and supply a review note.
5. Select **Save reviewed version**. A draft keeps the assignment open; approval or rejection completes it. Each save creates an immutable dataset revision.
6. Select **Show assignment history** to inspect assignment, draft, completion and cancellation events, including actor, note and referenced data version.

Reassignment and review are checked in the same database transaction as the label revision. If someone changes the task or collection while you are editing, refresh before saving again. A conflict never silently overwrites another review. Revoked credentials or changed roles are checked by the API on the next request.

## Assign a batch through the API

The assignment endpoint accepts 1–100 distinct record IDs per request. Supply the current dataset SHA-256 and each record's current assignment revision; use `0` only when it has no assignment history. The request succeeds atomically or changes nothing.

Read current assignments with:

```text
GET /v1/projects/{project}/datasets/{dataset}/review-assignments?limit=100&offset=0
GET /v1/projects/{project}/datasets/{dataset}/review-assignments?record_id={record_id}&limit=1
```

Submit this body to `POST /v1/projects/{project}/datasets/{dataset}/review-assignments` using an owner's bearer credential. Replace the illustrative record IDs, SHA-256 and actor ID with values from your workspace:

```json
{
  "targets": [
    {"record_id": "example-1", "expected_revision": 0},
    {"record_id": "example-2", "expected_revision": 3}
  ],
  "assignee": "annotator",
  "expected_hash": "DATASET_SHA256",
  "note": "Review rare routing cases against policy version 3"
}
```

The `assignee` field is required: use an actor ID to assign work, or explicit `assignee: null` to cancel currently assigned targets. Omitting the field is rejected. A reviewer cannot call this owner-only endpoint. List endpoints accept 1–200 items per page and an integer offset from 0 to 2,147,483,647. The project queue and paginated record history are available through:

```text
GET /v1/projects/{project}/review-assignments?mine=true&status=assigned&limit=50&offset=0
GET /v1/projects/{project}/datasets/{dataset}/review-assignments/history?record_id={record_id}&limit=20&offset=0
```

To save assigned labels through `POST /v1/projects/{project}/datasets/{dataset}/reviews`, include the current `assignment_revision` alongside `record_id`, `gold`, `approved`, `status`, `expected_hash` and `note`. A draft advances the assignment revision, so fetch the new value before the next save. Continue to use the project's complete [gold-label format](data-guide.md) for approval. Unassigned reviews support the existing request format without an assignment revision.

Queue pages show current workflow state; they are not frozen dataset snapshots. Historical dataset views exclude assignments for records appended after that version. Existing reserved training, calibration and test records retain their original labels and hashes.

## Upgrade and recover a workspace

Back up the workspace, then stop the API, browser and worker services before installing and reopening the updated application. Do not run mixed application versions against one workspace: the schema compatibility check occurs at startup. Schema version **7** retains assignment revision uniqueness, frozen domain error gates and backup-alert semantics; the migration preserves dataset hashes, labels and registered artifacts. Older application versions supporting only schemas through 6 refuse to open the upgraded workspace. Reverting requires a compatible backup, rather than manually lowering the version number.

Backups include assignment history and its exact dataset/record references. Restore validates assignment hashes, revision chains and record bindings before publishing a recovered workspace. Existing schema-3/4/5/6 backups can be restored and upgraded. Follow [backup and recovery](recovery-guide.md).

Assignments currently apply to positive domain-example collections. Vega applicability-negative collections use their separate [review workflow](applicability-guide.md). Assignment is work coordination, rather than a separate record-visibility permission: authorized project reviewers retain their existing read access. Creating an assignment does not reopen an approved record; explicitly save a pending draft when approval needs to change. Assignments do not establish label correctness or replace independent operational evaluation.
