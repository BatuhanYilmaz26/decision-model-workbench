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
