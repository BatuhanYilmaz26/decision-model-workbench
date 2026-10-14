"""User-facing applicability intake, review and reservation; no invented gold."""

import csv
import hashlib
import io
import json
import streamlit as st


def render_gates(api, submit, project, spec, principal, snapshots):
    st.subheader("Applicability evidence")
    st.write(
        "Collect real examples where a domain question should not be applied. These records train Vega's applicability gate; they carry no business decision labels. All new examples require review."
    )
    st.caption(
        "Reserve these negatives against a positive snapshot, then select Vega in the Data & training tab. The owner must set applicability acceptance criteria before the first training submission. An eight-file download is also available for standalone studies."
    )
    versions = api("GET", f"/v1/projects/{project}/entities/gate-dataset?limit=200")
    roots = {}
    for version in versions:
        roots.setdefault(version["payload"]["root"], version)
    chosen = st.selectbox(
        "Applicability collection",
        [None] + list(roots),
        format_func=lambda value: (
            "Create a new collection" if value is None else value[:12]
        ),
        key="gate-collection-" + project,
    )
    target, page = None, None
    if chosen:
        page = api(
            "GET",
            f"/v1/projects/{project}/gate-datasets/{roots[chosen]['id']}/rows?limit=1",
        )
        target = page["dataset"]
        if page["head"] != target["id"]:
            page = api(
                "GET",
                f"/v1/projects/{project}/gate-datasets/{page['head']}/rows?limit=1",
            )
            target = page["dataset"]
    can_edit = principal["role"] in ("admin", "editor")
    with st.form("gate-example-" + project):
        state = st.text_area(
            "Inapplicable source evidence", key="gate-state-" + project
        )
        family = st.text_input(
            "Applicability source family", key="gate-family-" + project
        )
        question_ids = st.multiselect(
            "Questions that should not apply",
            list(spec["questions"]),
            default=list(spec["questions"]),
            key="gate-questions-" + project,
        )
        reason = st.text_area(
            "Why these questions are inapplicable", key="gate-reason-" + project
        )
        observed = st.checkbox(
            "This is observed source evidence, not a synthetic or hypothetical example",
            key="gate-real-" + project,
        )
        entered = st.form_submit_button(
            "Save applicability example", disabled=not can_edit
        )
    if entered:
        if not observed or not family.strip():
            raise ValueError(
                "Confirm real source evidence and provide its original source family"
            )
        example = {
            "group_id": family,
            "state": state,
            "negative_for": question_ids,
            "reason": reason,
        }
        path = (
            f"/v1/projects/{project}/gate-datasets/{target['id']}/examples"
            if target
            else f"/v1/projects/{project}/gate-examples"
        )
        body = {"examples": [example]} | (
            {"expected_hash": target["payload"]["sha256"]} if target else {}
        )
        saved = api("POST", path, json=body)
        st.session_state["gate-next-root-" + project] = saved["payload"]["root"]
        st.rerun()
    template = io.StringIO()
    csv.writer(template).writerows(
        [
            ["id", "group_id", "state", "negative_for", "reason"],
            [
                "replace-with-real-id",
                "original-source-family",
                "Replace with observed inapplicable evidence",
                json.dumps(list(spec["questions"])),
                "Explain inapplicability",
            ],
        ]
    )
    st.download_button(
        "Download applicability CSV format",
        template.getvalue(),
        "applicability-examples.csv",
        "text/csv",
    )
    uploaded = st.file_uploader(
        "Upload applicability examples",
        type=["csv", "tsv", "xlsx", "json", "jsonl"],
        max_upload_size=10,
        key="gate-upload-" + project,
    )
    mapping, ready = None, uploaded is not None
    if uploaded and uploaded.name.lower().endswith((".csv", ".tsv", ".xlsx")):
        content = uploaded.getvalue()
        token = project + hashlib.sha256(content).hexdigest()
        initial = api(
            "POST",
            f"/v1/projects/{project}/imports/inspect",
            files={"file": (uploaded.name, content)},
        )
        worksheet = initial["worksheet"]
        if initial["worksheets"]:
            worksheet = st.selectbox(
                "Applicability worksheet",
                initial["worksheets"],
                key="gate-sheet-" + token,
            )
            if worksheet != initial["worksheet"]:
                initial = api(
                    "POST",
                    f"/v1/projects/{project}/imports/inspect",
                    files={"file": (uploaded.name, content)},
                    data={"worksheet": worksheet},
                )
        if initial.get("worksheet_error") or not initial["columns"]:
            st.warning(
                "Select a worksheet with applicability records: "
                + initial.get("worksheet_error", "No columns")
            )
            ready = False
        else:
            st.dataframe(initial["preview"], hide_index=True)
            mapping = {"worksheet": worksheet}
            for field, canonical, label in (
                ("id_column", "id", "Applicability ID column"),
                ("group_column", "group_id", "Applicability family column"),
                ("state_column", "state", "Applicability evidence column"),
                (
                    "questions_column",
                    "negative_for",
                    "Inapplicable question IDs column",
                ),
                ("reason_column", "reason", "Inapplicability reason column"),
            ):
                columns = initial["columns"]
                mapping[field] = st.selectbox(
                    label,
                    columns,
                    index=columns.index(canonical) if canonical in columns else 0,
                    key="gate-map-" + field + token + str(worksheet),
                )
            metadata = st.selectbox(
                "Optional applicability metadata column (JSON)",
                [None] + initial["columns"],
                key="gate-meta-" + token + str(worksheet),
            )
            if metadata:
                mapping["metadata_column"] = metadata
            st.caption(
                "The question column contains a JSON array of exact question IDs. Optional metadata preserves source timestamps and known relatives. Every uploaded approval flag is reset."
            )
    if st.button(
        "Import applicability file",
        disabled=not ready or not can_edit,
        key="gate-import-" + project,
    ):
        body = {"mapping": json.dumps(mapping)} if mapping else {}
        if target:
            body.update(dataset=target["id"], expected_hash=target["payload"]["sha256"])
        saved = api(
            "POST",
            f"/v1/projects/{project}/gate-datasets",
            files={"file": (uploaded.name, uploaded.getvalue())},
            data=body,
        )
        st.session_state["gate-next-root-" + project] = saved["payload"]["root"]
        st.rerun()
    if target:
        page_number = st.number_input(
            "Applicability record page",
            min_value=0,
            max_value=max(0, (target["payload"]["row_count"] - 1) // 50),
            value=0,
            step=1,
            key="gate-page-" + target["id"],
        )
        page = api(
            "GET",
            f"/v1/projects/{project}/gate-datasets/{target['id']}/rows?limit=50&offset={int(page_number)*50}",
        )
        st.caption(
            f"Approved: {target['payload']['reviewed_count']} · Rejected: {target['payload'].get('rejected_count', 0)} · Total: {page['total']}"
        )
        st.dataframe(
            [
                {
                    key: row[key]
                    for key in ("id", "group_id", "state", "negative_for", "reason")
                }
                | {"review_status": row["metadata"]["review_status"]}
                for row in page["rows"]
            ],
            hide_index=True,
        )
        selected = st.selectbox(
            "Applicability record to review",
            [row["id"] for row in page["rows"]],
            key="gate-record-" + target["id"],
        )
        row = next(row for row in page["rows"] if row["id"] == selected)
        with st.form("gate-review-" + target["id"] + selected):
            negative_for = st.multiselect(
                "Reviewed inapplicable questions",
                list(spec["questions"]),
                default=row["negative_for"],
            )
            reason = st.text_area(
                "Reviewed inapplicability explanation", value=row["reason"]
            )
            status = st.selectbox(
                "Applicability review decision",
                ["pending", "approved", "rejected"],
                index=["pending", "approved", "rejected"].index(
                    row["metadata"]["review_status"]
                ),
            )
            note = st.text_area("Applicability review note")
            reviewed = st.form_submit_button(
                "Record applicability review",
                disabled=principal["role"] not in ("admin", "reviewer"),
            )
        if reviewed:
            api(
                "POST",
                f"/v1/projects/{project}/gate-datasets/{target['id']}/reviews",
                json={
                    "record_id": selected,
                    "negative_for": negative_for,
                    "reason": reason,
                    "status": status,
                    "expected_hash": target["payload"]["sha256"],
                    "note": note,
                },
            )
            st.rerun()
        exported = api(
            "GET",
            f"/v1/projects/{project}/gate-datasets/{target['id']}/download",
            binary=True,
        )
        st.download_button(
            "Download current applicability revision",
            exported,
            "applicability-examples.jsonl",
            "application/x-ndjson",
        )
    st.subheader("Reserve applicability roles")
    positive = st.selectbox(
        "Frozen positive snapshot for applicability",
        [None] + [item["id"] for item in snapshots],
        format_func=lambda value: (
            value[:12] if value else "Select a reserved decision dataset"
        ),
        key="gate-positive-" + project,
    )
    selected_datasets = st.multiselect(
        "Reviewed applicability versions to reserve",
        [item["id"] for item in versions],
        key="gate-reserve-datasets-" + project,
    )
    st.caption(
        "Choose one revision per collection. Pending examples block reservation; rejected examples are excluded. Reviewed relatives follow immutable positive roles. A relationship joining different frozen positive roles refuses the study."
    )
    from .semantic import render_semantic

    semantic_audit, semantic_ready = render_semantic(
        api, submit, project, principal, selected_datasets, positive, applicability=True
    )
    if st.button(
        "Reserve applicability train / validation / calibration / test",
        disabled=not positive
        or not selected_datasets
        or not can_edit
        or not semantic_ready,
        key="gate-reserve-" + project,
    ):
        st.json(
            submit(
                project,
                "gate-prepare",
                {
                    "snapshot": positive,
                    "datasets": selected_datasets,
                    **({"semantic_audit": semantic_audit} if semantic_audit else {}),
                },
            )
        )
    reservations = api("GET", f"/v1/projects/{project}/entities/gate-snapshot")
    if reservations:
        st.dataframe(
            [
                {
                    "id": item["id"],
                    "positive_snapshot": item["payload"]["snapshot"],
                    **item["payload"]["counts"],
                }
                for item in reservations
            ],
            hide_index=True,
        )
        selected = st.selectbox(
            "Applicability reservation to export",
            [item["id"] for item in reservations],
            key="gate-export-" + project,
        )
        if st.button(
            "Prepare eight-file Vega study download", key="gate-archive-" + project
        ):
            st.session_state["gate-archive-content-" + selected] = api(
                "GET",
                f"/v1/projects/{project}/gate-snapshots/{selected}/download",
                binary=True,
            )
        content = st.session_state.get("gate-archive-content-" + selected)
        if content:
            st.download_button(
                "Download reserved Vega study inputs",
                content,
                "vega-study-inputs.zip",
                "application/zip",
                key="gate-archive-download-" + selected,
            )
