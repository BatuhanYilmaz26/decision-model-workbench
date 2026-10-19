"""Explicit text/structured evidence entry for the released decision schema."""

import streamlit as st
from decision_workbench.json_tools import strict_json


def evidence_input(label, key):
    mode = st.radio(
        "Evidence format", ("Text", "JSON"), horizontal=True, key=key + "-format"
    )
    value = st.text_area(label, key=key + "-body")
    st.caption(
        "Text sends exactly what you type. JSON parses an object, array or JSON string before sending it to the released model. Use the same evidence structure as your reviewed examples; omit future outcomes and unnecessary personal data."
    )
    return mode, value


def parsed_evidence(mode, value):
    if len(value.encode("utf-8")) > 64000:
        raise ValueError("Decision evidence exceeds 64,000 UTF-8 bytes")
    if mode == "Text":
        state = value
    elif mode == "JSON":
        state = strict_json(value)
    else:
        raise ValueError("Select Text or JSON evidence")
    if not isinstance(state, (str, dict, list)) or not state:
        raise ValueError("Provide nonempty text, a JSON object or a JSON array")
    return state


def release_guard(api, project, schema_sha256):
    """Bind an optional prediction precondition to one active-release read."""
    active = api("GET", f"/v1/projects/{project}/active")
    pin = st.checkbox(
        "Require the active release version",
        value=True,
        key="decision-release-" + project,
        help="Reject before execution if the release changes between this snapshot read and the API check. Requests already started may finish with the earlier version.",
    )
    if not active["model_id"]:
        st.info("Activate an eligible model version before making predictions.")
        return None, False
    expected = {
        "model_id": active["model_id"],
        "release_revision": active["revision"],
        "schema_sha256": schema_sha256,
    }
    with st.expander("Release identity for integrations"):
        st.code(f"Project ID: {project}")
        st.json(expected)
        st.caption(
            "Copy this identity into the expected_release field of the API client request. A mismatch returns a conflict rather than silently selecting another release."
        )
    return expected if pin else None, True
