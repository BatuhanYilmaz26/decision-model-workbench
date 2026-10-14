# Build a domain decision model

Start the application using [local setup](setup.md), then work through the following stages. The browser uses an authenticated API and an independent worker, so closing a page does not discard a project or queued job.

## 1. Describe the decision

Create a project with your domain, policy and data needs in natural language. Define the evidence available at decision time, allowed outcomes, missing-evidence behavior and escalation policy. Confirm stable question IDs and outcome descriptions before collecting labels. The [data guide](data-guide.md) provides the format and a fictional support example.

## 2. Collect and review examples

Enter examples or upload CSV, XLSX, TSV, JSON or JSONL. Preview a worksheet and map columns and outcome values. Imports create unapproved drafts; approve, correct or reject each example using an authorized reviewer. Appends and reviews create immutable revisions. If another reviewer changes the collection first, refresh after the stale-edit conflict before resubmitting.

## 3. Reserve independent evaluation data

Prepare approved real examples into training, validation, calibration and locked test roles. Keep source families and known relatives together; use [temporal](temporal-data-guide.md) and [semantic](semantic-data-guide.md) review where needed. Inspect readiness, class counts and rejected-example exclusions. Set owner quality and minimum-count criteria before training: the first submission freezes them.

## 4. Add reviewed training coverage

If your policy has gaps, configure an approved teacher and request bounded synthetic coverage from reserved training seeds. Review every candidate before extending training. Preserve validation, calibration and test reservations. Generated labels are proposals, and evaluation gold must remain independently reviewed operational evidence.

## 5. Train and inspect the export

Start with a CPU comparison baseline, then use [native Laya or Unsloth](training-guide.md) when a trained decision head is appropriate. [Vega](vega-guide.md) requires additional applicability-negative data. Use [remote GPUs](remote-training-guide.md) if local capacity is insufficient. Inspect the final exported-runtime report, calibration, per-question metrics and reload checks; a successful job alone does not establish useful domain quality.

## 6. Release and integrate

An owner activates an eligible model version after checking its frozen criteria. Applications call the authenticated decision endpoint, preserve release/schema identity and send abstained or failed decisions to the approved fallback. Use shadow traffic and a bounded canary before consequential automation. Roll back model and policy together when acceptance limits fail.

Policy or schema changes create an [owner-approved successor project](policy-versioning-guide.md). Review carried-forward domain text, questions and optional criteria in the browser; the successor starts with an empty data workspace and its own release endpoint. Retain the earlier model contract and use fresh independent gold if test findings informed the change. Follow [evaluation](evaluation-guide.md), [deployment](deployment-guide.md) and [recovery](recovery-guide.md) for operating the resulting service.
