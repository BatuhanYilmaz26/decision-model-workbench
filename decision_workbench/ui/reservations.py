"""Explicit, previewed source-group or date-boundary reservation controls."""

import hashlib
import json
import streamlit as st
from .guidance import render_hint

from decision_workbench.reservations import group_csv, read_group_csv, read_group_json


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def render_reservations(
    api, project, principal, datasets, semantic_audit, semantic_ready, base_snapshot
):
    if principal["role"] != "admin" or base_snapshot:
        return False, None
    enabled = st.checkbox(
        "Choose holdout groups or date cutoffs yourself",
        key="explicit-reservation-" + project,
    )
    if not enabled:
        return False, None
    render_hint("reservations")
    st.write(
        "Keep source families together, choose all four roles, preview the result and confirm it before reservation. This creates an immutable study; existing holdouts cannot be moved."
    )
    selected = {"datasets": datasets, "semantic_audit": semantic_audit}
    scope = project + "-" + identity(selected)
    inventory_key = "reservation-inputs-" + scope
    if st.button(
        "Inspect reservation inputs",
        disabled=not datasets or not semantic_ready,
        key="reservation-inspect-" + scope,
    ):
        st.session_state[inventory_key] = api(
            "POST", f"/v1/projects/{project}/reservation-preview", json=selected
        )
        st.session_state.pop("reservation-preview-" + scope, None)
    data = st.session_state.get(inventory_key)
    if not data:
        st.info(
            "Select reviewed dataset versions and inspect their source groups first."
        )
        return True, None
    key = scope + "-" + data["selection_sha256"]
    st.caption(
        f"{data['row_count']} eligible records · {len(data['groups'])} real source groups · {data['strategy']}"
    )
    mode = st.radio(
        "Assign reservation roles using",
        ["Source groups", "Date cutoffs"],
        key="reservation-mode-" + key,
    )
    embargo = int(
        st.number_input(
            "Minimum gap before each later role (seconds)",
            min_value=0,
            max_value=31 * 86400,
            step=1,
            key="reservation-embargo-" + key,
        )
    )
    if mode == "Source groups":
        state_key = "reservation-group-roles-" + key
        if state_key not in st.session_state:
            st.session_state[state_key] = {
                g["group_id"]: "train" for g in data["groups"] if g["forced_train"]
            }
        assignments = st.session_state[state_key]
        with st.expander("Import a complete assignment file"):
            st.write(
                "Fill every role using train, validation, calibration or test. Preserve each group identifier exactly. Synthetic or previously exposed families must stay in training."
            )
            try:
                st.download_button(
                    "Download group assignment CSV",
                    group_csv(data["groups"]),
                    "reservation-groups.csv",
                    "text/csv",
                    key="reservation-csv-" + key,
                )
            except ValueError as exc:
                st.info(str(exc))
            template = {
                g["group_id"]: "train" if g["forced_train"] else None
                for g in data["groups"]
            }
            st.download_button(
                "Download group assignment JSON",
                json.dumps(template, indent=2).encode(),
                "reservation-groups.json",
                "application/json",
                key="reservation-json-" + key,
            )
            upload = st.file_uploader(
                "Upload completed group assignments",
                type=["csv", "json"],
                max_upload_size=1,
                key="reservation-file-" + key,
            )
            if st.button(
                "Load assignment file",
                disabled=upload is None,
                key="reservation-load-" + key,
            ):
                try:
                    mapping = (
                        read_group_json
                        if upload.name.lower().endswith(".json")
                        else read_group_csv
                    )(upload.getvalue())
                    if set(mapping) != {g["group_id"] for g in data["groups"]}:
                        raise ValueError(
                            "Assign exactly the displayed groups; missing or extra identifiers are refused"
                        )
                    st.session_state[state_key] = mapping
                    st.session_state["reservation-edit-generation-" + key] = (
                        st.session_state.get("reservation-edit-generation-" + key, 0)
                        + 1
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        page = int(
            st.number_input(
                "Group assignment page (50 groups)",
                min_value=0,
                max_value=max(0, (len(data["groups"]) - 1) // 50),
                step=1,
                key="reservation-page-" + key,
            )
        )
        table = [
            {**g, "role": assignments.get(g["group_id"], "Unassigned")}
            for g in data["groups"][page * 50 : (page + 1) * 50]
        ]
        generation = st.session_state.get("reservation-edit-generation-" + key, 0)
        edited = st.data_editor(
            table,
            hide_index=True,
            num_rows="fixed",
            column_order=[
                "group_id",
                "role",
                "family_rows",
                "forced_train",
                "first_decision_at",
                "last_available_at",
                "family_id",
            ],
            disabled=list(data["groups"][0]) if data["groups"] else [],
            column_config={
                "group_id": st.column_config.TextColumn("Source group", width="medium"),
                "family_id": st.column_config.TextColumn(
                    "Linked family",
                    help="Groups with this same identifier must share a role.",
                ),
                "role": st.column_config.SelectboxColumn(
                    "Role",
                    width="small",
                    options=[
                        "Unassigned",
                        "train",
                        "validation",
                        "calibration",
                        "test",
                    ],
                    required=True,
                ),
            },
            key=f"reservation-grid-{key}-{page}-{generation}",
        )
        if st.button("Save assignments for this page", key="reservation-save-" + key):
            updated = dict(assignments)
            for row in edited:
                if row["role"] == "Unassigned":
                    updated.pop(row["group_id"], None)
                else:
                    updated[row["group_id"]] = row["role"]
            st.session_state[state_key] = updated
            st.rerun()
        st.caption(
            "Save each page before changing pages or previewing. The preview uses saved assignments."
        )
        policy = {
            "mode": "groups",
            "group_roles": dict(st.session_state[state_key]),
            "embargo_seconds": embargo,
        }
    else:
        if data["strategy"] != "chronological":
            st.info(
                "Date cutoffs need all three operational timestamps on every real record. Upload or map timestamps before using this mode."
            )
            return True, None
        st.write(
            "Enter ISO 8601 timestamps with a timezone. Each cutoff is the start of the named role; earlier decisions and available labels plus the gap must finish strictly before it. No records are silently dropped."
        )
        dates = [
            st.text_input(label, key="reservation-cutoff-" + key + str(i))
            for i, label in enumerate(
                ("Validation starts at", "Calibration starts at", "Test starts at")
            )
        ]
        policy = {"mode": "time", "cutoffs": dates, "embargo_seconds": embargo}
    draft_key = identity(policy)
    preview_key = "reservation-preview-" + scope
    if st.button(
        "Preview explicit reservation", key="reservation-preview-button-" + key
    ):
        try:
            result = api(
                "POST",
                f"/v1/projects/{project}/reservation-preview",
                json={**selected, "policy": policy},
            )
            st.session_state[preview_key] = {
                "draft_key": draft_key,
                "result": result,
                "policy": policy,
            }
        except ValueError as exc:
            st.session_state.pop(preview_key, None)
            st.error(str(exc))
    saved = st.session_state.get(preview_key)
    if not saved or saved["draft_key"] != draft_key:
        st.info("Preview the current saved assignments before confirming them.")
        return True, None
    result = saved["result"]
    st.write("Rows in each role")
    st.json(result["counts"])
    st.caption(
        "These counts do not establish class sufficiency or independent domain quality. Review the full source groups and time boundaries before training."
    )
    approved = st.checkbox(
        "I reviewed and approve these source groups, time boundaries and heldout roles",
        key="reservation-approved-" + result["sha256"],
    )
    if not approved:
        return True, None
    return True, {
        "reservation_policy": saved["policy"],
        "expected_reservation_sha256": result["sha256"],
        "reservation_approved": True,
    }
