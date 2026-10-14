# Export and verify project audit history

Capture an owner-approved audit window, download its canonical JSONL records and verify the file against a separately retained manifest checksum. Use this when a review, incident investigation or operational archive needs more than the recent audit view's 500 events.

Audit exports contain actor identities, action names, target identifiers and timestamps. Owner-supplied action notes may contain confidential information. Protect downloaded files as project data; the default `.dmtlog` extension is excluded from Git. The publication checker also refuses renamed files whose header identifies this export format.

## Capture in the browser

1. Connect with an owner credential (`admin` role) and select the project.
2. Open **Decisions and audit**, enable **Open audit exports**, and leave **Audit after event identifier** at `0` for the first window.
3. Describe the export purpose, approve the audit data download, and select **Capture audit window**.
4. Save the displayed manifest checksum in a separate trusted location. Inspect the captured pages, then select **Prepare verified audit download** and **Download verified audit window**.

The browser verifies the complete response before making the download available. It retains at most one prepared audit file in the browser session. Larger histories need multiple windows: when `has_more` is true, continue from the manifest's `last_id` and use its original `until_id` as the frozen end. Stop when a verified window reports `has_more: false`.

Captures contain up to **100,000 events** and **64 MiB of event records**, with pages of at most 200 events. The 20-second capture deadline is checked while scanning and before commit, including elapsed transaction waiting and authorization. A failed deadline check rolls back the capture. Blocking database, filesystem or identity-provider calls can extend response time. Existing write contention may cause an earlier database timeout; retry the same request rather than assuming it failed before publication.

The captured end excludes subsequent audit events, including the capture's own audit entry. Multiple windows with the same frozen end describe the same historical range even while new changes occur. A lower explicitly selected end exports only that selected range; it does not establish that the whole current history was exported.

## Verify a downloaded file

Use the checksum retained when you approved the capture:

```bash
domain-platform verify-audit-export --file project-audit.dmtlog --manifest-sha256 YOUR_RETAINED_MANIFEST_SHA256
```

Successful output includes `verified: true`, the event count, `has_more`, `next_after_id` and the frozen `until_id`. The verifier checks manifest identity, ordered and scoped events, per-page and complete-content hashes, byte counts, the mandatory completion footer and absence of trailing content. Missing, reordered, duplicated, altered or truncated records are refused. It does not print event contents.

A trusted checksum detects modification relative to that retained receipt. It does not prove the database was unmodified before capture, establish an external witness or provide WORM storage. SHA-256 is computed incrementally using Python's standard hashing interface. [Python `hashlib`](https://docs.python.org/3/library/hashlib.html)

## Use the API

Owners can create and inspect exports through these routes:

| Route | Result |
|---|---|
| `POST /v1/projects/{project}/audit-exports` | Frozen manifest and export identifier; HTTP 201 |
| `GET /v1/projects/{project}/audit-exports?limit=50&offset=0` | Owner-scoped export history |
| `GET /v1/projects/{project}/audit-exports/{export}` | Captured metadata and checksum |
| `GET /v1/projects/{project}/audit-exports/{export}/pages/{index}` | Verified event page, starting at index 0 |
| `GET /v1/projects/{project}/audit-exports/{export}/download` | Canonical JSONL stream with `X-Audit-Manifest-SHA256` |

The capture body contains `note`, a stable `request_key`, `after_id` (default `0`) and an optional `until_id`. Keep the same key and content when retrying an uncertain request; it returns the original export. Different content with an already-used key returns HTTP 409. **Start a new audit capture** creates a fresh browser draft and request key.

An export begins with its manifest, followed by event envelopes, then a completion footer. Save the manifest checksum separately and verify the entire file. A streaming response may begin with HTTP 200 and later be interrupted by revoked authorization, a transport error or changed source records. A successful HTTP status alone does not establish a complete export.

Authorization is checked before capture publication and between streamed pages. Editors, reviewers, inference credentials and other tenants cannot retrieve the export. Source events are read using project-scoped indexed cursors, and captures use the application's local SQLite transaction. [SQLite transaction behavior](https://www.sqlite.org/lang_transaction.html)

## Recover and retain records

Workspace backups include export metadata and its source audit events. Current recovery validates the captured pages before publishing a restored workspace; later recovery events do not change an earlier frozen file. Follow the [recovery guide](recovery-guide.md) and compare retained audit checksums after restoration.

Capture and download do not delete audit events, datasets, models or backups. Configure your enterprise archive's access, retention and key custody separately. Dataset deletion, automatic audit retention, offsite/WORM ingestion and distributed enterprise acceptance require additional controls. This feature exports existing project audit events; it does not add audit coverage for every possible read, external provider operation or infrastructure event.

[Primary source metadata](../research/audit-export-sources-2026-10-14.json) records the hashing and transaction references.
