"""Guided example entry and table mapping. The server owns validation and persistence."""

import hashlib
import json
import streamlit as st
from decision_workbench.data import options
from decision_workbench.json_tools import strict_json


def batch_examples(entered, questions):
    """Keep blank placeholders, incomplete rows and the batch limit unambiguous."""
    examples = []
    for number, row in enumerate(entered, 1):
        evidence = row.get("Input evidence")
        if evidence is not None and not isinstance(evidence, str):
            raise ValueError(f"Example row {number}: input evidence must be text")
        if not (evidence or "").strip():
            other = (
                "Example ID",
                "Source family",
                *("Outcome: " + name for name in questions),
            )
            if any(
                row.get(field) is not None and str(row[field]).strip()
                for field in other
            ) or row.get("Source") not in (None, "", "synthetic"):
                raise ValueError(
                    f"Example row {number}: add input evidence or clear the entire row"
                )
            continue
        examples.append(
            {
                "state": evidence,
                "id": row.get("Example ID") or None,
                "group_id": row.get("Source family") or None,
                "origin": row.get("Source") or "synthetic",
                "labels": {
                    name: row["Outcome: " + name]
                    for name in questions
                    if row.get("Outcome: " + name) not in (None, "")
                },
            }
        )
    if not 1 <= len(examples) <= 50:
        raise ValueError("Enter 1–50 examples with input evidence before saving")
    return examples


def collection_target(api, project, datasets, key):
    roots = {}
    for item in datasets:
        roots.setdefault(item["payload"]["root"], item)
    selected = st.selectbox(
        "Collection for " + key,
        [None] + sorted(roots),
        format_func=lambda value: (
            "Create a new collection" if value is None else f"Collection {value[:10]}"
        ),
        key="collection-" + key + "-" + project,
    )
    if selected is None:
        return None
    listed = roots[selected]
    page = api("GET", f"/v1/projects/{project}/datasets/{listed['id']}/rows?limit=1")
    if page["head"] != listed["id"]:
        page = api(
            "GET", f"/v1/projects/{project}/datasets/{page['head']}/rows?limit=1"
        )
    st.caption(
        f"Append to current version {page['dataset']['id'][:10]}; existing examples and historical versions remain intact."
    )
    return page["dataset"]


def render_examples(api, project, spec, principal, datasets):
    with st.expander("Enter a few examples without a file"):
        st.write(
            "Describe the evidence the model will receive. Labels can be left unknown and added during review."
        )
        target = collection_target(api, project, datasets, "new examples")
        batch = (
            st.radio(
                "Example entry", ["One example", "A batch of examples"], horizontal=True
            )
            == "A batch of examples"
        )
        generation_key = "example-generation-" + project
        generation = st.session_state.get(generation_key, 0)
        with st.form("examples-" + project + "-" + str(generation)):
            if batch:
                st.caption(
                    "Enter up to 50 examples. Entirely empty rows are ignored; rows with IDs, outcomes or an operational source need input evidence. Outcomes may be unknown. Source defaults to hypothetical/synthetic."
                )
                empty = {
                    "Input evidence": "",
                    "Example ID": "",
                    "Source family": "",
                    "Source": "synthetic",
                    **{"Outcome: " + name: None for name in spec["questions"]},
                }
                entered = st.data_editor(
                    [dict(empty)],
                    num_rows="dynamic",
                    hide_index=True,
                    column_config={
                        "Source": st.column_config.SelectboxColumn(
                            options=["synthetic", "real"],
                            required=True,
                            default="synthetic",
                        ),
                        **{
                            "Outcome: "
                            + name: st.column_config.SelectboxColumn(
                                options=options(question)
                            )
                            for name, question in spec["questions"].items()
                        },
                    },
                    key="batch-examples-" + project + "-" + str(generation),
                )
            else:
                evidence = st.text_area("Example input evidence")
                structured = st.checkbox("This evidence is JSON")
                identifier = st.text_input("Example identifier (optional)")
                group = st.text_input(
                    "Source family or customer/document identifier (optional)"
                )
                origin = st.selectbox(
                    "Example source",
                    ["synthetic", "real"],
                    format_func=lambda v: (
                        "Hypothetical or synthetic example"
                        if v == "synthetic"
                        else "Observed operational record"
                    ),
                )
                labels = {}
                for name, question in spec["questions"].items():
                    selected = st.selectbox(
                        "Example outcome: " + name,
                        [None] + options(question),
                        format_func=lambda v: (
                            "Unknown — label during review" if v is None else v
                        ),
                        key="example-label-"
                        + project
                        + "-"
                        + str(generation)
                        + "-"
                        + name,
                    )
                    if selected is not None:
                        labels[name] = selected
            saved = st.form_submit_button(
                "Save batch for review" if batch else "Save example for review",
                disabled=principal["role"] not in ("admin", "editor"),
            )
        if saved:
            if batch:
                examples = batch_examples(entered, spec["questions"])
            else:
                example = {
                    "state": strict_json(evidence) if structured else evidence,
                    "labels": labels,
                    "origin": origin,
                }
                if identifier.strip():
                    example["id"] = identifier.strip()
                if group.strip():
                    example["group_id"] = group.strip()
                examples = [example]
            path = (
                f"/v1/projects/{project}/examples"
                if target is None
                else f"/v1/projects/{project}/datasets/{target['id']}/examples"
            )
            body = {"examples": examples}
            if target:
                body["expected_hash"] = target["payload"]["sha256"]
            created = api("POST", path, json=body)
            st.session_state[generation_key] = generation + 1
            st.session_state["next_dataset_" + project] = created["id"]
            st.rerun()


def render_upload(api, project, spec, principal, datasets):
    st.write(
        "Choose a file, map its fields, then validate the complete batch before saving it for review. "
        "Previewing does not create or change a collection."
    )
    saved_key = "import-saved-" + project
    if saved_key in st.session_state:
        st.success(st.session_state.pop(saved_key))
    target = collection_target(api, project, datasets, "file import")
    generation_key = "upload-generation-" + project
    generation = st.session_state.get(generation_key, 0)
    uploaded = st.file_uploader(
        "Upload examples or operational data; labels may be missing",
        type=["csv", "tsv", "xlsx", "json", "jsonl"],
        max_upload_size=10,
        key="upload-" + project + "-" + str(generation),
    )
    mapping, worksheet = None, None
    if uploaded is not None and uploaded.name.lower().endswith(
        (".csv", ".tsv", ".xlsx")
    ):
        content = uploaded.getvalue()
        key = (
            project
            + "-"
            + hashlib.sha256(uploaded.name.encode() + b"\0" + content).hexdigest()
        )
        initial_key = "inspect-" + key
        if initial_key not in st.session_state:
            st.session_state[initial_key] = api(
                "POST",
                f"/v1/projects/{project}/imports/inspect",
                files={"file": (uploaded.name, content)},
            )
        inspection = st.session_state[initial_key]
        worksheet = inspection["worksheet"]
        if inspection["worksheets"]:
            worksheet = st.selectbox(
                "Worksheet to import", inspection["worksheets"], key="sheet-" + key
            )
            sheet_key = initial_key + "-" + worksheet
            if worksheet != inspection["worksheet"]:
                if sheet_key not in st.session_state:
                    st.session_state[sheet_key] = api(
                        "POST",
                        f"/v1/projects/{project}/imports/inspect",
                        files={"file": (uploaded.name, content)},
                        data={"worksheet": worksheet},
                    )
                inspection = st.session_state[sheet_key]
        if inspection.get("worksheet_error"):
            st.warning("Choose another worksheet: " + inspection["worksheet_error"])
            return
        key += "-" + hashlib.sha256((worksheet or "").encode()).hexdigest()[:12]
        columns = inspection["columns"]
        draft_key = "mapping-draft-" + key
        draft = st.session_state.get(draft_key, {})
        st.caption(
            f"{inspection['row_count']} rows · {len(columns)} columns · preview strings limited to 500 characters"
        )
        st.dataframe(inspection["preview"], hide_index=True)
        canonical = st.checkbox(
            "Use canonical state/questions/gold record columns",
            value=draft.get("canonical", "questions" in columns and "gold" in columns),
            key="canonical-" + key,
        )
        if not canonical:
            state_columns = st.multiselect(
                "Columns containing the input evidence",
                columns,
                default=[
                    name
                    for name in draft.get(
                        "state_columns", ["state"] if "state" in columns else []
                    )
                    if name in columns
                ],
                key="state-columns-" + key,
            )
            state_mode = "json" if len(state_columns) > 1 else "text"
            st.caption(
                "Multiple input columns preserve their names as JSON. Choose only information available at decision time; exclude label or future-outcome fields."
            )
            id_column = st.selectbox(
                "Unique example ID column",
                [None] + columns,
                index=([None] + columns).index(
                    draft.get("id_column", "id" if "id" in columns else None)
                ),
                format_func=lambda v: v or "Generate stable IDs from this file",
                key="id-column-" + key,
            )
            group_column = st.selectbox(
                "Source family / customer / document column",
                [None] + columns,
                index=([None] + columns).index(
                    draft.get(
                        "group_column", "group_id" if "group_id" in columns else None
                    )
                ),
                format_func=lambda v: v or "One source group per row",
                key="group-column-" + key,
            )
            temporal_columns = {}
            if st.checkbox(
                "Map decision and availability timestamps",
                value=bool(draft.get("temporal_columns")),
                key="temporal-enabled-" + key,
            ):
                st.caption(
                    "Use ISO timestamps with seconds and a timezone, for example 2026-10-01T09:00:00Z. Evidence availability describes the latest information included in the entire state. Timestamped real records require chronological reservations."
                )
                for field, label in (
                    ("decision_at", "Decision timestamp column"),
                    (
                        "evidence_available_at",
                        "Latest input evidence availability column",
                    ),
                    ("label_available_at", "Label availability column"),
                ):
                    selected = draft.get("temporal_columns", {}).get(field, field)
                    temporal_columns[field] = st.selectbox(
                        label,
                        columns,
                        index=columns.index(selected) if selected in columns else 0,
                        key="temporal-" + field + "-" + key,
                    )
            origin = st.selectbox(
                "File source",
                ["real", "synthetic"],
                index=["real", "synthetic"].index(draft.get("origin", "real")),
                format_func=lambda v: (
                    "Observed operational records"
                    if v == "real"
                    else "Synthetic or hypothetical examples"
                ),
                key="file-origin-" + key,
            )
            st.caption(
                "Set File source to observed only for actual operational records; choose synthetic for invented examples. Repeated customer/document families must share a group. Synthetic examples are eligible only for the training split after review."
            )
            labels, value_maps = {}, {}
            for name, question in spec["questions"].items():
                label_column = st.selectbox(
                    "Label column for " + name,
                    [None] + columns,
                    index=([None] + columns).index(
                        draft.get("labels", {}).get(
                            name, name if name in columns else None
                        )
                    ),
                    format_func=lambda v: v or "No labels yet",
                    key="label-column-" + key + "-" + name,
                )
                labels[name] = label_column
                if label_column:
                    values = inspection["column_summary"][label_column]
                    with st.expander("Map source values for " + name):
                        for raw in values["sample_values"]:
                            if raw in options(question) or (
                                question["type"] == "noul"
                                and raw.lower() in options(question)
                            ):
                                continue
                            mapped = st.selectbox(
                                f"{raw} → outcome",
                                [None] + options(question),
                                index=([None] + options(question)).index(
                                    draft.get("value_maps", {}).get(name, {}).get(raw)
                                ),
                                format_func=lambda v: (
                                    "Select an outcome" if v is None else v
                                ),
                                key="value-"
                                + key
                                + "-"
                                + name
                                + "-"
                                + hashlib.sha256(raw.encode()).hexdigest(),
                            )
                            if mapped is not None:
                                value_maps.setdefault(name, {})[raw] = mapped
                        if values["values_are_sampled"]:
                            st.caption(
                                "This is a bounded value sample. Unmapped invalid values stop import with a source-row error."
                            )
            mapping = {
                "worksheet": worksheet,
                "state_columns": state_columns,
                "state_mode": state_mode,
                "id_column": id_column,
                "group_column": group_column,
                "origin": origin,
                "labels": labels,
                "value_maps": value_maps,
                "temporal_columns": temporal_columns,
            }
        # Streamlit removes widgets not rendered on the current sheet. Retain
        # their user-selected defaults outside widget-owned session keys.
        st.session_state[draft_key] = {
            **draft,
            **(mapping or {}),
            "canonical": canonical,
        }
    data = {
        **({"mapping": json.dumps(mapping)} if mapping is not None else {}),
        **({"worksheet": worksheet} if worksheet is not None else {}),
        **(
            {"dataset": target["id"], "expected_hash": target["payload"]["sha256"]}
            if target
            else {}
        ),
    }
    request_key = hashlib.sha256(
        json.dumps(
            {
                "project": project,
                "actor": principal["actor"],
                "tenant": principal["tenant"],
                "questions": spec["questions"],
                "filename": uploaded.name if uploaded is not None else None,
                "upload_sha256": (
                    hashlib.sha256(uploaded.getvalue()).hexdigest()
                    if uploaded is not None
                    else None
                ),
                "form": data,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    preview_key = "import-preview-" + project
    if st.button(
        "Validate and preview import",
        disabled=uploaded is None or principal["role"] not in ("admin", "editor"),
        key="preview-import-" + project,
    ):
        st.session_state.pop(preview_key, None)
        try:
            preview = api(
                "POST",
                f"/v1/projects/{project}/imports/preview",
                files={"file": (uploaded.name, uploaded.getvalue())},
                data=data,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[preview_key] = {"request": request_key, "result": preview}
    saved = st.session_state.get(preview_key)
    preview = saved["result"] if saved and saved["request"] == request_key else None
    if preview:
        st.success(
            f"All {preview['row_count']:,} records passed format validation. Nothing has been saved yet."
        )
        st.write(
            f"{preview['rows_with_missing_outcomes']:,} records have unknown outcomes. "
            "Every imported record will need review, including those with complete labels."
        )
        if preview["target"]:
            st.caption(
                f"Collection size: {preview['target']['existing_rows']:,} existing + {preview['row_count']:,} new = {preview['target']['resulting_rows']:,} records."
            )
        else:
            st.caption("Save creates a new collection.")
        for warning in preview["warnings"]:
            st.warning(warning)
        st.dataframe(preview["preview"], hide_index=True)
        st.caption(
            "First ten normalized records; input evidence is limited to 500 characters in this preview. Full input is preserved when saved. Outcomes show the highest-probability label; canonical probability distributions are preserved."
        )
    elif saved and uploaded is not None:
        st.info(
            "The file, mapping or collection changed. Validate and preview again before saving."
        )
    if st.button(
        "Append file to collection" if target else "Save new collection for review",
        disabled=preview is None or principal["role"] not in ("admin", "editor"),
        key="save-import-" + project,
    ):
        if preview is None:
            raise ValueError(
                "The file, mapping or collection changed. Validate and preview again before saving."
            )
        created = api(
            "POST",
            f"/v1/projects/{project}/datasets",
            files={"file": (uploaded.name, uploaded.getvalue())},
            data={
                **data,
                "preview_sha256": preview["preview_sha256"],
            },
        )
        st.session_state.pop(preview_key, None)
        st.session_state[generation_key] = generation + 1
        st.session_state[saved_key] = (
            f"Saved {preview['row_count']:,} records for review. Open the collection below to review and label them."
        )
        st.session_state["next_dataset_" + project] = created["id"]
        st.rerun()
    st.caption(
        "Missing labels stay unknown. Imported review flags are reset; every record needs authenticated review before preparation."
    )
