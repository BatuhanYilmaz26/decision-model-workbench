"""Owner-confirmed, atomic and retry-safe policy successors."""

import copy
import hashlib
import json
import re
import time
import uuid

from decision_workbench.data import validate_questions
from .acceptance import normalize_criteria
from .store import Conflict, digest, encode


def create(
    service,
    principal,
    parent_project,
    name,
    description,
    questions,
    data_requirements,
    expected_parent_schema_sha256,
    expected_parent_policy_id,
    note,
    request_key,
    criteria=None,
):
    service.require(principal, ("admin",))
    parent = service.project(principal, parent_project)["payload"]
    from .taxonomy import lineage

    if len(lineage(service.store, principal["tenant"], parent_project)) >= 64:
        raise ValueError("Project ancestry has reached its supported depth")
    if (
        not name.strip()
        or len(name) > 120
        or not description.strip()
        or len(description) > 12000
    ):
        raise ValueError("Provide a successor name and decision policy")
    if not isinstance(data_requirements, str) or len(data_requirements) > 4000:
        raise ValueError("Data requirements must be text up to 4,000 characters")
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
        raise ValueError("Record why this policy successor is needed")
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
        raise ValueError("Use a request key of 1–128 characters")
    if not isinstance(expected_parent_schema_sha256, str) or not re.fullmatch(
        r"[a-f0-9]{64}", expected_parent_schema_sha256
    ):
        raise ValueError("Provide the displayed parent schema checksum")
    if expected_parent_policy_id is not None and not isinstance(
        expected_parent_policy_id, str
    ):
        raise ValueError(
            "Provide the displayed parent acceptance policy identifier or null"
        )
    validate_questions(questions)
    questions = copy.deepcopy(questions)
    if criteria is not None:
        criteria = copy.deepcopy(criteria)
        if criteria.pop("expected_id", None) is not None:
            raise ValueError(
                "A new successor acceptance policy has no existing expected_id"
            )
        criteria = normalize_criteria(questions, **criteria)
    request = {
        "parent_project": parent_project,
        "name": name.strip(),
        "description": description.strip(),
        "questions": questions,
        "data_requirements": data_requirements.strip(),
        "note": note.strip(),
        "expected_parent_schema_sha256": expected_parent_schema_sha256,
        "expected_parent_policy_id": expected_parent_policy_id,
        "criteria": criteria,
    }
    request_sha = digest(request)
    key_sha = hashlib.sha256(request_key.encode()).hexdigest()
    tenant = principal["tenant"]
    child = uuid.uuid4().hex
    policy_id = uuid.uuid4().hex if criteria is not None else None
    with service.store.transaction() as db:
        prior = db.execute(
            "SELECT id,payload FROM entities WHERE tenant=? AND kind='project' "
            "AND json_extract(payload,'$.parent_project')=? "
            "AND json_extract(payload,'$.successor.request_key_sha256')=?",
            (tenant, parent_project, key_sha),
        ).fetchone()
        if prior:
            if (
                json.loads(prior["payload"])["successor"]["request_sha256"]
                != request_sha
            ):
                raise Conflict(
                    "This successor request key was already used with different content"
                )
            child = prior["id"]
        else:
            current = db.execute(
                "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='project'",
                (parent_project, tenant, parent_project),
            ).fetchone()
            if current is None:
                raise LookupError("Parent project not found")
            parent = json.loads(current["payload"])
            head = db.execute(
                "SELECT policy_id FROM release_policy_heads WHERE tenant=? AND project=?",
                (tenant, parent_project),
            ).fetchone()
            head_id = head["policy_id"] if head else None
            if parent["schema_sha256"] != digest(parent["questions"]):
                raise ValueError("Parent project schema integrity check failed")
            if (
                parent["schema_sha256"] != expected_parent_schema_sha256
                or head_id != expected_parent_policy_id
            ):
                raise Conflict(
                    "Parent schema or acceptance criteria changed; refresh the successor draft"
                )
            parent_policy_sha = None
            if head_id:
                policy = db.execute(
                    "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='release-policy'",
                    (head_id, tenant, parent_project),
                ).fetchone()
                if policy is None:
                    raise ValueError("Parent acceptance policy is missing")
                policy = json.loads(policy["payload"])
                parent_policy_sha = digest(policy["criteria"])
                if parent_policy_sha != policy["sha256"]:
                    raise ValueError("Parent acceptance policy integrity check failed")
            receipt = {
                "request_sha256": request_sha,
                "request_key_sha256": key_sha,
                "parent_schema_sha256": expected_parent_schema_sha256,
                "parent_policy_id": head_id,
                "parent_policy_sha256": parent_policy_sha,
                "initial_policy_id": policy_id,
                "note": note.strip(),
                "owner": principal["actor"],
            }
            payload = {
                k: request[k]
                for k in (
                    "name",
                    "description",
                    "questions",
                    "data_requirements",
                    "parent_project",
                )
            }
            payload.update(
                policy_version=parent["policy_version"] + 1,
                schema_sha256=digest(questions),
                successor=receipt,
            )
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (child, "project", tenant, child, encode(payload), time.time()),
            )
            service.store.audit(
                db, tenant, child, principal["actor"], "create.project", child
            )
            if policy_id:
                policy = {"criteria": criteria, "sha256": digest(criteria)}
                db.execute(
                    "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    (
                        policy_id,
                        "release-policy",
                        tenant,
                        child,
                        encode(policy),
                        time.time(),
                    ),
                )
                db.execute(
                    "INSERT INTO release_policy_heads VALUES (?,?,?)",
                    (tenant, child, policy_id),
                )
                service.store.audit(
                    db,
                    tenant,
                    child,
                    principal["actor"],
                    "define.release-policy",
                    policy_id,
                )
            service.store.audit(
                db,
                tenant,
                parent_project,
                principal["actor"],
                "create.successor",
                child,
            )
    return service.project(principal, child)


def verify_receipt(store, entity):
    """Validate an optional successor receipt against retained immutable entities."""
    value = entity["payload"]
    receipt = value.get("successor")
    if receipt is None:
        return
    fields = {
        "request_sha256",
        "request_key_sha256",
        "parent_schema_sha256",
        "parent_policy_id",
        "parent_policy_sha256",
        "initial_policy_id",
        "note",
        "owner",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ValueError("Invalid policy successor receipt")
    tenant, project = entity["tenant"], entity["project"]
    parent = store.get(
        value["parent_project"], tenant, value["parent_project"], "project"
    )["payload"]
    if (
        value["policy_version"] != parent["policy_version"] + 1
        or receipt["parent_schema_sha256"] != parent["schema_sha256"]
    ):
        raise ValueError("Policy successor ancestry mismatch")
    for field in ("request_sha256", "request_key_sha256", "parent_schema_sha256"):
        if not isinstance(receipt[field], str) or not re.fullmatch(
            r"[a-f0-9]{64}", receipt[field]
        ):
            raise ValueError("Invalid policy successor receipt checksum")
    if (
        not isinstance(receipt["note"], str)
        or not 1 <= len(receipt["note"].strip()) <= 1000
        or not isinstance(receipt["owner"], str)
        or not receipt["owner"]
    ):
        raise ValueError("Invalid policy successor owner confirmation")
    parent_policy_sha = None
    if receipt["parent_policy_id"]:
        policy = store.get(
            receipt["parent_policy_id"],
            tenant,
            value["parent_project"],
            "release-policy",
        )["payload"]
        parent_policy_sha = digest(policy["criteria"])
        if policy["sha256"] != parent_policy_sha:
            raise ValueError("Parent acceptance policy checksum mismatch")
    if parent_policy_sha != receipt["parent_policy_sha256"]:
        raise ValueError("Parent policy successor binding mismatch")
    criteria = None
    if receipt["initial_policy_id"]:
        policy = store.get(
            receipt["initial_policy_id"], tenant, project, "release-policy"
        )["payload"]
        criteria = policy["criteria"]
        if (
            policy["sha256"] != digest(criteria)
            or normalize_criteria(value["questions"], **criteria) != criteria
        ):
            raise ValueError("Initial successor acceptance criteria mismatch")
    request = {
        k: value[k]
        for k in (
            "name",
            "description",
            "questions",
            "data_requirements",
            "parent_project",
        )
    }
    request.update(
        note=receipt["note"],
        expected_parent_schema_sha256=receipt["parent_schema_sha256"],
        expected_parent_policy_id=receipt["parent_policy_id"],
        criteria=criteria,
    )
    if digest(request) != receipt["request_sha256"]:
        raise ValueError("Policy successor request checksum mismatch")
