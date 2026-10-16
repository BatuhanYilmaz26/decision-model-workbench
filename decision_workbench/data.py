"""Canonical training rows and conservative group splitting."""

import json
import math
import random
from collections import defaultdict
from pathlib import Path


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def options(question):
    kind = question.get("type")
    if (
        not isinstance(question.get("instructions"), str)
        or not question["instructions"].strip()
    ):
        raise ValueError("Every question needs explicit instructions")
    if kind == "noul":
        return ["false", "true"]
    criteria = question.get("criteria")
    if kind == "choice":
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
            raise ValueError("choice needs 2–255 named criteria")
        if any(
            not isinstance(k, str)
            or not k.strip()
            or not isinstance(v, str)
            or not v.strip()
            for k, v in criteria.items()
        ):
            raise ValueError(
                "choice criteria need nonempty string keys and descriptions"
            )
        return list(criteria)
    if kind == "score":
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise ValueError("score needs 2–10 ordered levels")
        if any(not isinstance(v, str) or not v.strip() for v in criteria):
            raise ValueError("score levels need descriptions")
        return [str(i) for i in range(len(criteria))]
    raise ValueError("Question type must be choice, noul or score")


def validate_questions(questions):
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 64:
        raise ValueError("Define 1–64 questions")
    for name, question in questions.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(question, dict)
        ):
            raise ValueError("Questions need names and objects")
        options(question)


def validate(row, require_review=False, *, allow_missing_labels=False):
    if not isinstance(row, dict):
        raise ValueError("A row must be an object")
    if not isinstance(row.get("id"), str) or not row["id"].strip():
        raise ValueError("A row needs a nonempty string id")
    if not isinstance(row.get("group_id"), str) or not row["group_id"].strip():
        raise ValueError(
            "Set group_id to the original document/customer/example family"
        )
    state = row.get("state")
    if not isinstance(state, (str, dict, list)) or state in ("", {}, []):
        raise ValueError("state must be nonempty text or JSON")
    dumps(state)  # reject non-JSON values and NaN in structured state
    validate_questions(row.get("questions"))
    gold = row.get("gold")
    if allow_missing_labels and (
        not isinstance(row.get("metadata"), dict)
        or row["metadata"].get("reviewed") is not False
    ):
        allow_missing_labels = False
    if not isinstance(gold, dict) or (
        not set(gold) <= set(row["questions"])
        if allow_missing_labels
        else set(gold) != set(row["questions"])
    ):
        raise ValueError("gold must cover exactly the question ids")
    for name, question in row["questions"].items():
        if allow_missing_labels and name not in gold:
            continue
        target = gold[name]
        probs = target.get("probabilities") if isinstance(target, dict) else None
        keys = options(question)
        if not isinstance(probs, dict) or set(probs) != set(keys):
            raise ValueError(f"{name}: provide probabilities for exactly {keys}")
        values = list(probs.values())
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or not 0 <= v <= 1
            for v in values
        ):
            raise ValueError(f"{name}: probabilities must be finite numbers in [0,1]")
        if not math.isclose(sum(values), 1, abs_tol=1e-6):
            raise ValueError(f"{name}: probabilities must sum to 1")
    metadata = row.get("metadata", {})
    if not isinstance(metadata, dict) or metadata.get("origin") not in (
        "real",
        "synthetic",
    ):
        raise ValueError("metadata.origin must be real or synthetic")
    if type(metadata.get("reviewed")) is not bool:
        raise ValueError("metadata.reviewed must be a boolean")
    status = metadata.get(
        "review_status", "approved" if metadata["reviewed"] else "pending"
    )
    if (
        status not in ("pending", "approved", "rejected")
        or (status == "approved") != metadata["reviewed"]
    ):
        raise ValueError("metadata.review_status conflicts with metadata.reviewed")
    if require_review and metadata["reviewed"] is not True:
        raise ValueError(
            f"{row['id']}: review the labels before preparing training splits"
        )
    from .temporal import times

    times(row)
    return row


def hard_gold(questions, labels):
    result = {}
    for name, question in questions.items():
        value = labels[name]
        if question["type"] == "noul":
            if type(value) is bool:
                value = str(value).lower()
            elif str(value).lower() in ("true", "false"):
                value = str(value).lower()
            else:
                raise ValueError("noul labels must be true or false")
        else:
            value = str(value)
        keys = options(question)
        if value not in keys:
            raise ValueError(f"{name}: label {value!r} is outside {keys}")
        result[name] = {"probabilities": {key: float(key == value) for key in keys}}
    return result


def read_records(
    content, filename, spec=None, *, allow_missing_labels=False, max_rows=None
):
    """Accept canonical JSON/JSONL or a simple table with state and label columns."""
    extension = Path(filename).suffix.lower()
    if extension in (".xlsx", ".csv", ".tsv"):
        from .intake import read_table

        raw = [row for _, row in read_table(content, filename)["rows"]]
    elif extension == ".json":
        raw = json.loads(content.decode("utf-8-sig"))
        if not isinstance(raw, list):
            raise ValueError("JSON input must be an array of records")
    elif extension == ".jsonl":
        raw = [
            json.loads(line)
            for line in content.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
    else:
        raise ValueError("Supported formats: CSV, TSV, XLSX, JSON, JSONL")
    if not raw:
        raise ValueError("No records found")
    if max_rows is not None and len(raw) > max_rows:
        raise ValueError(
            f"Dataset exceeds the configured {max_rows:,}-row import limit"
        )
    rows = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Row {index} must be an object")
        if "questions" in item:
            for key in ("questions", "gold", "metadata"):
                if isinstance(item.get(key), str):
                    item[key] = json.loads(item[key])
            row = item
            if allow_missing_labels:
                if not isinstance(row.get("metadata", {}), dict):
                    raise ValueError(f"Row {index}: metadata must be an object")
                row.setdefault("metadata", {})["reviewed"] = False
                row["metadata"]["review_status"] = "pending"
                row.setdefault("gold", {})
        else:
            if spec is None:
                raise ValueError("Simple tables need a domain spec defining questions")
            questions = spec["questions"]
            reviewed = item.get("reviewed", False)
            if isinstance(reviewed, str):
                if reviewed.lower() not in ("true", "false", ""):
                    raise ValueError("reviewed must be true or false")
                reviewed = reviewed.lower() == "true"
            labels = {
                name: item[name]
                for name in questions
                if name in item and item[name] not in (None, "")
            }
            if not allow_missing_labels and set(labels) != set(questions):
                raise ValueError(
                    f"Row {index}: label columns must cover every question"
                )
            row = {
                "id": str(item.get("id") or f"row-{index}"),
                "group_id": str(
                    item.get("group_id") or item.get("id") or f"row-{index}"
                ),
                "state": item.get("state"),
                "questions": questions,
                "gold": hard_gold({name: questions[name] for name in labels}, labels),
                "metadata": {
                    "origin": item.get("origin") or "real",
                    "reviewed": False if allow_missing_labels else reviewed,
                    "source": filename,
                },
            }
            from .temporal import FIELDS

            if any(item.get(field) not in (None, "") for field in FIELDS):
                row["metadata"]["temporal"] = {
                    field: item.get(field) for field in FIELDS
                }
        rows.append(validate(row, allow_missing_labels=allow_missing_labels))
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate record ids; provide unique ids before merging")
    return rows


def jsonl(rows):
    return "".join(dumps(row) + "\n" for row in rows)


def state_key(row):
    state = row["state"]
    return (
        " ".join(state.split()).casefold() if isinstance(state, str) else dumps(state)
    )


def related_groups(rows, *, related_pairs=None):
    """Keep source families, identical states, semantic relatives and seeds together."""
    if not rows:
        raise ValueError("No records supplied")
    for row in rows:
        validate(row, require_review=True)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate record ids")
    parent = list(range(len(rows)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[root(b)] = root(a)

    seen_groups, seen_states, seen_targets = {}, {}, {}
    for i, row in enumerate(rows):
        for seen, key in (
            (seen_groups, row["group_id"]),
            (seen_states, state_key(row)),
        ):
            if key in seen:
                union(i, seen[key])
            else:
                seen[key] = i
        key = (state_key(row), dumps(row["questions"]))
        target = dumps(row["gold"])
        if key in seen_targets and seen_targets[key] != target:
            raise ValueError(
                "Conflicting labels for identical state and questions; adjudicate first"
            )
        seen_targets[key] = target
    by_id = {row["id"]: i for i, row in enumerate(rows)}
    from .semantic import linked_pairs

    for left, right in linked_pairs(rows) + list(related_pairs or []):
        if left not in by_id or right not in by_id or left == right:
            raise ValueError(
                "Semantic relatives must reference distinct included records"
            )
        union(by_id[left], by_id[right])
    for i, row in enumerate(rows):
        seed_ids = row["metadata"].get("seed_ids", [])
        if not isinstance(seed_ids, list) or any(
            not isinstance(value, str) for value in seed_ids
        ):
            raise ValueError("seed_ids must be a list of record ids")
        for seed_id in seed_ids:
            if seed_id in by_id:
                union(i, by_id[seed_id])
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[root(i)].append(row)
    return list(groups.values())


def prepare(
    rows,
    seed=3407,
    *,
    split_strategy="auto",
    related_pairs=None,
    training_only_ids=None,
):
    """Union groups AND identical states; synthetic families stay in training."""
    groups = related_groups(rows, related_pairs=related_pairs)
    from .temporal import strategy, chronological_groups, audit_splits

    mode = strategy(rows, split_strategy)
    real, training_only = [], []
    for group in groups:
        (
            training_only
            if any(
                r["metadata"]["origin"] == "synthetic"
                or r["id"] in (training_only_ids or set())
                for r in group
            )
            else real
        ).append(group)
    if mode == "chronological":
        real = chronological_groups(real)
    if len(real) < 4:
        raise ValueError(
            "Need at least four independent real groups (nonoverlapping availability windows for chronological data) for train/validation/calibration/test; collect more real data"
        )
    rng = random.Random(seed)
    if mode == "group-random":
        rng.shuffle(real)
    counts = [max(1, int(len(real) * p)) for p in (0.7, 0.1, 0.1, 0.1)]
    while sum(counts) > len(real):
        counts[0] -= 1
    counts[0] += len(real) - sum(counts)
    output = {name: [] for name in ("train", "validation", "calibration", "test")}
    position = 0
    for name, count in zip(output, counts):
        output[name] = [r for group in real[position : position + count] for r in group]
        position += count
    output["train"].extend(r for group in training_only for r in group)
    audit_splits(output)
    return output


def write_splits(splits, folder):
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("Choose a new output folder to preserve the previous split")
    folder.mkdir(parents=True, exist_ok=True)
    import hashlib

    manifest = {}
    for name, rows in splits.items():
        content = jsonl(rows)
        (folder / f"{name}.jsonl").write_bytes(content.encode("utf-8"))
        manifest[name] = {
            "rows": len(rows),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n"
    )
    return manifest
