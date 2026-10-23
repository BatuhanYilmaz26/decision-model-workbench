# Generate and review training data for your domain

<a id="generate-and-review-domain-training-data"></a>

[Install the application](../README.md#run-on-your-computer) · [Other workflows](README.md)

Describe the examples you need, supply reviewed examples of the intended format, and use a generative teacher to propose additional training candidates. This walkthrough covers setup, generation, review and export. It does not require downloading the decision model you intend to train later.

**For the app workflow you need:** the installed CPU application, a project with confirmed questions, real records reserved for evaluation, and access to one compatible generative teacher. A hosted teacher needs your API key and can incur charges; a local teacher needs its own running server, model assets and sufficient memory. Decision-scoring APIs do not write synthetic examples.

Only need candidate files from a few examples before you have evaluation data? Use the standalone option below. Independent real evaluation records are still required before relying on a trained model.

<details>
<summary>Generate a candidate JSONL file without starting the app</summary>

Activate the installed application environment. Start a compatible local generative server separately; the following command does not install or download its model.

Create a private `data/` folder in the repository root, then create `data/spec.json` with your policy in natural language and the approved questions. This fictional format example uses one category question:

```json
{
  "description": "Choose review when required approval is missing; choose process when it is explicitly present.",
  "questions": {
    "route": {
      "type": "choice",
      "instructions": "Which processing route does the supplied evidence support?",
      "criteria": {
        "process": "Required approval is explicitly present.",
        "review": "Required approval is missing."
      }
    }
  }
}
```

Create `data/train-seeds.jsonl` with your inspected training examples. Each line is a complete JSON object; use the same questions and outcome keys as the specification. The fictional seed below illustrates a complete probability target:

```jsonl
{"id":"seed-1","group_id":"source-1","state":"The required approval is missing.","questions":{"route":{"type":"choice","instructions":"Which processing route does the supplied evidence support?","criteria":{"process":"Required approval is explicitly present.","review":"Required approval is missing."}}},"gold":{"route":{"probabilities":{"process":0.0,"review":1.0}}},"metadata":{"origin":"synthetic","reviewed":true}}
```

The standalone tool trusts your seed file; setting `reviewed` is your assertion that you inspected it, not an authenticated app review. Put real source records in this file only after reviewing them, record their origin accurately and never include held-out evaluation records. For additional coverage needs, extend the specification's description without contradicting its labeling policy.

From the repository root, with a compatible teacher already serving at the stated URL:

```bash
python -m decision_workbench.cli generate --input data/train-seeds.jsonl --spec data/spec.json --base-url http://127.0.0.1:11434/v1 --model YOUR_SERVED_MODEL_ID --seed-count 1 --count 10 --out data/candidates.jsonl
```

Replace the model ID with the one your server actually serves. Choose a new output path. This command requests candidates and writes unreviewed JSONL; inspect every evidence/label pair before use. If your server requires schema output, add `--response-format json_schema`. For a hosted compatible endpoint, supply its HTTPS base and the `TEACHER_API_KEY` environment variable through your secret mechanism; this standalone key name differs from the app's `DMT_TEACHER_API_KEY`. The request transmits the specification and selected seeds and may incur charges.

This file workflow has no durable app approvals, billing ledger, reserved holdouts or automatic release. You can later import its candidates into the app, review them there and add them only to the training split.

</details>

## Prepare your project

If you already have a reserved snapshot, continue to teacher configuration. Otherwise:

1. Start `domain-platform run`, open the printed localhost address and create a domain project. Describe the evidence, policy and data requirements; confirm the questions and allowed outcomes.
2. In **Data and review**, enter examples or upload CSV, XLSX, TSV, JSON or JSONL. Use the downloadable project CSV template or map your existing columns to evidence, IDs/source families and outcomes. Validate the preview, save the collection, inspect every label and save an approved reviewed version.
3. Mark hypothetical records as synthetic/hypothetical. Reserve independent real examples for validation, calibration and test; a handful of illustrative seeds does not replace those records.
4. In **Prepare and generate**, select the reviewed revisions, leave **Preserve holdouts** unset for a first reservation, and choose **Reserve train / validation / calibration / test**. Follow the preparation job and select its resulting snapshot.

Only the selected snapshot's training records can become generation seeds. The [training walkthrough](product-workflow.md) explains preparation and its quality requirements if your records are not ready yet. No candidate generation is triggered just by starting the app.

## Configure a local or hosted teacher

For personal localhost use, you can configure the teacher on the startup screen:

1. Stop an existing launcher with **Ctrl+C** and wait for it to exit. In the activated application environment, run:

```bash
python -m streamlit run app.py --server.address 127.0.0.1
```

2. Open the printed browser address. Select **On this computer** and expand **Optional local model and synthetic generation settings** before starting the workspace.
3. Enter the teacher base URL and served model ID. For a local Ollama server the base is `http://127.0.0.1:11434/v1`; for a local LM Studio server it is commonly `http://127.0.0.1:1234/v1`. Start that teacher separately and use a model actually served by it. For a hosted teacher, use its documented compatible HTTPS base and enter its key in the masked teacher field.
4. Select a supported response format, then **Start local workspace**. Reopen your project and reserved snapshot. The generation controls show the configured teacher and endpoint.

The endpoint must support nonstreaming `POST /chat/completions` with the selected JSON output format. Ollama supports a subset of the compatible API and may forward to cloud models; a localhost URL does not prove local-only processing. Check the teacher's privacy, output-format and model support. [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility), [LM Studio structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output).

For `domain-platform run`, containers or shared workspaces, configure the environment **before startup** using the expandable reference at the end of this page. The automatically connected launcher does not show the personal setup form. Restart services to change teacher settings. Do not paste a teacher key into a dataset or the workspace-login credential field.

## Choose the teacher output format

Use `json_object` (the default) or `json_schema`, according to the teacher's documented support. JSON Schema mode sends `strict: false` so arbitrary text/JSON evidence remains possible. The server validates every candidate in either mode; valid JSON alone does not establish correct labels. Invalid or incomplete output is rejected without an automatic resend or format fallback.

The selected output contract is the project's canonical `state/questions/gold` format. Later model adapters convert approved data to the native training representation. Changing the teacher does not change the decision model's required outcomes.

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

### Download data or continue training

After preparing the extension, select its **Reserved data snapshot** and expand **Download reserved study data**. Confirm **I approve downloading this snapshot's evidence and reviewed labels**, choose **Prepare reserved study download**, then **Download reserved canonical study** to save the four canonical JSONL roles and checksum manifest. The archive contains evidence and gold labels: keep it private. This exports the reviewed study, not the target model's native tokenizer/tensor files.

To train in the app, select the extended snapshot in **Training jobs** and continue with the [training walkthrough](product-workflow.md#5-train-and-inspect-the-export). For a standalone native recipe, use only the selected model guide's conversion/export instructions. Generating or downloading data does not train or release a model.

**You now have:** reviewed additional training examples, preserved real evaluation sets, and a study you can export or train from.

<details>
<summary>Teacher environment settings, budgets and response-format details</summary>

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

### Startup commands and schema-assistant settings

The API and worker read matching `DMT_TEACHER_BASE_URL` and `DMT_TEACHER_MODEL` settings. Only the worker needs `DMT_TEACHER_API_KEY`. It does not load `.env` automatically. Configure these before starting services. Only the operator chooses endpoints; user job payloads cannot store credentials or arbitrary teacher URLs.

Use an approved generative endpoint supporting `POST /chat/completions` and either `json_object` or `json_schema` output. Localhost HTTP or remote HTTPS are supported. Choose the format documented by your teacher; the workbench does not automatically translate other APIs or retry with another format. No generation occurs simply by opening the application. Follow the generation steps above for previews, approvals, cost estimates, history and explicit retries.

For a local teacher already running at port 1234, set these before starting the workbench. Replace the model identifier with the name served by your endpoint:

```powershell
$env:DMT_TEACHER_BASE_URL = "http://127.0.0.1:1234/v1"
$env:DMT_TEACHER_MODEL = "YOUR_SERVED_MODEL_ID"
$env:DMT_TEACHER_RESPONSE_FORMAT = "json_schema"
domain-platform run
```

```bash
export DMT_TEACHER_BASE_URL="http://127.0.0.1:1234/v1"
export DMT_TEACHER_MODEL="YOUR_SERVED_MODEL_ID"
export DMT_TEACHER_RESPONSE_FORMAT="json_schema"
domain-platform run
```

Leave `DMT_TEACHER_RESPONSE_FORMAT` unset or blank to preserve `json_object`. The setting applies to synthetic candidates and schema suggestions; set it identically in API and worker services. In a directly started Streamlit app, expand **Optional local model and synthetic generation settings**, enter the teacher address/model/key and choose **Teacher response format** before **Start local workspace**. **Use configured default** preserves the environment setting. The launcher `domain-platform run` connects automatically, so configure its teacher through the environment before startup. Restart services to change settings; queued requests need their approved configuration or a new approval.

JSON Schema mode sends a schema with `strict: false`, allowing arbitrary text/JSON evidence and teacher-proposed question names. This is a format hint, not a promise of schema adherence or correct labels. Complete responses are validated and still require human review. Check your selected model/server's compatibility. [LM Studio structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output), [vLLM structured output](https://docs.vllm.ai/en/latest/features/structured_outputs/)

The optional [schema assistant](schema-assistant-guide.md) uses the same teacher to draft questions before project creation. Configure the API and worker consistently, then approve the domain-brief request in the browser. Its timeout and daily request limit are configurable separately; every proposed question still requires owner review.

If your teacher requires `max_completion_tokens` or rejects temperature, set
`DMT_SCHEMA_TOKEN_PARAMETER=max_completion_tokens` and
`DMT_SCHEMA_TEMPERATURE=omit` for schema drafts, plus the corresponding
`DMT_GENERATION_TOKEN_PARAMETER` and `DMT_GENERATION_TEMPERATURE` for synthetic
candidates. In direct personal startup, **Teacher token limit parameter** and
**Omit teacher temperature** apply those choices to both workflows. Detailed
[schema controls](schema-assistant-guide.md#set-up-the-teacher) and
[generation controls](synthetic-generation-guide.md) explain their independent
output allowances. Restart services after changing settings.

</details>

<details>
<summary>Cancellation, uncertain requests, API integration and maintenance</summary>

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

</details>
