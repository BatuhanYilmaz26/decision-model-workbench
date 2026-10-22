# Generate and review domain training data

Turn a coverage description and selected training examples into a saved dataset of candidates. You inspect the exact outgoing request, approve data transfer and charges, then review every generated label before training.

[Guide index](README.md) · [Data formats and review](data-guide.md) · [Project overview](../README.md)

## Prepare your project

1. Create a project with explicit decision questions, allowed outcomes and domain policy.
2. Import or enter examples, identify their operational or synthetic origin accurately, and review their labels.
3. [Reserve real validation, calibration and test records](reservation-guide.md) before generation. Only the chosen snapshot's training records can become seeds.
4. Configure a compatible **generative chat teacher** in the API and worker. A decision-scoring model cannot write new examples.

The workflow uses nonstreaming `POST /chat/completions` relative to the configured base URL, JSON-object output, one choice and a complete text response. Your selected model must support these fields. JSON mode alone does not guarantee schema adherence; the worker validates the entire candidate batch and rejects incomplete, refused, tool-call or malformed output. [JSON output behavior](https://developers.openai.com/api/docs/guides/structured-outputs)

## Configure a local or hosted teacher

For a local Ollama server, set the base URL to `http://127.0.0.1:11434/v1` and choose a model already installed on that server. No teacher credential is required for an unauthenticated local endpoint. Ollama implements a subset of the compatible API and can also route to cloud models; a loopback URL alone does not establish local-only processing. Check the selected model and server configuration. [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)

For a hosted teacher, use its approved HTTPS base URL and model identifier. URLs cannot contain credentials, queries or fragments. Configure `DMT_TEACHER_API_KEY` only in the worker environment. Domain policy and selected examples go to that endpoint; review the provider's processing, retention and pricing terms first.

Set environment variables **before starting** the application. Python commands do not automatically load `.env.example`. With Compose, copy the relevant settings into your private `.env`; the manifest shares nonsecret settings between API and worker and supplies the key only to the worker. Within a container, `127.0.0.1` refers to that container; use an approved HTTPS gateway or an explicitly configured network arrangement for a teacher running on another host.

| Setting | Default / purpose |
|---|---|
| `DMT_TEACHER_BASE_URL` | Required teacher API base URL |
| `DMT_TEACHER_MODEL` | Required compatible generative model identifier |
| `DMT_TEACHER_API_KEY` | Optional worker-only credential |
| `DMT_GENERATION_TIMEOUT_SECONDS` | `120`; allowed range 5–180 |
| `DMT_GENERATION_OUTPUT_TOKENS` | `6000`; allowed range 256–16384 |
| `DMT_GENERATION_TOKEN_PARAMETER` | `max_tokens` or `max_completion_tokens` |
| `DMT_GENERATION_TEMPERATURE` | `0.7`; a number from 0 to 2, or `omit` |
| `DMT_GENERATION_CALLS_PER_DAY` | `20`; rolling 24-hour request reservations per tenant and endpoint |
| `DMT_GENERATION_MAX_INFLIGHT` | `1`; allowed range 1–4, including unresolved outcomes |
| `DMT_GENERATION_MAX_ATTEMPTS` | `3`; allowed range 1–5 per retry chain |
| `DMT_GENERATION_INPUT_USD_PER_MILLION` | Optional input-token price |
| `DMT_GENERATION_OUTPUT_USD_PER_MILLION` | Optional output-token price; configure both rates or neither |
| `DMT_GENERATION_MAX_ESTIMATED_REQUEST_USD` | Optional per-request token-cost estimate limit |
| `DMT_GENERATION_MAX_ESTIMATED_DAY_USD` | Optional rolling 24-hour reserved-estimate limit |

Some models require `max_completion_tokens` and omit configurable temperature. Select the contract supported by your model; the workbench does not translate every provider's API. Reasoning tokens may consume a model's completion allowance. [Chat completion parameters](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

Token prices are operator-entered assumptions. An empty price is **unknown**, not free. The input planning allowance is the UTF-8 request byte count plus 1,024; the output allowance is the configured token limit. This is not an exact tokenizer count or guaranteed bill. Estimates exclude infrastructure, fees and price changes. Reported response usage is retained when valid, but is not a billing attestation. Local estimate limits do not enforce an account-wide monetary ceiling. Configure provider-side spending controls separately.

## Choose the teacher output format

Set `DMT_TEACHER_RESPONSE_FORMAT` to `json_object` (default) or `json_schema` before starting the API and worker. For a directly started personal Streamlit app, use the same choice under **Optional local model and synthetic generation settings**. The generation screen shows the active format; the request preview includes the complete schema when selected. See [teacher setup](setup.md#configure-a-teacher) for commands and credential handling.

JSON Schema mode describes the row count, evidence types, question IDs and probability keys. It uses `strict: false`: arbitrary JSON evidence remains possible, and the server still validates every candidate, including normalized probabilities, before saving a batch. Invalid output is not repaired or automatically resent. Both formats produce pending-review data, preserve fixed real holdouts and use the same approval, accounting and recovery workflow. Choose a format supported by your endpoint; [LM Studio](https://lmstudio.ai/docs/developer/openai-compat/structured-output) and [vLLM](https://docs.vllm.ai/en/latest/features/structured_outputs/) document schema output.

The standalone `domain-decisions generate` command also accepts `--response-format json_schema`. This CLI creates unreviewed candidate files; use the application for durable approvals, independent reservations and review history.

## Preview and approve a batch

In the preparation tab, select a reserved snapshot and find **Generate training candidates**.

1. Describe the gaps to cover: rare outcomes, missing evidence, policy boundaries, negation, language or input length. Specify what makes a label correct.
2. Request 1–50 candidates and select 0–10 reviewed training seeds. The selector uses the first requested records in the chosen snapshot's training split; inspect them in the preview.
3. Select **Preview synthetic request**. Read the exact policy, questions and seed states/targets, configured endpoint/model, output allowance and cost estimate.
4. Approve the displayed data transfer and any charges, then select **Approve and queue synthetic generation**. Only a workspace owner can approve a request.
5. Use **Refresh generation request** to inspect the saved job. Closing the browser does not discard it. Repeating the same approval reopens that job rather than contacting the teacher again.

An approval binds the snapshot, policy version, seed identities, coverage, complete outgoing body, teacher settings, price assumptions and application source. A changed preview must be approved again. Configuration drift at execution fails before dispatch.

The saved history shows the job, request identity, selected seeds, response metadata and candidate dataset. The private response archive can contain domain data; it is included in workspace backups. API history omits its raw body. Requested and reported teacher names identify the configured route and returned string, not immutable model weights.

## Review and extend training

Successful output is a **pending, unreviewed** dataset. Every row retains synthetic origin, seed ancestry and request/policy/snapshot/response references. A complete valid response is required; a partially valid batch is not published.

Open the data tab, refresh collections and select the returned dataset. Check evidence and labels independently, correct mistakes, reject unusable examples and record review notes. Inspect copying, personal data, class balance, duplicates and policy consistency. Teacher confidence is not ground truth.

Prepare an extension using the original snapshot as the base and the reviewed synthetic revision as additional data. The original real evaluation roles remain fixed; seed descendants stay in training. Then follow [training](training-guide.md) and [evaluation](evaluation-guide.md). More candidates do not establish improved domain performance.

## Cancel, inspect and retry

Cancellation stops the local transport when it can; a remote teacher may still process or charge for a received request. The supervised deadline covers child startup and communication, subject to operating-system process creation/termination limits. Neither action proves upstream cancellation.

| Saved outcome | Next action |
|---|---|
| Pending | Refresh or cancel the existing job |
| Not dispatched | The terminal attempt has no committed dispatch receipt; an explicit retry can be prepared |
| Response received | Inspect the result or validation failure; a failed attempt can be explicitly retried |
| Unknown | Check local transport exit and upstream completion; capacity remains reserved |
| Owner attested complete | The checked unknown outcome is closed locally; an explicit retry can be prepared |

For an unknown terminal outcome, supply both confirmations, an evidence reference and a note. This records an owner attestation and frees local capacity; it does not verify a refund, provider receipt or billed amount. Unresolved dispatched calls retain capacity across the daily window. Failed, cancelled and unsent reservations still count toward their original daily allowance. Estimated budgets retain the greater of the planned and available usage-based estimate; earlier unpriced requests cannot be treated as zero when a daily estimate limit is enabled.

Select **Prepare an explicit retry** only after inspecting a terminal failed, cancelled or interrupted attempt. Preview and approve the retry separately. It preserves the original request and teacher configuration, has a bounded attempt number and cannot branch into competing retries. A source/configuration change requires a separately approved new batch. Existing candidates are reviewed rather than automatically regenerated.

To intentionally request another batch, select **Prepare a separate new generation batch**, preview it and approve it. There are no automatic provider retries after timeouts, transport loss or candidate-validation failure.

## Integrate through the workspace API

Authenticate as the workspace owner. The interactive API schema is available at `/docs` on your running API server.

| Method and path | Purpose |
|---|---|
| `GET /v1/generation-teacher` | Inspect configured teacher availability |
| `POST /v1/projects/{project}/generation-preview` | Preview `{snapshot, count, seed_count, coverage, retry_of}` |
| `POST /v1/projects/{project}/jobs` | Submit `kind="generate"`, a stable `request_key`, the selection and approvals |
| `GET /v1/projects/{project}/generation-plans?limit=20&offset=0` | Page through saved requests |
| `GET /v1/projects/{project}/generation-plans/{plan}` | Inspect request, outcome and candidate dataset |
| `POST /v1/projects/{project}/jobs/{job}/cancel` | Request cancellation |
| `POST /v1/projects/{project}/generation-plans/{plan}/settle` | Record a checked unknown outcome |

Submission `config` requires `data_approved=true`, `cost_approved=true` and `expected_preview_sha256` equal to the preview's `sha256`. Preserve the same request key when recovering a lost submission response. Changing the key creates a new approval attempt subject to quotas. Settlement requires the current `status_sha256` as `expected_status_sha256`, both confirmation fields, `evidence_ref` and `note`.

## Recovery and maintenance

Stop all services before upgrading a workspace to schema 11, and restart them with the same application version. Older schema-8/9/10 applications refuse the newer workspace. [Recovery](recovery-guide.md) validates the archived request, response, candidate dataset and retry/settlement references without regenerating historical prompts. Restoring unfinished jobs does not replay teacher calls. Historical generation jobs without a request archive cannot dispatch under this workflow; approve a new request instead.

The standalone generation helper is a maintenance tool with bounded output and a socket timeout. It does not provide the application's durable approvals, supervised whole-call deadline, budget history or retry reconciliation. Use the application or workspace API when those controls are required.

For a maintenance export, provide a file containing only reviewed **training** seeds. `--seed-count` selects the first 0–10 records; the default selects up to three available records. Only the selected records are sent and recorded as ancestors. The specification supplies the reviewed domain policy and question map. Choose a new output path:

```bash
python -m decision_workbench.cli generate \
  --input runs/domain/train.jsonl --spec domain/spec.json \
  --base-url http://127.0.0.1:11434/v1 --model YOUR_LOCAL_TEACHER \
  --seed-count 3 --count 10 --out runs/domain/candidates.jsonl
```

The command does not reserve holdouts or verify that your source file excludes evaluation examples. Prepare independent roles first, and keep the generated output unreviewed until it has been checked against your policy. Never supply validation, calibration or test records as the seed file.
