# Use Perplexity Decider with your domain

[Model directory](README.md) · [All guides](../README.md) · [Project overview](../../README.md)

Evaluate a domain decision with Perplexity's hosted models, calibrate it against
your reviewed examples and release it through the local application. No local
GPU is needed. You supply your own Perplexity key and approve the evidence sent
to the service.

## Choose a model

| Profile in the template | Request and response model | Use |
|---|---|---|
| `perplexity` | `pplx-decider-v1.2` | Evaluate the larger model against domain gold |
| `perplexity-flash` | `pplx-decider-flash-v1.2` | Compare the smaller model's quality, latency and cost |

The upstream interface also accepts latest aliases and two retired names.
As documented on 18 October 2026, `pplx-decider-v1.1-27b` and
`pplx-decider-v1-27b` select v1.2. The response repeats the name requested;
it does not identify immutable serving weights. Even a versioned name can be
redirected when retired. Re-evaluate after provider changes and monitor domain
quality. See the [official model-name contract](https://docs.perplexity.ai/docs/decisions/quickstart#model-names).

Perplexity answers all questions in one request jointly. Adding a question can
change other answers. Keep the schema and question grouping identical during
evaluation and serving. Start Flash with a single question; use a related group
only after validating it on your data. The application sends the entire fixed
project schema in every request and does not split it automatically.
[Grouping guidance](https://docs.perplexity.ai/docs/decisions/quickstart#grouping-questions-in-one-request)

## Evaluate and release in the localhost application

1. Follow the [root installation steps](../../README.md#run-on-your-computer).
   Create a key in the [Perplexity console](https://console.perplexity.ai/).
2. Copy [hosted-profiles.json](../../examples/hosted-profiles.json) to the private
   path `data/config/decision-profiles.json`. Keep the Perplexity entries you
   want and set their `tenants` to `["local"]` for personal mode. Shared operators
   use their approved workspace identities.
3. Supply the key privately before starting the launcher. PowerShell:

   ```powershell
   $decisionKey = Read-Host "Perplexity API key" -AsSecureString
   $env:DMT_PROVIDER_PERPLEXITY_API_KEY = [System.Net.NetworkCredential]::new("", $decisionKey).Password
   Remove-Variable decisionKey
   $env:DMT_DECISION_PROFILES_FILE = (Resolve-Path data/config/decision-profiles.json).Path
   domain-platform run
   ```

   Bash/Zsh:

   ```bash
   export DMT_PROVIDER_PERPLEXITY_API_KEY="$(python -c 'import getpass; print(getpass.getpass("Perplexity API key: "))')"
   export DMT_DECISION_PROFILES_FILE="$PWD/data/config/decision-profiles.json"
   domain-platform run
   ```

   Restart an existing launcher after changing configuration. The API/worker
   receive the secret; the Streamlit process does not. Do not enter it in the
   workspace-login field or commit it to Git. `.env` files are not loaded
   automatically. For containers, follow [operator configuration](../hosted-decisions-guide.md#configure-operator-owned-profiles).
4. Describe the decision, define allowed outcomes, upload or enter examples,
   review labels and [reserve independent data](../reservation-guide.md).
   Set owner acceptance criteria before the first study. Reviewed synthetic
   candidates may extend training data; they do not replace real heldouts.
5. Open **Training jobs → Evaluate a hosted decision model**, choose a profile,
   approve data transfer and queue the evaluation. Only evidence and questions
   go upstream. Local calibration uses the reserved calibration split;
   validation/test report quality without fitting.
6. Review metrics and usage, activate an eligible version under **Models and
   releases**, then use **Make decision** or the [application API](../product-workflow.md#6-release-and-integrate).
   Compare Full and Flash using separate studies. Never transfer a fitted
   calibration policy automatically between them.

The saved artifact contains your schema, profile and local temperatures, rather
than provider weights. Follow [hosted operations](../hosted-decisions-guide.md)
for successful-request caching, rollback, call history, uncertain outcomes and
recovery. Profiles created before this Perplexity contract update require a
successor evaluation before release or inference: their snapshots did not record
the retired-name remapping and joint-question contract. Retain historical
artifacts for audit; do not edit their hashes or calibrations to bypass the check.

## Call the API from an existing project

Use the [runnable Python example](../../examples/perplexity/README.md) with your
own key, or integrate the same request builder, bounded HTTP transport and typed
response validator. The fixed endpoint is
`POST https://api.perplexity.ai/v1/decisions` with Bearer authentication and no
trailing slash. Handle failures and abstention through your application's
fallback; probabilities do not authorize business actions.

## Limits, usage and compatibility

This adapter supports text/JSON with 1–64 questions, 2–255 described choice
options and 2–10 described score levels. Image parts and extra question fields
are rejected. Perplexity's larger upstream limits and image support are separate
from this application's supported subset. The complete encoded request is
limited to 64,000 UTF-8 bytes, without truncation. This is a local byte guard,
not an exact token count. [Upstream schema and limits](https://docs.perplexity.ai/api-reference/decisions-post)

The template uses a 30-second total local deadline, including transport startup.
Timeout, invalid responses and HTTP errors take the safe failure path without
automatic retries. A local timeout does not prove that upstream work or billing
stopped. Inspect and reconcile unknown attempts before deliberately making
another call. Perplexity's organization rate limit spans Full and Flash; local
quotas are per tenant/profile, not an organization-wide rate or dollar cap.
[Provider limits and errors](https://docs.perplexity.ai/docs/decisions/quickstart#rate-limits)

The documented input tariffs on 18 October 2026 are $0.02 per million tokens for
Full and $0.01 for Flash, with output tokens uncharged. Token counts in `usage`
can still include outputs. The application records reported token usage and
does not invent an invoice amount when `usage.cost` is absent. Grouping can
change both probabilities and input billing; confirm current prices before use.
[Pricing](https://docs.perplexity.ai/docs/decisions/quickstart#pricing)

The hosted connector has official-contract, fictional HTTP and local application
tests. These do not establish account availability, actual billing or domain
accuracy. Before using sensitive records, check your organization's provider
terms and evaluate representative data. The separate
[open v1.1 checkpoint](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b)
is not hosted v1.2. Its local training, serving and cloud-training integration
are not available in this workbench yet.
