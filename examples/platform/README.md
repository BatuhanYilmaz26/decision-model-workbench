# Call a released domain model

[Examples](../README.md) | [Project overview](../../README.md) | [API integration](../../docs/api-integration-guide.md)

Use the same endpoint for a released CPU baseline, native model or hosted decision policy. The client sends your evidence and an approved workbench release identity, validates the response and returns either `accepted` or `review_required`. It performs one attempt and never executes a business action.

## Start the API and obtain an inference credential

For an experimental PostgreSQL metadata workspace, use
[postgres_workspace.py](postgres_workspace.py) with the
[PostgreSQL guide](../../docs/postgresql-guide.md). Metadata-only configuration
keeps artifacts local; use the [shared-artifact guide](../../docs/shared-artifacts-guide.md)
to configure S3 storage and separate model caches.

Follow the [local quick start](../../README.md#run-on-your-computer), prepare data, train/evaluate and activate an eligible version. Keep its model/provider environment configured. The default personal browser app connects automatically and needs no workspace key from you. External applications still authenticate to its API.

For a persistent local integration key, stop the personal launcher before starting operator-managed localhost mode on the same workspace. From the activated application environment, create a **new private key file**:

```bash
domain-platform provision-key --file data/local-api-keys.json --tenant local --actor local-owner --role admin
domain-platform provision-key --file data/local-api-keys.json --tenant local --actor application-client --role inference
domain-platform run --home data/local-workspace --keys-file data/local-api-keys.json
```

Each provision command prints its token once. Use the owner token for the browser connection and the inference token for this client. The file stores hashes, is under the ignored `data/` directory, and must remain private. Use the actual home/tenant for an existing managed workspace rather than the personal defaults above. Reuse an existing key configuration or follow [key rotation](../../docs/authentication-guide.md#workspace-key-lifecycle) if an actor was already provisioned. Shared deployments can supply a compatible API access token instead.

The launcher prints the API/browser addresses. Keep the model environment and any approved provider profile/key settings used by your workspace; changing authentication mode does not install or replace a model runtime.

## Prepare one request

1. In the browser's prediction view, open **Release identity for integrations**. Copy the project ID and the complete model/revision/schema identity from an approved release. Administrative API clients can obtain the active model/revision from `GET /v1/projects/{project}/active` and the schema hash from the project payload in `GET /v1/projects`.
2. Copy [request.json](request.json) to private working storage. Replace its zero-valued model/schema placeholders and revision with that identity. Replace `state` with your domain's decision-time evidence and choose a threshold evaluated on independent domain data. The server's release supplies the questions; do not add arbitrary questions.
3. Give each logical decision a distinct `request_key`. Preserve that key only for an explicitly authorized retry of the same request. Hosted policies use it for call accounting; it does not make your application's downstream side effects idempotent.

```json
{
  "state": {"evidence": "Your decision-time evidence"},
  "threshold": 0.8,
  "request_key": "your-logical-decision-001",
  "expected_release": {
    "model_id": "00000000000000000000000000000000",
    "release_revision": 1,
    "schema_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
```

The zero IDs are placeholders, not a released example model. A mismatch returns HTTP 409 before model/provider dispatch. This field compares the active release snapshot; it cannot select an inactive version. A rollback creates a new revision even when it restores the same model. Update approved client configuration deliberately rather than automatically following a changed release.

The API may finish an already-started request using its captured version while another release is activated; the result retains that version's identity. The guard pins the workbench model/policy version, not otherwise-unattested provider weights. Older requests that omit the field retain current-release behavior; older servers reject the new field and need upgrading before using this client.

## Run the client

The root requirements already install HTTPX. To copy [predict.py](predict.py) into another application, install its only third-party dependency in that application's environment:

```bash
python -m pip install httpx==0.28.1
```

Run from the repository root using your actual project ID and private request file:

```bash
python examples/platform/predict.py --project YOUR_PROJECT_ID --request data/private-request.json
```

The default API URL is `http://127.0.0.1:8080`. Use `--base-url https://YOUR_WORKBENCH_API` for a remote deployment; this is your workbench API, not the decision provider's URL. The client allows HTTP only on loopback, refuses redirects and ignores environment proxies by default.

Enter the inference token at the masked terminal prompt, or inject `DMT_INFERENCE_KEY` through your process/secret manager. Noninteractive use requires that variable. Never put the token in the request JSON, URL or command arguments. This workspace credential differs from the provider API keys configured on the server.

The default HTTP inactivity budget is 420 seconds to accommodate cold model loading. Adjust `--timeout SECONDS` for your deployment's load, queue and inference limits; this is not a latency guarantee or proof of provider cancellation. For an existing backend, import `request_decision` from the copied module and reuse a configured `httpx.Client` through its `client` argument.

## Handle the result

| Result | Required handling |
|---|---|
| `status: accepted` | Read `decision.answers` only after your application's authorization and business checks. The release/response checks passed and no abstention was reported; correctness is not guaranteed. |
| `status: review_required`, `reason: abstained` | Send the case to your approved review/fallback path. The wrapper omits `decision` so an uncertain selected label is not presented as an action. |
| `reason: conflict`, HTTP 409 | Inspect the active release and logical request key. Do not silently re-pin, generate a replacement key or retry. |
| `reason: unavailable`, `http_error`, `invalid_request` or `invalid_response` | Use fallback and investigate configuration, availability or response validity. No automatic retry occurs. |

The CLI exits with `0` for accepted output and `2` when review/fallback is required. Keep raw evidence out of application logs. A timeout does not prove a hosted request stopped or was not billed; follow [hosted call history and reconciliation](../../docs/hosted-decisions-guide.md#reconcile-an-uncertain-call) before another paid attempt. Test the full action/review workflow on independent domain data before enabling automation.
