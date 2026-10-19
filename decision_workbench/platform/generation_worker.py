"""Supervised generation with response capture and fenced candidate publication."""

import base64
from datetime import datetime, timezone
import hashlib
import os
import socket
import threading
import time

from decision_workbench.synthetic_contracts import (
    candidate_rows,
    completion,
    request_body,
    response_metadata,
)
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.resources import ResourceLease
from .generation_plans import (
    RECORD_KINDS,
    load,
    record,
    record_id,
    sealed,
    teacher_spec,
)
from .generation_profiles import teacher_profile, token_cost
from .store import Conflict, encode


def response_values(data, process_sha256, profile, received_at):
    meta = response_metadata(data)
    usage = meta["usage"]
    return {
        "raw_base64": base64.b64encode(data).decode("ascii"),
        "response_sha256": hashlib.sha256(data).hexdigest(),
        "process_sha256": process_sha256,
        "received_at": received_at,
        **meta,
        "reported_cost_usd": (
            None
            if usage is None
            else token_cost(usage["prompt_tokens"], usage["completion_tokens"], profile)
        ),
    }


def expected_rows(plan, seeds, response, identifier):
    data = base64.b64decode(response["raw_base64"], validate=True)
    parsed = completion(data)
    return candidate_rows(
        plan["policy"],
        seeds,
        parsed,
        plan["selector"]["count"],
        batch_id=identifier,
        created_at=datetime.fromtimestamp(
            response["received_at"], timezone.utc
        ).isoformat(),
        provenance={
            "teacher_model": plan["profile"]["model"],
            "generation_plan_id": identifier,
            "generation_plan_sha256": plan["sha256"],
            "request_sha256": plan["request_sha256"],
            "prompt_sha256": plan["request_sha256"],
            "policy_sha256": plan["policy_sha256"],
            "policy_version": plan["policy"]["policy_version"],
            "snapshot_id": plan["selector"]["snapshot"],
            "response_sha256": response["response_sha256"],
        },
    )


def provenance(plan, identifier, response):
    return {
        "kind": "generated-v2",
        "generation_plan": identifier,
        "generation_plan_sha256": plan["sha256"],
        "snapshot": plan["selector"]["snapshot"],
        "prompt_sha256": plan["request_sha256"],
        "teacher_model": plan["profile"]["model"],
        "response_sha256": response["response_sha256"],
    }


def verify_history(store, tenant, project, entity):
    """Validate saved artifacts without current teacher credentials or prompt code."""
    identifier = entity["id"]
    plan = entity["payload"]
    entity, job, seeds, _ = load(
        store, tenant, project, {"id": identifier, "sha256": plan["sha256"]}
    )
    process = record(
        store, tenant, project, "generation-process", identifier, plan["sha256"]
    )
    response = record(
        store, tenant, project, "generation-response", identifier, plan["sha256"]
    )
    result = record(
        store, tenant, project, "generation-result", identifier, plan["sha256"]
    )
    settlement = record(
        store, tenant, project, "generation-settlement", identifier, plan["sha256"]
    )
    if response:
        if (
            not process
            or response["payload"]["process_sha256"] != process["payload"]["sha256"]
        ):
            raise ValueError("Generation response has no matching process receipt")
        r = response["payload"]
        data = base64.b64decode(r["raw_base64"], validate=True)
        if {
            k: v
            for k, v in r.items()
            if k not in {"format", "plan_id", "plan_sha256", "sha256"}
        } != response_values(
            data, process["payload"]["sha256"], plan["profile"], r["received_at"]
        ):
            raise ValueError("Archived teacher response usage or identity changed")
    if result:
        if not response:
            raise ValueError("A generated dataset has no archived teacher response")
        expected = expected_rows(plan, seeds, response["payload"], identifier)
        binding = result["payload"]
        dataset = store.get(
            binding["dataset_id"], tenant, project, "dataset", include_rows=False
        )["payload"]
        rows = list(store.verify_dataset(binding["dataset_id"], tenant, project))
        if (
            binding["rows"] != len(expected)
            or binding["response_sha256"] != response["payload"]["response_sha256"]
            or dataset["sha256"] != binding["dataset_sha256"]
            or dataset["provenance"]
            != provenance(plan, identifier, response["payload"])
            or rows != expected
        ):
            raise ValueError(
                "Generated candidate dataset differs from its approved request and response"
            )
        expected_result = {
            "dataset": binding["dataset_id"],
            "rows": len(expected),
            "generation_plan": identifier,
            "response_sha256": response["payload"]["response_sha256"],
        }
        if job["status"] == "succeeded" and job["result"] != expected_result:
            raise ValueError(
                "Successful generation job differs from its published candidate dataset"
            )
    elif job["status"] == "succeeded":
        raise ValueError("Successful generation has no atomic candidate result")
    if settlement and (
        not process
        or response
        or job["status"] in ("queued", "running", "cancel_requested")
        or settlement["payload"]["job_id"] != job["id"]
        or settlement["payload"]["process_sha256"] != process["payload"]["sha256"]
    ):
        raise ValueError(
            "Generation outcome settlement is inconsistent with its terminal attempt"
        )
    return {
        "process": process,
        "response": response,
        "result": result,
        "settlement": settlement,
    }


def validate_history(store, entities):
    """Validate both directions of generation references in a stopped workspace."""
    histories = {}
    children = set()
    for entity in entities.values():
        if entity["kind"] != "generation-plan":
            continue
        parent = entity["payload"].get("selector", {}).get("retry_of")
        if parent:
            key = (entity["tenant"], entity["project"], parent)
            if key in children:
                raise ValueError("A generation request has competing retry attempts")
            children.add(key)
        histories[entity["id"]] = verify_history(
            store, entity["tenant"], entity["project"], entity
        )
    for entity in entities.values():
        if entity["kind"] in RECORD_KINDS:
            identifier = entity["payload"].get("plan_id")
            plan = entities.get(identifier)
            if (
                not plan
                or plan["kind"] != "generation-plan"
                or (entity["tenant"], entity["project"])
                != (plan["tenant"], plan["project"])
                or entity["id"] != record_id(entity["kind"], identifier)
            ):
                raise ValueError(
                    "A generation history record has no matching scoped request"
                )
            record(
                store,
                entity["tenant"],
                entity["project"],
                entity["kind"],
                identifier,
                plan["payload"]["sha256"],
            )
        elif (
            entity["kind"] == "dataset"
            and isinstance(entity["payload"].get("provenance"), dict)
            and entity["payload"].get("provenance", {}).get("kind") == "generated-v2"
        ):
            identifier = entity["payload"]["provenance"].get("generation_plan")
            plan = entities.get(identifier)
            result = histories.get(identifier, {}).get("result")
            if (
                not plan
                or (entity["tenant"], entity["project"])
                != (plan["tenant"], plan["project"])
                or not result
                or result["payload"]["dataset_id"] != entity["id"]
            ):
                raise ValueError(
                    "A generated dataset has no matching atomic generation result"
                )
    with store.transaction() as db:
        jobs = db.execute("SELECT * FROM jobs WHERE kind='generate'").fetchall()
    for row in jobs:
        job = store.decode_job(row)
        binding = job["config"].get("generation_plan")
        if binding is None:
            # Old requests retain their history; workers refuse unapproved dispatch.
            continue
        plan = entities.get(binding.get("id")) if isinstance(binding, dict) else None
        if not plan or plan["kind"] != "generation-plan":
            raise ValueError("A generation job has no archived approved request")
        _, approved_job, _, _ = load(store, job["tenant"], job["project"], binding)
        if approved_job["id"] != job["id"]:
            raise ValueError("A generation job reuses another request's approval")


def run(worker, job, cancelled):
    from .hosted_transport import run_process, TransportFailure

    binding = job["config"].get("generation_plan")
    if not binding:
        raise ValueError(
            "This historical generation job has no approved request archive; preview and approve a new request"
        )
    entity, saved_job, seeds, body = load(
        worker.store, job["tenant"], job["project"], binding, require_current=True
    )
    plan = entity["payload"]
    identifier = entity["id"]
    profile = plan["profile"]
    if (
        saved_job["id"] != job["id"]
        or profile != teacher_profile()
        or plan["source_sha256"] != source_fingerprint()
        or body
        != request_body(
            teacher_spec(plan["policy"], plan["selector"]["coverage"]),
            seeds,
            profile["model"],
            plan["selector"]["count"],
            output_tokens=profile["max_output_tokens"],
            token_parameter=profile["token_parameter"],
            temperature=profile["temperature"],
            output_format=profile.get("response_format", "json_object"),
        )
    ):
        raise ValueError(
            "Teacher, source or approved request changed; inspect this request before approving another attempt"
        )
    if any(
        record(
            worker.store,
            job["tenant"],
            job["project"],
            kind,
            identifier,
            plan["sha256"],
        )
        for kind in (
            "generation-process",
            "generation-response",
            "generation-result",
            "generation-settlement",
        )
    ):
        raise Conflict(
            "This generation attempt already has dispatch history; it cannot contact the teacher again"
        )
    if cancelled.is_set():
        raise Conflict("Generation cancelled before dispatch")
    watch_stop = threading.Event()
    watcher = None
    process_receipt = None

    def started(process, token):
        nonlocal process_receipt
        payload = sealed(
            "generation-process",
            identifier,
            plan["sha256"],
            {
                "host_sha256": hashlib.sha256(
                    socket.gethostname().encode()
                ).hexdigest(),
                "pid": process.pid,
                "parent_pid": os.getpid(),
                "token": token,
                "started_at": time.time(),
            },
        )
        # run_process does not write approved data/key to the child until this commits.
        process_receipt = worker.store.add(
            "generation-process",
            job["tenant"],
            job["project"],
            job["actor"],
            payload,
            record_id("generation-process", identifier),
            job=job,
        )

    def observe(process):
        nonlocal watcher
        if cancelled.is_set():
            if process.poll() is None:
                process.kill()
            return

        def watch():
            while not watch_stop.wait(0.05):
                if cancelled.is_set():
                    if process.poll() is None:
                        process.kill()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()

    try:
        lease = ResourceLease(worker.store.folder / "synthetic-teacher.lock")
        with lease.hold(timeout=2, cancelled=cancelled):
            worker.store.heartbeat(
                job,
                "Sending the approved policy and selected training seeds to the teacher",
            )
            data = run_process(
                profile,
                body,
                os.environ.get("DMT_TEACHER_API_KEY", ""),
                observe=observe,
                record_started=started,
                lease=lease,
            )
        if cancelled.is_set():
            raise Conflict(
                "Generation cancelled; a teacher request may still have been processed"
            )
        if process_receipt is None:
            raise ValueError("Generation transport has no committed process receipt")
        payload = sealed(
            "generation-response",
            identifier,
            plan["sha256"],
            response_values(
                data, process_receipt["payload"]["sha256"], profile, time.time()
            ),
        )
        saved = worker.store.add(
            "generation-response",
            job["tenant"],
            job["project"],
            job["actor"],
            payload,
            record_id("generation-response", identifier),
            job=job,
        )
        rows = expected_rows(plan, seeds, saved["payload"], identifier)
        if cancelled.is_set():
            raise Conflict("Generation cancelled before candidate publication")

        def finalize(db, dataset_id, dataset_payload):
            result = sealed(
                "generation-result",
                identifier,
                plan["sha256"],
                {
                    "dataset_id": dataset_id,
                    "dataset_sha256": dataset_payload["sha256"],
                    "response_sha256": payload["response_sha256"],
                    "rows": len(rows),
                },
            )
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    record_id("generation-result", identifier),
                    "generation-result",
                    job["tenant"],
                    job["project"],
                    encode(result),
                    time.time(),
                ),
            )
            worker.store.audit(
                db,
                job["tenant"],
                job["project"],
                job["actor"],
                "publish.generation-result",
                identifier,
            )

        dataset = worker.store.add_dataset(
            job["tenant"],
            job["project"],
            job["actor"],
            rows,
            provenance(plan, identifier, payload),
            job=job,
            finalize=finalize,
        )
        return {
            "dataset": dataset["id"],
            "rows": len(rows),
            "generation_plan": identifier,
            "response_sha256": payload["response_sha256"],
        }
    except TransportFailure:
        raise ValueError(
            "Generation transport failed or timed out; the teacher may have processed the request. Inspect and close the outcome before approving a retry"
        ) from None
    finally:
        watch_stop.set()
        if watcher is not None:
            watcher.join(timeout=2)
