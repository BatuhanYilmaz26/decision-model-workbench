# Define and evolve your decision schema

[Documentation](README.md) · [Project overview](../README.md)

You own the decision policy and allowed outcomes. Optional teachers can draft questions or suggest category changes; review and confirm them before using a schema.

## Choose a section

- [Draft questions with a teacher](#question-drafting)

- [Discover and confirm categories](#category-discovery)

<a id="question-drafting"></a>

## Draft questions with a teacher

<a id="question-drafting--draft-decision-questions-with-a-teacher"></a>

Describe your domain and have a configured generative teacher propose decision questions and outcomes. Review the draft, correct its assumptions and edit the questions before creating a project. The assistant does not label records, set acceptance thresholds or approve a policy for you.

<a id="question-drafting--set-up-the-teacher"></a>

### Set up the teacher

Start with [local setup](setup.md#configuration--configure-a-teacher). The API and independent worker must use the same teacher configuration. Set these environment variables before starting the application:

| Setting | Purpose |
|---|---|
| `DMT_TEACHER_BASE_URL` | Approved teacher API base, including its version path, such as `http://127.0.0.1:11434/v1` |
| `DMT_TEACHER_MODEL` | The installed or provider-supported generative model identifier |
| `DMT_TEACHER_RESPONSE_FORMAT` | `json_object` by default; choose `json_schema` when required by the endpoint |
| `DMT_TEACHER_API_KEY` | Optional credential, supplied to the services rather than saved in a project |
| `DMT_SCHEMA_TIMEOUT_SECONDS` | Total suggestion deadline; default 60 seconds, allowed range 5–180 |
| `DMT_SCHEMA_CALLS_PER_DAY` | Maximum new schema requests per tenant in a rolling 24 hours; default 20, allowed range 1–100 |
| `DMT_SCHEMA_TOKEN_PARAMETER` | `max_tokens` by default; choose `max_completion_tokens` when required by your teacher |
| `DMT_SCHEMA_OUTPUT_TOKENS` | Output allowance; default 3,000, allowed range 256–16,384 |
| `DMT_SCHEMA_TEMPERATURE` | Default `0.2`; a finite number from 0 to 2, or `omit` to leave the parameter out |

The endpoint must support `POST /chat/completions`, the selected JSON output mode and the configured model. JSON Schema mode is non-strict; proposed questions still pass server validation and owner review. See [teacher setup](setup.md#configuration--configure-a-teacher) for format selection and startup commands. Loopback HTTP and remote HTTPS are supported; embedded credentials, query strings and fragments in the base URL are refused. For a local endpoint, install and start a compatible teacher separately. For example, [Ollama documents its compatible endpoint and structured-output options](https://docs.ollama.com/api/openai-compatibility). Compatibility must be checked with the selected teacher; a decision-only model is not a generative teacher.

Restart the services after changing configuration. The browser displays the configured endpoint and model to the owner, without the API key. It cannot choose an arbitrary endpoint. You can build questions manually without any teacher or external account.

With [Docker Compose](container-guide.md), set these variables in your private `.env` file. Compose passes the teacher address, model and schema limits to both API and worker, and the teacher credential only to the worker. A container's loopback address points to that container, not the host computer. Use a reachable approved HTTPS endpoint when the teacher runs elsewhere; the default Compose configuration does not provide an Ollama service or configure a TLS gateway for a host teacher.

<a id="question-drafting--request-and-review-a-draft"></a>

### Request and review a draft
For a teacher that requires the newer token parameter and rejects temperature,
set `DMT_SCHEMA_TOKEN_PARAMETER=max_completion_tokens` and
`DMT_SCHEMA_TEMPERATURE=omit` before starting API and worker services. Leave the
optional settings blank to preserve the defaults. Synthetic generation has its
own `DMT_GENERATION_*` controls. Direct personal setup offers **Teacher token
limit parameter** and **Omit teacher temperature**, applying these choices to
both workflows; **Use configured default** preserves your environment settings.

The browser shows the active token allowance and whether temperature is omitted.
All controls form part of the approved request. Changing them requires a new
approval; saved history retains its original request. No automatic parameter
fallback or billable retry is performed. Check your model's parameter support;
`max_completion_tokens` can include reasoning as well as visible output.
[Chat Completions parameters](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)


1. Open **Create a domain project** and select **Suggest decision questions with a teacher**. This optional workflow requires a workspace owner.
2. Enter a project name, decision policy and data requirements. Explain the evidence available, allowed actions, exclusions and cases needing review. Do not paste evaluation examples or secrets into this brief.
3. Check the displayed teacher endpoint. Explicitly approve sending the brief and any charges, then select **Request schema suggestions**.
4. The independent worker processes the request. Use **Refresh schema suggestion** or reopen a saved suggestion later; a browser refresh does not trigger another teacher call.
5. Read the assumptions. Review every question and outcome in **Review or edit suggested questions (JSON)**. Update the domain policy and data requirements if the draft misunderstood them.
6. Explicitly approve the reviewed policy and outcomes, then select **Create project from reviewed suggestions**. The new project opens with an empty dataset; enter or upload examples using the [data guide](data-guide.md#data-contract).

The suggested schema is limited to eight questions, up to twenty categories per choice question and ten ordered levels per rating question. It can include category, yes/no and ordered-rating questions. An owner can replace the draft with any manually reviewed schema supported by the platform before confirmation. The confirmation records the original proposal identity and the reviewed schema identity separately.

<a id="question-drafting--understand-what-is-sent-and-saved"></a>

### Understand what is sent and saved

The teacher receives the domain brief and a fixed schema-drafting instruction. This workflow does not select, upload or send training, validation, calibration or test records. A hosted teacher still receives the supplied brief, so use an approved endpoint for private domain information.

The workspace saves the brief, approvals, requested endpoint/model, exact request body and checksum, application source identity, job and unapproved proposal. Saving the request body preserves completed history when the application's drafting instructions change. Confirmation creates the reviewed project atomically and records both the reviewed domain brief and schema identities. It creates no dataset, model or release and does not transfer data from an existing project.

The reported model identifier is retained alongside the requested identifier. An echoed model name is not immutable weight attestation. A syntactically valid schema does not establish that its rules are correct for your domain.

<a id="question-drafting--handle-failures-and-cancellation"></a>

### Handle failures and cancellation

Only one teacher request is attempted per suggestion job. Repeating an identical request with the same request key returns the existing job. The browser retains a key for repeated submissions of the same approved form. An invalid response, refusal, duplicate JSON keys, truncated completion or unsupported question structure fails without creating a project.

Use **Cancel schema suggestion** to cancel a queued or running job. Running transport is supervised and bounded by the configured deadline. Cancellation or a timeout cannot retract a request already received by the teacher or prove that no charge occurred. Inspect the teacher/provider outcome before intentionally submitting a new request. The daily limit counts reserved requests, including failed or cancelled jobs; it is a request-count limit rather than a monetary budget.

If the teacher configuration or application source changes before dispatch, the job fails before contacting the teacher. After a worker interruption, inspect the saved job; it is not automatically replayed. Completed drafts remain available for review even if the teacher is later disabled.

Workspace backups preserve intakes, completed drafts and confirmed project provenance. Follow [backup and recovery](recovery-guide.md#local-backups) for capture and restore. Stop all services before upgrading; mixed-version workers and recovery tools are not supported for this workflow.

<a id="question-drafting--api-integration"></a>

### API integration

Use an owner credential with the existing authenticated API. See [authentication](authentication-guide.md#workspace-credentials) for provisioning.

| Endpoint | Purpose |
|---|---|
| `GET /v1/schema-teacher` | Inspect whether a teacher is configured; no request is sent |
| `POST /v1/schema-intakes` | Approve and queue a domain brief with an 8–128 character request key |
| `GET /v1/schema-intakes?limit=20&offset=0` | List the tenant's saved intakes; maximum page size 100 |
| `GET /v1/schema-intakes/{id}` | Read the brief and job status/result |
| `POST /v1/schema-intakes/{id}/cancel` | Request cancellation |
| `POST /v1/schema-intakes/{id}/confirm` | Create a project from an explicitly reviewed completed draft |

The creation body contains `name`, `description`, `data_requirements`, `request_key`, `data_approved: true` and `cost_approved: true`. Confirmation contains the reviewed `name`, `description`, `data_requirements`, `questions`, `expected_proposal_sha256` from the result and `confirmed: true`.

Each intake creates at most one reviewed project. An identical confirmation returns that project; a different confirmation after creation conflicts. Saved suggestions show **Open reviewed project** after confirmation. The detail endpoint includes its `confirmed_project` identifier and name. To change an existing project's policy later, use the [policy-successor workflow](project-lifecycle-guide.md#policy-successors).

<a id="category-discovery"></a>

## Discover and confirm categories

<a id="category-discovery--discover-and-confirm-a-domain-taxonomy"></a>

Use this workflow when real examples suggest missing categories in an existing **choice** question. It proposes a schema change before a new training study. It does not train a model, automatically label data, or change a released model.

The [TypeLLM invoice cookbook](https://typellm.ai/cookbook/discover-invoice-categories?v=2) demonstrates an enum plus a conditional category-name field. TypeLLM uses constrained generation over autoregressive models; typed generation can still choose the wrong category. The current [HTTP contract](https://typellm.ai/docs/api) accepts `context`, typed `questions`, a model and options at `POST https://api.typellm.ai/v1/generate`. This application extends that pattern with authenticated proposals, immutable reviews and owner-confirmed schema versions. [Dated source captures](../research/provenance/taxonomy-sources-2026-10-11.json) preserve the API, cookbook, package metadata and repository revision `fd9b97a06386d1898d2c241796c3c81bd10db9c0`.

<a id="category-discovery--user-workflow"></a>

### User workflow

1. Open a project, import real examples through its [data workflow](data-guide.md#data-contract), and open **Open taxonomy workspace**. Choose the choice question and 1–20 eligible examples from the current page. Inspect their states. Synthetic and rejected records are refused; unlabelled real examples may be used.
2. Enter a proposed category name and save a manual proposal. Alternatively, select an operator-enabled TypeLLM profile, separately approve the selected-state/policy transfer and billable call, then queue discovery. Watch the durable job in Jobs. Only the selected states and IDs, question instructions, approved categories and project description are sent; gold targets are omitted.
3. Select the completed proposal. For each candidate, inspect the source examples, then **add** a canonical category with a definition, mark it as an **alias** of an existing or another approved new category, or **reject** it. Canonical IDs use lower-case ASCII snake case; review reasons are required. Owner/reviewer revisions use compare-and-swap: stale concurrent edits return 409 and require a refresh.
4. The owner confirms the complete reviewed proposal, gives the successor project a name and records a reason. Confirmation atomically creates one empty successor project and a source/review-bound confirmation. Existing categories, other questions, parent policy, models and release remain unchanged. Separate proposals can create separate branches; they do not merge automatically.
5. Select the successor in the project selector. Download its new data format, explicitly annotate/import data against the new schema, review it, reserve the four roles and train/evaluate a new model. There is no automatic transfer of old gold, provider assignments, models or release activation. Aliases are recorded in the confirmation; import does not silently rewrite labels with them.

Selected examples influence taxonomy selection even if every proposal is rejected or a provider outcome is unknown. Their IDs, source families, normalized exact states and declared semantic/seed relatives remain **training-only** in this project and its descendants. Existing holdouts and known relatives cannot be selected. Reservation and discovery insertion recheck these constraints transactionally; snapshot consumption and recovery recheck them again. These controls cover known relationships, not undiscovered semantic leakage. A successor needs fresh independent evaluation gold: reimporting old reserved records does not make them unseen. Successor preparation does not automatically inherit every parent record's original role. A chronological study may refuse late taxonomy examples rather than relax time boundaries. Establish representative independent gold and follow [temporal](reservation-guide.md#time-boundaries), [semantic](reservation-guide.md#semantic-relatives) and [evaluation](evaluation-guide.md#evaluation) guidance.

<a id="category-discovery--operator-configuration"></a>

### Operator configuration

Copy [the illustrative profile](../examples/taxonomy-profiles.json) into private operator configuration; replace its tenant/project scopes. Set `DMT_TAXONOMY_PROFILES_FILE` in both API and worker and the selected `DMT_TAXONOMY_TYPESAFE_API_KEY` in the worker environment. Compose forwards this selected variable to API/worker only; the web process receives no provider credential. In Compose, an example location is `/run/config/taxonomy-profiles.json`, backed by `data/config/taxonomy-profiles.json`. Python commands do not automatically load `.env.example`.

This adapter calls HTTP directly with the installed platform `httpx`; it does not require or claim tested installation of the TypeLLM SDK. Package metadata and current repository prose differ on enum capacity. The adapter deliberately supports **2–23 approved categories plus one proposal sentinel**, up to twenty selected rows, one request and no automatic retries. It rejects request-size/response/deadline overflow rather than truncate or publish partial proposals. Profile limits control rolling 24-hour calls, unresolved concurrent calls, request bytes and rows. Those limits are admission controls, not a monetary budget or provider billing estimate.

The profile freezes request and expected response model strings. `typellm-latest` is a mutable upstream alias; matching it does not attest checkpoint identity or prevent upstream weight changes. The adapter explicitly disables thinking and refuses unexpected thinking usage. Model/profile/package/data drift before dispatch is refused. Success stores normalized candidates, assignments, usage and response SHA; the raw response is not retained. Local hashes bind stored evidence, not provider authenticity.

The one-shot child receives the selected credential through a private pipe and calls the fixed HTTPS endpoint. A deadline or cancellation stops the local child; upstream processing may still finish and incur a charge. Uncertain calls retain capacity across restarts. Use **Uncertain taxonomy call settlement** only after checking that local transport stopped and the provider request finished, and record a reference and reason. This is an owner attestation, not a refund or automatic retry. Reconcile unknown calls manually, including after a parent-process crash; there is no unattended settlement service.

<a id="category-discovery--api-and-evidence"></a>

### API and evidence

Use the authenticated project's routes: `GET taxonomy-profiles`, `POST taxonomy-proposals` for manual proposals, `POST jobs` with kind `taxonomy-discover` for consented discovery, `GET taxonomy-proposals/{id}`, `POST taxonomy-proposals/{id}/reviews`, and `POST taxonomy-proposals/{id}/confirm`. Manual proposal fields are `dataset`, `question_id`, `record_ids` and `candidates` (`name`, `example_ids`). Discovery config adds `profile_id`, `data_approved=true` and `cost_approved=true`. Review fields are `candidate_id`, `decision`, `canonical_id`, `description`, `expected_revision`, `expected_hash` and `note`. Confirmation fields are `expected_state_sha256`, `name` and `note`. Unknown-call inspection/settlement uses `taxonomy-discoveries/{id}` and its `/settle` route. Inspect `/docs` for the strict live schema; use [workspace authentication](authentication-guide.md#workspace-credentials).

Fictional provider responses exercise the real API/worker/HTTP parser, isolated process, review and recovery paths. They do not establish TypeLLM account execution, category quality, calibration, source truth or enterprise production acceptance.

<a id="category-discovery--recover-interrupted-discovery"></a>

### Recover interrupted discovery

The worker owns the HTTP transport process. On supported Windows/Linux profiles, worker loss ends that child; a restarted worker marks the expired job interrupted and does not replay its request. This cannot cancel work already accepted by the upstream provider or establish whether it was charged.

An unresolved discovery keeps its call reservation. Inspect the saved discovery and provider activity before using the owner reconciliation controls. Confirm local termination and upstream completion only when you can support both; closing a connection is not proof of a refund or provider cancellation. Review any completed proposal before confirming a new schema.
