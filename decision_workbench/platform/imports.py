"""Shared upload validation and read-only, content-bound import previews."""

import hashlib
from pathlib import Path

from decision_workbench.data import dumps, options, read_records
from decision_workbench.intake import check_upload, map_table
from .store import Conflict, digest


def load_upload(principal, spec, content, filename, mapping=None, worksheet=None):
    check_upload(content, filename)
    if worksheet is not None and (
        not isinstance(worksheet, str)
        or not worksheet.strip()
        or Path(filename).suffix.lower() != ".xlsx"
    ):
        raise ValueError("Worksheet selection applies only to XLSX and needs a name")
    if mapping is not None and worksheet is not None:
        if not isinstance(mapping, dict) or mapping.get("worksheet") != worksheet:
            raise ValueError("Worksheet must match the column mapping")
    try:
        rows = (
            map_table(content, filename, spec["questions"], mapping)
            if mapping is not None
            else read_records(
                content,
                filename,
                spec,
                allow_missing_labels=True,
                max_rows=20000,
                worksheet=worksheet,
            )
        )
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError(
            "Input does not match the project format; check column mappings and JSON records"
        ) from exc
    for number, row in enumerate(rows, 1):
        if row["questions"] != spec["questions"]:
            raise ValueError(
                f"Record {number}: uploaded questions differ from the project schema"
            )
        if len(dumps(row["state"]).encode()) > 64000:
            raise ValueError(f"Record {number}: each input state is limited to 64 KB")
        row["metadata"]["reviewed"] = False
        row["metadata"]["review_status"] = "pending"
        row["metadata"]["uploaded_by"] = principal["actor"]
    return rows, {
        "filename": Path(filename).name,
        "upload_sha256": hashlib.sha256(content).hexdigest(),
        "mapping": mapping,
        **({"worksheet": worksheet} if worksheet is not None else {}),
    }


def preview_upload(
    store, principal, project, spec, rows, provenance, dataset=None, expected_hash=None
):
    """Validate the target without creating data or reserving a collection version."""
    if (dataset is None) != (expected_hash is None):
        raise ValueError(
            "Appending requires both a dataset version and its expected hash"
        )
    target = None
    if dataset is not None:
        original = store.get(
            dataset, principal["tenant"], project, "dataset", include_rows=False
        )["payload"]
        if (
            original["sha256"] != expected_hash
            or store.dataset_head(dataset, principal["tenant"], project) != dataset
        ):
            raise Conflict(
                "This collection changed; refresh its latest version and preview again"
            )
        if original["row_count"] + len(rows) > 20000:
            raise ValueError("A local collection is limited to 20,000 rows")
        with store.transaction() as db:
            existing = {
                r[0]
                for r in db.execute(
                    "SELECT record_id FROM dataset_members WHERE root=?",
                    (original["root"],),
                )
            }
        collisions = [row["id"] for row in rows if row["id"] in existing]
        if collisions:
            raise ValueError(
                "A record identifier already exists in this collection: "
                + collisions[0]
            )
        target = {
            "id": dataset,
            "sha256": expected_hash,
            "existing_rows": original["row_count"],
            "resulting_rows": original["row_count"] + len(rows),
        }
    missing = {
        name: sum(name not in row["gold"] for row in rows) for name in spec["questions"]
    }
    warnings = []
    if any(missing.values()):
        warnings.append(
            "Some outcomes are unknown. Complete them during review before approval."
        )
    mapping = provenance.get("mapping")
    if mapping:
        overlap = set(mapping["state_columns"]) & {
            v for v in mapping.get("labels", {}).values() if v
        }
        if overlap:
            warnings.append(
                "Input evidence includes a mapped label column. Remove label or future-outcome fields unless they are truly available at decision time."
            )
        if not mapping.get("group_column"):
            warnings.append(
                "No source-family column is mapped. Related examples need a shared family before reserving evaluation data."
            )
    preview = []
    for row in rows[:10]:
        state = row["state"] if isinstance(row["state"], str) else dumps(row["state"])
        preview.append(
            {
                "id": row["id"],
                "group_id": row["group_id"],
                "input_evidence": state[:500],
                "input_truncated": len(state) > 500,
                "origin": row["metadata"]["origin"],
                "missing_outcomes": [
                    name for name in spec["questions"] if name not in row["gold"]
                ],
                "outcomes": {
                    name: max(
                        options(spec["questions"][name]), key=gold["probabilities"].get
                    )
                    for name, gold in row["gold"].items()
                },
            }
        )
    binding = {
        "format": "domain-import-preview-v1",
        "tenant": principal["tenant"],
        "actor": principal["actor"],
        "project": project,
        "schema": digest(spec["questions"]),
        "provenance": provenance,
        "records_sha256": digest(rows),
        "target": target,
    }
    return {
        "preview_sha256": digest(binding),
        "upload_sha256": provenance["upload_sha256"],
        "filename": provenance["filename"],
        "row_count": len(rows),
        "target": target,
        "rows_with_missing_outcomes": sum(
            bool(set(spec["questions"]) - set(r["gold"])) for r in rows
        ),
        "missing_per_question": missing,
        "origin_counts": {
            v: sum(r["metadata"]["origin"] == v for r in rows)
            for v in ("real", "synthetic")
        },
        "preview": preview,
        "preview_string_limit": 500,
        "warnings": warnings,
        "review_status": "pending",
    }
