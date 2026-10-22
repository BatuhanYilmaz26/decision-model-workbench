# Archive or restore a project

[All guides](README.md) · [Application guide](app-guide.md) · [Backup and recovery](recovery-guide.md)

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

## Prepare the project

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

## Use the browser application

Select the project and open **Project lifecycle** above its usual workflow.
Copy the displayed project ID into **Confirm project ID**, read and select the
acknowledgment, then choose **Archive project**.

The archived view explains why the normal screens are unavailable. Owners can
use the same confirmation to choose **Restore project**. Other roles see a
message directing them to an owner. If another owner changed the lifecycle,
refresh and confirm the current state before trying again.

## Use the API

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

## Upgrade and recover

Back up the workspace and stop every API, worker and browser service before
upgrading. This release uses SQLite schema **11** and PostgreSQL workspace
protocol **13**. Restart all nodes with the same application version; mixed
versions are unsupported. Existing projects begin active without changing their
policy, model, dataset or release identities.

Lifecycle transitions form an immutable revision/hash chain. Local backup
verification checks it, and recovery preserves archived states while recording
the operator restore event. [PostgreSQL/shared-storage recovery](shared-artifacts-guide.md)
still needs a coordinated database/object procedure.

Use [permanent database deletion](project-deletion-guide.md) when you intend to remove archived project records. Archival itself is not deletion, anonymization, secure erasure or a retention expiry.
It does not remove backups, exports, logs, model caches, external training
volumes or data previously sent to a provider. Use your separate retention and
deletion procedures for those copies. The [local transfer cleanup](local-stage-cleanup-guide.md)
and [shared-object cleanup](shared-object-retention-guide.md) tools also preserve
registered models belonging to archived projects.
