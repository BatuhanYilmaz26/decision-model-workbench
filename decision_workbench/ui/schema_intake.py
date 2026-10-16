"""Optional, explicitly approved teacher suggestions for a new domain project."""

import hashlib
import json
import uuid

import streamlit as st
from decision_workbench.platform.schema_intake import strict_json


def render_schema_assistant(api, principal):
    if principal["role"] != "admin":
        return
    if not st.checkbox(
        "Suggest decision questions with a teacher", key="schema-assistant-visible"
    ):
        return
    st.write(
        "Describe your domain first. The teacher drafts questions and outcomes; you review and edit them before creating a project. No datasets or evaluation records are sent."
    )
    profile = api("GET", "/v1/schema-teacher")
    if profile["available"]:
        teacher = profile["teacher"]
        st.caption(
            f"Configured teacher: {teacher['model']} at {teacher['base_url']} · {teacher['timeout_seconds']} second timeout"
        )
        attempt = st.session_state.get("schema-request-attempt", 0)
        with st.form("schema-assistant-request-" + str(attempt)):
            name = st.text_input("Suggested project name", max_chars=120)
            description = st.text_area(
                "Describe the decision and its rules", max_chars=12000
            )
            needs = st.text_area("Data requirements for this decision", max_chars=4000)
            data_approved = st.checkbox(
                "I approve sending this domain brief to the configured teacher"
            )
            cost_approved = st.checkbox(
                "I approve any charges for this schema suggestion request"
            )
            requested = st.form_submit_button("Request schema suggestions")
        if requested:
            request = {
                "name": name,
                "description": description,
                "data_requirements": needs,
                "data_approved": data_approved,
                "cost_approved": cost_approved,
            }
            # Repeated clicks on the same approved form reuse the durable request.
            identity = hashlib.sha256(
                json.dumps(request, sort_keys=True).encode()
            ).hexdigest()
            key = "schema-request-" + str(attempt) + "-" + identity
            if key not in st.session_state:
                st.session_state[key] = uuid.uuid4().hex
            request["request_key"] = st.session_state[key]
            value = api("POST", "/v1/schema-intakes", json=request)
            st.session_state["schema-selected-intake"] = value["id"]
            st.session_state["schema-history-page"] = 0
            st.rerun()
        st.caption(
            "A failed or timed-out request may still have reached the teacher. Repeating the same form reopens the same job; it does not retry the provider call."
        )
    else:
        st.info(profile["reason"])
        st.write(
            "You can still build questions manually below. Configure a teacher in the workspace services to enable suggestions."
        )
    page = st.number_input(
        "Schema suggestion history page", min_value=0, step=1, key="schema-history-page"
    )
    history = api("GET", f"/v1/schema-intakes?limit=20&offset={int(page) * 20}")
    if not history:
        st.caption(
            "No saved suggestions on this page. Request one above or build questions manually below."
        )
        return
    choices = {v["id"]: v["payload"]["brief"]["name"] for v in history}
    if st.session_state.get("schema-selected-intake") not in choices:
        st.session_state.pop("schema-selected-intake", None)
    selected = st.selectbox(
        "Saved schema suggestion",
        list(choices),
        format_func=lambda v: f"{choices[v]} · {v[:8]}",
        key="schema-selected-intake",
    )
    if st.button("Refresh schema suggestion"):
        st.rerun()
    value = api("GET", f"/v1/schema-intakes/{selected}")
    job = value["job"]
    st.write(f"Suggestion status: {job['status']}")
    st.caption(job["progress"])
    if job["status"] in ("queued", "running", "cancel_requested"):
        st.write(
            "The independent worker keeps this request across browser refreshes. No project exists until you confirm a completed draft."
        )
        if job["status"] != "cancel_requested" and st.button(
            "Cancel schema suggestion"
        ):
            api("POST", f"/v1/schema-intakes/{selected}/cancel")
            st.rerun()
        return
    if job["status"] != "succeeded":
        st.warning(
            job["error"] or "This suggestion did not complete. No project was created."
        )
        st.caption(
            "A new request may incur another charge. Inspect the prior outcome first, then enter and approve a new brief."
        )

        def new_attempt():
            st.session_state["schema-request-attempt"] = (
                st.session_state.get("schema-request-attempt", 0) + 1
            )

        st.button("Prepare a new schema request", on_click=new_attempt)
        return
    if value["confirmed_project"]:
        project = value["confirmed_project"]
        st.success(f"This suggestion already created {project['name']}.")
        if st.button("Open reviewed project"):
            st.session_state["next_project"] = project["id"]
            st.rerun()
        st.caption(
            "To change its policy or outcomes, open the project and create a policy successor."
        )
        return
    result = job["result"]
    st.write(
        "Review every question, outcome and assumption. A valid schema is not evidence that the domain policy is correct."
    )
    for item in result["assumptions"]:
        st.warning(item)
    key = selected + "-" + result["proposal_sha256"]
    with st.form("confirm-schema-" + key):
        name = st.text_input(
            "Reviewed project name",
            value=value["brief"]["name"],
            key="schema-name-" + key,
        )
        description = st.text_area(
            "Reviewed domain policy",
            value=value["brief"]["description"],
            key="schema-policy-" + key,
        )
        needs = st.text_area(
            "Reviewed data requirements",
            value=value["brief"]["data_requirements"],
            key="schema-data-" + key,
        )
        edited = st.text_area(
            "Review or edit suggested questions (JSON)",
            value=json.dumps(result["questions"], indent=2),
            height=300,
            key="schema-questions-" + key,
        )
        approved = st.checkbox(
            "I reviewed and approve this policy and every decision outcome",
            key="schema-approve-" + key,
        )
        create = st.form_submit_button("Create project from reviewed suggestions")
    if create:
        try:
            proposed = strict_json(edited)
        except (ValueError, RecursionError):
            st.error(
                "Enter valid question JSON with unique field names before creating the project."
            )
            return
        project = api(
            "POST",
            f"/v1/schema-intakes/{selected}/confirm",
            json={
                "name": name,
                "description": description,
                "data_requirements": needs,
                "questions": proposed,
                "expected_proposal_sha256": result["proposal_sha256"],
                "confirmed": approved,
            },
        )
        st.session_state["next_project"] = project["id"]
        st.rerun()
