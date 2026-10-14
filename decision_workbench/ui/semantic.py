"""Local candidate audit and immutable owner/reviewer adjudication workflow."""

import streamlit as st


def render_semantic(
    api, submit, project, principal, datasets, base_snapshot, *, applicability=False
):
    scope = ("gate-" if applicability else "") + project
    prefix = "Combined " if applicability else ""
    profile = api("GET", f"/v1/projects/{project}/semantic-profile")["profile"]
    if not profile:
        st.caption(
            "No local semantic profile is configured for this project. Exact/source/time checks alone do not establish semantic independence."
        )
        return None, True
    with st.expander(
        prefix + "Find and review semantic relatives", expanded=profile["required"]
    ):
        st.write(
            f"Local model: {profile['model']} · revision {profile['revision'][:12]}"
        )
        st.caption(
            "The audit compares every included state using the pinned local CPU encoder. Similarity proposes pairs for review; it does not establish duplicate status or guarantee decontamination. Review all candidates before reservation. Synthetic extensions include the original reserved data in the audit."
        )
        threshold = st.number_input(
            prefix + "Candidate cosine threshold",
            min_value=0.5,
            max_value=1.0,
            value=0.85,
            step=0.01,
            key="semantic-threshold-" + scope,
        )
        if st.button(
            prefix + "Queue local semantic audit",
            disabled=not datasets
            or (applicability and not base_snapshot)
            or principal["role"] != "admin",
            key="semantic-queue-" + scope,
        ):
            st.json(
                submit(
                    project,
                    "gate-semantic-audit" if applicability else "semantic-audit",
                    {
                        "datasets": datasets,
                        "threshold": threshold,
                        **(
                            {
                                (
                                    "snapshot" if applicability else "base_snapshot"
                                ): base_snapshot
                            }
                            if base_snapshot
                            else {}
                        ),
                    },
                )
            )
        audits = api("GET", f"/v1/projects/{project}/entities/semantic-audit?limit=50")
        audits = [
            a
            for a in audits
            if bool(a["payload"].get("applicability")) == applicability
        ]
        chosen = st.selectbox(
            prefix + "Reviewed semantic audit for this reservation",
            [None] + [a["id"] for a in audits],
            format_func=lambda value: value or "Select a completed audit",
            key="semantic-audit-" + scope,
        )
        if not chosen:
            return None, not profile["required"]
        summary = next(a["payload"]["report"] for a in audits if a["id"] == chosen)
        pages = max(0, (summary["candidate_pairs"] - 1) // 25)
        page = st.number_input(
            prefix + "Semantic candidate page (25 pairs)",
            min_value=0,
            max_value=pages,
            value=0,
            step=1,
            key="semantic-page-" + chosen,
        )
        result = api(
            "GET",
            f"/v1/projects/{project}/semantic-audits/{chosen}/pairs?limit=25&offset={int(page)*25}",
        )
        st.write(
            f"{result['row_count']} records · {result['compared_pairs']} comparisons · {result['candidate_pairs']} candidates · {result['pending_pairs']} pending reviews"
        )
        if result["pairs"]:
            pair_id = st.selectbox(
                prefix + "Semantic candidate pair",
                [pair["id"] for pair in result["pairs"]],
                format_func=lambda value: next(
                    f"{p['left']} / {p['right']} · {p['similarity']:.4f}"
                    for p in result["pairs"]
                    if p["id"] == value
                ),
                key="semantic-pair-" + chosen,
            )
            pair = next(p for p in result["pairs"] if p["id"] == pair_id)
            left, right = st.columns(2)
            with left:
                st.write(pair["left"])
                (
                    st.json(pair["left_state"])
                    if isinstance(pair["left_state"], (dict, list))
                    else st.text(pair["left_state"])
                )
            with right:
                st.write(pair["right"])
                (
                    st.json(pair["right_state"])
                    if isinstance(pair["right_state"], (dict, list))
                    else st.text(pair["right_state"])
                )
            if pair["review"]:
                st.caption(
                    f"Current review: {pair['review']['decision']} · revision {pair['review']['revision']}"
                )
            decision = st.radio(
                "Relationship decision",
                ["related", "independent"],
                key="semantic-decision-" + chosen + "-" + pair_id,
            )
            note = st.text_input(
                "Semantic review reason",
                max_chars=1000,
                key="semantic-reason-" + chosen + "-" + pair_id,
            )
            if st.button(
                prefix + "Save semantic relationship review",
                disabled=not note.strip()
                or principal["role"] not in ("admin", "reviewer"),
                key="semantic-review-" + scope + "-" + pair_id,
            ):
                api(
                    "POST",
                    f"/v1/projects/{project}/semantic-audits/{chosen}/reviews",
                    json={
                        "pair_id": pair_id,
                        "decision": decision,
                        "expected_revision": (
                            pair["review"]["revision"] if pair["review"] else 0
                        ),
                        "expected_hash": result["sha256"],
                        "note": note,
                    },
                )
                st.rerun()
        return chosen, result["pending_pairs"] == 0
