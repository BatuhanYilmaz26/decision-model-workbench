"""Example-bound discovery, immutable review and owner-confirmed schema branching."""

import streamlit as st


def render_taxonomy(api, submit, project, spec, principal, dataset_id, rows):
    if not st.checkbox("Open taxonomy workspace", key="taxonomy-open-" + project):
        return
    prefix = f"/v1/projects/{project}"
    role = principal["role"]
    st.subheader("Discover and review categories")
    st.caption(
        "Selected examples and known relatives become training-only, including in successor projects. "
        "Proposals need human review. Owner confirmation creates an empty project with a new schema; "
        "existing labels, models and releases stay with the original project."
    )
    choices = [k for k, q in spec["questions"].items() if q["type"] == "choice"]
    if not choices:
        st.info("This project has no choice question to expand.")
        return
    question = st.selectbox(
        "Taxonomy question", choices, key="taxonomy-question-" + project
    )
    eligible = [
        r
        for r in rows
        if r["metadata"]["origin"] == "real"
        and r["metadata"].get("review_status") != "rejected"
    ]
    ids = st.multiselect(
        "Taxonomy source examples on this page",
        [r["id"] for r in eligible],
        max_selections=20,
        key="taxonomy-examples-" + dataset_id,
    )
    for row in eligible:
        if row["id"] in ids:
            with st.expander("Taxonomy example " + row["id"]):
                st.json(row["state"])
    candidate = st.text_input(
        "Proposed category name", max_chars=80, key="taxonomy-name-" + project
    )
    if st.button(
        "Save manual taxonomy proposal",
        disabled=not ids or not candidate.strip() or role not in ("admin", "editor"),
    ):
        result = api(
            "POST",
            prefix + "/taxonomy-proposals",
            json={
                "dataset": dataset_id,
                "record_ids": ids,
                "question_id": question,
                "candidates": [{"name": candidate, "example_ids": ids}],
            },
        )
        st.session_state["taxonomy-proposal-" + project] = result["id"]
        st.rerun()
    profiles = api("GET", prefix + "/taxonomy-profiles")
    if profiles:
        profile = st.selectbox(
            "Taxonomy discovery profile",
            [p["id"] for p in profiles],
            key="taxonomy-profile-" + project,
        )
        chosen = next(p for p in profiles if p["id"] == profile)
        st.caption(
            f"TypeLLM model alias: {chosen['model']} · up to {chosen['max_rows']} selected examples in one call. A model alias can change upstream."
        )
        data = st.checkbox(
            "I approve sending these selected states and the project policy to TypeLLM",
            key="taxonomy-data-" + project,
        )
        cost = st.checkbox(
            "I approve this billable taxonomy discovery call",
            key="taxonomy-cost-" + project,
        )
        if st.button(
            "Queue taxonomy discovery",
            disabled=role != "admin" or not ids or not data or not cost,
        ):
            st.json(
                submit(
                    project,
                    "taxonomy-discover",
                    {
                        "dataset": dataset_id,
                        "record_ids": ids,
                        "question_id": question,
                        "profile_id": profile,
                        "data_approved": data,
                        "cost_approved": cost,
                    },
                )
            )
    else:
        st.caption(
            "An operator can enable TypeLLM discovery with a scoped server/worker profile. Manual proposals work without a provider."
        )
    proposals = api("GET", prefix + "/entities/taxonomy-proposal?limit=50")
    options = [p["id"] for p in proposals]
    if options:
        key = "taxonomy-proposal-" + project
        if st.session_state.get(key) not in options:
            st.session_state[key] = options[0]
        proposal_id = st.selectbox("Taxonomy proposal to review", options, key=key)
        page = api("GET", prefix + "/taxonomy-proposals/" + proposal_id)
        st.write(f"{page['pending']} categories await review")
        st.json(page["proposal"]["provenance"], expanded=False)
        if page["candidates"]:
            cid = st.selectbox(
                "Taxonomy candidate",
                [c["id"] for c in page["candidates"]],
                format_func=lambda value: next(
                    c["name"] for c in page["candidates"] if c["id"] == value
                ),
                key="taxonomy-candidate-" + proposal_id,
            )
            selected = next(c for c in page["candidates"] if c["id"] == cid)
            st.write("Source example IDs: " + ", ".join(selected["example_ids"]))
            if selected["review"]:
                st.json(selected["review"])
            decision = st.radio(
                "Taxonomy review decision",
                ["add", "alias", "reject"],
                key="taxonomy-decision-" + cid,
            )
            canonical = st.text_input(
                "Canonical category ID or alias target",
                max_chars=64,
                key="taxonomy-canonical-" + cid,
            )
            definition = st.text_area(
                "New category definition",
                max_chars=1000,
                key="taxonomy-definition-" + cid,
            )
            reason = st.text_input(
                "Taxonomy review reason", max_chars=1000, key="taxonomy-reason-" + cid
            )
            if st.button(
                "Save taxonomy review",
                disabled=role not in ("admin", "reviewer")
                or not reason.strip()
                or bool(page["confirmation"]),
            ):
                api(
                    "POST",
                    prefix + "/taxonomy-proposals/" + proposal_id + "/reviews",
                    json={
                        "candidate_id": cid,
                        "decision": decision,
                        "canonical_id": None if decision == "reject" else canonical,
                        "description": definition if decision == "add" else "",
                        "expected_revision": (
                            selected["review"]["payload"]["revision"]
                            if selected["review"]
                            else 0
                        ),
                        "expected_hash": page["proposal"]["sha256"],
                        "note": reason,
                    },
                )
                st.rerun()
        if page["confirmation"]:
            st.success("Taxonomy confirmed into a successor project")
            st.json(page["confirmation"])
        else:
            name = st.text_input(
                "Successor project name",
                max_chars=200,
                key="taxonomy-successor-" + proposal_id,
            )
            note = st.text_input(
                "Owner schema confirmation reason",
                max_chars=1000,
                key="taxonomy-confirm-reason-" + proposal_id,
            )
            consent = st.checkbox(
                "I confirm the reviewed taxonomy as a new project schema",
                key="taxonomy-confirm-" + proposal_id,
            )
            if st.button(
                "Confirm taxonomy and create successor",
                disabled=role != "admin"
                or page["pending"] != 0
                or not name.strip()
                or not note.strip()
                or not consent,
            ):
                confirmed_successor = api(
                    "POST",
                    prefix + "/taxonomy-proposals/" + proposal_id + "/confirm",
                    json={
                        "expected_state_sha256": page["state_sha256"],
                        "name": name,
                        "note": note,
                    },
                )
                st.session_state["next_project"] = confirmed_successor["successor"][
                    "id"
                ]
                st.rerun()
    if role == "admin":
        with st.expander("Uncertain taxonomy call settlement"):
            calls = api("GET", prefix + "/entities/taxonomy-discovery?limit=50")
            if calls:
                call = st.selectbox(
                    "Taxonomy call",
                    [c["id"] for c in calls],
                    key="taxonomy-call-" + project,
                )
                detail = api("GET", prefix + "/taxonomy-discoveries/" + call)
                st.json(detail)
                st.caption(
                    "Unknown outcomes retain capacity. Verify termination and the provider outcome independently; this records an owner attestation, not a refund or an automatic retry."
                )
                stopped = st.checkbox(
                    "I verified the local transport stopped",
                    key="taxonomy-stopped-" + call,
                )
                finished = st.checkbox(
                    "I verified the provider request finished",
                    key="taxonomy-finished-" + call,
                )
                reference = st.text_input(
                    "Taxonomy provider settlement reference",
                    key="taxonomy-reference-" + call,
                )
                note = st.text_input(
                    "Taxonomy settlement reason", key="taxonomy-settlement-note-" + call
                )
                if st.button(
                    "Record taxonomy settlement",
                    disabled=not stopped
                    or not finished
                    or not reference.strip()
                    or not note.strip(),
                ):
                    st.json(
                        api(
                            "POST",
                            prefix + "/taxonomy-discoveries/" + call + "/settle",
                            json={
                                "expected_outcome_sha256": detail["outcome_sha256"],
                                "local_transport_stopped": stopped,
                                "provider_finished": finished,
                                "reference": reference,
                                "note": note,
                            },
                        )
                    )
