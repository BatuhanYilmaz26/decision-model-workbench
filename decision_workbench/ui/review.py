"""Annotation assignment and immutable label review in the browser."""

import json
from urllib.parse import urlencode

import streamlit as st
from decision_workbench.data import options, hard_gold
from decision_workbench.platform.review_assignments import MAX_OFFSET


def render_queue(api, project, principal):
    with st.expander("Review work queue"):
        mine = st.checkbox(
            "Only my assignments",
            value=principal["role"] != "admin",
            key="assignment-mine-" + project,
        )
        page = st.number_input(
            "Assignment page (25 tasks per page)",
            min_value=0,
            max_value=MAX_OFFSET // 25,
            step=1,
            key="assignment-page-" + project,
        )
        query = urlencode(
            {
                "mine": str(mine).lower(),
                "status": "assigned",
                "limit": 25,
                "offset": int(page) * 25,
            }
        )
        items = api("GET", f"/v1/projects/{project}/review-assignments?{query}")[
            "items"
        ]
        if not items:
            st.caption("No open assignments on this page.")
            return
        st.dataframe(
            [
                {
                    "example": item["payload"]["record_id"],
                    "reviewer": item["payload"]["assignee"],
                    "revision": item["payload"]["revision"],
                    "reason": item["payload"]["note"],
                }
                for item in items
            ],
            hide_index=True,
        )
        selected = st.selectbox(
            "Assigned example",
            [item["id"] for item in items],
            format_func=lambda identifier: next(
                item["payload"]["record_id"]
                for item in items
                if item["id"] == identifier
            ),
            key="assignment-selection-" + project,
        )
        if st.button("Open assigned example", key="assignment-open-" + project):
            item = next(item for item in items if item["id"] == selected)
            st.session_state["next_dataset_" + project] = item["dataset_head"]
            st.session_state["review-search-" + project] = item["payload"]["record_id"]
            st.rerun()


def render_assignment(api, project, principal, dataset, record_id, is_latest):
    base = f"/v1/projects/{project}/datasets/{dataset['id']}/review-assignments"
    query = urlencode({"record_id": record_id, "limit": 1})
    items = api("GET", base + "?" + query)["items"]
    assignment = items[0]["payload"] if items else None
    revision = assignment["revision"] if assignment else 0
    if assignment:
        st.caption(
            f"Assignment: {assignment['status']} · reviewer: {assignment['assignee'] or 'unassigned'} · revision {revision}"
        )
        if (
            assignment["status"] == "assigned"
            and assignment["assignee"] != principal["actor"]
        ):
            st.info(
                "This example is assigned to another reviewer. Workspace owners may review it with a recorded note."
            )
    if principal["role"] == "admin":
        with st.expander("Assign review work"):
            st.caption(
                "Use the exact actor ID from your workspace's trusted identity configuration. Assignment does not change labels or approval."
            )
            with st.form(
                "assignment-form-"
                + dataset["id"]
                + "-"
                + record_id
                + "-"
                + str(revision)
            ):
                action = st.selectbox(
                    "Assignment action", ["Assign or reassign", "Cancel assignment"]
                )
                assignee = st.text_input(
                    "Reviewer actor ID",
                    value=(
                        assignment["assignee"]
                        if assignment and assignment["assignee"]
                        else ""
                    ),
                )
                note = st.text_input("Assignment reason")
                saved = st.form_submit_button("Save assignment", disabled=not is_latest)
            if saved:
                api(
                    "POST",
                    base,
                    json={
                        "targets": [
                            {"record_id": record_id, "expected_revision": revision}
                        ],
                        "assignee": (
                            assignee if action == "Assign or reassign" else None
                        ),
                        "expected_hash": dataset["payload"]["sha256"],
                        "note": note,
                    },
                )
                st.rerun()
    if st.checkbox(
        "Show assignment history",
        key="assignment-history-" + dataset["id"] + "-" + record_id,
    ):
        history_page = st.number_input(
            "Assignment history page (20 events)",
            min_value=0,
            max_value=MAX_OFFSET // 20,
            step=1,
            key="assignment-history-page-" + dataset["id"] + "-" + record_id,
        )
        params = urlencode(
            {"record_id": record_id, "limit": 20, "offset": int(history_page) * 20}
        )
        history = api("GET", base + "/history?" + params)["items"]
        st.dataframe(
            [
                {
                    "revision": item["payload"]["revision"],
                    "action": item["payload"]["event"],
                    "actor": item["payload"]["updated_by"],
                    "reviewer": item["payload"]["assignee"],
                    "note": item["payload"]["note"],
                    "dataset": item["payload"]["dataset_id"],
                }
                for item in history
            ],
            hide_index=True,
        )
    return assignment


def render_review(api, project, spec, principal, dataset, dataset_id, rows, is_latest):
    if not rows:
        st.info(
            "No example matches this ID. Clear the search to browse the collection."
        )
        return
    record_id = st.selectbox("Example to review", [r["id"] for r in rows])
    row = next(r for r in rows if r["id"] == record_id)
    if isinstance(row["state"], str):
        st.text(row["state"])
    else:
        st.json(row["state"])
    assignment = render_assignment(
        api, project, principal, dataset, record_id, is_latest
    )
    with st.form("review-" + dataset_id + "-" + record_id):
        label_values = {}
        for question_name, question in spec["questions"].items():
            values = options(question)
            target = row["gold"].get(question_name)
            current = (
                max(target["probabilities"], key=target["probabilities"].get)
                if target
                else None
            )
            selected = st.selectbox(
                question["instructions"],
                [None] + values,
                index=values.index(current) + 1 if current else 0,
                format_func=lambda v: ("Unknown — choose a label" if v is None else v),
                key="label-" + dataset_id + "-" + record_id + "-" + question_name,
            )
            if selected is not None:
                label_values[question_name] = selected
        advanced_targets = st.checkbox("Advanced: edit soft probability targets")
        gold = (
            st.text_area(
                "Gold probability distributions (used only with advanced targets)",
                json.dumps(row["gold"], indent=2),
            )
            if advanced_targets
            else None
        )
        review_status = st.selectbox(
            "Review decision",
            ["pending", "approved", "rejected"],
            index=["pending", "approved", "rejected"].index(
                row["metadata"].get(
                    "review_status",
                    "approved" if row["metadata"]["reviewed"] else "pending",
                )
            ),
            format_func=lambda value: {
                "pending": "Save draft labels",
                "approved": "Approve complete labels",
                "rejected": "Reject and exclude from future preparation",
            }[value],
        )
        approved = review_status == "approved"
        note = st.text_input("Review note")
        reviewed = st.form_submit_button(
            "Save reviewed version",
            disabled=not is_latest
            or (
                assignment is not None
                and assignment["status"] == "assigned"
                and principal["role"] != "admin"
                and assignment["assignee"] != principal["actor"]
            )
            or principal["role"] not in ("admin", "reviewer"),
        )
    if reviewed:
        saved = api(
            "POST",
            f"/v1/projects/{project}/datasets/{dataset_id}/reviews",
            json={
                "record_id": record_id,
                "gold": (
                    json.loads(gold)
                    if advanced_targets
                    else hard_gold(
                        {k: spec["questions"][k] for k in label_values},
                        label_values,
                    )
                ),
                "approved": approved,
                "status": review_status,
                "expected_hash": dataset["payload"]["sha256"],
                "assignment_revision": assignment["revision"] if assignment else None,
                "note": note,
            },
        )
        # Streamlit prohibits changing an instantiated widget; apply on the next run.
        st.session_state["next_dataset_" + project] = saved["id"]
        st.rerun()
