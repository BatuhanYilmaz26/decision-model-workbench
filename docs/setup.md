# Local setup and user workflow

[All guides](README.md) · [Project overview](../README.md)

Install the application, start its API and worker, then open the browser interface. Run the commands from the repository root. Data preparation supports Python 3.10+; the Windows application profile uses Python 3.13. CPU baseline training uses the application environment; Laya, Unsloth and Vega use separately configured model environments. See [dependency profiles](../requirements/README.md) for the Linux container and Windows model versions.

## Install and start

For complete Windows and Linux/macOS commands, including cloning and activation, follow the [local quick start](../README.md#run-on-your-computer). If you have not cloned the repository yet:

```bash
git clone https://github.com/BatuhanYilmaz26/decision-model-workbench.git
cd decision-model-workbench
```

Using your selected application Python (3.13 on Windows or 3.12 on Linux), create a virtual environment:

```bash
python -m venv .venv
```

Activate using `.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` on Linux/macOS. Then:

```bash
python -m pip install -r requirements.txt
python -m pip check
domain-platform run
```

The root requirements file installs the project with its UI and CPU application dependencies, using the definitions in `pyproject.toml`. It does not install GPU training runtimes or every optional provider client. Select a [dependency profile](../requirements/README.md) when you need an exact platform lock or optional model/provider packages.

Open the displayed browser address. Your personal workspace opens automatically: no account, workspace key or cloud subscription is required. The launcher starts an authenticated loopback API, independent worker and Streamlit client. CPU baseline training needs no GPU or model download. Projects persist in `data/local-workspace`; Ctrl+C stops the services. Use `--home` to choose a different personal directory.

Personal mode defaults model and generated-module caches to a private `model-cache` directory beside your workspace (`data/model-cache` with the default layout). Explicit `DMT_MODEL_CACHE`, `HF_HOME` and `UNSLOTH_COMPILE_LOCATION` settings take precedence. Use writable storage for these caches; a read-only global cache can prevent model-library startup.

Alternatively, start the browser app directly from the repository root:

```bash
python -m streamlit run app.py --server.address 127.0.0.1
```

Choose **On this computer → Start local workspace**. The app starts its API and worker once for the browser-server lifetime, rather than on every page refresh. Optional local settings let you select an existing model-environment Python executable and a local or approved hosted synthetic teacher before starting. Restart the browser server to change those settings; uploads and project history are retained. Closing a browser tab does not cancel jobs. Stopping the browser server stops the personal services; unfinished jobs are reconciled after restart and are not automatically replayed.

## Personal and shared workspaces

Personal mode is for one person on a loopback-bound server. Browsers able to access that server share the same local owner identity. The API still authenticates every request using a random credential created for each service lifetime; only its hash is written to temporary service configuration. The browser never asks you to copy that credential. Bind shared deployments to their intended network address and use authenticated shared-workspace connections instead; personal mode is disabled when the browser server is not bound to a literal loopback address.

Personal projects use tenant `local`. When configuring scoped hosted-model or cloud profiles for this mode, include that tenant and the intended project IDs. Profile credentials stay in operator configuration; the UI selects approved profiles rather than accepting arbitrary provider endpoints in training jobs. Backup keys, vaults and schedules remain operator CLI workflows linked from the guides.

To use separate people/roles, stable API credentials, or an existing operator workspace:

```bash
domain-platform provision-key --file data/operator-keys.json --tenant my-enterprise --actor owner --role admin
domain-platform run --keys-file data/operator-keys.json
```

Provisioning prints a bearer credential once and stores its hash. Choose **Shared workspace**, enter the API address and that credential, then connect. This mode retains the existing default `data/workspace`; it does not merge projects with the personal workspace. For a team-hosted API, use its HTTPS address and the administrator-issued credential. Selecting cloud training does not require moving your workspace: you can retain local projects while sending an explicitly approved training study to a provider.

The credential file must stay operator-controlled. Roles are admin, editor, reviewer and inference. Provision separate people/applications with separate actor names. Live key rotation/revocation is implemented; see the [authentication guide](authentication-guide.md). The trusted JWT adapter is available, while interactive enterprise SSO and real identity-provider qualification require separate operational validation.

## Use the application

The entry screen links local installation, hosted decision APIs, synthetic teacher configuration and AWS, Azure, GCP, Modal and RunPod training guides. After connection, **Computer, model environment and provider setup → Check available hardware and model packages** inspects the configured worker and serving environments without downloading models. It reports installed packages, CUDA/MPS availability and GPU memory. Availability is separate from successful backend execution, sufficient free memory and domain quality. CPU baseline is the default training backend. **Training location** selects this computer or cloud GPU; provider selection and approval appear under **Train on a cloud GPU** after reserving data. Hosted evaluation is a separate action because it does not train provider weights.

1. **Create a project.** Describe the domain/policy and separately describe required data, rare cases, languages and evidence fields. Build questions with the category/yes-no/rating controls. Confirm the schema. Advanced JSON import is optional.
2. **Supply examples.** Enter individual examples or a batch of up to 50 into a new or existing collection, or upload CSV/XLSX/TSV/JSON/JSONL. Preview worksheets, map evidence/ID/source-family/label columns and translate source values to allowed outcomes. Missing labels stay unknown. Choose the operational/synthetic source type accurately; the direct example form defaults to synthetic.
3. **Review.** Choose draft, approved or rejected status. Pending records block preparation; rejected records stay in history and are excluded from new snapshots with reviewer reasons. Select a dataset version and a page of records. Choose labels, record a note and save. Unknown selectors stay unassigned; partial annotations can be saved unapproved, and approval requires every question. Use Analyze data readiness to inspect coverage, duplicates, conflicts and missing labels. Probability targets are an advanced option. Every change stores an immutable revision of the changed record; imported review flags are reset. Concurrent stale reviews return a conflict and must be refreshed. Select the final revision of each upload for preparation.
4. **Reserve.** Queue preparation after all selected labels are reviewed. Refresh the workspace to see the job result and snapshot. Four independent real groups are the format minimum, not evidence of sufficient quality.
5. **Generate candidates.** Select a reserved snapshot, describe coverage and explicitly approve sending the policy/seeds to the configured teacher. The worker uses only training seeds. Review the resulting candidate dataset. Extend the original snapshot with that reviewed synthetic version using the holdout-preservation selector; do not re-split a study after inspecting its test.
6. **Set gates and train.** An admin saves acceptance criteria and minimum reviewed training/calibration examples per outcome in Training jobs before the first training submission. Choose a snapshot and backend, queue training and inspect the result. That submission freezes and pins the gate version. The CPU baseline is a real fixed-schema lexical classifier. Its training split must contain every allowed class and hard labels. Unsloth needs the GPU worker configuration below.
7. **Evaluate/release.** Inspect sample counts, class coverage, accuracy/F1/calibration and confusion matrices. Admin activation applies the accuracy, macro-F1, ECE and sample-count criteria frozen before training, and requires every test class. The default form requests 100 real test examples per question. Criteria cannot be changed at release time. These basic gates do not replace domain-owner qualification.
8. **Use/rollback.** Call Decisions or the HTTP endpoint. Low-confidence answers are marked abstained. Activate an earlier eligible model version with a rollback reason; release history remains in audit.

The release endpoint supports CPU baseline, native Laya, the pinned Unsloth profile and the separately pinned [Vega engine/domain-adapter profile](vega-guide.md). Decision-head releases require qualified export/reload, complete file integrity, matching schema and the frozen owner gates. Vega also requires paired reviewed applicability data and explicit positive-acceptance/negative-false-open criteria.

## Policy changes and workspace upgrades

An owner can create a new project with **Policy successor of** pointing to an existing project. Supply and confirm the successor schema/policy. Its policy version increments, and its data, jobs and releases have a separate scope; the earlier project/model remains available. Data is not silently copied. If previous test outcomes informed the new policy or gates, reserve fresh independent gold for the new study.

The local store upgrades supported older schemas to schema 9 in a transaction on startup. Existing dataset hashes and pinned snapshots are preserved; old full-row JSON is converted to normalized roots. New reviews store a single changed record; appends add compact membership manifests. Historical membership and hashes remain pinned. Back up and stop all services before an upgrade using the [operator recovery commands](recovery-guide.md); that guide lists the supported backup schema versions and recovery constraints. Older applications refuse schema 9 rather than ignore its approved generation and reservation contracts; mixed-version operation is unsupported. A legacy training run without frozen criteria cannot be newly activated; retrain under approved criteria in a successor project. An already active version is preserved by migration.

Dataset lists return compact metadata. `GET /v1/projects/{project}/datasets/{dataset}/rows?limit=50&offset=0` returns a bounded page; the maximum is 200. Historical versions are readable, and only the latest collection revision accepts another review or append. Downloads reconstruct and integrity-check the immutable version.

## Configure a teacher

The API and worker read matching `DMT_TEACHER_BASE_URL` and `DMT_TEACHER_MODEL` settings. Only the worker needs `DMT_TEACHER_API_KEY`. It does not load `.env` automatically. Configure these before starting services. Only the operator chooses endpoints; user job payloads cannot store credentials or arbitrary teacher URLs.

Use an approved generative endpoint supporting `POST /chat/completions` and either `json_object` or `json_schema` output. Localhost HTTP or remote HTTPS are supported. Choose the format documented by your teacher; the workbench does not automatically translate other APIs or retry with another format. No generation occurs simply by opening the application. Follow the [synthetic-generation guide](synthetic-generation-guide.md) for previews, approvals, cost estimates, history and explicit retries.

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

## Configure model training and serving

Use the [tested dependency profiles](../requirements/README.md) and [training guide](training-guide.md). Install this project into the isolated model environment. In PowerShell, configure both the worker and resident inference runtime before starting the launcher:

```powershell
$env:DMT_WORKER_PYTHON = (Resolve-Path .venv-models/Scripts/python.exe).Path
$env:DMT_MODEL_PYTHON = $env:DMT_WORKER_PYTHON
$env:DMT_MODEL_CACHE = Join-Path (Get-Location) "data/model-cache/hub"
$env:HF_HOME = Join-Path (Get-Location) "data/model-cache"
$env:UNSLOTH_COMPILE_LOCATION = Join-Path (Get-Location) "data/model-cache/unsloth_compiled_cache"
.venv/Scripts/domain-platform.exe run
```

The API and browser stay in the lightweight platform environment. `DMT_MODEL_PYTHON` falls back to `DMT_WORKER_PYTHON`, then the API interpreter. Native Laya supports its configured automatic device selection; Unsloth's selected application profile requires compatible CUDA dependencies. Vega has its own package/assets and applicability requirements. CPU and Apple MPS support differ by engine; the application does not translate CUDA training to an arbitrary GPU. Use the CPU baseline or a qualified cloud GPU when your hardware cannot run the selected model. Run `domain-worker` directly in the model environment if starting services separately; use the same `DMT_HOME`.

Unsloth generates optimized trainer modules when its runtime is imported. Its default `unsloth_compiled_cache` location is relative to the process's working directory, so imports from the repository root can create that folder beside the source. These are generated library files and are excluded from Git. The setting above directs future generation into the private model cache; set it before importing Unsloth or launching the model services. You do not need to edit or commit those generated modules. [Upstream cache setting](https://github.com/unslothai/unsloth-zoo/blob/main/unsloth_zoo/compiler.py)

Laya/Unsloth jobs need a Hub model ID, an exact 40-character revision, bounded settings and a prepared snapshot. Their worker child has a four-hour ceiling, retains private logs and registers the completed export. Use the pinned frozen-encoder Laya or Unsloth encoder-LoRA profile; check memory, export/reload and inference before changing the encoder, backbone or precision. Other native profiles use their linked settings and deadlines. Optimizer resume is not supported.

One resident model process serves head requests. Its OS resource lease is shared with training within the workspace. Queued/running GPU work unloads inference and returns HTTP 503 with a retry hint; inference resumes when that work finishes or is cancelled. Idle models unload after 30 seconds by default. Operator variables `DMT_INFERENCE_IDLE_SECONDS`, `DMT_INFERENCE_TIMEOUT_SECONDS` and `DMT_MODEL_LOAD_TIMEOUT_SECONDS` bound residency/request/loading. The browser allows 420 seconds for release/decision requests, covering the default 240-second model load and 120-second inference deadlines plus response overhead. Set `DMT_UI_MODEL_TIMEOUT_SECONDS` in the web process when changing those deadlines. If a client/network timeout occurs, refresh and inspect the active version before retrying: an in-flight release may commit after the client disconnects. This is single-host coordination; multiple workspaces and multi-host capacity scheduling need separate qualification.

### Configure native Perplexity training and serving

The open v1.1 profile uses a separate Linux/CUDA environment and a complete
preinstalled checkpoint. Set `DMT_PERPLEXITY_PYTHON` and
`DMT_PERPLEXITY_CHECKPOINT` in the API/worker environment before starting the
workspace. These are operator paths, not fields accepted from training requests.
The app never downloads this large checkpoint. Follow the [native application
setup](models/perplexity-native.md#train-through-the-application) for requirements,
resource floors and model-load/UI deadlines. Native full-weight jobs use a
24-hour default deadline; `DMT_PERPLEXITY_TRAINING_TIMEOUT_SECONDS` accepts
600–604800 seconds. For managed native cloud studies and independent admission,
follow the [Perplexity cloud training guide](remote-training/perplexity.md).
The [hosted API workflow](models/perplexity.md) needs no native GPU.

## Run services individually

For the Linux CPU application image and a repeatable isolated end-to-end check, see the [container setup and qualification guide](container-guide.md). GPU model environments remain separately qualified.

Set `DMT_HOME` to the same workspace for API/worker and `DMT_KEYS_FILE` to the credential file for API:

```bash
domain-platform serve --host 127.0.0.1 --port 8080
domain-worker
python -m streamlit run app.py --server.address 127.0.0.1
```

The API exposes `/health`, local storage/authentication-configuration/operational-collector `/ready`, and interactive `/docs`. Readiness does not certify a GPU, trained model, cloud or worker availability.

```http
POST /v1/projects/PROJECT_ID/decisions
Authorization: Bearer YOUR_INFERENCE_CREDENTIAL
Content-Type: application/json

{"state":"Input evidence for your domain","threshold":0.8}
```

Tenant and role come from the credential, not request-supplied identifiers. Responses include model, release revision, schema hash and probabilities.

## Portable containers

For Compose, provision `data/config/operator-keys.json` and mount its directory so atomic key rotation becomes visible. Local launcher credentials can remain at `data/operator-keys.json`. The manifests can be inspected with:

```bash
docker compose config --quiet
docker compose up --build
```

Ports bind to loopback. The named workspace volume persists data and artifacts; the operator file mounts read-only. The web service's operator-configured internal API address may use HTTP on its private container network. Arbitrary remote user-entered addresses require HTTPS.

Follow the [CPU container guide](container-guide.md) or the separate [GPU model guide](model-container-guide.md), then run the lifecycle verifier on your built image. Check mounted data, permissions and restart behavior in the target environment. This SQLite profile coordinates services on one host; use a separate storage and scheduling design for multi-host deployment.

## Checks and maintenance

```bash
python -m unittest discover -s tests -v
python scripts/check_repository.py
python -m pip check
```

The standalone preparation CLI and research scripts remain maintenance tools. They do not replace the application lifecycle. Research refresh requires manual review of narrative, prices and benchmark methods.

For consistent workspace/model backups, trusted checksum verification and fresh-directory recovery, follow the [operator recovery guide](recovery-guide.md). Restored unfinished jobs require explicit resubmission; credentials are supplied separately.

Use the [authentication guide](authentication-guide.md) for key rotation, revocation and trusted JWT configuration. Paste an existing API access token into the browser credential field; interactive identity-provider login and automatic token refresh are not available.

## Hosted provider alternative

Use the [hosted decision workflow](hosted-decisions-guide.md) to evaluate provider weights and fit a local calibration policy. Studies and later inference send state to the selected provider. Configure scoped profiles, check account behavior and data-use terms, and set measured release criteria before enabling the route.

## Managed cloud training

Choose [managed AWS](managed-cloud-training.md), [GCP](managed-gcp-training.md), [Azure](managed-azure-training.md), [RunPod](managed-runpod-training.md) or [Modal](managed-modal-training.md) to submit frozen studies from the application. Install the selected provider's control dependencies in the API/worker environment, configure approved infrastructure and obtain separate owner approvals for data transfer and paid compute. Check the provider's retained-storage charges and termination behavior. [Provider compatibility checks](verification.md#provider-compatibility-checks)

## Local semantic review

Before reserving a domain study, optionally configure the [local semantic audit workflow](semantic-data-guide.md). Its operator profile pins approved cached model/tokenizer assets and an isolated CPU runtime; the baseline application image does not supply those model assets. Required scoped profiles block unchecked reservations and require immutable owner/reviewer adjudication of every candidate pair. This complements source-family and temporal checks; domain threshold/recall qualification remains required.

## Reviewed category discovery

Use the [taxonomy workflow](taxonomy-guide.md) for manual proposals or consented TypeLLM discovery. Configure scoped operator profiles and keep credentials in server/worker configuration. Review proposals and confirm a successor schema; discovery examples stay training-only, and existing labels/releases are not transferred. Evaluate category quality and provider costs on representative records before relying on discovery.

## Separate Liquid model environment

For d1-3B, install the separate [Liquid profile](models/liquid.md#train-with-the-local-trl-runner), set `DMT_LIQUID_PYTHON` in the API and worker environments, and restart the services. The [application workflow](models/liquid.md#train-and-release-through-the-localhost-application) uses that interpreter for training and resident serving while keeping the CPU application and existing Laya/Unsloth/Vega profiles separate. Optional `DMT_LIQUID_CACHE` selects its private checkpoint cache. Configure these before starting personal mode, or use its optional Liquid interpreter field.

For the separate FastVisionModel route, follow [Liquid Unsloth setup](models/liquid.md#train-with-fastvisionmodel-and-unsloth) and configure `DMT_LIQUID_UNSLOTH_PYTHON`. Both profiles can coexist; select the matching Liquid training backend.

For a pinned D3 variant, install the separate [D3 family profile](models/d3-family.md#install-the-application-and-model-environment),
set `DMT_D3_PYTHON` to its absolute Python executable in the API/worker environment,
and restart the services. Personal mode also offers a D3 interpreter field before
startup. Optional `DMT_D3_CACHE` selects its public checkpoint cache. Follow
[D3 application training](models/d3.md#train-and-release-in-the-app) for review,
CPU/CUDA selection, frozen studies, release and prediction. The CPU application
process does not import or install the model runtime.
