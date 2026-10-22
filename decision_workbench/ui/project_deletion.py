"""Reviewed permanent deletion and private owner receipt lookup."""

import hashlib
import json
from datetime import datetime, timezone
import streamlit as st


def namespace(principal):
    return (
        "deletion-receipt-"
        + hashlib.sha256(
            json.dumps(
                [
                    st.session_state.get("api_url"),
                    principal["tenant"],
                    principal["actor"],
                ]
            ).encode()
        ).hexdigest()
    )


def show_receipt(value):
    st.json(value, expanded=False)
    st.download_button(
        "Save deletion receipt",
        json.dumps(value, separators=(",", ":")),
        "project-deletion-" + value["project"] + ".dmtdelete",
        "application/json",
        on_click="ignore",
    )


def render_lookup(api, principal):
    if principal["role"] != "admin":
        return
    key = namespace(principal)
    with st.sidebar.expander("Deleted project receipts"):
        if st.button("Find a deletion receipt", key=key + "-open"):
            st.session_state[key + "-open-state"] = True
        if st.session_state.get(key + "-open-state"):
            identifier = st.text_input("Deleted project ID", key=key + "-id")
            if st.button(
                "Look up receipt", key=key + "-fetch", disabled=len(identifier) != 32
            ):
                st.session_state[key] = api(
                    "GET", f"/v1/projects/{identifier}/deletion"
                )
                st.session_state.pop(key + "-audit-page", None)
        value = st.session_state.get(key)
        if value:
            show_receipt(value)
            if st.button("Show retained audit page", key=key + "-audit"):
                st.session_state[key + "-audit-page"] = api(
                    "GET", f"/v1/projects/{value['project']}/deletion/audit?limit=100"
                )
            page = st.session_state.get(key + "-audit-page")
            if page:
                st.json(page, expanded=False)
                if page["has_more"] and st.button(
                    "Next retained audit page", key=key + "-next-audit"
                ):
                    st.session_state[key + "-audit-page"] = api(
                        "GET",
                        f"/v1/projects/{value['project']}/deletion/audit?limit=100&after_id={page['next_after_id']}",
                    )
                    st.rerun()


def render_delete(api, project, principal):
    with st.expander("Permanently delete project data"):
        st.warning(
            "This removes the archived project's records from the application database. It cannot be undone here. Model files, caches, backups, logs and provider storage need separate cleanup; database deletion is not secure media erasure."
        )
        days = st.number_input(
            "Minimum days archived before deletion",
            min_value=0,
            max_value=3650,
            value=30,
            key="delete-days-" + project,
        )
        mode = st.selectbox(
            "Historical audit events",
            ["retain", "erase"],
            format_func=lambda v: (
                "Retain events (including actor IDs and notes)"
                if v == "retain"
                else "Erase historical events; keep only the deletion receipt"
            ),
            key="delete-audit-" + project,
        )
        settings = {"audit_mode": mode, "min_archived_days": int(days)}
        key = "delete-preview-" + project
        saved = st.session_state.get(key)
        if saved and saved["settings"] != settings:
            st.session_state.pop(key, None)
            saved = None
        if st.button(
            "Preview permanent deletion", key="delete-preview-button-" + project
        ):
            saved = {
                "settings": settings,
                "preview": api(
                    "POST", f"/v1/projects/{project}/deletion/preview", json=settings
                ),
            }
            st.session_state[key] = saved
        if saved:
            preview = saved["preview"]
            plan = preview["plan"]
            sha = preview["plan_sha256"]
            first, second = st.columns(2)
            first.metric("Record versions to unlink", plan["records_to_remove"])
            second.metric("Shared within workspace", plan["shared_records_retained"])
            st.write(
                f"Remove {plan['entities']} project metadata records and {plan['jobs']} job records. Retain {plan['usage_records']} minimal usage counters."
            )
            st.caption(
                "Eligible at (UTC): "
                + datetime.fromtimestamp(plan["eligible_at"], timezone.utc).isoformat()
            )
            st.caption(
                "Minimal hashed quota counters and an owner deletion receipt remain. Unlinking removes this project's references; content needed elsewhere stays stored. Counts describe your workspace and do not report other tenants' storage. Save external resource information before deleting; this action makes no cloud calls."
            )
            st.download_button(
                "Save reviewed deletion plan",
                json.dumps(preview, separators=(",", ":")),
                "project-deletion-plan-" + project + ".dmtdelete",
                "application/json",
                key="delete-plan-download-" + sha,
                on_click="ignore",
            )
            if not preview["eligible"]:
                st.info(
                    "The selected archived retention period has not elapsed. Deletion will be refused until eligible."
                )
            confirmed = st.text_input(
                "Type project ID to delete", key="delete-confirm-" + sha
            )
            approved = st.checkbox(
                "I reviewed this plan and audit choice, and recorded any external copies needing separate cleanup.",
                key="delete-approved-" + sha,
            )
            if st.button(
                "Delete project data permanently",
                key="delete-apply-" + sha,
                disabled=not preview["eligible"]
                or confirmed != project
                or not approved,
            ):
                value = api(
                    "POST",
                    f"/v1/projects/{project}/deletion",
                    json={**settings, "plan_sha256": sha, "confirmation": project},
                )
                st.session_state[namespace(principal)] = value
                st.session_state.pop(namespace(principal) + "-audit-page", None)
                st.success(
                    "Project records deleted. Save the receipt; refresh the workspace to leave this project."
                )
                show_receipt(value)
                st.stop()
