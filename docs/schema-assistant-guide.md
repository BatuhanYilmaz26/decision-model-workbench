# Draft decision questions with a teacher

[All guides](README.md) · [Project overview](../README.md)

Describe your domain and have a configured generative teacher propose decision questions and outcomes. Review the draft, correct its assumptions and edit the questions before creating a project. The assistant does not label records, set acceptance thresholds or approve a policy for you.

## Set up the teacher

Start with [local setup](setup.md#configure-a-teacher). The API and independent worker must use the same teacher configuration. Set these environment variables before starting the application:

| Setting | Purpose |
|---|---|
| `DMT_TEACHER_BASE_URL` | Approved teacher API base, including its version path, such as `http://127.0.0.1:11434/v1` |
| `DMT_TEACHER_MODEL` | The installed or provider-supported generative model identifier |
| `DMT_TEACHER_API_KEY` | Optional credential, supplied to the services rather than saved in a project |
| `DMT_SCHEMA_TIMEOUT_SECONDS` | Total suggestion deadline; default 60 seconds, allowed range 5–180 |
| `DMT_SCHEMA_CALLS_PER_DAY` | Maximum new schema requests per tenant in a rolling 24 hours; default 20, allowed range 1–100 |

The endpoint must support `POST /chat/completions`, JSON object output mode and the configured model. Loopback HTTP and remote HTTPS are supported; embedded credentials, query strings and fragments in the base URL are refused. For a local endpoint, install and start a compatible teacher separately. For example, [Ollama documents its compatible endpoint and structured-output options](https://docs.ollama.com/api/openai-compatibility). Compatibility must be checked with the selected teacher; a decision-only model is not a generative teacher.

Restart the services after changing configuration. The browser displays the configured endpoint and model to the owner, without the API key. It cannot choose an arbitrary endpoint. You can build questions manually without any teacher or external account.

With [Docker Compose](container-guide.md), set these variables in your private `.env` file. Compose passes the teacher address, model and schema limits to both API and worker, and the teacher credential only to the worker. A container's loopback address points to that container, not the host computer. Use a reachable approved HTTPS endpoint when the teacher runs elsewhere; the default Compose configuration does not provide an Ollama service or configure a TLS gateway for a host teacher.

## Request and review a draft

1. Open **Create a domain project** and select **Suggest decision questions with a teacher**. This optional workflow requires a workspace owner.
2. Enter a project name, decision policy and data requirements. Explain the evidence available, allowed actions, exclusions and cases needing review. Do not paste evaluation examples or secrets into this brief.
3. Check the displayed teacher endpoint. Explicitly approve sending the brief and any charges, then select **Request schema suggestions**.
4. The independent worker processes the request. Use **Refresh schema suggestion** or reopen a saved suggestion later; a browser refresh does not trigger another teacher call.
5. Read the assumptions. Review every question and outcome in **Review or edit suggested questions (JSON)**. Update the domain policy and data requirements if the draft misunderstood them.
6. Explicitly approve the reviewed policy and outcomes, then select **Create project from reviewed suggestions**. The new project opens with an empty dataset; enter or upload examples using the [data guide](data-guide.md).

The suggested schema is limited to eight questions, up to twenty categories per choice question and ten ordered levels per rating question. It can include category, yes/no and ordered-rating questions. An owner can replace the draft with any manually reviewed schema supported by the platform before confirmation. The confirmation records the original proposal identity and the reviewed schema identity separately.

## Understand what is sent and saved

The teacher receives the domain brief and a fixed schema-drafting instruction. This workflow does not select, upload or send training, validation, calibration or test records. A hosted teacher still receives the supplied brief, so use an approved endpoint for private domain information.

The workspace saves the brief, approvals, requested endpoint/model, exact request body and checksum, application source identity, job and unapproved proposal. Saving the request body preserves completed history when the application's drafting instructions change. Confirmation creates the reviewed project atomically and records both the reviewed domain brief and schema identities. It creates no dataset, model or release and does not transfer data from an existing project.

The reported model identifier is retained alongside the requested identifier. An echoed model name is not immutable weight attestation. A syntactically valid schema does not establish that its rules are correct for your domain.

## Handle failures and cancellation

Only one teacher request is attempted per suggestion job. Repeating an identical request with the same request key returns the existing job. The browser retains a key for repeated submissions of the same approved form. An invalid response, refusal, duplicate JSON keys, truncated completion or unsupported question structure fails without creating a project.

Use **Cancel schema suggestion** to cancel a queued or running job. Running transport is supervised and bounded by the configured deadline. Cancellation or a timeout cannot retract a request already received by the teacher or prove that no charge occurred. Inspect the teacher/provider outcome before intentionally submitting a new request. The daily limit counts reserved requests, including failed or cancelled jobs; it is a request-count limit rather than a monetary budget.

If the teacher configuration or application source changes before dispatch, the job fails before contacting the teacher. After a worker interruption, inspect the saved job; it is not automatically replayed. Completed drafts remain available for review even if the teacher is later disabled.

Workspace backups preserve intakes, completed drafts and confirmed project provenance. Follow [backup and recovery](recovery-guide.md) for capture and restore. Stop all services before upgrading; mixed-version workers and recovery tools are not supported for this workflow.

## API integration

Use an owner credential with the existing authenticated API. See [authentication](authentication-guide.md) for provisioning.

| Endpoint | Purpose |
|---|---|
| `GET /v1/schema-teacher` | Inspect whether a teacher is configured; no request is sent |
| `POST /v1/schema-intakes` | Approve and queue a domain brief with an 8–128 character request key |
| `GET /v1/schema-intakes?limit=20&offset=0` | List the tenant's saved intakes; maximum page size 100 |
| `GET /v1/schema-intakes/{id}` | Read the brief and job status/result |
| `POST /v1/schema-intakes/{id}/cancel` | Request cancellation |
| `POST /v1/schema-intakes/{id}/confirm` | Create a project from an explicitly reviewed completed draft |

The creation body contains `name`, `description`, `data_requirements`, `request_key`, `data_approved: true` and `cost_approved: true`. Confirmation contains the reviewed `name`, `description`, `data_requirements`, `questions`, `expected_proposal_sha256` from the result and `confirmed: true`.

Each intake creates at most one reviewed project. An identical confirmation returns that project; a different confirmation after creation conflicts. Saved suggestions show **Open reviewed project** after confirmation. The detail endpoint includes its `confirmed_project` identifier and name. To change an existing project's policy later, use the [policy-successor workflow](policy-versioning-guide.md).
