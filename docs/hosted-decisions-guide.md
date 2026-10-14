# Use a hosted decision model through the platform

The platform supports TypeSafe, OpenRouter, Perplexity and Cloudflare decision adapters. An owner evaluates a fixed project schema against reserved domain records, fits a local temperature policy, and releases the eligible version through the same authenticated decision endpoint as local models. This study does not train, download or export the provider's weights.

Start with the [API guide](api-integration-guide.md) for provider contracts and application integration. Configure an operator profile and check it with the selected account before running a domain study. [Provider compatibility checks](verification.md#provider-compatibility-checks)

## Configure operator-owned profiles

Copy [the profile template](../examples/hosted-profiles.json) to a private operator directory, and keep only the providers you intend to enable. Replace `tenants` with your configured workspace identities. Configure each provider credential in the dedicated environment variable named by `key_env`; the profile contains no credential values. Both API and worker processes need the profile path and their provider keys. Python commands do not automatically load `.env` files.

For PowerShell, set the profile location before starting the existing launcher:

```powershell
$env:DMT_DECISION_PROFILES_FILE = (Resolve-Path data/config/decision-profiles.json).Path
domain-platform run --keys-file data/operator-keys.json
```

Inject the selected `DMT_PROVIDER_TYPESAFE_API_KEY`, `DMT_PROVIDER_OPENROUTER_API_KEY`, `DMT_PROVIDER_PERPLEXITY_API_KEY` or `DMT_PROVIDER_CLOUDFLARE_API_KEY` through your operator secret mechanism. Do not place a key in a URL, repository file, command example or browser form. The local launcher removes provider/teacher credentials from its Streamlit child; Compose passes provider credentials only to API/worker services. Compose's existing read-only configuration-directory mount allows a profile at `/run/config/decision-profiles.json`; set that container path through `DMT_DECISION_PROFILES_FILE`. Container execution still needs qualification.

| Field | Meaning |
|---|---|
| `id`, `provider`, `model` | Operator-selected route and request model |
| `response_model` | Exact model identity observed during qualification; a request alias can resolve to a different dated response |
| `response_provider`, `provider_slug` | OpenRouter response identity and approved routing slug; the request restricts `provider.only` and disables fallback |
| `account_id` | Cloudflare account; replace the template's all-zero example |
| `tenants` | Explicit permitted workspace identities |
| `key_env` | Dedicated `DMT_PROVIDER_...` credential variable |
| `max_calls_day`, `max_inflight` | Shared rolling 24-hour call quota and unresolved-call capacity per tenant/profile |
| `timeout_seconds` | 3–30 second communication budget including child startup; parent terminates and waits for an overdue transport |
| `max_request_bytes` | Entire encoded model/state/questions request budget, rejected before transport |

There is no client-supplied URL or credential field. Direct providers use fixed HTTPS endpoints; redirects and environment proxy inheritance are disabled. OpenRouter requires both its provider slug allowlist and response identity checks. Confirm the slug against the [provider directory](https://openrouter.ai/provider/typesafe) and [routing contract](https://openrouter.ai/docs/guides/routing/provider-selection); the response check remains necessary. Request/model/routing acceptance is not proven until exercised with the actual account.

Changing a public profile field disables serving/release for earlier policy artifacts until the exact profile is restored or a successor study is qualified. Credential rotation and tenant revocation do not require putting a secret in an artifact. Removing a profile or tenant disables both fresh calls and cached result delivery. Live operator profile files and credential values are outside portable model/backup artifacts. The public evaluated profile snapshot remains inside policy artifacts and the call ledger.

Cloudflare uses its account-scoped REST envelope, not the Workers binding envelope. Its initial text/JSON profile conservatively caps the entire request at 1,024 UTF-8 bytes and constrains question IDs. This is an application byte bound, not a tokenizer/context guarantee: qualify the actual provider's truncation and usable context before enterprise release. Media/generation fields are not supported by these adapters. [Clef Flash contract](https://developers.cloudflare.com/workers-ai/models/clef-flash/)

## Run an owner-approved study

1. Create the project, review examples and prepare the four-role snapshot using [the normal workflow](setup.md).
2. Set acceptance criteria before the first local training, remote study or hosted evaluation. The same frozen class-count/quality policy applies; results cannot be inspected and then used to loosen those gates.
3. In **Training jobs → Evaluate a hosted decision model**, select an approved profile. Confirm that the provider is authorized to receive study records and future inference states after release. Queue hosted evaluation.
4. The independent worker validates all request sizes before any transport, checks remaining call quota, and sends only calibration/validation/test state and pinned questions. Training seeds and gold labels are not sent. Shared activity can consume remaining quota after preflight; a later quota failure stops the study.
5. Local per-question temperature selection uses only the calibration split. Inspect calibration, validation and locked-test metrics, call IDs and reported usage. Successful registration creates an inactive JSON policy artifact containing the schema, provider profile and calibration.
6. Owner activation applies the frozen test gates and checks profile/credential/artifact availability. Future states then go to that external provider. Activation performs no paid health probe and does not prove upstream availability. Rollback increments the release revision and applies the same eligibility checks.

The corresponding API submission is:

```json
{
  "kind": "hosted-evaluate",
  "config": {
    "snapshot": "YOUR_SCOPED_SNAPSHOT_ID",
    "profile_id": "openrouter",
    "data_approved": true
  },
  "request_key": "YOUR_UNIQUE_STUDY_KEY"
}
```

POST it to `/v1/projects/{project}/jobs` using the owner's workspace credential. GET `/v1/projects/{project}/decision-profiles` exposes safe profile metadata to owners/editors; GET `/v1/projects/{project}/provider-calls?limit=50&offset=0` exposes paged call outcomes only to owners. The general project job/release routes remain unchanged.

## Serve, retry and account for calls

Call `/v1/projects/{project}/decisions` with the application's scoped inference credential:

```json
{
  "state": "Decision-time evidence",
  "threshold": 0.9,
  "request_key": "UNIQUE_APPLICATION_DECISION_KEY"
}
```

Use your evaluated threshold; `0.9` is a transport illustration. Answers follow the platform schema and use the locally calibrated winner probability for confidence/abstention. Provider distribution-based confidence is retained separately, together with raw rounded probabilities and sanitized usage/model/request metadata. A fractional provider score remains a probability-weighted expectation; the canonical answer is the most likely ordinal level.

The optional 8–128 character request key makes a successful hosted inference replay return its stored result without another upstream request. It is scoped to the tenant/project and binds state, questions, frozen profile, released model/revision and threshold. Changing those under the same key is a conflict. Omitting the key creates a fresh billable attempt each time. A pending, failed or ambiguous attempt is never automatically retried; inspect its owner call record before deliberately using a new key. This is inference idempotency, not business-action idempotency. Application code still owns authorization, side effects, review and fallback.

Reservations/outcomes survive restarts and backup/restore. They retain request hashes and probability/usage metadata, not raw input state or gold. A missing outcome remains pending/unknown; failed and ambiguous calls consume the rolling quota and retain unresolved-call capacity without automatic expiry, including after restart or recovery. Worker cancellation or lease loss prevents model registration; an upstream call already sent may still finish and incur a charge. Portable recovery preserves the ledger and locally calibrated policy, and does not replay calls. Supply approved operator profiles/credentials separately after recovery. [Recovery guide](recovery-guide.md)

Calls have no automatic retries. Provider errors, malformed JSON, duplicate JSON keys, changed model/provider identity, missing questions, invalid probabilities, compressed/oversized responses and time-budget failures produce a defined API failure rather than a synthetic answer. Responses are limited to 1 MiB. Production HTTP runs in a dedicated one-shot Python process; only the selected credential and request cross private pipes, and provider/teacher/application secret environment variables are not inherited. The parent applies the profile budget to process startup/communication, then kills and waits for an overdue child. It discards late responses rather than returning them as successful decisions. A fixture transport injected explicitly by tests runs in-process and does not qualify the production deadline.

Process creation itself can block in an operating-system API, and termination/cleanup can add time beyond the request budget. The configured value is not a promise of an exact HTTP response deadline under every OS failure. A parent crash can leave a child without a recorded outcome; the reservation still blocks capacity indefinitely. Owner history includes the durable child PID, parent PID, host hash and opaque process token recorded before dispatch. Verify host and exact token before acting on a PID; PIDs can be reused, and restored receipts describe historical processes. Verify local process termination before manually reconciling that record. A hosted study has a one-hour deadline checked between calls. Local process termination does not prove upstream cancellation or a billing refund. [Python subprocess timeout/termination contract](https://docs.python.org/3.13/library/subprocess.html), [HTTPX inactivity timeouts](https://www.python-httpx.org/advanced/timeouts/).

The rolling call quota is a hard local reservation limit, not a dollar spending guarantee. `max_inflight` is a shared limit on unresolved attempts: a successful validated result frees its slot; a missing/unknown result retains it indefinitely. An owner settlement can explicitly free it after local/upstream verification. This conservative accounting prevents automatic capacity recycling after a timeout; it does not verify provider-side execution or establish an account-wide provider concurrency limit. Usage cost is recorded only when the provider returns it; missing cost stays unknown, and an unspecified currency is not inferred. Include review/fallback/errors, startup, provider retention terms and actual billed receipts in a domain cost comparison. A profile allowlist is not proof of a provider's private-region/data-retention promise. Public usage rankings and fixture accuracy do not establish enterprise quality.

## Reconcile an uncertain call

In **Training jobs → Evaluate a hosted decision model → Show provider call history**, an owner can inspect `capacity_held`, the immutable outcome hash and any recorded settlement. Only unresolved records offer the settlement form. Stop/reconcile an owning evaluation job first; queued/running/cancel-requested jobs cannot have their capacity manually released.

1. Verify that the local transport has stopped. For a parent crash, check the relevant host/process; elapsed time alone is insufficient evidence.
2. Verify the upstream request has finished, using the provider request ID/operations record when available. If its execution remains uncertain, retain the reservation. Do not invent a completion or billing receipt.
3. Supply an evidence reference and a note, and confirm both conditions. The platform records an **owner attestation**, not independently verified provider confirmation.
4. Record settlement. It releases unresolved-call capacity only. It does not remove the reservation from the daily quota, refund cost, alter probabilities/outcomes, replay the original request or make its failed key reusable.

The owner API is `POST /v1/projects/{project}/provider-calls/{call_id}/reconcile`:

```json
{
  "expected_outcome_sha256": "HASH_FROM_FRESH_OWNER_CALL_HISTORY",
  "local_transport_stopped": true,
  "provider_finished": true,
  "reference": "Private operations receipt or case identifier",
  "note": "Why local and upstream completion are established"
}
```

A changed outcome/hash is a conflict. A successful call cannot be settled because its capacity is already free. Missing outcomes and unconfirmed local termination require waiting past the profile budget plus a 10-second grace before owner attestation is accepted; this waiting period never proves cessation. Identical settlement submission is idempotent, and a different note/reference cannot overwrite it. Settlement/audit history survives backup/restore. Legacy unknown outcomes use the same conservative policy. Monitored orphan-process cleanup, automated provider-receipt/billing verification and account-wide monetary enforcement require separate operational validation.
