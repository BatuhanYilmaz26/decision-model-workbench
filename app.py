"""Browser client for durable projects, review, jobs, models and decisions."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS, get_profile

import csv
import io
import json
import math
import os
from pathlib import Path
import uuid
import httpx
import streamlit as st
from decision_workbench.data import validate_questions
from decision_workbench.ui.intake import render_examples, render_upload
from decision_workbench.ui.onboarding import render_connection, render_runtime
from decision_workbench.ui.enterprise_auth import sign_out
from decision_workbench.ui.guidance import (
    render_help_navigation,
    render_hint,
    render_role_help,
)

st.set_page_config(page_title="Decision Model Workbench", layout="wide")
st.title("Decision Model Workbench")
st.caption(
    "Define a domain · review data · train · evaluate · release · make decisions"
)
render_help_navigation()

# A render owns its client; reruns and separate browser sessions never share it.
_api_client = None


def api(method, path, **kwargs):
    global _api_client
    binary = kwargs.pop("binary", False)
    timeout = 30.0
    if method == "GET" and path == "/v1/runtime-capabilities":
        timeout = 75.0
    if method == "POST" and path.endswith(("/releases", "/decisions")):
        timeout = float(os.environ.get("DMT_UI_MODEL_TIMEOUT_SECONDS", "420"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(
                "The operator must configure a finite positive model-action timeout"
            )
    if _api_client is None:
        _api_client = httpx.Client(
            base_url=st.session_state["api_url"],
            headers={"Authorization": "Bearer " + st.session_state["credential"]},
            timeout=30.0,
            trust_env=st.session_state.get("connection_mode")
            not in ("personal", "oidc"),
        )
    try:
        response = _api_client.request(method, path, timeout=timeout, **kwargs)
    except httpx.TimeoutException as exc:
        raise ValueError(
            "Timed out waiting for the service. The request may still complete; refresh the workspace before submitting again."
        ) from exc
    if response.is_error:
        try:
            detail = response.json().get("detail", response.status_code)
        except ValueError:
            detail = f"Service returned HTTP {response.status_code}"
        raise ValueError(str(detail))
    return response.content if binary else response.json()


def submit(project, kind, config):
    return api(
        "POST",
        f"/v1/projects/{project}/jobs",
        json={"kind": kind, "config": config, "request_key": uuid.uuid4().hex},
    )


render_connection(Path(__file__).resolve().parent)

principal = st.session_state["principal"]
with st.sidebar:
    st.write(f"Workspace: {principal['tenant']}")
    st.caption(f"{principal['actor']} · {principal['role']}")
    render_role_help(principal["role"])
    if st.button("Sign out"):
        sign_out()
    if st.button("Refresh workspace"):
        st.rerun()

try:
    capabilities = render_runtime(api, principal)
    next_project = st.session_state.pop("next_project", None)
    if next_project:
        st.session_state["project_history_page"] = 0
        st.session_state["active_project"] = next_project
    project_page = st.sidebar.number_input(
        "Project history page", min_value=0, step=1, key="project_history_page"
    )
    projects = api("GET", f"/v1/projects?limit=100&offset={int(project_page) * 100}")
    labels = {p["id"]: p["payload"]["name"] for p in projects}
    if st.session_state.get("active_project") not in labels:
        st.session_state.pop("active_project", None)
    project = (
        st.sidebar.selectbox(
            "Project",
            list(labels),
            format_func=lambda value: f"{labels[value]} · {value[:8]}",
            key="active_project",
        )
        if projects
        else None
    )
    if project is None:
        st.subheader("Start with your domain")
        st.write(
            "Open Create a domain project below. Describe the decision you want to make, the evidence available and the allowed answers. Then enter a few examples or upload a spreadsheet."
        )
        st.markdown(
            "1. **Define** your domain and decision questions.\n2. **Add and review** real examples (CSV, XLSX, TSV, JSON or JSONL).\n3. **Reserve** independent training and evaluation data; optionally generate training candidates.\n4. **Train** on a CPU, a compatible local accelerator or an approved cloud GPU.\n5. **Evaluate and release**, then make decisions or connect the API to your application."
        )
        st.link_button(
            "Follow the complete workflow",
            "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/product-workflow.md",
        )
    with st.expander("Create a domain project", expanded=not projects):
        render_hint("project")
        from decision_workbench.ui.schema_intake import render_schema_assistant

        render_schema_assistant(api, principal)
        st.write(
            "Describe the policy and data you need. Build and confirm the questions your model should decide."
        )
        if "schema_draft" not in st.session_state:
            st.session_state["schema_draft"] = {}
        with st.form("add-question"):
            question_id = st.text_input(
                "Question identifier", placeholder="routing_team"
            )
            question_kind = st.selectbox(
                "Answer format",
                ["choice", "noul", "score"],
                format_func=lambda v: {
                    "choice": "Choose one category",
                    "noul": "Yes or no",
                    "score": "Ordered rating",
                }[v],
            )
            instructions = st.text_area(
                "What should the model decide? Include the decision rule."
            )
            criteria = st.text_area(
                "Outcomes: category_key | description, one per line. For ratings, one level description per line. Yes/no needs no outcomes."
            )
            add_question = st.form_submit_button(
                "Add or update question",
                disabled=principal["role"] not in ("admin", "editor"),
            )
        if add_question:
            question = {"type": question_kind, "instructions": instructions}
            if question_kind == "choice":
                pairs = [
                    line.split("|", 1) for line in criteria.splitlines() if line.strip()
                ]
                if any(len(pair) != 2 for pair in pairs):
                    raise ValueError("Each category needs key | description")
                keys = [pair[0].strip() for pair in pairs]
                if len(set(keys)) != len(keys):
                    raise ValueError("Category keys must be unique")
                question["criteria"] = {
                    pair[0].strip(): pair[1].strip() for pair in pairs
                }
            elif question_kind == "score":
                question["criteria"] = [
                    line.strip() for line in criteria.splitlines() if line.strip()
                ]
            validate_questions({question_id: question})
            st.session_state["schema_draft"][question_id] = question
        st.dataframe(
            [
                {"question": k, "format": v["type"], "rule": v["instructions"]}
                for k, v in st.session_state["schema_draft"].items()
            ],
            hide_index=True,
        )
        if st.session_state["schema_draft"]:
            remove = st.selectbox(
                "Remove a draft question",
                [None] + list(st.session_state["schema_draft"]),
                format_func=lambda value: value or "Select a question",
            )
            if st.button("Remove question", disabled=remove is None):
                del st.session_state["schema_draft"][remove]
                st.rerun()
        with st.expander("Advanced: import an existing JSON schema"):
            with st.form("schema-import"):
                schema_json = st.text_area("Existing schema JSON")
                import_schema = st.form_submit_button("Import schema")
            if import_schema:
                proposed = json.loads(schema_json)
                validate_questions(proposed)
                st.session_state["schema_draft"] = proposed
                st.rerun()
        with st.form("project"):
            parent_project = st.selectbox(
                "Policy successor of",
                [None] + list(labels),
                format_func=lambda v: labels.get(v, "New independent project"),
                disabled=principal["role"] != "admin",
            )
            name = st.text_input("Project name")
            description = st.text_area(
                "Describe your domain, required data, decision policy and boundary cases",
                height=150,
                help="Explain what the model may decide, the evidence available at decision time, policy exceptions and cases needing human review.",
            )
            data_requirements = st.text_area(
                "Describe the data you need: examples, rare cases, languages and evidence fields",
                max_chars=4000,
                help="Describe operational examples, rare outcomes, languages, evidence fields and source families. Exclude future outcomes and unnecessary personal data. Enter or upload a few examples after creating the project.",
            )
            confirmed = st.checkbox("I confirm this schema and policy for the project")
            create = st.form_submit_button(
                "Create project", disabled=principal["role"] not in ("admin", "editor")
            )
        if create:
            if not confirmed:
                raise ValueError("Confirm the schema and policy first")
            validate_questions(st.session_state["schema_draft"])
            created_project = api(
                "POST",
                "/v1/projects",
                json={
                    "name": name,
                    "description": description,
                    "questions": st.session_state["schema_draft"],
                    "parent_project": parent_project,
                    "data_requirements": data_requirements,
                },
            )
            st.session_state["schema_draft"] = {}
            st.session_state["next_project"] = created_project["id"]
            st.rerun()
    if project is None:
        st.info("Create your first project to begin.")
        st.stop()
    spec = next(p["payload"] for p in projects if p["id"] == project)
    st.header(spec["name"])
    st.write(spec["description"])
    st.caption(
        f"Policy version {spec['policy_version']} · {len(spec['questions'])} decision questions"
    )
    successor_notice = st.session_state.pop("successor_notice", None)
    if successor_notice:
        st.success(successor_notice)
    from decision_workbench.ui.successors import render_successor

    render_successor(api, project, spec, principal)
    if principal["role"] == "inference":
        st.subheader("Make a decision")
        render_hint("decisions")
        from decision_workbench.ui.prediction_input import (
            evidence_input,
            parsed_evidence,
            release_guard,
        )

        evidence_mode, evidence_body = evidence_input(
            "Input evidence", "inference-evidence-" + project
        )
        expected_release, prediction_ready = release_guard(
            api, project, spec["schema_sha256"]
        )
        if st.button("Decide", disabled=not prediction_ready):
            st.json(
                api(
                    "POST",
                    f"/v1/projects/{project}/decisions",
                    json={
                        "state": parsed_evidence(evidence_mode, evidence_body),
                        "threshold": 0.8,
                        "expected_release": expected_release,
                    },
                )
            )
        st.stop()
    history_page = st.sidebar.number_input(
        "Dataset history page", min_value=0, value=0, step=1, key="history-" + project
    )
    datasets = api(
        "GET",
        f"/v1/projects/{project}/entities/dataset?limit=50&offset={int(history_page) * 50}",
    )
    snapshots = api("GET", f"/v1/projects/{project}/entities/snapshot")
    models = api("GET", f"/v1/projects/{project}/entities/model")
    tabs = st.tabs(
        [
            "Data and review",
            "Prepare and generate",
            "Training jobs",
            "Models and releases",
            "Decisions and audit",
            "Applicability",
        ]
    )
    st.caption(
        "Work left to right: add and review examples → reserve data / generate candidates → train locally, evaluate a hosted API or prepare cloud training → release a tested version → make decisions. Applicability is an additional requirement for Vega."
    )
    with tabs[5]:
        render_hint("applicability")
        from decision_workbench.ui.gates import render_gates

        next_gate_root = st.session_state.pop("gate-next-root-" + project, None)
        if next_gate_root:
            st.session_state["gate-collection-" + project] = next_gate_root
        render_gates(api, submit, project, spec, principal, snapshots)
    with tabs[0]:
        render_hint("data")
        from decision_workbench.ui.review import render_queue

        render_queue(api, project, principal)
        headers = ["id", "group_id", "state", *spec["questions"], "reviewed"]
        template = io.StringIO()
        writer = csv.writer(template)
        writer.writerow(headers)
        writer.writerow(
            [
                "example-1",
                "source-family-1",
                "Replace with your domain example",
                *["LABEL" for _ in spec["questions"]],
                "false",
            ]
        )
        st.download_button(
            "Download this project’s CSV format",
            template.getvalue(),
            "domain-examples.csv",
            "text/csv",
        )
        st.json(spec["questions"], expanded=False)
        render_examples(api, project, spec, principal, datasets)
        render_upload(api, project, spec, principal, datasets)
        if datasets:
            selection_key = "dataset-" + project
            next_version = st.session_state.pop("next_dataset_" + project, None)
            if next_version:
                st.session_state[selection_key] = next_version
            selected_version = st.session_state.get(selection_key)
            if selected_version and selected_version not in [d["id"] for d in datasets]:
                extra = api(
                    "GET",
                    f"/v1/projects/{project}/datasets/{selected_version}/rows?limit=1",
                )
                datasets.insert(0, extra["dataset"])
            dataset_id = st.selectbox(
                "Dataset version",
                [d["id"] for d in datasets],
                key=selection_key,
                format_func=lambda k: (
                    f"{k[:10]} · {next(d for d in datasets if d['id'] == k)['payload']['row_count']} rows"
                ),
            )
            dataset = next(d for d in datasets if d["id"] == dataset_id)
            row_page = st.number_input(
                "Example page (50 rows per page)",
                min_value=0,
                max_value=max(0, (dataset["payload"]["row_count"] - 1) // 50),
                value=0,
                step=1,
                key="row-page-" + dataset_id,
            )
            search_record = st.text_input(
                "Find example by ID", key="review-search-" + project
            ).strip()
            from urllib.parse import urlencode

            row_query = urlencode(
                {
                    "limit": 50,
                    "offset": 0 if search_record else int(row_page) * 50,
                    **({"record_id": search_record} if search_record else {}),
                }
            )
            page = api(
                "GET",
                f"/v1/projects/{project}/datasets/{dataset_id}/rows?{row_query}",
            )
            rows = page["rows"]
            from decision_workbench.ui.taxonomy import render_taxonomy

            render_taxonomy(api, submit, project, spec, principal, dataset_id, rows)
            st.write(
                f"Approved: {dataset['payload']['reviewed_count']} · Rejected: {dataset['payload'].get('rejected_count', 0)} · Total: {dataset['payload']['row_count']}"
            )
            if st.button("Analyze data readiness", key="quality-button-" + dataset_id):
                st.session_state["quality-" + dataset_id] = api(
                    "GET", f"/v1/projects/{project}/datasets/{dataset_id}/quality"
                )
            quality = st.session_state.get("quality-" + dataset_id)
            if quality:
                with st.expander("Data readiness report", expanded=True):
                    report = quality["report"]
                    for blocker in report["preparation_blockers"]:
                        st.warning(blocker)
                    for warning in report["warnings"]:
                        st.info(warning)
                    st.dataframe(
                        [
                            {
                                "question": name,
                                "missing": item["missing_rows"],
                                "reviewed": item["reviewed_rows"],
                                "class_counts": item["class_counts"],
                            }
                            for name, item in report["per_question"].items()
                        ],
                        hide_index=True,
                    )
                    st.json(report, expanded=False)
            is_latest = page["head"] == dataset_id
            if not is_latest:
                st.info(
                    "This immutable historical version can be inspected. Open its latest version to submit another review."
                )
                if st.button("Open latest review version"):
                    st.session_state["next_dataset_" + project] = page["head"]
                    st.rerun()
            from decision_workbench.ui.review import render_review

            render_review(
                api, project, spec, principal, dataset, dataset_id, rows, is_latest
            )
    with tabs[1]:
        render_hint("prepare")
        chosen = st.multiselect(
            "Dataset versions to prepare (approved rows; rejected rows excluded)",
            [d["id"] for d in datasets],
        )
        st.caption(
            "Select one revision per source upload. Duplicate record IDs and unreviewed rows stop preparation. Grouped real holdouts are reserved before synthetic generation."
        )
        base_snapshot = st.selectbox(
            "Preserve holdouts from an existing snapshot when adding synthetic data",
            [None] + [s["id"] for s in snapshots],
            format_func=lambda value: value or "Create the first reservation",
        )
        split_strategy = st.selectbox(
            "Reservation strategy",
            ["auto", "chronological", "group-random"],
            disabled=bool(base_snapshot),
        )
        from decision_workbench.ui.semantic import render_semantic

        semantic_audit, semantic_ready = render_semantic(
            api, submit, project, principal, chosen, base_snapshot
        )
        from decision_workbench.ui.reservations import render_reservations

        explicit_requested, explicit_config = render_reservations(
            api,
            project,
            principal,
            chosen,
            semantic_audit,
            semantic_ready,
            base_snapshot,
        )
        st.caption(
            "Auto uses chronological roles when real records declare decision/evidence/label timestamps; otherwise it preserves grouped random reservations. Chronological roles keep prior decisions and available labels strictly before the next role. Synthetic extensions preserve the original reservation."
        )
        if st.button(
            "Reserve train / validation / calibration / test",
            disabled=not chosen
            or not semantic_ready
            or (explicit_requested and explicit_config is None)
            or principal["role"] not in ("admin", "editor"),
        ):
            st.json(
                submit(
                    project,
                    "prepare",
                    {
                        "datasets": chosen,
                        "split_strategy": (
                            "auto"
                            if base_snapshot or explicit_requested
                            else split_strategy
                        ),
                        **(explicit_config or {}),
                        **(
                            {"semantic_audit": semantic_audit} if semantic_audit else {}
                        ),
                        **({"base_snapshot": base_snapshot} if base_snapshot else {}),
                    },
                )
            )
        if snapshots:
            snapshot_id = st.selectbox(
                "Reserved data snapshot", [s["id"] for s in snapshots]
            )
            snapshot = next(s for s in snapshots if s["id"] == snapshot_id)
            st.json(snapshot["payload"]["counts"])
            with st.expander("Download reserved study data"):
                st.caption(
                    "Download all four canonical JSONL roles and their checksum manifest for a standalone model workflow. This includes evidence and gold labels; store it privately. Downloading does not train a model or submit cloud compute."
                )
                export_key = "snapshot-export-" + project + "-" + snapshot_id
                export_approved = st.checkbox(
                    "I approve downloading this snapshot's evidence and reviewed labels",
                    key=export_key + "-approved",
                )
                if st.button(
                    "Prepare reserved study download",
                    disabled=not export_approved,
                    key=export_key + "-prepare",
                ):
                    study_body = api(
                        "GET",
                        f"/v1/projects/{project}/snapshots/{snapshot_id}/download",
                        binary=True,
                    )
                    for old_key in list(st.session_state):
                        if old_key.startswith("snapshot-export-bytes-"):
                            del st.session_state[old_key]
                    st.session_state["snapshot-export-bytes-" + export_key] = study_body
                download_body = st.session_state.get(
                    "snapshot-export-bytes-" + export_key
                )
                if download_body is not None and export_approved:
                    st.download_button(
                        "Download reserved canonical study",
                        download_body,
                        "reserved-study.zip",
                        "application/zip",
                        key=export_key + "-save",
                    )
            if snapshot["payload"].get("temporal_audit"):
                with st.expander("Declared temporal reservation audit"):
                    st.json(snapshot["payload"]["temporal_audit"])
        from decision_workbench.ui.generation import render_generation

        render_generation(api, project, principal, snapshot if snapshots else None)
    with tabs[2]:
        render_hint("training")
        st.write(
            "Training runs in the independent worker; you can close this browser without losing the job."
        )
        acceptance = api("GET", f"/v1/projects/{project}/release-policy")
        with st.expander(
            "Acceptance criteria — set before the first training run",
            expanded=acceptance["policy"] is None,
        ):
            render_hint("criteria")
            existing = (
                acceptance["policy"]["payload"]["criteria"]
                if acceptance["policy"]
                else {
                    "min_accuracy": 0.8,
                    "min_macro_f1": 0.8,
                    "max_ece": 0.1,
                    "min_test_rows": 100,
                }
            )
            criteria_key = (
                "acceptance-"
                + project
                + "-"
                + (acceptance["policy"]["id"] if acceptance["policy"] else "new")
            )
            error_mode = st.radio(
                "Configure domain error criteria",
                ["Guided tables", "Advanced JSON"],
                horizontal=True,
                key="error-mode-" + project,
                disabled=acceptance["locked"] or principal["role"] != "admin",
            )
            st.caption(
                "Save acceptance criteria applies the selected editor and ordinary quality gates together. Switching editors discards unsaved changes; saved criteria reload from the workspace. If another owner changes the policy, review the refreshed form before saving."
            )
            with st.form(criteria_key):
                minimum_accuracy = st.number_input(
                    "Minimum accuracy for every question",
                    0.0,
                    1.0,
                    float(existing["min_accuracy"]),
                )
                minimum_f1 = st.number_input(
                    "Minimum macro-F1 for every question",
                    0.0,
                    1.0,
                    float(existing["min_macro_f1"]),
                )
                maximum_ece = st.number_input(
                    "Maximum ECE (10 equal-width bins)",
                    0.0,
                    1.0,
                    float(existing["max_ece"]),
                )
                minimum_test_rows = st.number_input(
                    "Minimum real test rows for every question",
                    min_value=10,
                    value=existing["min_test_rows"],
                )
                minimum_training_class = st.number_input(
                    "Minimum reviewed training examples per outcome",
                    0,
                    100000,
                    int(existing.get("min_training_examples_per_class", 5)),
                )
                minimum_calibration_class = st.number_input(
                    "Minimum reviewed calibration examples per outcome",
                    0,
                    100000,
                    int(existing.get("min_calibration_examples_per_class", 1)),
                )
                vega_enabled = st.checkbox(
                    "Enable Vega applicability acceptance criteria",
                    value="vega" in existing,
                )
                vega_limits = existing.get("vega", {})
                vega_positive = st.number_input(
                    "Minimum Vega positive accepted fraction",
                    0.0,
                    1.0,
                    float(vega_limits.get("min_positive_accepted_fraction", 0.8)),
                )
                vega_negative = st.number_input(
                    "Maximum Vega negative false-open rate",
                    0.0,
                    1.0,
                    float(vega_limits.get("max_negative_false_open_rate", 0.05)),
                )
                vega_negative_rows = st.number_input(
                    "Minimum Vega negative test rows for every question",
                    10,
                    20000,
                    int(vega_limits.get("min_negative_test_rows", 100)),
                )
                from decision_workbench.ui.error_policy import render_criteria

                error_policy_body = render_criteria(
                    existing,
                    spec["questions"],
                    acceptance["locked"] or principal["role"] != "admin",
                    mode=error_mode,
                    key=criteria_key + "-domain",
                )
                save_criteria = st.form_submit_button(
                    "Save acceptance criteria",
                    disabled=acceptance["locked"] or principal["role"] != "admin",
                )
            if save_criteria:
                from decision_workbench.ui.error_policy_editor import policy_from_editor

                selected_error_policy = policy_from_editor(
                    error_policy_body, spec["questions"]
                )
                api(
                    "PUT",
                    f"/v1/projects/{project}/release-policy",
                    json={
                        "min_accuracy": minimum_accuracy,
                        "min_macro_f1": minimum_f1,
                        "max_ece": maximum_ece,
                        "min_test_rows": int(minimum_test_rows),
                        "min_training_examples_per_class": int(minimum_training_class),
                        "min_calibration_examples_per_class": int(
                            minimum_calibration_class
                        ),
                        "expected_id": (
                            acceptance["policy"]["id"] if acceptance["policy"] else None
                        ),
                        "vega": (
                            {
                                "min_positive_accepted_fraction": vega_positive,
                                "max_negative_false_open_rate": vega_negative,
                                "min_negative_test_rows": int(vega_negative_rows),
                            }
                            if vega_enabled
                            else None
                        ),
                        "error_policy": selected_error_policy,
                    },
                )
                st.rerun()
            st.caption(
                "Every test class must also be represented. The first training submission permanently freezes these criteria for this project. To change policy, schema or criteria, an owner creates a successor project."
            )
            if acceptance["locked"]:
                st.info(
                    "Criteria frozen; releases use the immutable version recorded in each training run."
                )

        if snapshots:
            training_snapshot = st.selectbox(
                "Training snapshot", [s["id"] for s in snapshots]
            )
            backend = st.selectbox(
                "Training backend",
                [
                    "cpu-baseline",
                    "laya-native",
                    "unsloth",
                    "vega",
                    "liquid-trl",
                    "liquid-unsloth",
                    "liquid-omni",
                    "perplexity-native",
                    *D3_BACKENDS,
                ],
                format_func=lambda value: {
                    "cpu-baseline": "CPU comparison baseline",
                    "laya-native": "Laya",
                    "unsloth": "Unsloth decision fine-tuning",
                    "vega": "Vega domain adapter",
                    "liquid-trl": "Liquid d1-3B (TRL)",
                    "liquid-unsloth": "Liquid d1-3B (Unsloth)",
                    "liquid-omni": "Liquid d1-omni-600M (native head, text/JSON)",
                    "perplexity-native": "Perplexity open v1.1 (native CUDA, experimental)",
                    **{
                        name: get_profile(name).label + " (native readout, text/JSON)"
                        for name in D3_BACKENDS
                    },
                }[value],
            )
            training_location = st.radio(
                "Training location",
                (
                    ["This computer", "Cloud GPU"]
                    if principal["role"] == "admin"
                    else ["This computer"]
                ),
                horizontal=True,
                key="training-location-"
                + project
                + (
                    "-d3"
                    if backend in D3_BACKENDS
                    else "-perplexity" if backend == "perplexity-native" else ""
                ),
            )
            if principal["role"] != "admin":
                st.caption(
                    "Ask a workspace owner to configure and approve cloud training. Your role can queue local training."
                    if principal["role"] == "editor"
                    else "Ask a workspace owner or editor to arrange model training."
                )
            if training_location == "Cloud GPU":
                st.info(
                    "Choose a model backend above, then open Train on a cloud GPU below. Local training is disabled in this mode; data/compute approval is required before provider submission."
                )
            elif backend == "cpu-baseline":
                st.info(
                    "Runs on your CPU with the platform dependencies. No GPU or model download is required. This lexical classifier is a useful baseline for your domain."
                )
            elif backend == "laya-native":
                st.info(
                    "Native Laya automatically selects a device supported by its model environment. CPU execution can be slow. Install the separate dependencies before queueing; inspect hardware above."
                )
            elif backend == "unsloth":
                st.info(
                    "This Unsloth profile needs a compatible CUDA model environment. It cannot use the CPU-only platform environment. Use a cloud GPU if your computer cannot run it."
                )
            elif backend in D3_BACKENDS:
                st.info(
                    "Adapt the selected D3 model's original answer-code readout while keeping its native backbone frozen. Use reviewed text/JSON and independent calibration data. The exported device is retained for serving; cloud training requires a compatible pinned image and an approved provider profile."
                )
                st.link_button(
                    "D3 installation, data and training guide",
                    "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/models/d3-family.md",
                )
            elif backend == "liquid-omni":
                st.info(
                    "Adapt the original omni decision head while keeping its text trunk and media encoders frozen. This profile accepts text and JSON. Cloud studies require a compatible image with the pinned checkpoint already cached. Images and audio are not supported by this adapter."
                )
                st.link_button(
                    "Omni installation, data and training guide",
                    "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/models/liquid-omni.md",
                )
            elif backend in ("liquid-trl", "liquid-unsloth"):
                st.info(
                    "Liquid d1-3B uses its separate model environment. Local training reserves the model resource and qualifies the export before registration. Cloud GPU studies require an approved compatible image; returned exports are independently measured before release."
                )
                st.link_button(
                    "Liquid installation, data and training guide",
                    "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/models/liquid.md",
                )
            else:
                st.info(
                    "Vega uses its separate pinned model environment and applicability data. Follow the Vega guide to qualify the device and required cached assets."
                )
            local_ready = True
            if training_location == "This computer":
                from decision_workbench.ui.onboarding import local_backend_available

                if capabilities is None:
                    capabilities = (
                        api("GET", "/v1/runtime-capabilities")
                        if principal["role"] == "admin"
                        else None
                    )
                    if capabilities:
                        st.session_state["runtime-capabilities"] = capabilities
                if capabilities:
                    local_ready, reason = local_backend_available(backend, capabilities)
                    if not local_ready:
                        st.warning(reason)
            config = {"snapshot": training_snapshot, "backend": backend}
            vega_ready = True
            if backend == "perplexity-native":
                from decision_workbench.ui.perplexity_native import training_options

                local_ready = (
                    training_options(
                        config,
                        spec["questions"],
                        remote=training_location == "Cloud GPU",
                    )
                    and local_ready
                )
            elif backend in D3_BACKENDS:
                from decision_workbench.ui.d3 import training_options

                local_ready = (
                    training_options(
                        config, capabilities, cloud=training_location == "Cloud GPU"
                    )
                    and local_ready
                )
            elif backend == "liquid-omni":
                from decision_workbench.ui.liquid_omni import training_options

                local_ready = (
                    training_options(
                        config, capabilities, cloud=training_location == "Cloud GPU"
                    )
                    and local_ready
                )
            elif backend in ("liquid-trl", "liquid-unsloth"):
                from decision_workbench.training.liquid_local import PROFILE

                config.update({**PROFILE, "backend": backend})
                st.caption(
                    "Pinned base: "
                    + PROFILE["model"]
                    + " · revision "
                    + PROFILE["revision"]
                )
                config["device"] = st.selectbox(
                    "Liquid compute device",
                    (
                        ["cuda"]
                        if training_location == "Cloud GPU"
                        else (
                            ["auto", "cuda"]
                            if backend == "liquid-unsloth"
                            else ["auto", "cuda", "cpu"]
                        )
                    ),
                    format_func=lambda v: {
                        "auto": (
                            "Automatic CUDA"
                            if backend == "liquid-unsloth"
                            else "Automatic: CUDA if available, otherwise CPU"
                        ),
                        "cuda": "NVIDIA CUDA GPU",
                        "cpu": "CPU (at least 20 GiB available RAM)",
                    }[v],
                )
                config["epochs"] = int(
                    st.number_input("Liquid training epochs", 1, 20, 1)
                )
                config["batch_size"] = int(
                    st.number_input("Liquid examples per micro-batch", 1, 32, 1)
                )
                config["rank"] = int(st.number_input("Liquid LoRA rank", 1, 128, 4))
                config["grad_accum"] = int(
                    st.number_input("Liquid gradient accumulation", 1, 128, 4)
                )
                config["max_length"] = int(
                    st.number_input(
                        "Liquid complete input token budget", 64, 32768, 256
                    )
                )
                maximum_steps = int(
                    st.number_input(
                        "Liquid maximum optimizer attempts (0 uses all epochs)",
                        0,
                        100000,
                        0,
                    )
                )
                if maximum_steps:
                    config["max_steps"] = maximum_steps
                checkpoint_download = st.checkbox(
                    "Allow downloading the pinned public Liquid checkpoint (about 6.25 GB)",
                    value=False,
                    disabled=training_location == "Cloud GPU",
                )
                config["download"] = (
                    checkpoint_download
                    if training_location == "This computer"
                    else False
                )
                st.caption(
                    "This downloads model assets only; your training records stay local. Training/validation need one-hot labels. Reserve at least eight real calibration records. A small compatibility run does not establish domain quality."
                )
                if capabilities:
                    hardware = (
                        capabilities.get("model_profiles", {})
                        .get(backend, {})
                        .get("training", {})
                    )
                    if (
                        config["device"] == "cpu"
                        and (hardware.get("ram_available_gib") or 0) < 20
                    ):
                        local_ready = False
                        st.warning(
                            "CPU d1-3B training needs at least 20 GiB available RAM. Choose CUDA, another installed model or the CPU comparison baseline."
                        )
                    elif config["device"] == "cuda" and not hardware.get("cuda"):
                        local_ready = False
                        st.warning(
                            "CUDA is unavailable in the configured Liquid environment."
                        )
            elif backend == "vega":
                from decision_workbench.training.vega_local import PROFILE

                gate_versions = api(
                    "GET", f"/v1/projects/{project}/entities/gate-snapshot?limit=200"
                )
                paired_gates = [
                    g["id"]
                    for g in gate_versions
                    if g["payload"]["snapshot"] == training_snapshot
                ]
                vega_ready = (
                    bool(paired_gates)
                    and acceptance["policy"] is not None
                    and "vega" in acceptance["policy"]["payload"]["criteria"]
                )
                if paired_gates:
                    config["gate_snapshot"] = st.selectbox(
                        "Paired applicability snapshot", paired_gates
                    )
                else:
                    st.warning(
                        "Reserve reviewed applicability negatives for this positive snapshot in the Applicability tab."
                    )
                if (
                    acceptance["policy"] is None
                    or "vega" not in acceptance["policy"]["payload"]["criteria"]
                ):
                    st.warning(
                        "The owner must enable Vega applicability criteria before the first training run."
                    )
                config.update(PROFILE)
                st.caption(
                    "Pinned Vega 0.8B engine and Qwen 0.8B backbone. Use the separate Vega environment locally or a compatible Vega image on your cloud GPU."
                )
                config["epochs"] = int(st.number_input("Vega epochs", 1, 100, 2))
                config["batch_size"] = int(st.number_input("Vega batch size", 1, 64, 8))
                config["rank"] = int(st.number_input("Vega adapter rank", 1, 64, 8))
                config["max_steps"] = int(
                    st.number_input(
                        "Vega maximum optimizer steps (0 uses all epochs)", 0, 100000, 0
                    )
                )
                config["learning_rate"] = st.number_input(
                    "Vega learning rate", 0.0000001, 0.1, 0.0003, format="%.7f"
                )
                config["gate_weight"] = st.number_input(
                    "Vega applicability loss weight", 0.01, 100.0, 1.0
                )
                config["offline"] = st.checkbox(
                    "Use only cached pinned Vega/backbone assets",
                    value=True,
                    help="Keep enabled for a pre-populated cache. Disable only when the runtime and provider network allow downloading pinned assets; downloads consume time and storage. Network-restricted jobs need a complete cache.",
                )
                st.info(
                    "Training requires sixty independent real calibration families, complete negative coverage in every role and the owner's negative test-count floor. Release checks both ordinary quality and applicability limits. Model abstention always reaches the caller."
                )
            elif backend != "cpu-baseline":
                profile_key = project + "-" + backend
                st.caption(
                    "The default encoder and revision passed the documented local native/Unsloth acceptance profile. Other models need their own hardware and export qualification."
                )
                config["model"] = st.text_input(
                    "Base Hub model",
                    "convaiinnovations/laya",
                    key="base-model-" + profile_key,
                )
                config["revision"] = st.text_input(
                    "Pinned 40-character Hub revision",
                    "7b928d828b7b0e022f929d9bd2e44165aa270148",
                    key="base-revision-" + profile_key,
                )
                config["epochs"] = int(st.number_input("Epochs", 1, 20, 1))
                config["batch_size"] = int(
                    st.number_input("Micro-batch size", 1, 32, 2)
                )
                config["grad_accum"] = int(
                    st.number_input("Gradient accumulation", 1, 128, 4)
                )
                config["max_length"] = int(
                    st.number_input("Input token budget", 64, 16384, 256)
                )
                if backend == "unsloth":
                    config["rank"] = int(st.number_input("LoRA rank", 1, 128, 8))
                    maximum_steps = int(
                        st.number_input(
                            "Maximum optimizer steps (0 uses all epochs)", 0, 100000, 0
                        )
                    )
                    if maximum_steps:
                        config["max_steps"] = maximum_steps
                st.info(
                    "Requires the isolated model worker. Release requires successful calibration, exported-model reload and the frozen acceptance criteria. Training temporarily reserves the local GPU."
                )
            else:
                st.caption(
                    "Actual lexical classifier trained on your fixed schema. Use as a comparison baseline; it does not fine-tune Laya or an LLM."
                )
            if st.button(
                "Queue training",
                disabled=acceptance["policy"] is None
                or not vega_ready
                or training_location != "This computer"
                or not local_ready
                or principal["role"] not in ("admin", "editor"),
            ):
                st.json(submit(project, "train", config))
        if snapshots and principal["role"] == "admin":
            with st.expander("Evaluate a hosted decision model"):
                render_hint("hosted")
                profiles = api("GET", f"/v1/projects/{project}/decision-profiles")
                if not profiles:
                    st.info(
                        "Ask your operator to configure an approved decision provider for this workspace."
                    )
                else:
                    chosen = st.selectbox(
                        "Approved decision provider profile",
                        [p["id"] for p in profiles],
                    )
                    selected_profile = next(p for p in profiles if p["id"] == chosen)
                    st.json(selected_profile)
                    if selected_profile["provider"] == "perplexity":
                        st.info(
                            "Perplexity evaluates all questions together. Keep the same question group when evaluating and serving. Flash is more sensitive to grouping; start with one question per project unless a related group is validated. The returned model name echoes your request and does not pin remote weights."
                        )
                        st.link_button(
                            "Configure Perplexity Decider and your API key",
                            "https://github.com/BatuhanYilmaz26/decision-model-workbench/blob/main/docs/models/perplexity.md",
                        )
                    st.write(
                        "This evaluates provider answers and fits local calibration; it does not train the provider's weights. Calibration, validation and test states are sent to the provider. Calls may incur charges."
                    )
                    hosted_approved = st.checkbox(
                        "This provider is approved to receive study data and future inference states after owner release",
                        key="hosted-approved-" + project,
                    )
                    if st.button(
                        "Queue hosted evaluation",
                        disabled=not hosted_approved or acceptance["policy"] is None,
                    ):
                        st.json(
                            submit(
                                project,
                                "hosted-evaluate",
                                {
                                    "snapshot": training_snapshot,
                                    "profile_id": chosen,
                                    "data_approved": True,
                                },
                            )
                        )
                    if st.checkbox(
                        "Show provider call history", key="hosted-history-" + project
                    ):
                        history = api("GET", f"/v1/projects/{project}/provider-calls")
                        st.json(history)
                        unresolved = {c["id"]: c for c in history if c["capacity_held"]}
                        if unresolved:
                            st.warning(
                                "Uncertain calls retain capacity. Verify local transport termination and provider settlement before releasing it. This action sends no provider request and never retries the original key."
                            )
                            with st.form("settle-provider-call-" + project):
                                call_id = st.selectbox(
                                    "Uncertain provider call", list(unresolved)
                                )
                                reference = st.text_input(
                                    "Provider settlement or operator evidence reference",
                                    max_chars=500,
                                )
                                note = st.text_input(
                                    "Call reconciliation note", max_chars=1000
                                )
                                local_stopped = st.checkbox(
                                    "I verified that the local transport is stopped"
                                )
                                upstream_finished = st.checkbox(
                                    "I verified that the provider finished this request"
                                )
                                settle = st.form_submit_button(
                                    "Record provider settlement"
                                )
                            if settle:
                                api(
                                    "POST",
                                    f"/v1/projects/{project}/provider-calls/{call_id}/reconcile",
                                    json={
                                        "expected_outcome_sha256": unresolved[call_id][
                                            "outcome_sha256"
                                        ],
                                        "local_transport_stopped": local_stopped,
                                        "provider_finished": upstream_finished,
                                        "reference": reference,
                                        "note": note,
                                    },
                                )
                                st.success(
                                    "Settlement recorded. Capacity released; the original request will not be replayed."
                                )
                                st.rerun()
        if snapshots and principal["role"] == "admin":
            with st.expander("Train on a cloud GPU"):
                render_hint("cloud")
                st.write(
                    "Prepare an immutable study using the model settings above, then follow the provider guide. This action allocates no paid resources."
                )
                if backend == "cpu-baseline":
                    st.info(
                        "Select a compatible native model and cloud image for remote GPU training."
                    )
                else:
                    if backend == "vega":
                        st.info(
                            "Vega prepares eight paired data files. Managed launch needs an operator image profile declaring the vega backend. Use the Vega cloud guide for image/cache setup and independent admission."
                        )
                    approved = st.checkbox(
                        "The selected provider is approved to receive this study's domain data",
                        key="remote-approved-" + project,
                    )
                    with st.form("remote-study-" + project):
                        provider = st.selectbox(
                            "Cloud training provider",
                            ["aws", "azure", "gcp", "modal", "runpod"],
                        )
                        region = st.text_input(
                            "Approved provider region or environment"
                        )
                        gpu = st.text_input("Approved GPU or instance identity")
                        image = st.text_input("Qualified training image digest")
                        prepare_remote = st.form_submit_button(
                            "Prepare remote training study",
                            disabled=not approved
                            or training_location != "Cloud GPU"
                            or acceptance["policy"] is None
                            or not vega_ready,
                        )
                    if prepare_remote:
                        st.json(
                            submit(
                                project,
                                "remote-study",
                                {
                                    **config,
                                    "provider": provider,
                                    "region": region,
                                    "gpu": gpu,
                                    "image_digest": image,
                                },
                            )
                        )
                studies = api("GET", f"/v1/projects/{project}/entities/remote-study")
                for study in studies:
                    if st.button(
                        "Retrieve remote inputs " + study["id"][:10],
                        key="retrieve-study-" + study["id"],
                    ):
                        payload = api(
                            "GET",
                            f"/v1/projects/{project}/remote-studies/{study['id']}/download",
                            binary=True,
                        )
                        st.download_button(
                            "Download frozen study",
                            payload,
                            file_name="remote-study.zip",
                            mime="application/zip",
                            key="download-study-" + study["id"],
                        )
                cloud_profiles = api("GET", "/v1/cloud-profiles")
                if cloud_profiles and studies:
                    with st.form("cloud-approval-" + project):
                        cloud_study = st.selectbox(
                            "Frozen study", [x["id"] for x in studies]
                        )
                        cloud_profile = st.selectbox(
                            "Managed training profile",
                            [x["id"] for x in cloud_profiles],
                        )
                        selected_profile = next(
                            x for x in cloud_profiles if x["id"] == cloud_profile
                        )
                        st.json(selected_profile)
                        if selected_profile["provider"] == "runpod":
                            st.info(
                                "RunPod's runtime limit bounds the trainer process group. You must separately terminate the dedicated Pod and observe shutdown; GPU and retained storage charges require provider verification. Stop permanently terminates this Pod while retaining its network volume."
                            )
                        if selected_profile["provider"] == "modal":
                            st.info(
                                "Modal runs one offline GPU Sandbox with a provider lifetime limit. Stop terminates its execution and retains the Volume. Modal managed Volumes and application logs are stored in the United States. Region selects compute placement; image provenance and billing require operator qualification. Unknown creation is never resubmitted."
                            )
                        cloud_data = st.checkbox(
                            "I approve transferring this study to the selected Modal workspace and its US-based managed Volume"
                            if selected_profile["provider"] == "modal"
                            else "I approve transferring this study to the selected cloud account and region"
                        )
                        cloud_compute = st.checkbox(
                            "I approve paid training within this profile's instance and runtime limits"
                            if selected_profile["provider"] != "runpod"
                            else "I approve paid RunPod compute and will verify Pod termination and retained storage costs"
                        )
                        approve_cloud = st.form_submit_button("Approve cloud run")
                    if approve_cloud:
                        st.json(
                            api(
                                "POST",
                                f"/v1/projects/{project}/cloud-runs",
                                json={
                                    "study": cloud_study,
                                    "profile_id": cloud_profile,
                                    "data_approved": cloud_data,
                                    "compute_approved": cloud_compute,
                                    "request_key": "cloud-approve-"
                                    + cloud_study
                                    + "-"
                                    + cloud_profile,
                                },
                            )
                        )
                for run in api("GET", f"/v1/projects/{project}/cloud-runs"):
                    state = run["state"]
                    with st.expander(
                        "Cloud run " + run["id"][:10] + " · " + state["phase"]
                    ):
                        st.json(state)
                        actions = []
                        if not state["upload_attempted"] and state["phase"] == "ready":
                            actions.append(("submit", "Submit approved paid training"))
                        if (
                            not state["create_attempted"]
                            and state["phase"] != "abandoned"
                        ):
                            actions.append(
                                ("abandon", "Abandon before compute submission")
                            )
                        if state["create_attempted"]:
                            actions.append(("observe", "Observe the same provider job"))
                            if state["phase"] not in ("Completed", "Failed", "Stopped"):
                                actions.append(
                                    (
                                        "stop",
                                        (
                                            "Terminate RunPod compute (network volume retained)"
                                            if run["payload"]["profile"]["provider"]
                                            == "runpod"
                                            else (
                                                "Terminate Modal Sandbox (Volume retained)"
                                                if run["payload"]["profile"]["provider"]
                                                == "modal"
                                                else "Request provider stop"
                                            )
                                        ),
                                    )
                                )
                            if state["phase"] == "Completed" and not state.get(
                                "admission_job"
                            ):
                                actions.append(
                                    (
                                        "retrieve",
                                        "Retrieve and queue independent admission",
                                    )
                                )
                        for action, label in actions:
                            if st.button(
                                label, key="cloud-" + run["id"] + "-" + action
                            ):
                                st.json(
                                    api(
                                        "POST",
                                        f"/v1/projects/{project}/cloud-runs/{run['id']}/actions",
                                        json={
                                            "action": action,
                                            "revision": state["revision"],
                                            "request_key": f"cloud-{action}-"
                                            + uuid.uuid4().hex,
                                        },
                                    )
                                )
                st.caption(
                    "Managed controls currently support AWS, Azure, GCP, Modal and RunPod. Other providers use the five-provider guides and operator import-remote. Cancelling a local control job does not stop cloud compute: request provider stop and observe its terminal status. Retrieved models remain inactive until independent admission and owner release. RunPod stop permanently terminates its dedicated Pod; the approved network volume remains. Trainer exit does not establish billing shutdown. Storage/image/log costs continue after training."
                )
        if principal["role"] in ("admin", "editor"):
            with st.expander("Workspace health and alerts"):
                render_hint("health")
                health = api("GET", f"/v1/projects/{project}/operations")
                backup_health = health["collector"].get("backup")
                if backup_health:
                    if backup_health["configured"] and not backup_health["healthy"]:
                        st.warning(backup_health["message"])
                    else:
                        st.caption(backup_health["message"])
                if health["collector"]["status"] != "healthy":
                    st.error(
                        "Operational monitoring is unavailable. Ask your operator to inspect the service."
                    )
                if health["worker_alive"]:
                    st.caption(
                        "Workspace worker heartbeat is fresh. GPU capability and available capacity are checked separately."
                    )
                else:
                    st.warning(
                        "No fresh workspace worker heartbeat. Queued work needs a running worker."
                    )
                st.write(
                    {
                        "jobs": health["job_counts"],
                        "oldest queue seconds": round(
                            health["oldest_queue_seconds"], 1
                        ),
                        "alerts": health["incident_counts"],
                    }
                )
                for incident in health["incidents"]:
                    if (
                        incident["status"] == "open"
                        and incident["acknowledged_at"] is None
                    ):
                        if incident["severity"] == "error":
                            st.error(incident["message"])
                        else:
                            st.warning(incident["message"])
                    st.caption(
                        f"{incident['kind']} · {incident['status']} · occurrence {incident['occurrences']} · {incident['id'][:10]}"
                    )
                    if incident["acknowledged_by"]:
                        st.caption("Acknowledged by " + incident["acknowledged_by"])
                    if st.checkbox(
                        "Show alert history", key="incident-history-" + incident["id"]
                    ):
                        st.dataframe(
                            api(
                                "GET",
                                f"/v1/projects/{project}/operations/incidents/{incident['id']}/history",
                            ),
                            hide_index=True,
                        )
                    if principal["role"] == "admin" and incident["status"] == "open":
                        with st.form(
                            "incident-action-"
                            + incident["id"]
                            + "-"
                            + str(incident["revision"])
                        ):
                            choices = (
                                ["acknowledge"]
                                if incident["kind"]
                                in (
                                    "worker_unavailable",
                                    "queue_delayed",
                                    "job_lease_expired",
                                )
                                else ["acknowledge", "resolve"]
                            )
                            action = st.selectbox(
                                "Alert action",
                                choices,
                                key="incident-action-choice-" + incident["id"],
                            )
                            note = st.text_input(
                                "Action note",
                                key="incident-action-note-" + incident["id"],
                            )
                            if st.form_submit_button("Save alert action"):
                                api(
                                    "POST",
                                    f"/v1/projects/{project}/operations/incidents/{incident['id']}/actions",
                                    json={
                                        "revision": incident["revision"],
                                        "action": action,
                                        "note": note,
                                    },
                                )
                                st.rerun()
                if health["incident_counts"]["total"] > health["limit"]:
                    st.caption(
                        "Showing the first alert page. Older history is available through the paginated operations API."
                    )
        jobs = api("GET", f"/v1/projects/{project}/jobs")
        render_hint("jobs")
        st.dataframe(
            [
                {
                    "id": j["id"],
                    "kind": j["kind"],
                    "status": j["status"],
                    "progress": j["progress"],
                    "error": j["error"],
                }
                for j in jobs
            ],
            hide_index=True,
        )
        for job in jobs:
            if job["result"]:
                st.json({"job": job["id"], "result": job["result"]}, expanded=False)
            if job["status"] in ("queued", "running") and principal["role"] in (
                "admin",
                "editor",
            ):
                if st.button("Cancel " + job["id"][:10], key=job["id"]):
                    api("POST", f"/v1/projects/{project}/jobs/{job['id']}/cancel")
                    st.rerun()
    with tabs[3]:
        render_hint("models")
        active = api("GET", f"/v1/projects/{project}/active")
        st.caption(
            f"Active version: {active['model_id'] or 'None'} · release revision {active['revision']}"
        )
        if models:
            selected_model = st.selectbox(
                "Evaluated model version", [m["id"] for m in models]
            )
            model = next(m for m in models if m["id"] == selected_model)
            report = model["payload"]["report"]
            st.subheader("Held-out test results")
            st.caption(
                f"Backend: {report['backend']} · "
                + " · ".join(
                    f"{name}: {count}"
                    for name, count in report.get("counts", {}).items()
                )
            )
            if report["backend"] == "hosted":
                st.info(
                    "Releasing this version sends future inference states to its approved external provider. The local artifact contains the evaluated schema and calibration policy, not provider weights."
                )
            st.dataframe(
                [
                    {
                        "Question": name,
                        "Test examples": result["count"],
                        "Accuracy": result["accuracy"],
                        "Macro F1": result["macro_f1"],
                        "Calibration error": result["ece_10_equal_width_bins"],
                    }
                    for name, result in report["test"].items()
                ],
                hide_index=True,
            )
            st.caption(
                "Accuracy and macro F1 are higher-is-better; calibration error is lower-is-better. These results apply only to this reserved test set."
            )
            if report["backend"] == "vega":
                from decision_workbench.ui.vega import render_applicability

                render_applicability(
                    report, model, api("GET", f"/v1/projects/{project}/release-policy")
                )
            with st.expander("Confusion matrices and class coverage"):
                for name, result in report["test"].items():
                    st.write(
                        name + " — rows are actual labels; columns are predicted labels"
                    )
                    st.dataframe(
                        [
                            {
                                "Actual label": actual,
                                **dict(zip(result["labels"], values)),
                            }
                            for actual, values in zip(
                                result["labels"], result["confusion"]
                            )
                        ],
                        hide_index=True,
                    )
                    st.write("Test examples per outcome", result["test_class_counts"])
            with st.expander("Full evaluation, provenance and export report"):
                st.json(report)
            from decision_workbench.ui.error_policy import render_report

            render_report(report)
            for limitation in report.get("limitations", []):
                st.caption(limitation)
            if model["payload"].get("data_gate_counts"):
                with st.expander("Training and calibration outcome counts"):
                    st.json(model["payload"]["data_gate_counts"])
            st.caption(
                "Release eligibility uses the owner criteria frozen before training or hosted evaluation. Thresholds cannot be changed here."
            )
            release_note = st.text_input("Release or rollback reason")
            if st.button(
                "Activate selected version", disabled=principal["role"] != "admin"
            ):
                st.json(
                    api(
                        "POST",
                        f"/v1/projects/{project}/releases",
                        json={
                            "model_id": selected_model,
                            "expected_revision": active["revision"],
                            "note": release_note,
                        },
                    )
                )
            st.caption(
                "Reactivating an earlier eligible version is a rollback. The server verifies the evaluated policy and its local runtime or hosted provider profile before release."
            )
    with tabs[4]:
        render_hint("decisions")
        from decision_workbench.ui.prediction_input import (
            evidence_input,
            parsed_evidence,
            release_guard,
        )

        evidence_mode, evidence_body = evidence_input(
            "Evidence for the released model", "decision-evidence-" + project
        )
        threshold = st.slider("Abstain below confidence", 0.0, 1.0, 0.8)
        expected_release, prediction_ready = release_guard(
            api, project, spec["schema_sha256"]
        )
        if st.button("Make decision", disabled=not prediction_ready):
            st.json(
                api(
                    "POST",
                    f"/v1/projects/{project}/decisions",
                    json={
                        "state": parsed_evidence(evidence_mode, evidence_body),
                        "threshold": threshold,
                        "expected_release": expected_release,
                    },
                )
            )
        st.code(f"POST /v1/projects/{project}/decisions")
        if principal["role"] == "admin":
            st.dataframe(api("GET", f"/v1/projects/{project}/audit"), hide_index=True)
            from decision_workbench.ui.audit_exports import render_audit_exports

            render_audit_exports(api, project)
except (ValueError, KeyError, httpx.HTTPError) as exc:
    st.error(str(exc))
finally:
    if _api_client is not None:
        _api_client.close()
