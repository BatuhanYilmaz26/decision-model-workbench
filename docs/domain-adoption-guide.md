# From a hosted API to your own domain decision model

Build one domain-specific decision workflow from intake to application rollout. This guide uses support-ticket routing to connect hosted APIs, reviewed examples, synthetic coverage, local or cloud training and release. Follow the linked walkthroughs for executable commands, and evaluate each stage against your own operational records.

## Choose the entry point

| What you need | Start here | What must exist before business automation |
|---|---|---|
| Add a judgment to an existing application without training | [Hosted HTTP/SDK integration](api-integration-guide.md) | A fixed schema, response validation, a matched domain evaluation and a fallback |
| Build a new decision-driven application | [Five application blueprints](application-blueprints.md) | The business system's permissions, persistence and action transaction, as well as the model |
| Train a stable specialist from reviewed domain examples | [Data intake](data-guide.md), then [Laya/Unsloth training](training-guide.md) | Independent real holdouts, reviewed training data and exported-runtime qualification |
| Train with insufficient local GPU memory | [Five remote training workflows](remote-training-guide.md) | A qualified GPU image, approved data transfer, sufficient regional capacity and a recorded job lifecycle |
| Adapt Vega's engine | [Vega training](vega-guide.md) and [applicability data](applicability-guide.md) | Positive and negative evidence, separate selection/calibration/test, complete pinned assets and the distinct adapter protocol |

Hosted Jev use does not create downloadable customer-trained weights. The reviewed TypeSafe model documentation does not establish a customer fine-tuning API. Remote infrastructure runs the selected open-weight trainer; it does not turn a hosted API model into a trainable checkpoint. [TypeSafe model documentation](https://docs.typesafe.ai/models)

## Work through one example: support-ticket routing

Begin with an owner brief such as:

> Route English and Turkish B2B support tickets to billing, technical or general review. Use only the ticket and product information available when it arrives. Ambiguous tickets go to review. The model proposes a queue; the support service checks permissions and commits assignment. Missing an urgent technical incident is more costly than unnecessary review.

Ask the owner for these inputs in natural language, then show the proposed structured contract for confirmation:

| Input to collect | Why it changes the implementation |
|---|---|
| Decision and permitted outcomes | Defines the fixed question and canonical label IDs |
| Policy and evidence available at decision time | Defines instructions and the state builder; excludes future outcomes |
| Missing-evidence and unfamiliar-topic behavior | Defines an explicit review branch and optional applicability questions |
| Critical errors, review capacity and latency budget | Defines acceptance criteria and the application threshold policy |
| Languages, lengths, entities and changing policy periods | Defines coverage, family grouping and temporal evaluation |
| Data ownership and approved local/cloud processing | Determines which teacher, storage and compute routes may receive examples |

A few examples help establish the format. They do not establish adequate training or evaluation coverage. Offer manual entry and CSV/XLSX/TSV/JSON/JSONL upload, preview worksheets, map input and label columns, and preserve unlabeled drafts. The [data guide](data-guide.md) describes the actual importer and review limits.

### 1. Establish the existing application's baseline

Measure the current queue handler on a reserved operational sample: critical misroutes, per-language quality, accepted coverage, review effort, completed-task latency and total handling cost. Preserve whole ticket families together. Historical evidence must represent what was known when the decision was made, rather than the corrected ticket after resolution.

Implement a backend adapter around `propose_queue`, as shown in the [API guide](api-integration-guide.md). Keep the provider key in the backend. Validate question IDs, answer types, complete probability distributions and the qualified model/provider identity. The existing service owns authentication, allowed destinations and an idempotent assignment transaction. The model's proposal cannot itself confer permission.

Direct Jev and OpenRouter use different URLs and model selectors. Cloudflare's Workers binding has a different HTTP envelope, and Vega's `boolean`/`probs` fields require an explicit adapter. TypeLLM supports conditional typed generation, including open-ended category names; it is suitable for proposing a taxonomy, not silently expanding a deployed decision schema. [TypeSafe API](https://docs.typesafe.ai/api), [OpenRouter Decisions](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request), [TypeLLM workflows](https://typellm.ai/docs/workflows)

### 2. Turn examples into a reviewed domain dataset

Use stable IDs for both questions and labels. A compact raw example for format exploration is:

```json
{
  "id": "draft-001",
  "group_id": "fictional-ticket-family-001",
  "state": "Please send a copy of last month's invoice.",
  "questions": {
    "team": {
      "type": "choice",
      "instructions": "Which queue should review this ticket?",
      "criteria": {
        "billing": "Invoices, charges and refunds",
        "technical": "Product failures and configuration",
        "general_review": "Other topics or insufficient evidence"
      }
    }
  },
  "gold": {
    "team": {
      "probabilities": {
        "billing": 1.0,
        "technical": 0.0,
        "general_review": 0.0
      }
    }
  },
  "metadata": {"origin": "synthetic", "reviewed": false}
}
```

This fictional example is a draft, not real evaluation evidence. Confirm that the questions match the project exactly. Actual uploads reset review metadata: a reviewer must approve or correct each included label through the authenticated workflow. Related versions, translations, counterfactuals and generated descendants share their source family. See [semantic review](semantic-data-guide.md) and [chronological reservations](temporal-data-guide.md) for additional independence checks.

Reserve real validation, calibration and locked test before generation. Validation selects model settings; calibration fits the probability/abstention policy; test evaluates the final exported runtime once the study choices are fixed. Keep taxonomy-discovery examples and known relatives training-only. [Reviewed taxonomy workflow](taxonomy-guide.md)

### 3. Request the synthetic coverage you actually need

Ask the user to describe gaps, for example: “Generate short Turkish billing requests, ambiguous billing-versus-technical tickets, explicit negation, unfamiliar topics and urgent technical incidents. Preserve our queue definitions and label every case using only the supplied evidence.” Show a coverage plan before generation and request a few example styles if the policy is underspecified.

Send the generative teacher only approved training seeds and the confirmed policy/schema. Store teacher and prompt versions plus seed lineage with each candidate. Review and correct the candidates before training, and measure the teacher's error patterns on representative domain examples. A decision head scores permitted outcomes; it does not generate examples. Synthetic labels and provider confidence do not replace operational gold. [Synthetic generation and provenance](data-guide.md)

### 4. Train locally or move the same study to a GPU provider

Train a CPU baseline alongside native Laya or the Unsloth decision-head route. Compare them with the hosted candidate on the same independent evidence. Keep the question contract and acceptance policy frozen across compute locations.

If local memory is insufficient, choose [AWS SageMaker AI](remote-training/aws.md), [Azure ML](remote-training/azure.md), [GCP custom training](remote-training/gcp.md), [Modal](remote-training/modal.md) or [RunPod](remote-training/runpod.md). Those guides include staging, API/SDK/CLI submission, observation, complete-export retrieval and stopping. GPU training requires a qualified training image; the repository's CPU application image does not supply CUDA/model dependencies. Compare [full costs and memory requirements](remote-training/provider-selection.md).

For platform-managed jobs, prepare a frozen remote study before submission and consume its exact request with the matching runner. A direct operator recipe and a managed study have different provenance contracts. Use [remote admission](remote-admission-guide.md) to bring a managed export back to the local platform. Vega has separate local application and standalone eight-file protocols; its remote admission/image protocol remains required. Do not send Vega inputs to the four-file Laya/Unsloth runner.

### 5. Qualify the returned model and reconnect the application

Retrieve the full export, including its report, manifest, calibration, tokenizer/configuration and required weights. Check source, model, data and runtime identity against the approved study. Run reload-equivalence and held-out evaluation through the runtime that will serve predictions. Inspect per-question and subgroup errors, calibration, selective coverage, critical errors and expected review load. Benchmarks help choose candidates; they do not establish these results for your domain. [Evaluation guide](evaluation-guide.md), [benchmark interpretation](benchmarks.md)

After owner approval, release the admitted model and call `POST /v1/projects/{project_id}/decisions` using an inference-only credential. The existing application adapter checks model/schema/release identity and abstention before proposing an assignment. The current endpoint uses the active release; it does not offer atomic caller-selected version pinning. Applications requiring that guarantee need a dedicated versioned serving route before deployment. [Local API example](api-integration-guide.md), [authentication](authentication-guide.md)

Use shadow traffic, then an approved bounded canary. Track accepted and escalated cases, including timeouts and provider failures. Sample accepted decisions for human review so training data does not contain only escalations. Roll back model and policy together when the frozen limits fail.

## Decide whether the specialist improves the application

Compare at a fixed quality and critical-error target, rather than declaring the model with the lowest inference price the winner. A useful accounting unit is **total cost per successfully completed task**: inference plus training amortization, storage, idle capacity, retries, review and error handling. Report accepted coverage and review cost alongside model accuracy. Public request rankings measure adoption, while JevBench scores, author-run results and your operational study have different samples and cost assumptions. [OpenRouter ranking](https://openrouter.ai/rankings/decisions), [versioned JevBench](https://benchmarkheaven.com/jev-models/v1.6.6)
