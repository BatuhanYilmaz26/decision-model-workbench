"""Owner capture, inspection and verified audit downloads."""

import io
import uuid
import streamlit as st
from .guidance import render_hint
from decision_workbench.platform.audit_exports import (
    verify_stream,
    MAX_BYTES,
    MAX_MANIFEST,
    MAX_LINE,
)


def render_audit_exports(api, project):
    st.caption(
        "The recent audit view shows up to 500 events. Capture frozen audit windows to inspect and export older history."
    )
    if not st.checkbox("Open audit exports", key="audit-open-" + project):
        return
    render_hint("audit")
    prefix = f"/v1/projects/{project}/audit-exports"
    request_key = "audit-request-" + project
    if request_key not in st.session_state:
        st.session_state[request_key] = uuid.uuid4().hex
    if st.button("Start a new audit capture", key="audit-reset-" + project):
        st.session_state[request_key] = uuid.uuid4().hex
        st.rerun()
    draft = request_key + "-" + st.session_state[request_key]
    fixed_end = st.checkbox(
        "Use a previously frozen audit end identifier", key=draft + "-fixed"
    )
    with st.form(draft):
        after = st.text_input(
            "Audit after event identifier",
            value="0",
            key=draft + "-after",
            help="Start with 0, or use the last_id from a verified prior window.",
        )
        until = (
            st.text_input("Frozen audit end identifier", key=draft + "-until")
            if fixed_end
            else None
        )
        note = st.text_area("Audit export purpose", max_chars=1000, key=draft + "-note")
        confirmed = st.checkbox(
            "I approve downloading this project's audit identities and notes",
            key=draft + "-approved",
        )
        capture = st.form_submit_button("Capture audit window")
    if capture:
        if not confirmed:
            raise ValueError("Approve the audit data download before capture")
        body = {
            "after_id": int(after),
            "until_id": int(until) if fixed_end else None,
            "note": note,
            "request_key": st.session_state[request_key],
        }
        entity = api("POST", prefix, json=body)
        st.session_state["audit-selected-" + project] = entity["id"]
        st.session_state[request_key] = uuid.uuid4().hex
        st.success(
            "Audit window captured. Retain its manifest checksum separately and verify the complete download."
        )
    history = st.number_input(
        "Audit export history page",
        min_value=0,
        value=0,
        step=1,
        key="audit-history-" + project,
    )
    exports = api("GET", prefix + f"?limit=50&offset={int(history)*50}")
    selected_key = "audit-selected-" + project
    selected = st.session_state.get(selected_key)
    if selected and selected not in [e["id"] for e in exports]:
        exports.insert(0, api("GET", prefix + "/" + selected))
    if not exports:
        st.info("Capture an audit window to begin.")
        return
    by_id = {e["id"]: e for e in exports}
    identifier = st.selectbox("Frozen audit export", list(by_id), key=selected_key)
    export = api("GET", prefix + "/" + identifier)
    manifest = export["payload"]["manifest"]
    checksum = export["payload"]["manifest_sha256"]
    st.write(
        f"{manifest['count']} events · {manifest['byte_count']} data bytes · after {manifest['after_id']} through {manifest['last_id']}"
    )
    st.code(checksum)
    st.caption(
        "Save this manifest checksum in a separate trusted location. It detects later modification against that retained receipt; it is not an external audit witness."
    )
    if manifest["has_more"]:
        st.warning(
            f"More events remain in this frozen history. Continue after {manifest['last_id']} with end identifier {manifest['until_id']}."
        )
    else:
        st.caption(
            f"This window reaches the requested frozen history end {manifest['until_id']}."
        )
    if manifest["segments"]:
        page_index = st.number_input(
            "Audit record page",
            min_value=0,
            max_value=len(manifest["segments"]) - 1,
            value=0,
            step=1,
            key="audit-page-" + identifier,
        )
        page = api("GET", prefix + f"/{identifier}/pages/{int(page_index)}")
        st.dataframe(page["rows"], hide_index=True)
    download_key = "audit-download-" + project + "-" + identifier + "-" + checksum
    if st.button("Prepare verified audit download", key=download_key + "-prepare"):
        body = api("GET", prefix + "/" + identifier + "/download", binary=True)
        if len(body) > MAX_BYTES + MAX_MANIFEST + MAX_LINE:
            raise ValueError("Audit download exceeds the browser's bounded window size")
        verify_stream(io.BytesIO(body), checksum)
        # Bound the session to one retained audit download across projects.
        for name in list(st.session_state):
            if name.startswith("audit-download-") and isinstance(
                st.session_state[name], bytes
            ):
                del st.session_state[name]
        st.session_state[download_key] = body
    if download_key in st.session_state:
        st.download_button(
            "Download verified audit window",
            st.session_state[download_key],
            "project-audit.dmtlog",
            "application/x-ndjson",
            key=download_key + "-save",
        )
