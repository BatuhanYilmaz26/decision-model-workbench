"""Owner workflow for reviewing and creating a new policy version."""

import json
import uuid
import streamlit as st

from decision_workbench.data import validate_questions


def render_successor(api, project, spec, principal):
    if principal["role"] != "admin":
        return
    with st.expander("Create a policy successor"):
        st.caption(
            "Start a new policy version from this domain and question schema. "
            "The current project's data, models, acceptance gates and active release stay attached to it. "
            "The successor starts empty: review labels and reserve independent evaluation data before training."
        )
        if not st.checkbox("Plan a policy successor", key="successor-open-" + project):
            return
        acceptance = api("GET", f"/v1/projects/{project}/release-policy")
        parent_policy = acceptance["policy"]
        policy_id = parent_policy["id"] if parent_policy else None
        base = (
            "successor-"
            + project
            + "-"
            + spec["schema_sha256"]
            + "-"
            + (policy_id or "none")
        )
        if base not in st.session_state:
            st.session_state[base] = uuid.uuid4().hex
        if st.button("Start a fresh successor draft", key=base + "-reset"):
            st.session_state[base] = uuid.uuid4().hex
            st.rerun()
        st.caption(
            "A fresh draft discards unsaved edits and uses a new request key. Retries of the same draft return the same successor."
        )
        key = base + "-" + st.session_state[base]
        st.write(f"Source policy version: {spec['policy_version']}")
        st.dataframe(
            [
                {
                    "Question": name,
                    "Format": question["type"],
                    "Decision rule": question["instructions"],
                }
                for name, question in spec["questions"].items()
            ],
            hide_index=True,
        )
        carry = st.checkbox(
            "Include acceptance criteria in the successor",
            value=parent_policy is not None,
            key=key + "-carry",
        )
        with st.form(key):
            name = st.text_input(
                "Successor project name",
                value=f"{spec['name']} v{spec['policy_version'] + 1}"[:120],
                max_chars=120,
                key=key + "-name",
            )
            description = st.text_area(
                "Successor domain and decision policy",
                value=spec["description"],
                max_chars=12000,
                height=150,
                key=key + "-description",
            )
            requirements = st.text_area(
                "Successor data requirements",
                value=spec.get("data_requirements", ""),
                max_chars=4000,
                key=key + "-requirements",
            )
            schema = st.text_area(
                "Successor question schema (JSON)",
                value=json.dumps(spec["questions"], indent=2),
                height=220,
                key=key + "-schema",
                help="Keep the copied schema or explicitly edit question IDs, rules and outcomes. Existing records and gold are never relabeled automatically.",
            )
            criteria = None
            if carry:
                defaults = (
                    parent_policy["payload"]["criteria"]
                    if parent_policy
                    else {
                        "min_accuracy": 0.8,
                        "min_macro_f1": 0.8,
                        "max_ece": 0.1,
                        "min_test_rows": 100,
                        "min_training_examples_per_class": 5,
                        "min_calibration_examples_per_class": 1,
                    }
                )
                criteria = st.text_area(
                    "Successor acceptance criteria (JSON)",
                    value=json.dumps(defaults, indent=2),
                    height=200,
                    key=key + "-criteria",
                    help="Review copied criteria for the successor's schema. Update domain error-cost and critical-label IDs when changing outcomes. You can also edit these criteria in Training jobs before submitting the first study.",
                )
            note = st.text_area(
                "Reason for the policy successor", max_chars=1000, key=key + "-note"
            )
            confirmed = st.checkbox(
                "I approve this successor policy, schema and selected criteria",
                key=key + "-confirmed",
            )
            create = st.form_submit_button("Create reviewed successor")
        if create:
            if not confirmed:
                raise ValueError(
                    "Approve the successor policy, schema and criteria first"
                )
            questions = json.loads(schema)
            validate_questions(questions)
            body = {
                "name": name,
                "description": description,
                "questions": questions,
                "data_requirements": requirements,
                "criteria": json.loads(criteria) if carry else None,
                "expected_parent_schema_sha256": spec["schema_sha256"],
                "expected_parent_policy_id": policy_id,
                "note": note,
                "request_key": st.session_state[base],
            }
            successor = api("POST", f"/v1/projects/{project}/successors", json=body)
            st.session_state["next_project"] = successor["id"]
            st.session_state["successor_notice"] = (
                "Policy successor created. Review or upload examples, confirm acceptance criteria and reserve independent data before training."
            )
            st.rerun()
