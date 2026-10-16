"""Preview, approve and inspect durable synthetic-generation requests."""

import uuid

import streamlit as st


def render_generation(api, project, principal, snapshot):
    st.subheader("Generate training candidates")
    st.write(
        "Describe the gaps in your training data. A generative teacher proposes examples; "
        "you review their evidence and labels before adding them to training. "
        "Validation, calibration and test records are never selected as seeds."
    )
    if principal["role"] != "admin":
        st.info(
            "A workspace owner must approve teacher data transfer and charges. You can review candidates in the data tab when they are available."
        )
        return
    key = "generation-" + project
    profile = api("GET", "/v1/generation-teacher")
    retry = st.session_state.get(key + "-retry")
    if retry:
        st.info(
            "Retrying the selected terminal request with its original coverage and seeds. Preview and approve this attempt separately."
        )
        selection = retry["selector"].copy()
        selection["retry_of"] = retry["id"]
        st.json(selection)
    elif snapshot:
        coverage = st.text_area(
            "Describe the synthetic data you need: rare outcomes, languages, lengths and policy boundaries",
            max_chars=4000,
            key=key + "-coverage",
        )
        count = st.number_input(
            "Candidates", min_value=1, max_value=50, value=10, key=key + "-count"
        )
        available = min(10, snapshot["payload"]["counts"]["train"])
        seeds = st.number_input(
            "Approved training seeds to send",
            min_value=0,
            max_value=available,
            value=min(3, available),
            key=key + "-seeds-" + snapshot["id"],
        )
        selection = {
            "snapshot": snapshot["id"],
            "count": int(count),
            "seed_count": int(seeds),
            "coverage": coverage.strip(),
            "retry_of": None,
        }
    else:
        selection = None
        st.info(
            "Reserve reviewed real evaluation data first. Saved generation requests remain available below."
        )
    if profile["available"]:
        st.caption(
            "Teacher configuration is set by the workspace operator. A localhost address can still forward requests to a hosted model; confirm the teacher's processing and retention settings."
        )
        teacher = profile["teacher"]
        st.text("Teacher: " + teacher["model"])
        st.text("Endpoint: " + teacher["base_url"])
        with st.expander("Teacher limits and token prices"):
            st.json(teacher)
    else:
        st.info(profile["reason"])
    if st.button(
        "Preview synthetic request",
        key=key + "-preview-button",
        disabled=selection is None or not profile["available"],
    ):
        value = api(
            "POST", f"/v1/projects/{project}/generation-preview", json=selection
        )
        st.session_state[key + "-preview"] = {"selection": selection, "value": value}
    saved = st.session_state.get(key + "-preview")
    if saved and saved["selection"] == selection:
        value = saved["value"]
        st.write(
            "Review the exact policy, questions and training examples sent in this request."
        )
        with st.expander("Approved request content", expanded=True):
            st.json({"approved_teacher": value["profile"]})
            st.code(value["request_body"], language="json")
            st.json(
                {
                    "seed_ids": value["seed_ids"],
                    "request_sha256": value["request_sha256"],
                    "attempt": value["attempt"],
                }
            )
        estimate = value["estimate"]
        if estimate["usd"] is None:
            st.warning(
                "Token prices are not configured. Cost is unknown. Check the teacher's pricing before approving."
            )
        else:
            st.write("Token-cost planning estimate (USD):", estimate["usd"])
        st.json(estimate)
        st.caption(
            "The input allowance is request bytes plus 1,024; it is not a tokenizer measurement. Estimates exclude infrastructure, fees and price changes. Configured estimate limits are local admission controls, not guaranteed invoices or account-wide spending caps."
        )
        nonce = st.session_state.get(key + "-batch", 0)
        approval_key = key + "-approval-" + value["sha256"] + "-" + str(nonce)
        data_approved = st.checkbox(
            "I approve sending this displayed policy and training seeds to the configured teacher",
            key=approval_key + "-data",
        )
        cost_approved = st.checkbox(
            "I approve any charges for this generation attempt",
            key=approval_key + "-cost",
        )
        if approval_key + "-request" not in st.session_state:
            st.session_state[approval_key + "-request"] = uuid.uuid4().hex
        submitted = st.session_state.get(approval_key + "-submitted")
        if st.button(
            "Approve and queue synthetic generation",
            key=approval_key + "-submit",
            disabled=not data_approved
            or not cost_approved
            or bool(submitted)
            or not profile["available"],
        ):
            job = api(
                "POST",
                f"/v1/projects/{project}/jobs",
                json={
                    "kind": "generate",
                    "request_key": st.session_state[approval_key + "-request"],
                    "config": {
                        **selection,
                        "data_approved": True,
                        "cost_approved": True,
                        "expected_preview_sha256": value["sha256"],
                    },
                },
            )
            st.session_state[approval_key + "-submitted"] = job["id"]
            st.session_state[key + "-selected"] = job["config"]["generation_plan"]["id"]
            st.session_state[key + "-page"] = 0
            st.rerun()
        if submitted:
            st.success(
                "Request saved. Inspect its status below; repeating this approval reopens the same job."
            )
    elif saved:
        st.caption(
            "Your selection changed. Preview the new request before approving it."
        )
    if st.button("Prepare a separate new generation batch", key=key + "-new"):
        st.session_state[key + "-batch"] = st.session_state.get(key + "-batch", 0) + 1
        st.session_state.pop(key + "-preview", None)
        st.session_state.pop(key + "-retry", None)
        st.rerun()
    render_history(api, project, key)


def render_history(api, project, key):
    st.write("Saved generation requests")
    page = st.number_input(
        "Generation history page",
        min_value=0,
        max_value=107374182,
        step=1,
        key=key + "-page",
    )
    history = api(
        "GET",
        f"/v1/projects/{project}/generation-plans?limit=20&offset={int(page) * 20}",
    )
    if not history:
        st.caption("No requests on this page.")
        return
    choices = {r["id"]: r for r in history}
    if st.session_state.get(key + "-selected") not in choices:
        st.session_state.pop(key + "-selected", None)
    selected = st.selectbox(
        "Saved generation request",
        list(choices),
        format_func=lambda identifier: f"{identifier[:8]} · candidates: {choices[identifier]['candidates']}",
        key=key + "-selected",
    )
    if st.button("Refresh generation request", key=key + "-refresh"):
        st.rerun()
    value = api("GET", f"/v1/projects/{project}/generation-plans/{selected}")
    job = value["job"]
    st.write("Job status:", job["status"], "· Teacher outcome:", value["state"])
    st.caption(job["progress"])
    if job["error"]:
        st.warning(job["error"])
    with st.expander("Saved request and response metadata"):
        st.json({k: v for k, v in value.items() if k != "request_body"})
        st.code(value["request_body"], language="json")
    if value["dataset"]:
        st.success(
            "Candidates are saved and pending review. Open the data tab, refresh collections and review this dataset before extending the original reservation."
        )
        st.code(value["dataset"]["id"])
    if job["status"] in ("queued", "running"):
        if st.button("Cancel synthetic generation", key=key + "-cancel-" + selected):
            api("POST", f"/v1/projects/{project}/jobs/{job['id']}/cancel")
            st.rerun()
        st.caption(
            "Cancellation stops the local transport. A teacher may still process or charge for a request it already received."
        )
    elif value["state"] == "unknown":
        st.warning(
            "The teacher outcome is unresolved. Capacity remains reserved. Check the local transport and the teacher before confirming completion; this does not verify billing or erase the attempt."
        )
        with st.form(key + "-settle-" + value["status_sha256"]):
            local = st.checkbox("I verified that the local transport has exited")
            upstream = st.checkbox(
                "I verified that the teacher is no longer processing this request"
            )
            evidence = st.text_input("Outcome evidence reference", max_chars=500)
            note = st.text_area("Outcome confirmation note", max_chars=1000)
            confirm = st.form_submit_button("Confirm the checked generation outcome")
        if confirm:
            api(
                "POST",
                f"/v1/projects/{project}/generation-plans/{selected}/settle",
                json={
                    "expected_status_sha256": value["status_sha256"],
                    "local_exit_confirmed": local,
                    "upstream_complete_confirmed": upstream,
                    "evidence_ref": evidence,
                    "note": note,
                },
            )
            st.rerun()
    if (
        job["status"] in ("failed", "cancelled", "interrupted")
        and value["state"]
        in ("response_received", "owner_attested_complete", "not_dispatched")
        and not value["dataset"]
    ):
        st.caption(
            "A retry is a new, separately approved attempt. It keeps the original request and teacher configuration, and may incur another charge. Retries are bounded and never automatic."
        )
        if st.button("Prepare an explicit retry", key=key + "-retry-" + selected):
            st.session_state[key + "-retry"] = {
                "id": selected,
                "selector": value["selector"],
            }
            st.session_state[key + "-batch"] = (
                st.session_state.get(key + "-batch", 0) + 1
            )
            st.session_state.pop(key + "-preview", None)
            st.rerun()
