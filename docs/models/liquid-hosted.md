# Use Liquid d1 and d1-ultra through their API

[Model directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Use either hosted model for a bounded decision without installing model weights
or a GPU runtime. The workbench can evaluate your domain schema, fit local
calibration and release a provider-backed policy through its decision API.
The integration supports text/JSON evidence and choice, yes/no and ordered-score
questions. Provider-account and domain qualification are required before use
with enterprise data; local fixture checks do not establish either.

## Choose the hosted or trainable route

| What you need | Route |
|---|---|
| Call Liquid's hosted weights | This guide: `d1` or `d1-ultra` |
| Fine-tune and serve your own domain weights | [Open d1-3B training](liquid.md) |
| Generate additional training examples | A separate [generative teacher and human review](../synthetic-generation-guide.md) |

Liquid's direct endpoint is
`https://api.liquid.ai/decisions/v1/systemone`, using Bearer authentication.
Both hosted models use the same typed interface. They charge for input, with
supplied content billed again for each question; output-token usage is zero.
Check [Liquid's API documentation](https://docs.liquid.ai/lfm/models/d1) and
[current pricing](https://www.liquid.ai/pricing) before selecting a model.
Hosted access does not provide a customer-training checkpoint.

## Configure the localhost application

1. Install and start the application using the [root quick start](../../README.md#run-on-your-computer).
   Create a Liquid account and API key in the [Liquid console](https://console.liquid.ai/).
2. Copy [hosted-profiles.json](../../examples/hosted-profiles.json) to
   `data/config/decision-profiles.json`. Keep the `liquid-d1` and/or
   `liquid-d1-ultra` entries you want. Set their `tenants` to `["local"]` for
   personal mode, or your approved workspace tenants for shared use.
3. Enter your own `DMT_PROVIDER_LIQUID_API_KEY` using the
   [masked terminal prompts](../hosted-decisions-guide.md#enter-your-provider-api-key),
   or supply it through your operator secret manager. Set the profile path before starting the launcher:

   ```powershell
   $env:DMT_DECISION_PROFILES_FILE = (Resolve-Path data/config/decision-profiles.json).Path
   domain-platform run
   ```

   For Bash/Zsh, after supplying the same secret through your environment:

   ```bash
   export DMT_DECISION_PROFILES_FILE="$PWD/data/config/decision-profiles.json"
   domain-platform run
   ```

   Restart an already running launcher after changing operator configuration.
   Python does not automatically load `.env.example`. The browser receives no
   provider key. Compose forwards this credential to API/worker only; use
   `/run/config/decision-profiles.json` for its mounted configuration directory.
4. Define your decision and labels. Enter/upload examples, review them and
   [reserve independent data](../reservation-guide.md). Set owner acceptance
   criteria before evaluating. Your synthetic candidates remain training-only;
   they must not replace real validation/calibration/test records.
5. Open **Training jobs → Evaluate a hosted decision model**, select the approved
   Liquid profile and confirm permission to send study data and later inference
   states. Queue evaluation. Study requests contain evidence/questions, not gold.
6. Inspect results, calibration, abstention and call usage. Activate an eligible
   version under **Models and releases**, then use **Make decision** or the
   [released-model API](../product-workflow.md#6-release-and-integrate).

This saves a local schema/profile/calibration policy, not Liquid's weights.
Switching to the other hosted model requires another evaluated policy; do not
reuse fitted temperatures or thresholds automatically. Earlier eligible releases
remain available for rollback. Follow the [hosted workflow](../hosted-decisions-guide.md)
for call history, idempotency, quotas, failures, reconciliation and recovery.

## Add a direct call to an existing Python application

Install this repository's [CPU requirements](../../requirements.txt) in your
application environment. The example uses its typed response validator and
bounded one-shot HTTP process. Run from the repository root, or replace the
profile-template path with your application's private configuration path.

```python
import json
import os
from pathlib import Path
from decision_workbench.json_tools import strict_json
from decision_workbench.platform.hosted import (
    decode_response, public_profile, request_body, validate_profile,
)
from decision_workbench.platform.hosted_transport import run_process

profiles = json.loads(Path("examples/hosted-profiles.json").read_text(encoding="utf-8"))
profile = next(p for p in profiles["profiles"] if p["id"] == "liquid-d1")
validate_profile(profile)
key = os.environ[profile["key_env"]]
if not key:
    raise ValueError("Configure the Liquid provider credential")
questions = {
    "needs_review": {
        "type": "noul",
        "instructions": "Does the evidence explicitly say that required information is missing?",
    }
}
body = request_body(profile, "The required approval record is missing.", questions)
raw = run_process(public_profile(profile), body, key)
result = decode_response(strict_json(raw), profile, questions)
print(result["answers"]["needs_review"]["probabilities"])
```

Select `liquid-d1-ultra` instead to call the other model. Catch transport and
validation failures at your application boundary and return your existing review
or fallback state. This standalone example returns provider probabilities; it
does not apply a saved domain temperature, reserve durable calls or supply
business-action idempotency. Use the released-model API for the platform's
calibration and accounting. Your application remains responsible for authorization
and side effects.

## Call directly from a TypeScript backend

This minimal Node.js example checks one binary response. Keep the key on your
server. For all three primitives, local calibration and durable call accounting,
use the workbench's released-model endpoint instead.

```typescript
const key = process.env.DMT_PROVIDER_LIQUID_API_KEY;
if (!key) throw new Error("Configure the Liquid provider credential");
const model = "d1-ultra";
const response = await fetch("https://api.liquid.ai/decisions/v1/systemone", {
  method: "POST",
  headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
  redirect: "error",
  signal: AbortSignal.timeout(15000),
  body: JSON.stringify({
    model,
    state: "The required approval record is missing.",
    questions: {
      needs_review: {
        type: "noul",
        instructions: "Does the evidence explicitly say that required information is missing?",
      },
    },
  }),
});
if (!response.ok) throw new Error("Decision provider unavailable; use fallback");
const body = await response.json();
const answer = body?.answers?.needs_review;
if (body?.model !== model || Object.keys(body?.answers ?? {}).join() !== "needs_review"
    || answer?.type !== "noul" || typeof answer.noul !== "number"
    || !Number.isFinite(answer.noul) || answer.noul < 0 || answer.noul > 1
    || !Number.isInteger(body?.usage?.input_tokens) || body.usage.input_tokens < 0
    || body?.usage?.output_tokens !== 0) {
  throw new Error("Invalid decision response; use fallback");
}
console.log({ probability: answer.noul, model: body.model, usage: body.usage });
```

Use a threshold chosen from your own held-out data, and handle thrown errors at
the caller. A timeout does not prove the upstream request stopped or was refunded;
do not automatically retry an uncertain call.

## Compatibility and release checks

The workbench checks the selected model name, exact questions, finite complete
distributions, score semantics and input/output usage. It keeps reported provider
confidence separate from the canonical calibrated winner probability. A model
name is not an immutable weight digest; re-evaluate after provider changes even
when the name remains the same.

Liquid also documents image inputs, SDKs, OpenRouter and Vercel gateway routes.
Those are separate capabilities. This application adapter does not forward a
top-level image array, and this guide does not qualify those SDK/gateway/media
combinations. Do not embed file paths and assume they become uploaded images.
For their upstream interfaces, see the [publisher guide](https://docs.liquid.ai/lfm/models/d1).

Before transferring sensitive data, verify account availability, processing and
retention terms, response behavior, quotas and actual billed usage. Measure your
complete application, including fallback and review. Local call limits do not
enforce an account-wide dollar budget. See [provider compatibility checks](../verification.md#provider-compatibility-checks).
