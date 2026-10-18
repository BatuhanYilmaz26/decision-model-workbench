# Build a domain decision model

[All guides](README.md) · [Project overview](../README.md)

Start the application using [local setup](setup.md), then work through the following stages. The browser uses an authenticated API and an independent worker, so closing a page does not discard a project or queued job.

For the exact tab names, controls, optional workflows and troubleshooting, follow the [complete browser user guide](app-guide.md).

## 1. Describe the decision

Create a project with your domain, policy and data needs in natural language. Define the evidence available at decision time, allowed outcomes, missing-evidence behavior and escalation policy. Confirm stable question IDs and outcome descriptions before collecting labels. The [data guide](data-guide.md) provides the format and a fictional support example.

Build the questions manually, or use the optional [schema assistant](schema-assistant-guide.md) to request a teacher-generated draft. Review and edit its proposed questions and assumptions, then explicitly confirm the policy before creating the project. No examples are labelled or approved by this drafting step.

## 2. Collect and review examples

Enter examples or upload CSV, XLSX, TSV, JSON or JSONL. Preview a worksheet and map columns and outcome values. Imports create unapproved drafts; approve, correct or reject each example using an authorized reviewer. Appends and reviews create immutable revisions. If another reviewer changes the collection first, refresh after the stale-edit conflict before resubmitting.

## 3. Reserve independent evaluation data

Prepare approved real examples into training, validation, calibration and locked test roles. Keep source families and known relatives together; use [temporal](temporal-data-guide.md) and [semantic](semantic-data-guide.md) review where needed. Inspect readiness, class counts and rejected-example exclusions. Set owner quality and minimum-count criteria before training: the first submission freezes them.

Use automatic reservations or [assign source groups and date cutoffs explicitly](reservation-guide.md). Preview counts and confirm the role assignment before queuing preparation. Existing project families retain their original roles in later preparations.

## 4. Add reviewed training coverage

If your policy has gaps, follow [synthetic generation](synthetic-generation-guide.md) to configure a teacher, preview the policy and reserved training seeds, approve data transfer and charges, and inspect the saved outcome. Review every candidate before extending training. Preserve validation, calibration and test reservations. Generated labels are proposals, and evaluation gold must remain independently reviewed operational evidence.

## 5. Train and inspect the export

Start with a CPU comparison baseline, then use [native Laya or Unsloth](training-guide.md) when a trained decision head is appropriate. [Vega](vega-guide.md) requires additional applicability-negative data. Use [remote GPUs](remote-training-guide.md) if local capacity is insufficient. Inspect the final exported-runtime report, calibration, per-question metrics and reload checks; a successful job alone does not establish useful domain quality.

## 6. Release and integrate

An owner activates an eligible model version after checking its frozen criteria. Applications call the authenticated decision endpoint, preserve release/schema identity and send abstained or failed decisions to the approved fallback. Use shadow traffic and a bounded canary before consequential automation. Roll back model and policy together when acceptance limits fail.

Policy or schema changes create an [owner-approved successor project](policy-versioning-guide.md). Review carried-forward domain text, questions and optional criteria in the browser; the successor starts with an empty data workspace and its own release endpoint. Retain the earlier model contract and use fresh independent gold if test findings informed the change. Follow [evaluation](evaluation-guide.md), [deployment](deployment-guide.md) and [recovery](recovery-guide.md) for operating the resulting service.
