# Create and operate a policy successor

[All guides](README.md) · [Project overview](../README.md)

Create a new project version when your decision rules, outcome schema or frozen acceptance criteria need to change. The successor starts from an owner-reviewed draft of the current domain, questions and optional criteria. The original project's records, models and active release remain available under its existing API routes.

Use this workflow after clarifying a policy exception, adding an outcome, or deciding that different error-cost limits are needed. If evaluation findings influenced the change, obtain fresh independent evaluation gold; a new project identifier does not make previously inspected examples unseen.

## Review the successor in the browser

1. Connect with an owner credential and select the source project. Owners use the application's `admin` role; editors and reviewers cannot create policy successors.
2. Open **Create a policy successor**, then enable **Plan a policy successor**.
3. Review the copied domain policy, data requirements and question schema. Give the successor a useful name and explicitly edit any changed instructions, question IDs or outcomes in the schema JSON.
4. Choose whether to include acceptance criteria. When the parent has a saved policy, its exact criteria fill the draft. Review accuracy, calibration, support, Vega applicability and domain error-cost gates against the successor's schema. Update cost and critical-label outcome IDs when changing outcomes; incompatible criteria are refused.
5. Record the reason for the change, approve the displayed policy/schema/criteria, and select **Create reviewed successor**.

The browser opens the new project on the first history page and confirms creation. Its policy version is the parent's version plus one. Different successors may branch from the same parent; their version numbers describe ancestry rather than a global sequence.

Acceptance criteria are saved atomically with the project when included. They remain editable through **Training jobs** until the successor's first training, hosted evaluation or remote study submission freezes them. An unchecked criteria option creates no acceptance policy; configure one before starting a study. The owner's confirmation and source schema/policy identities are retained with the successor.

Changing the parent acceptance policy invalidates a draft based on its previous version. Refresh and review the new draft before approving it. **Start a fresh successor draft** discards unsaved edits and starts a new request; it is useful when you intentionally want another branch.

## Add and evaluate data for the new policy

Download the successor's CSV format or use its direct-example and upload controls. Annotate and review each example against the new schema. Existing gold, review approvals, synthetic candidates, holdouts, applicability collections, taxonomy aliases, trained weights and releases are not transferred automatically.

Taxonomy-selection examples and known relatives retain their training-only restriction across project ancestry. Other prior records' split roles are not automatically inherited: establish independent evidence and reserve new validation, calibration and locked test roles explicitly. Follow [data collection](data-guide.md), [temporal reservations](temporal-data-guide.md), [semantic audits](semantic-data-guide.md) and [evaluation](evaluation-guide.md).

Train and evaluate a new model for the successor. Keep callers on the parent project until you have accepted the successor's model and changed your application routing deliberately. Releasing a successor model activates only that successor project's endpoint; it does not redirect the parent. Retain the old endpoint for rollback and compare decisions using your domain's acceptance policy.

## Create a successor through the API

Read the source project and its acceptance policy, then submit their displayed identifiers. Use one stable `request_key` for retries of the same reviewed request:

```python
import copy
import os
import uuid
import httpx

parent_id = os.environ["DMT_PARENT_PROJECT_ID"]
with httpx.Client(
    base_url=os.environ["DMT_API_URL"],
    headers={"Authorization": "Bearer " + os.environ["DMT_WORKSPACE_TOKEN"]},
    timeout=30,
) as client:
    parent_response = client.get(f"/v1/projects/{parent_id}")
    parent_response.raise_for_status()
    parent = parent_response.json()["payload"]
    policy_response = client.get(f"/v1/projects/{parent_id}/release-policy")
    policy_response.raise_for_status()
    policy = policy_response.json()["policy"]
    request = {
        "name": "Reviewed support policy v2",
        "description": parent["description"],
        "data_requirements": parent.get("data_requirements", ""),
        "questions": copy.deepcopy(parent["questions"]),
        "criteria": copy.deepcopy(policy["payload"]["criteria"]) if policy else None,
        "expected_parent_schema_sha256": parent["schema_sha256"],
        "expected_parent_policy_id": policy["id"] if policy else None,
        "note": "Owner reviewed the successor rules and data requirements",
        "request_key": uuid.uuid4().hex,
    }
    # Review and edit the request before submission. Persist its key for retries.
    response = client.post(f"/v1/projects/{parent_id}/successors", json=request)
    response.raise_for_status()
    successor = response.json()
    print(successor["id"], successor["payload"]["policy_version"])
```

The endpoint returns HTTP 201 with the project record. Repeating the same key and normalized content returns the original successor, including after the parent criteria change. Reusing the key with different content returns HTTP 409. A new request with stale source schema/policy identifiers also returns 409; refresh rather than silently substituting the new source policy.

Project creation, initial criteria and audit records commit in one local SQLite transaction. A failed transaction publishes none of them. This path supports at most 64 projects in one ancestry chain. It uses the existing single-host storage model; it does not establish distributed transaction or enterprise identity acceptance.

## Recover both policy versions

Include both projects in your ordinary [workspace backup](recovery-guide.md). Recovery validates the successor's retained source and initial-policy references, request checksum and ancestry before publishing a restored workspace. Later acceptance-policy revisions do not change that initial receipt. Test the parent and successor endpoints separately after recovery.
