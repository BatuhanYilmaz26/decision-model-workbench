"""Example-bound taxonomy proposals and atomic owner-confirmed successor schemas."""

import copy
import json
import re
import time
import unicodedata
import uuid

from decision_workbench.data import state_key, validate_questions
from .store import Conflict, digest, encode


def name_key(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("A proposed category name needs 1â€“80 characters")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise ValueError("Category names cannot contain control/format characters")
    return re.sub(
        r"[\s_-]+", "_", unicodedata.normalize("NFKC", name).strip().casefold()
    )


def footprints(rows):
    return [
        {
            "id": r["id"],
            "group_id": r["group_id"],
            "state_sha256": digest(state_key(r)),
            "related_ids": sorted(
                set(r["metadata"].get("semantic_links", []))
                | set(r["metadata"].get("seed_ids", []))
            ),
        }
        for r in rows
    ]


def exposed(db, tenant, projects):
    result = []
    for project in projects:
        for r in db.execute(
            "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind IN ('taxonomy-proposal','taxonomy-discovery')",
            (tenant, project),
        ):
            result.extend(json.loads(r["payload"])["footprints"])
    return result


def matching_ids(rows, footprint):
    ids = {r["id"] for r in footprint} | {
        i for r in footprint for i in r["related_ids"]
    }
    groups = {r["group_id"] for r in footprint}
    states = {r["state_sha256"] for r in footprint}
    return {
        r["id"]
        for r in rows
        if r["id"] in ids
        or r["group_id"] in groups
        or digest(state_key(r)) in states
        or set(r["metadata"].get("semantic_links", [])) & ids
        or set(r["metadata"].get("seed_ids", [])) & ids
    }


def training_only_ids(store, tenant, project, rows):
    projects = lineage(store, tenant, project)
    with store.transaction() as db:
        return matching_ids(rows, exposed(db, tenant, projects))


def refuse_exposed_holdouts(db, tenant, projects, splits):
    held = [r for role, rows in splits.items() if role != "train" for r in rows]
    if matching_ids(held, exposed(db, tenant, projects)):
        raise ValueError(
            "Taxonomy-selection examples and known relatives must remain training-only"
        )


def verify_snapshot(store, tenant, project, snapshot):
    projects = lineage(store, tenant, project)
    with store.transaction() as db:
        refuse_exposed_holdouts(db, tenant, projects, snapshot["splits"])


def lineage(store, tenant, project):
    result = []
    while project:
        if project in result or len(result) >= 64:
            raise ValueError("Invalid/cyclic project ancestry")
        result.append(project)
        project = store.get(project, tenant, project, "project")["payload"].get(
            "parent_project"
        )
    return result


def refuse_holdouts(db, tenant, projects, selected):
    held_ids, held_groups, held_states, held_links = set(), set(), set(), set()
    for project in projects:
        snapshots = db.execute(
            "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind='snapshot'",
            (tenant, project),
        ).fetchall()
        for item in snapshots:
            for role, records in json.loads(item["payload"])["splits"].items():
                if role == "train":
                    continue
                for r in records:
                    held_ids.add(r["id"])
                    held_groups.add(r["group_id"])
                    held_states.add(state_key(r))
                    held_links.update(r["metadata"].get("semantic_links", []))
                    held_links.update(r["metadata"].get("seed_ids", []))
    for r in selected:
        if (
            r["id"] in held_ids
            or r["id"] in held_links
            or r["group_id"] in held_groups
            or state_key(r) in held_states
            or set(r["metadata"].get("semantic_links", [])) & held_ids
            or set(r["metadata"].get("seed_ids", [])) & held_ids
        ):
            raise ValueError(
                "Reserved holdouts and known relatives cannot be used for taxonomy selection"
            )


def selection(
    store, tenant, project, dataset, record_ids, question_id, check_holdouts=True
):
    spec = store.get(project, tenant, project, "project")["payload"]
    question = spec["questions"].get(question_id)
    if not question or question["type"] != "choice":
        raise ValueError("Taxonomy discovery needs an existing choice question")
    if (
        not isinstance(record_ids, list)
        or not 1 <= len(record_ids) <= 20
        or any(not isinstance(i, str) or not i for i in record_ids)
        or len(set(record_ids)) != len(record_ids)
    ):
        raise ValueError("Select 1â€“20 unique source record IDs")
    version = store.get(dataset, tenant, project, "dataset", include_rows=False)[
        "payload"
    ]
    rows = store.verify_dataset(dataset, tenant, project)
    by_id = {r["id"]: r for r in rows}
    if not set(record_ids) <= set(by_id):
        raise ValueError("Selected examples are absent from the pinned dataset")
    selected = [by_id[i] for i in sorted(record_ids)]
    if any(
        r["metadata"]["origin"] != "real"
        or r["metadata"].get("review_status") == "rejected"
        for r in selected
    ):
        raise ValueError(
            "Discover from eligible source examples, not synthetic or rejected records"
        )
    if check_holdouts:
        parents = lineage(store, tenant, project)
        with store.transaction() as db:
            refuse_holdouts(db, tenant, parents, selected)
    return spec, version["sha256"], selected


def build_proposal(
    store,
    principal,
    project,
    dataset,
    record_ids,
    question_id,
    candidates,
    provenance,
    job=None,
    assignments=None,
):
    spec, sha, rows = selection(
        store, principal["tenant"], project, dataset, record_ids, question_id
    )
    if not isinstance(candidates, list) or len(candidates) > 20:
        raise ValueError("Provide at most twenty proposed names")
    ids = {r["id"] for r in rows}
    grouped = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"name", "example_ids"}:
            raise ValueError("Candidates contain only name and example_ids")
        key = name_key(candidate["name"])
        examples = candidate["example_ids"]
        if (
            not isinstance(examples, list)
            or not examples
            or any(not isinstance(i, str) for i in examples)
            or len(set(examples)) != len(examples)
            or not set(examples) <= ids
        ):
            raise ValueError("Every proposed name must reference selected examples")
        entry = grouped.setdefault(
            key,
            {
                "id": digest({"category_name": key}),
                "name": candidate["name"].strip(),
                "key": key,
                "example_ids": [],
            },
        )
        entry["example_ids"] = sorted(set(entry["example_ids"]) | set(examples))
    if assignments is not None:
        if not isinstance(assignments, dict) or set(assignments) != ids:
            raise ValueError(
                "Discovery assignments must cover exactly the selected examples"
            )
    value = {
        "format": "domain-taxonomy-proposal-v1",
        "question_id": question_id,
        "schema_sha256": spec["schema_sha256"],
        "dataset": dataset,
        "dataset_sha256": sha,
        "record_hashes": {r["id"]: digest(r) for r in rows},
        "footprints": footprints(rows),
        "candidates": sorted(grouped.values(), key=lambda c: c["key"]),
        "assignments": assignments,
        "provenance": provenance,
    }
    value["sha256"] = digest(value)
    parents = lineage(store, principal["tenant"], project)
    return store.add(
        "taxonomy-proposal",
        principal["tenant"],
        project,
        principal["actor"],
        value,
        job=job,
        validate_db=lambda db: refuse_holdouts(db, principal["tenant"], parents, rows),
    )


def checked_proposal(store, tenant, project, proposal_id):
    p = store.get(proposal_id, tenant, project, "taxonomy-proposal")["payload"]
    if (
        set(p)
        != {
            "format",
            "question_id",
            "schema_sha256",
            "dataset",
            "dataset_sha256",
            "record_hashes",
            "footprints",
            "candidates",
            "assignments",
            "provenance",
            "sha256",
        }
        or p["format"] != "domain-taxonomy-proposal-v1"
        or not isinstance(p["candidates"], list)
        or len(p["candidates"]) > 20
        or not isinstance(p["record_hashes"], dict)
        or not isinstance(p["provenance"], dict)
    ):
        raise ValueError("Malformed taxonomy proposal")
    if digest({k: v for k, v in p.items() if k != "sha256"}) != p["sha256"]:
        raise ValueError("Taxonomy proposal content hash mismatch")
    spec, sha, rows = selection(
        store,
        tenant,
        project,
        p["dataset"],
        list(p["record_hashes"]),
        p["question_id"],
        False,
    )
    if (
        spec["schema_sha256"] != p["schema_sha256"]
        or sha != p["dataset_sha256"]
        or {r["id"]: digest(r) for r in rows} != p["record_hashes"]
        or footprints(rows) != p["footprints"]
    ):
        raise ValueError("Taxonomy proposal source identity mismatch")
    seen = set()
    for c in p["candidates"]:
        if (
            not isinstance(c, dict)
            or set(c) != {"id", "name", "key", "example_ids"}
            or name_key(c["name"]) != c["key"]
            or c["id"] != digest({"category_name": c["key"]})
            or c["id"] in seen
            or not isinstance(c["example_ids"], list)
            or not c["example_ids"]
            or any(not isinstance(i, str) for i in c["example_ids"])
            or c["example_ids"] != sorted(set(c["example_ids"]))
            or not set(c["example_ids"]) <= set(p["record_hashes"])
        ):
            raise ValueError("Taxonomy candidate identity mismatch")
        seen.add(c["id"])
    if p["assignments"] is not None and (
        not isinstance(p["assignments"], dict)
        or set(p["assignments"]) != set(p["record_hashes"])
        or any(
            v
            not in list(spec["questions"][p["question_id"]]["criteria"])
            + ["__propose__"]
            for v in p["assignments"].values()
        )
    ):
        raise ValueError("Taxonomy assignment identity mismatch")
    return p


def latest_reviews(db, tenant, project, proposal):
    records = db.execute(
        "SELECT * FROM entities WHERE tenant=? AND project=? AND kind='taxonomy-review' AND json_extract(payload,'$.proposal_id')=? ORDER BY json_extract(payload,'$.revision')",
        (tenant, project, proposal),
    ).fetchall()
    output = {}
    for r in records:
        value = json.loads(r["payload"])
        output[value["candidate_id"]] = {"id": r["id"], "payload": value}
    return output


def state_hash(proposal, reviews):
    return digest(
        {
            "proposal_sha256": proposal["sha256"],
            "reviews": {
                k: {"id": v["id"], "sha256": digest(v["payload"])}
                for k, v in reviews.items()
            },
        }
    )


def page(service, principal, project, identifier):
    service.require(principal, ("admin", "editor", "reviewer"))
    service.project(principal, project)
    proposal = checked_proposal(service.store, principal["tenant"], project, identifier)
    with service.store.transaction() as db:
        reviews = latest_reviews(db, principal["tenant"], project, identifier)
        confirmation = db.execute(
            "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='taxonomy-commit'",
            (
                digest({"taxonomy_commit": identifier})[:32],
                principal["tenant"],
                project,
            ),
        ).fetchone()
    rows = {
        r["id"]: r
        for r in service.store.verify_dataset(
            proposal["dataset"], principal["tenant"], project
        )
    }
    return {
        "proposal_id": identifier,
        "proposal": proposal,
        "state_sha256": state_hash(proposal, reviews),
        "pending": sum(c["id"] not in reviews for c in proposal["candidates"]),
        "confirmation": json.loads(confirmation["payload"]) if confirmation else None,
        "candidates": [
            {
                **c,
                "review": reviews.get(c["id"]),
                "examples": [
                    {"id": i, "state": rows[i]["state"]} for i in c["example_ids"]
                ],
            }
            for c in proposal["candidates"]
        ],
    }


def adjudicate(
    service,
    principal,
    project,
    identifier,
    candidate_id,
    decision,
    canonical_id,
    description,
    expected_revision,
    expected_hash,
    note,
):
    service.require(principal, ("admin", "reviewer"))
    service.project(principal, project)
    p = checked_proposal(service.store, principal["tenant"], project, identifier)
    if p["sha256"] != expected_hash or candidate_id not in {
        c["id"] for c in p["candidates"]
    }:
        raise Conflict("Refresh the taxonomy proposal and candidate")
    if (
        decision not in ("add", "alias", "reject")
        or type(expected_revision) is not int
        or expected_revision < 0
    ):
        raise ValueError("Select add, alias or reject with the current revision")
    if (
        not isinstance(note, str)
        or not 1 <= len(note.strip()) <= 1000
        or not isinstance(description, str)
        or len(description) > 1000
    ):
        raise ValueError("Provide a review reason and bounded definition")
    if decision == "reject":
        if canonical_id is not None or description:
            raise ValueError("Rejected candidates cannot define a category")
    elif (
        not isinstance(canonical_id, str) or not canonical_id or len(canonical_id) > 128
    ):
        raise ValueError("Choose a canonical category ID")
    if decision == "add" and (
        not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", canonical_id)
        or canonical_id == "new"
        or not description.strip()
    ):
        raise ValueError(
            "New canonical IDs need snake_case, a definition, and cannot be new"
        )
    with service.store.transaction() as db:
        reviews = latest_reviews(db, principal["tenant"], project, identifier)
        current = reviews.get(candidate_id)
        if (current["payload"]["revision"] if current else 0) != expected_revision:
            raise Conflict("Another reviewer advanced this candidate")
        if db.execute(
            "SELECT 1 FROM entities WHERE id=? AND kind='taxonomy-commit'",
            (digest({"taxonomy_commit": identifier})[:32],),
        ).fetchone():
            raise Conflict("This proposal was confirmed; its reviews are frozen")
        value = {
            "proposal_id": identifier,
            "proposal_sha256": p["sha256"],
            "candidate_id": candidate_id,
            "decision": decision,
            "canonical_id": canonical_id,
            "description": description.strip(),
            "revision": expected_revision + 1,
            "reviewer": principal["actor"],
            "note": note.strip(),
        }
        rid = uuid.uuid4().hex
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                rid,
                "taxonomy-review",
                principal["tenant"],
                project,
                encode(value),
                time.time(),
            ),
        )
        service.store.audit(
            db, principal["tenant"], project, principal["actor"], "review.taxonomy", rid
        )
    return service.store.get(rid, principal["tenant"], project, "taxonomy-review")


def confirm(
    service,
    principal,
    project,
    identifier,
    expected_state_sha256,
    name,
    note,
    authorize=None,
):
    service.require(principal, ("admin",))
    parent = service.project(principal, project)["payload"]
    p = checked_proposal(service.store, principal["tenant"], project, identifier)
    if (
        not isinstance(name, str)
        or not 1 <= len(name.strip()) <= 120
        or not isinstance(note, str)
        or not 1 <= len(note.strip()) <= 1000
    ):
        raise ValueError("Name the successor and record the owner confirmation")
    if p["provenance"].get("job_id"):
        job = service.store.job(p["provenance"]["job_id"], principal["tenant"], project)
        if (
            job["status"] != "succeeded"
            or job["result"].get("proposal_id") != identifier
        ):
            raise ValueError("Discovery has not completed successfully")
    commit_id = digest({"taxonomy_commit": identifier})[:32]
    successor = uuid.uuid4().hex
    with service.store.transaction() as db:
        if db.execute("SELECT 1 FROM entities WHERE id=?", (commit_id,)).fetchone():
            raise Conflict("This taxonomy proposal already has a successor")
        reviews = latest_reviews(db, principal["tenant"], project, identifier)
        if state_hash(p, reviews) != expected_state_sha256:
            raise Conflict("Taxonomy reviews changed; refresh before confirmation")
        if set(reviews) != {c["id"] for c in p["candidates"]}:
            raise ValueError("Review every proposed name before confirmation")
        questions = copy.deepcopy(parent["questions"])
        criteria = questions[p["question_id"]]["criteria"]
        keys = {name_key(k): k for k in criteria}
        aliases = {}
        added = []
        for entry in reviews.values():
            r = entry["payload"]
            if r["decision"] == "add":
                canonical = r["canonical_id"]
                if name_key(canonical) in keys:
                    raise ValueError(
                        "Canonical ID duplicates an existing/additional category; adjudicate it as an alias"
                    )
                criteria[canonical] = r["description"]
                keys[name_key(canonical)] = canonical
                added.append(canonical)
        if not added:
            raise ValueError(
                "A successor needs at least one owner-approved new category"
            )
        for c in p["candidates"]:
            r = reviews[c["id"]]["payload"]
            if r["decision"] == "alias":
                if r["canonical_id"] not in criteria:
                    raise ValueError(
                        "Alias target is not an existing or approved new category"
                    )
            if r["decision"] != "reject":
                existing = keys.get(c["key"])
                if existing and existing != r["canonical_id"]:
                    raise ValueError(
                        "A proposed alias cannot rename another canonical category"
                    )
                aliases[c["key"]] = r["canonical_id"]
        validate_questions(questions)
        value = {
            "name": name.strip(),
            "description": parent["description"],
            "data_requirements": parent.get("data_requirements", ""),
            "parent_project": project,
            "policy_version": parent["policy_version"] + 1,
            "questions": questions,
            "schema_sha256": digest(questions),
            "taxonomy_commit": commit_id,
        }
        confirmation = {
            "proposal_id": identifier,
            "proposal_sha256": p["sha256"],
            "state_sha256": expected_state_sha256,
            "review_ids": [r["id"] for r in reviews.values()],
            "review_hashes": {r["id"]: digest(r["payload"]) for r in reviews.values()},
            "parent_schema_sha256": parent["schema_sha256"],
            "successor_project": successor,
            "successor_schema_sha256": value["schema_sha256"],
            "aliases": aliases,
            "added": sorted(added),
            "owner": principal["actor"],
            "note": note.strip(),
        }
        confirmation["sha256"] = digest(confirmation)
        if authorize:
            authorize()
        for eid, kind, scope, payload in (
            (successor, "project", successor, value),
            (commit_id, "taxonomy-commit", project, confirmation),
        ):
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (eid, kind, principal["tenant"], scope, encode(payload), time.time()),
            )
            service.store.audit(
                db,
                principal["tenant"],
                scope,
                principal["actor"],
                "create." + kind,
                eid,
            )
    return {
        "successor": service.store.get(
            successor, principal["tenant"], successor, "project"
        ),
        "confirmation": service.store.get(
            commit_id, principal["tenant"], project, "taxonomy-commit"
        ),
        "labels_transferred": False,
        "release_changed": False,
    }


def validate_history(store):
    """Reconstruct source and pinned schema/review links without provider/model imports."""
    with store.transaction() as db:
        items = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM entities WHERE kind IN ('taxonomy-proposal','taxonomy-review','taxonomy-commit')"
            )
        ]
    by_id = {r["id"]: {**r, "payload": json.loads(r["payload"])} for r in items}
    for item in by_id.values():
        tenant, project = item["tenant"], item["project"]
        v = item["payload"]
        if item["kind"] == "taxonomy-proposal":
            p = checked_proposal(store, tenant, project, item["id"])
            if p["provenance"]["mode"] == "typellm":
                call = store.get(
                    p["provenance"]["call_id"], tenant, project, "taxonomy-discovery"
                )["payload"]
                outcome = store.get(
                    call["result_id"], tenant, project, "taxonomy-result"
                )["payload"]
                merged = {}
                for c in outcome["result"]["candidates"]:
                    key = name_key(c["name"])
                    item = merged.setdefault(
                        key,
                        {
                            "id": digest({"category_name": key}),
                            "key": key,
                            "name": c["name"].strip(),
                            "example_ids": [],
                        },
                    )
                    item["example_ids"] = sorted(
                        set(item["example_ids"]) | set(c["example_ids"])
                    )
                if (
                    call["job_id"] != p["provenance"]["job_id"]
                    or call["dataset"] != p["dataset"]
                    or call["record_hashes"] != p["record_hashes"]
                    or call["profile"] != p["provenance"]["profile"]
                    or outcome["status"] != "succeeded"
                    or outcome["result"]["assignments"] != p["assignments"]
                    or outcome["result"]["response_sha256"]
                    != p["provenance"]["response_sha256"]
                    or p["candidates"]
                    != sorted(merged.values(), key=lambda c: c["key"])
                    or p["provenance"]["provider_id"]
                    != outcome["result"]["provider_id"]
                    or p["provenance"]["response_model"]
                    != outcome["result"]["response_model"]
                    or p["provenance"]["source_sha256"] != call["source_sha256"]
                ):
                    raise ValueError("Taxonomy proposal provider provenance mismatch")
            elif p["provenance"]["mode"] != "manual" or p["assignments"] is not None:
                raise ValueError("Invalid taxonomy proposal provenance")
        elif item["kind"] == "taxonomy-review":
            parent = store.get(v["proposal_id"], tenant, project, "taxonomy-proposal")[
                "payload"
            ]
            if (
                v["proposal_sha256"] != parent["sha256"]
                or v["candidate_id"] not in {c["id"] for c in parent["candidates"]}
                or v["decision"] not in ("add", "alias", "reject")
                or type(v["revision"]) is not int
                or v["revision"] < 1
                or not isinstance(v["note"], str)
                or not v["note"].strip()
                or not isinstance(v["description"], str)
            ):
                raise ValueError("Taxonomy review identity mismatch")
            if v["decision"] in ("add", "alias") and (
                not isinstance(v["canonical_id"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v["canonical_id"])
                or v["canonical_id"] == "new"
                or (v["decision"] == "add" and not v["description"].strip())
                or (v["decision"] == "alias" and v["description"])
            ):
                raise ValueError("Malformed taxonomy category definition")
            if v["decision"] == "reject" and (
                v["canonical_id"] is not None or v["description"]
            ):
                raise ValueError("Rejected taxonomy review defines a category")
        else:
            if (
                item["id"] != digest({"taxonomy_commit": v["proposal_id"]})[:32]
                or digest({k: x for k, x in v.items() if k != "sha256"}) != v["sha256"]
            ):
                raise ValueError("Taxonomy confirmation identity mismatch")
            p = checked_proposal(store, tenant, project, v["proposal_id"])
            if p["provenance"]["mode"] == "typellm":
                job = store.job(p["provenance"]["job_id"], tenant, project)
                if (
                    job["status"] != "succeeded"
                    or job["result"].get("proposal_id") != v["proposal_id"]
                ):
                    raise ValueError(
                        "Confirmed taxonomy discovery job was not successful"
                    )
            reviews = {}
            for rid in v["review_ids"]:
                r = store.get(rid, tenant, project, "taxonomy-review")["payload"]
                if (
                    digest(r) != v["review_hashes"].get(rid)
                    or r["proposal_id"] != v["proposal_id"]
                    or r["candidate_id"] in reviews
                ):
                    raise ValueError("Pinned taxonomy review content mismatch")
                reviews[r["candidate_id"]] = {"id": rid, "payload": r}
            if (
                set(reviews) != {c["id"] for c in p["candidates"]}
                or state_hash(p, reviews) != v["state_sha256"]
                or p["sha256"] != v["proposal_sha256"]
            ):
                raise ValueError("Taxonomy confirmation coverage mismatch")
            parent = store.get(project, tenant, project, "project")["payload"]
            child = store.get(
                v["successor_project"], tenant, v["successor_project"], "project"
            )["payload"]
            expected = copy.deepcopy(parent["questions"])
            criteria = expected[p["question_id"]]["criteria"]
            aliases = {}
            added = []
            for r in reviews.values():
                x = r["payload"]
                if x["decision"] == "add":
                    if x["canonical_id"] in criteria:
                        raise ValueError("Taxonomy confirmation overwrites a category")
                    criteria[x["canonical_id"]] = x["description"]
                    added.append(x["canonical_id"])
            for c in p["candidates"]:
                r = reviews[c["id"]]["payload"]
                if r["decision"] != "reject":
                    if r["canonical_id"] not in criteria:
                        raise ValueError("Missing taxonomy alias target")
                    aliases[c["key"]] = r["canonical_id"]
            if (
                child["questions"] != expected
                or child["schema_sha256"] != digest(expected)
                or child["schema_sha256"] != v["successor_schema_sha256"]
                or child["parent_project"] != project
                or child["policy_version"] != parent["policy_version"] + 1
                or child.get("taxonomy_commit") != item["id"]
                or v["parent_schema_sha256"] != parent["schema_sha256"]
                or v["aliases"] != aliases
                or v["added"] != sorted(added)
            ):
                raise ValueError("Taxonomy successor schema identity mismatch")
    return len(items)
