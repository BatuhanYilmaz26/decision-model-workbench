"""Explain reversible project archival and require explicit owner confirmation."""

import streamlit as st


def render_lifecycle(api, project, principal):
    current = api("GET", f"/v1/projects/{project}/lifecycle")
    archived = current["state"] == "archived"
    if archived:
        st.warning(
            "This project is archived. Restore it to review data, run jobs or make decisions."
        )
    if principal["role"] == "admin":
        with st.expander("Project lifecycle", expanded=archived):
            st.write(
                "Archive a project to pause new operations and predictions while retaining its data, releases, audit history and usage accounting. Restore it to resume the existing released model."
            )
            st.caption(
                "Archiving does not delete data or stop cloud billing. Finish/cancel pending jobs, observe unfinished cloud runs and reconcile uncertain paid calls first. Existing downloads can finish; resident runtimes follow their normal idle shutdown."
            )
            st.code(project, language=None)
            confirmed = st.text_input(
                "Confirm project ID", key="lifecycle-confirm-" + project
            )
            approved = st.checkbox(
                "I understand that data is retained and restoration resumes the existing release.",
                key="lifecycle-approved-" + project,
            )
            if st.button(
                "Restore project" if archived else "Archive project",
                key="lifecycle-change-" + project,
                disabled=not approved or confirmed != project,
            ):
                api(
                    "PUT",
                    f"/v1/projects/{project}/lifecycle",
                    json={
                        "state": "active" if archived else "archived",
                        "expected_revision": current["revision"],
                        "confirmation": project,
                    },
                )
                st.rerun()
    elif archived:
        st.info("Ask a workspace owner to restore this project.")
    return not archived
