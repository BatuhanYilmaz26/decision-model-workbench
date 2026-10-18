"""HTTP application boundary. Configure hashed keys before startup."""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    UploadFile,
    Query,
    Form,
)
from fastapi.responses import JSONResponse, Response, FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from decision_workbench.data import jsonl
from .store import Store, Conflict, digest
from .service import Service
from .paging import MAX_OFFSET
from .runtime_broker import RuntimeUnavailable
from .cloud_aws import CloudUnavailable
from .operations import OperationsMonitor
from .auth import (
    Authentication,
    AuthenticationError,
    AuthenticationUnavailable,
    UnmappedIdentity,
)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ProjectBody(Body):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=12000)
    questions: dict[str, Any]
    parent_project: str | None = None
    data_requirements: str = Field(default="", max_length=4000)


class SchemaIntakeBody(Body):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=12000)
    data_requirements: str = Field(default="", max_length=4000)
    request_key: str = Field(min_length=8, max_length=128)
    data_approved: bool
    cost_approved: bool


class ReservationPreviewBody(Body):
    datasets: list[str] = Field(min_length=1, max_length=20)
    semantic_audit: str | None = None
    policy: dict[str, Any] | None = None


class GenerationPreviewBody(Body):
    snapshot: str = Field(min_length=1, max_length=128)
    count: int = Field(ge=1, le=50)
    seed_count: int = Field(ge=0, le=10)
    coverage: str = Field(default="", max_length=4000)
    retry_of: str | None = Field(default=None, min_length=1, max_length=128)


class GenerationSettlementBody(Body):
    expected_status_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    local_exit_confirmed: bool
    upstream_complete_confirmed: bool
    evidence_ref: str = Field(min_length=1, max_length=500)
    note: str = Field(min_length=1, max_length=1000)


class SchemaConfirmBody(Body):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=12000)
    data_requirements: str = Field(default="", max_length=4000)
    questions: dict[str, Any]
    expected_proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool


class ReviewBody(Body):
    record_id: str
    gold: dict[str, Any]
    approved: bool = False
    status: Literal["pending", "approved", "rejected"] | None = None
    expected_hash: str
    note: str = Field(min_length=1, max_length=1000)
    assignment_revision: int | None = Field(default=None, ge=0, strict=True)


class AssignmentTarget(Body):
    record_id: str = Field(min_length=1, max_length=256)
    expected_revision: int = Field(ge=0, strict=True)


class AssignmentBody(Body):
    targets: list[AssignmentTarget] = Field(min_length=1, max_length=100)
    assignee: str | None = Field(min_length=1, max_length=256)
    expected_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=1, max_length=1000)


class SemanticReviewBody(Body):
    pair_id: str
    decision: Literal["related", "independent"]
    expected_revision: int = Field(ge=0)
    expected_hash: str
    note: str = Field(min_length=1, max_length=1000)


class TaxonomyProposalBody(Body):
    dataset: str
    question_id: str
    record_ids: list[str] = Field(min_length=1, max_length=20)
    candidates: list[dict[str, Any]] = Field(min_length=1, max_length=20)


class TaxonomyReviewBody(Body):
    candidate_id: str
    decision: Literal["add", "alias", "reject"]
    canonical_id: str | None = None
    description: str = Field(default="", max_length=1000)
    expected_revision: int = Field(ge=0)
    expected_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=1, max_length=1000)


class TaxonomyConfirmBody(Body):
    expected_state_sha256: str = Field(min_length=64, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=1000)


class ExamplesBody(Body):
    examples: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class AppendExamplesBody(ExamplesBody):
    expected_hash: str = Field(min_length=64, max_length=64)


class GateReviewBody(Body):
    record_id: str = Field(min_length=1, max_length=256)
    negative_for: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)
    status: Literal["pending", "approved", "rejected"]
    expected_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(min_length=1, max_length=1000)


class JobBody(Body):
    kind: str
    config: dict[str, Any]
    request_key: str


class ReleaseBody(Body):
    model_id: str
    expected_revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=1000)


class ReleasePolicyBody(Body):
    vega: dict[str, Any] | None = None
    error_policy: dict[str, Any] | None = None
    min_accuracy: float = Field(default=0.8, ge=0, le=1)
    min_macro_f1: float = Field(default=0.8, ge=0, le=1)
    max_ece: float = Field(default=0.1, ge=0, le=1)
    min_test_rows: int = Field(default=100, ge=10)
    expected_id: str | None = None
    min_training_examples_per_class: int = Field(default=5, ge=0, le=100000)
    min_calibration_examples_per_class: int = Field(default=1, ge=0, le=100000)


class SuccessorBody(Body):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=12000)
    questions: dict[str, Any]
    data_requirements: str = Field(default="", max_length=4000)
    expected_parent_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_parent_policy_id: str | None
    criteria: ReleasePolicyBody | None = None
    note: str = Field(min_length=1, max_length=1000)
    request_key: str = Field(min_length=1, max_length=128)


class IncidentActionBody(Body):
    revision: int = Field(ge=1)
    action: Literal["acknowledge", "resolve"]
    note: str = Field(min_length=1, max_length=1000)


class AuditExportBody(Body):
    note: str = Field(min_length=1, max_length=1000)
    request_key: str = Field(min_length=1, max_length=128)
    after_id: int = Field(default=0, ge=0, le=9223372036854775807)
    until_id: int | None = Field(default=None, ge=0, le=9223372036854775807)


class ProviderReconciliationBody(Body):
    expected_outcome_sha256: str = Field(min_length=64, max_length=64)
    local_transport_stopped: bool
    provider_finished: bool
    reference: str = Field(min_length=1, max_length=500)
    note: str = Field(min_length=1, max_length=1000)


class CloudRunBody(Body):
    study: str = Field(min_length=32, max_length=32)
    profile_id: str = Field(min_length=1, max_length=64)
    data_approved: bool
    compute_approved: bool
    request_key: str = Field(min_length=8, max_length=128)


class CloudActionBody(Body):
    action: Literal["submit", "observe", "stop", "abandon", "retrieve"]
    revision: int = Field(ge=1)
    request_key: str = Field(min_length=8, max_length=128)


class DecisionBody(Body):
    state: Any
    request_key: str | None = Field(default=None, min_length=8, max_length=128)
    threshold: float = Field(default=0.8, ge=0, le=1)


def create_app(home=None, keys=None, hosted=None):
    auth = Authentication(
        keys=keys,
        keys_file=os.environ.get("DMT_KEYS_FILE") if keys is None else None,
        sso_file=os.environ.get("DMT_SSO_FILE") if keys is None else None,
    )
    store = Store(home or os.environ.get("DMT_HOME", "data/workspace"))
    service = Service(store, hosted=hosted)
    monitor = OperationsMonitor(store)

    @asynccontextmanager
    async def lifespan(app):
        monitor.start()
        try:
            yield
        finally:
            try:
                monitor.close()
            finally:
                service.runtime.close()

    app = FastAPI(title="Domain Decision Platform", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.service = service
    app.state.monitor = monitor

    app.state.authentication = auth

    def identity(authorization: str | None = Header(default=None)):
        return auth.authenticate(authorization)

    def authorize_again(authorization, expected):
        if auth.authenticate(authorization) != expected:
            raise AuthenticationError(
                "Credential identity or permissions changed during the request"
            )

    @app.exception_handler(AuthenticationError)
    async def unauthorized(request, exc):
        return JSONResponse(
            {"detail": str(exc)},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthenticationUnavailable)
    async def authentication_unavailable(request, exc):
        return JSONResponse(
            {"detail": str(exc)}, status_code=503, headers={"Retry-After": "5"}
        )

    @app.exception_handler(UnmappedIdentity)
    async def identity_not_mapped(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request_id = uuid.uuid4().hex
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > 11 * 1024 * 1024:
                return JSONResponse(
                    {"detail": "Request exceeds 11 MiB"}, status_code=413
                )
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        parts = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 11 * 1024 * 1024:
                return JSONResponse(
                    {"detail": "Request exceeds 11 MiB"}, status_code=413
                )
            parts.append(chunk)
        request._body = b"".join(parts)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(
            {"detail": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 400
        )

    from .hosted import HostedUnavailable, HostedQuota

    @app.exception_handler(HostedUnavailable)
    async def hosted_unavailable(request, exc):
        return JSONResponse(
            {"detail": str(exc)},
            status_code=429 if isinstance(exc, HostedQuota) else 503,
        )

    @app.get("/v1/projects/{project}/decision-profiles")
    def decision_profiles(project: str, p=Depends(identity)):
        service.require(p, ("admin", "editor"))
        service.project(p, project)
        from .hosted import profiles, public_profile

        return [
            public_profile(value)
            for value in profiles()
            if p["tenant"] in value["tenants"]
        ]

    @app.get("/v1/projects/{project}/provider-calls")
    def provider_calls(
        project: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        calls = store.list(p["tenant"], project, "provider-call", limit, offset)
        for entry in calls:
            result_id = digest({"call": entry["id"]})[:32]
            try:
                result = store.get(
                    result_id, p["tenant"], project, "provider-call-result"
                )["payload"]
            except LookupError:
                result = {"status": "pending_or_unknown"}
            entry["outcome"] = result
            entry["outcome_sha256"] = digest(result)
            try:
                entry["transport"] = store.get(
                    digest({"provider_call_transport": entry["id"]})[:32],
                    p["tenant"],
                    project,
                    "provider-call-transport",
                )["payload"]
            except LookupError:
                entry["transport"] = None
            from .hosted import reconciliation_id

            try:
                entry["reconciliation"] = store.get(
                    reconciliation_id(entry["id"]),
                    p["tenant"],
                    project,
                    "provider-call-reconciliation",
                )["payload"]
            except LookupError:
                entry["reconciliation"] = None
            entry["capacity_held"] = (
                result["status"] != "succeeded" and entry["reconciliation"] is None
            )
        return calls

    @app.post("/v1/projects/{project}/provider-calls/{call_id}/reconcile")
    def reconcile_provider_call(
        project: str,
        call_id: str,
        body: ProviderReconciliationBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        from .hosted import reconcile_call

        return reconcile_call(
            store,
            p,
            project,
            call_id,
            **body.model_dump(),
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.exception_handler(CloudUnavailable)
    @app.exception_handler(RuntimeUnavailable)
    async def model_unavailable(request, exc):
        return JSONResponse(
            {"detail": str(exc)}, status_code=503, headers={"Retry-After": "5"}
        )

    @app.exception_handler(LookupError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(PermissionError)
    async def denied(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.get("/health")
    def health():
        return {"status": "alive"}

    @app.get("/ready")
    def ready():
        from .hosted import profiles

        profiles()
        from .taxonomy_provider import profiles as taxonomy_profiles

        taxonomy_profiles("", "")
        auth.configuration()
        if monitor.state()["status"] != "healthy":
            raise HTTPException(503, "Operational collector is unavailable")
        backup_health = monitor.state()["backup"]
        if backup_health["required"] and not backup_health["healthy"]:
            raise HTTPException(503, "Required backup monitoring is unhealthy")
        with store.transaction() as db:
            db.execute("SELECT 1").fetchone()
        return {
            "status": "ready",
            "storage": "sqlite-local",
            "authentication_configuration": "valid",
            "operational_collector": "healthy",
            "gpu_and_cloud_acceptance": "pending",
        }

    @app.get("/v1/me")
    def me(p=Depends(identity)):
        return p

    @app.get("/v1/runtime-capabilities")
    def runtime_capabilities(p=Depends(identity)):
        if p["role"] != "admin":
            raise PermissionError(
                "Only workspace owners can inspect runtime configuration"
            )
        from .capabilities import runtime_capabilities as inspect

        return inspect()

    @app.get("/v1/projects")
    def projects(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        return store.list(p["tenant"], kind="project", limit=limit, offset=offset)

    @app.get("/v1/schema-teacher")
    def schema_teacher(p=Depends(identity)):
        service.require(p, ("admin",))
        from .schema_intake import teacher_profile

        try:
            return {"available": True, "teacher": teacher_profile()}
        except ValueError as exc:
            return {"available": False, "reason": str(exc)}

    @app.get("/v1/generation-teacher")
    def generation_teacher(p=Depends(identity)):
        service.require(p, ("admin",))
        from .generation_profiles import teacher_profile

        try:
            return {"available": True, "teacher": teacher_profile()}
        except ValueError as exc:
            return {"available": False, "reason": str(exc)}

    @app.post("/v1/schema-intakes", status_code=202)
    def new_schema_intake(
        body: SchemaIntakeBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .schema_intake import submit

        values = body.model_dump()
        return submit(
            service,
            p,
            {k: values.pop(k) for k in ("name", "description", "data_requirements")},
            **values,
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.get("/v1/schema-intakes")
    def schema_intakes(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        return store.list(p["tenant"], kind="schema-intake", limit=limit, offset=offset)

    @app.get("/v1/schema-intakes/{intake}")
    def schema_intake(intake: str, p=Depends(identity)):
        from .schema_intake import view

        return view(service, p, intake)

    @app.post("/v1/schema-intakes/{intake}/cancel")
    def cancel_schema_intake(intake: str, p=Depends(identity)):
        from .schema_intake import view

        value = view(service, p, intake)
        store.cancel(value["job"]["id"], p["tenant"], intake, p["actor"])
        return view(service, p, intake)

    @app.post("/v1/schema-intakes/{intake}/confirm", status_code=201)
    def confirm_schema_intake(
        intake: str,
        body: SchemaConfirmBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .schema_intake import confirm

        values = body.model_dump()
        return confirm(
            service,
            p,
            intake,
            {k: values.pop(k) for k in ("name", "description", "data_requirements")},
            proposed_questions=values.pop("questions"),
            **values,
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.post("/v1/projects", status_code=201)
    def new_project(body: ProjectBody, p=Depends(identity)):
        return service.create_project(p, **body.model_dump())

    @app.get("/v1/projects/{project}")
    def get_project(project: str, p=Depends(identity)):
        return service.project(p, project)

    @app.post("/v1/projects/{project}/successors", status_code=201)
    def new_successor(project: str, body: SuccessorBody, p=Depends(identity)):
        from .successors import create

        return create(service, p, project, **body.model_dump())

    @app.get("/v1/projects/{project}/entities/{kind}")
    def entities(
        project: str,
        kind: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        service.project(p, project)
        if kind not in (
            "dataset",
            "gate-dataset",
            "gate-snapshot",
            "snapshot",
            "reservation-plan",
            "model",
            "remote-study",
            "semantic-audit",
            "taxonomy-proposal",
            "taxonomy-discovery",
            "taxonomy-commit",
        ):
            raise HTTPException(404, "Resource kind not found")
        if p["role"] == "inference":
            raise PermissionError(
                "Inference credentials cannot read datasets or artifacts"
            )
        return store.list(p["tenant"], project, kind, limit=limit, offset=offset)

    @app.get("/v1/projects/{project}/datasets/{dataset}/rows")
    def dataset_rows(
        project: str,
        dataset: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        record_id: str | None = Query(default=None, min_length=1, max_length=256),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor", "reviewer"))
        service.project(p, project)
        entity = store.get(dataset, p["tenant"], project, "dataset", include_rows=False)
        return {
            "dataset": entity,
            "rows": store.dataset_rows(
                dataset,
                p["tenant"],
                project,
                limit=limit,
                offset=offset,
                record_id=record_id,
            ),
            "total": entity["payload"]["row_count"],
            "head": store.dataset_head(dataset, p["tenant"], project),
            "offset": offset,
            "limit": limit,
        }

    @app.get("/v1/projects/{project}/release-policy")
    def release_policy(project: str, p=Depends(identity)):
        service.project(p, project)
        service.require(p, ("admin", "editor", "reviewer"))
        return store.release_policy(p["tenant"], project)

    @app.put("/v1/projects/{project}/release-policy")
    def configure_policy(project: str, body: ReleasePolicyBody, p=Depends(identity)):
        return service.configure_release_policy(p, project, **body.model_dump())

    @app.post("/v1/projects/{project}/datasets", status_code=201)
    async def upload(
        project: str,
        file: UploadFile,
        mapping: str | None = Form(default=None),
        dataset: str | None = Form(default=None),
        expected_hash: str | None = Form(default=None),
        worksheet: str | None = Form(default=None, max_length=256),
        preview_sha256: str | None = Form(default=None, min_length=64, max_length=64),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor"))
        body = await file.read(10 * 1024 * 1024 + 1)
        if mapping is not None and len(mapping) > 32000:
            raise ValueError("Mapping configuration is limited to 32 KB")
        return await run_in_threadpool(
            service.upload,
            p,
            project,
            body,
            file.filename or "upload",
            parse_import_mapping(mapping),
            dataset,
            expected_hash,
            worksheet,
            preview_sha256,
        )

    def parse_import_mapping(mapping):
        if mapping is not None:
            if len(mapping) > 32000:
                raise ValueError("Mapping configuration is limited to 32 KB")
            from decision_workbench.json_tools import strict_json

            try:
                parsed = strict_json(mapping)
                if not isinstance(parsed, dict):
                    raise ValueError("Mapping configuration must be a JSON object")
                return parsed
            except RecursionError as exc:
                raise ValueError("Mapping JSON is nested too deeply") from exc
        return None

    @app.post("/v1/projects/{project}/imports/preview")
    async def preview_upload(
        project: str,
        file: UploadFile,
        mapping: str | None = Form(default=None),
        dataset: str | None = Form(default=None),
        expected_hash: str | None = Form(default=None),
        worksheet: str | None = Form(default=None, max_length=256),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor"))
        content = await file.read(10 * 1024 * 1024 + 1)
        return await run_in_threadpool(
            service.preview_upload,
            p,
            project,
            content,
            file.filename or "upload",
            parse_import_mapping(mapping),
            dataset,
            expected_hash,
            worksheet,
        )

    @app.post("/v1/projects/{project}/imports/inspect")
    async def inspect_upload(
        project: str,
        file: UploadFile,
        worksheet: str | None = Form(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor", "reviewer"))
        content = await file.read(10 * 1024 * 1024 + 1)
        return await run_in_threadpool(
            service.inspect_upload,
            p,
            project,
            content,
            file.filename or "upload",
            worksheet,
        )

    @app.post("/v1/projects/{project}/examples", status_code=201)
    def examples(project: str, body: ExamplesBody, p=Depends(identity)):
        return service.save_examples(p, project, body.examples)

    @app.post("/v1/projects/{project}/datasets/{dataset}/examples", status_code=201)
    def append_examples(
        project: str, dataset: str, body: AppendExamplesBody, p=Depends(identity)
    ):
        return service.save_examples(
            p, project, body.examples, dataset, body.expected_hash
        )

    @app.get("/v1/projects/{project}/datasets/{dataset}/quality")
    def quality(project: str, dataset: str, p=Depends(identity)):
        return service.dataset_quality(p, project, dataset)

    @app.post("/v1/projects/{project}/gate-examples", status_code=201)
    def gate_examples(project: str, body: ExamplesBody, p=Depends(identity)):
        return service.gates.examples(p, project, body.examples)

    @app.post(
        "/v1/projects/{project}/gate-datasets/{dataset}/examples", status_code=201
    )
    def append_gate_examples(
        project: str, dataset: str, body: AppendExamplesBody, p=Depends(identity)
    ):
        return service.gates.examples(
            p, project, body.examples, dataset, body.expected_hash
        )

    @app.post("/v1/projects/{project}/gate-datasets", status_code=201)
    async def gate_upload(
        project: str,
        file: UploadFile,
        mapping: str | None = Form(default=None),
        dataset: str | None = Form(default=None),
        expected_hash: str | None = Form(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor"))
        service.project(p, project)
        body = await file.read(10 * 1024 * 1024 + 1)
        if mapping is not None and len(mapping) > 32000:
            raise ValueError("Mapping configuration is limited to 32 KB")
        return await run_in_threadpool(
            service.gates.upload,
            p,
            project,
            body,
            file.filename or "upload",
            json.loads(mapping) if mapping is not None else None,
            dataset,
            expected_hash,
        )

    @app.get("/v1/projects/{project}/gate-datasets/{dataset}/rows")
    def gate_rows(
        project: str,
        dataset: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        return service.gates.page(p, project, dataset, limit, offset)

    @app.post("/v1/projects/{project}/gate-datasets/{dataset}/reviews", status_code=201)
    def gate_review(
        project: str, dataset: str, body: GateReviewBody, p=Depends(identity)
    ):
        return service.gates.review(p, project, dataset, **body.model_dump())

    @app.get("/v1/projects/{project}/gate-datasets/{dataset}/download")
    def gate_download(project: str, dataset: str, p=Depends(identity)):
        service.require(p, ("admin", "editor", "reviewer"))
        service.project(p, project)
        rows = store.verify_dataset(dataset, p["tenant"], project, kind="gate-dataset")
        return Response(
            jsonl(rows),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": "attachment; filename=applicability-examples.jsonl"
            },
        )

    @app.get("/v1/projects/{project}/gate-snapshots/{snapshot}/download")
    def gate_study_download(
        project: str,
        snapshot: str,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        import hashlib

        content = service.gates.archive(p, project, snapshot)
        authorize_again(authorization, p)
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=vega-study-inputs.zip",
                "X-DMT-Archive-SHA256": hashlib.sha256(content).hexdigest(),
            },
        )

    @app.get("/v1/projects/{project}/taxonomy-profiles")
    def taxonomy_profiles(project: str, p=Depends(identity)):
        service.require(p, ("admin", "editor", "reviewer"))
        service.project(p, project)
        from .taxonomy_provider import profiles, public_profile

        return [public_profile(v) for v in profiles(p["tenant"], project)]

    @app.post("/v1/projects/{project}/taxonomy-proposals", status_code=201)
    def taxonomy_propose(project: str, body: TaxonomyProposalBody, p=Depends(identity)):
        service.require(p, ("admin", "editor"))
        service.project(p, project)
        from .taxonomy import build_proposal

        return build_proposal(
            store,
            p,
            project,
            **body.model_dump(),
            provenance={"mode": "manual", "actor": p["actor"]},
        )

    @app.get("/v1/projects/{project}/taxonomy-proposals/{proposal}")
    def taxonomy_page(project: str, proposal: str, p=Depends(identity)):
        from .taxonomy import page

        return page(service, p, project, proposal)

    @app.post(
        "/v1/projects/{project}/taxonomy-proposals/{proposal}/reviews", status_code=201
    )
    def taxonomy_review(
        project: str, proposal: str, body: TaxonomyReviewBody, p=Depends(identity)
    ):
        from .taxonomy import adjudicate

        return adjudicate(service, p, project, proposal, **body.model_dump())

    @app.post(
        "/v1/projects/{project}/taxonomy-proposals/{proposal}/confirm", status_code=201
    )
    def taxonomy_confirm(
        project: str,
        proposal: str,
        body: TaxonomyConfirmBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .taxonomy import confirm

        return confirm(
            service,
            p,
            project,
            proposal,
            **body.model_dump(),
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.post(
        "/v1/projects/{project}/taxonomy-discoveries/{call}/settle", status_code=201
    )
    def taxonomy_settle(
        project: str,
        call: str,
        body: ProviderReconciliationBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .taxonomy_provider import settle

        return settle(
            service,
            p,
            project,
            call,
            **body.model_dump(),
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.get("/v1/projects/{project}/taxonomy-discoveries/{call}")
    def taxonomy_outcome(project: str, call: str, p=Depends(identity)):
        service.require(p, ("admin",))
        service.project(p, project)
        record = store.get(call, p["tenant"], project, "taxonomy-discovery")
        try:
            outcome = store.get(
                record["payload"]["result_id"], p["tenant"], project, "taxonomy-result"
            )["payload"]
        except LookupError:
            outcome = {"status": "pending_or_unknown"}
        return {"call": record, "outcome": outcome, "outcome_sha256": digest(outcome)}

    @app.get("/v1/projects/{project}/semantic-profile")
    def semantic_profile(project: str, p=Depends(identity)):
        service.require(p, ("admin", "editor", "reviewer"))
        service.project(p, project)
        from .semantic import profile_for, public_profile

        profile = profile_for(p["tenant"], project)
        return {"profile": public_profile(profile) if profile else None}

    @app.get("/v1/projects/{project}/semantic-audits/{audit}/pairs")
    def semantic_pairs(
        project: str,
        audit: str,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        from .semantic import audit_page

        return audit_page(service, p, project, audit, limit, offset)

    @app.post("/v1/projects/{project}/semantic-audits/{audit}/reviews", status_code=201)
    def semantic_review(
        project: str, audit: str, body: SemanticReviewBody, p=Depends(identity)
    ):
        from .semantic import review

        return review(service, p, project, audit, **body.model_dump())

    @app.post("/v1/projects/{project}/datasets/{dataset}/reviews", status_code=201)
    def review(project: str, dataset: str, body: ReviewBody, p=Depends(identity)):
        return service.review(p, project, dataset, **body.model_dump())

    @app.post(
        "/v1/projects/{project}/datasets/{dataset}/review-assignments", status_code=201
    )
    def assign_reviews(
        project: str, dataset: str, body: AssignmentBody, p=Depends(identity)
    ):
        return service.review_assignments.assign(
            p, project, dataset, **body.model_dump()
        )

    @app.get("/v1/projects/{project}/review-assignments")
    def review_queue(
        project: str,
        mine: bool = False,
        status: Literal["assigned", "completed", "cancelled"] | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        return service.review_assignments.queue(
            p, project, mine=mine, status=status, limit=limit, offset=offset
        )

    @app.get("/v1/projects/{project}/datasets/{dataset}/review-assignments")
    def dataset_assignments(
        project: str,
        dataset: str,
        record_id: str | None = Query(default=None, min_length=1, max_length=256),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        return service.review_assignments.queue(
            p, project, dataset=dataset, record_id=record_id, limit=limit, offset=offset
        )

    @app.get("/v1/projects/{project}/datasets/{dataset}/review-assignments/history")
    def assignment_history(
        project: str,
        dataset: str,
        record_id: str = Query(min_length=1, max_length=256),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        return service.review_assignments.history(
            p, project, dataset, record_id, limit, offset
        )

    @app.get("/v1/projects/{project}/datasets/{dataset}/download")
    def download(project: str, dataset: str, p=Depends(identity)):
        service.require(p, ("admin", "editor", "reviewer"))
        service.project(p, project)
        rows = store.verify_dataset(dataset, p["tenant"], project)
        return Response(
            jsonl(rows),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=dataset.jsonl"},
        )

    @app.get("/v1/projects/{project}/snapshots/{snapshot}/download")
    def snapshot_inputs(
        project: str,
        snapshot: str,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .snapshot_export import study_archive

        body = study_archive(service, p, project, snapshot)
        authorize_again(authorization, p)
        return Response(
            body,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=reserved-study.zip"},
        )

    @app.get("/v1/projects/{project}/remote-studies/{study}/download")
    def remote_inputs(
        project: str,
        study: str,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        from .remote import study_archive

        archive = study_archive(store, p, project, study)
        authorize_again(authorization, p)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename="remote-study.zip",
        )

    @app.get("/v1/cloud-profiles")
    def cloud_profiles(p=Depends(identity)):
        return service.cloud.available(p)

    @app.post("/v1/projects/{project}/cloud-runs")
    def approve_cloud(
        project: str,
        body: CloudRunBody,
        authorization: str = Header(default=""),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        authorize_again(authorization, p)
        return service.cloud.create(
            p,
            project,
            body.study,
            body.profile_id,
            body.data_approved,
            body.compute_approved,
            body.request_key,
        )

    @app.get("/v1/projects/{project}/cloud-runs")
    def cloud_runs(
        project: str,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        return [
            service.cloud.get(p, project, item["id"])
            for item in store.list(p["tenant"], project, "cloud-run", limit, offset)
        ]

    @app.get("/v1/projects/{project}/cloud-runs/{run_id}")
    def cloud_run(project: str, run_id: str, p=Depends(identity)):
        service.project(p, project)
        return service.cloud.get(p, project, run_id)

    @app.post("/v1/projects/{project}/cloud-runs/{run_id}/actions", status_code=202)
    def cloud_action(
        project: str,
        run_id: str,
        body: CloudActionBody,
        authorization: str = Header(default=""),
        p=Depends(identity),
    ):
        service.project(p, project)
        authorize_again(authorization, p)
        return service.cloud.queue(
            p, project, run_id, body.action, body.revision, body.request_key
        )

    @app.post("/v1/projects/{project}/reservation-preview")
    def reservation_preview(
        project: str, body: ReservationPreviewBody, p=Depends(identity)
    ):
        from .reservations import preview

        return preview(service, p, project, **body.model_dump())

    @app.post("/v1/projects/{project}/generation-preview")
    def generation_preview(
        project: str, body: GenerationPreviewBody, p=Depends(identity)
    ):
        from .generation_plans import preview

        return preview(service, p, project, body.model_dump())

    @app.get("/v1/projects/{project}/generation-plans")
    def generation_plans(
        project: str,
        limit: int = Query(default=20, ge=1, le=20),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        from .generation import list_requests

        return list_requests(service, p, project, limit, offset)

    @app.get("/v1/projects/{project}/generation-plans/{plan}")
    def generation_plan(project: str, plan: str, p=Depends(identity)):
        from .generation import view

        return view(service, p, project, plan)

    @app.post("/v1/projects/{project}/generation-plans/{plan}/settle")
    def settle_generation(
        project: str,
        plan: str,
        body: GenerationSettlementBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .generation import settle

        return settle(
            service,
            p,
            project,
            plan,
            **body.model_dump(),
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.post("/v1/projects/{project}/jobs", status_code=202)
    def submit(
        project: str,
        body: JobBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        return service.submit(
            p,
            project,
            **body.model_dump(exclude={"request_key"}),
            key=body.request_key,
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.get("/v1/projects/{project}/jobs")
    def jobs(project: str, p=Depends(identity)):
        service.require(p, ("admin", "editor", "reviewer"))
        service.project(p, project)
        return store.jobs(p["tenant"], project)

    @app.post("/v1/projects/{project}/jobs/{job}/cancel")
    def cancel(project: str, job: str, p=Depends(identity)):
        service.require(p, ("admin", "editor"))
        service.project(p, project)
        return store.cancel(job, p["tenant"], project, p["actor"])

    @app.get("/v1/projects/{project}/active")
    def active(project: str, p=Depends(identity)):
        service.project(p, project)
        return store.active(p["tenant"], project)

    @app.post("/v1/projects/{project}/releases")
    def release(
        project: str,
        body: ReleaseBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        return service.activate(
            p,
            project,
            **body.model_dump(),
            authorize=lambda: authorize_again(authorization, p),
        )

    @app.post("/v1/projects/{project}/decisions")
    def decision(
        project: str,
        body: DecisionBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        result = service.predict(p, project, **body.model_dump())
        authorize_again(authorization, p)
        return result

    @app.get("/v1/projects/{project}/operations")
    def operations(
        project: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor"))
        service.project(p, project)
        result = store.operations(
            p["tenant"], project, monitor.worker_max_age, limit, offset
        )
        result["collector"] = monitor.state()
        return result

    @app.post("/v1/projects/{project}/operations/incidents/{incident}/actions")
    def incident_action(
        project: str,
        incident: str,
        body: IncidentActionBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        authorize_again(authorization, p)
        return store.incident_action(
            p["tenant"], project, incident, p["actor"], **body.model_dump()
        )

    @app.get("/v1/projects/{project}/operations/incidents/{incident}/history")
    def incident_history(
        project: str,
        incident: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        p=Depends(identity),
    ):
        service.require(p, ("admin", "editor"))
        service.project(p, project)
        return store.incident_history(p["tenant"], project, incident, limit, offset)

    @app.get("/v1/projects/{project}/audit")
    def audit(project: str, p=Depends(identity)):
        service.require(p, ("admin",))
        service.project(p, project)
        return store.events(p["tenant"], project)

    @app.post("/v1/projects/{project}/audit-exports", status_code=201)
    def create_audit_export(
        project: str,
        body: AuditExportBody,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        from .audit_exports import create

        result = create(
            service,
            p,
            project,
            **body.model_dump(),
            authorize=lambda: authorize_again(authorization, p),
        )
        authorize_again(authorization, p)
        return result

    @app.get("/v1/projects/{project}/audit-exports")
    def audit_exports(
        project: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        result = store.list(p["tenant"], project, "audit-export", limit, offset)
        authorize_again(authorization, p)
        return result

    @app.get("/v1/projects/{project}/audit-exports/{export}")
    def audit_export(
        project: str,
        export: str,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        from .audit_exports import get_manifest

        result = get_manifest(store, p["tenant"], project, export)[0]
        authorize_again(authorization, p)
        return result

    @app.get("/v1/projects/{project}/audit-exports/{export}/pages/{index}")
    def audit_export_page(
        project: str,
        export: str,
        index: int,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        from .audit_exports import page

        result = page(store, p["tenant"], project, export, index)
        authorize_again(authorization, p)
        return result

    @app.get("/v1/projects/{project}/audit-exports/{export}/download")
    def audit_export_download(
        project: str,
        export: str,
        authorization: str | None = Header(default=None),
        p=Depends(identity),
    ):
        service.require(p, ("admin",))
        service.project(p, project)
        from .audit_exports import get_manifest, stream, line

        entity, _ = get_manifest(store, p["tenant"], project, export)
        authorize_again(authorization, p)

        def chunks():
            try:
                yield from stream(
                    store,
                    p["tenant"],
                    project,
                    export,
                    lambda: authorize_again(authorization, p),
                )
            except Exception as exc:
                logging.getLogger(__name__).error(
                    "Audit download interrupted: %s", type(exc).__name__
                )
                yield line(
                    {
                        "error": "Audit download is incomplete; check authorization and source integrity, then retry"
                    }
                )

        return StreamingResponse(
            chunks(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": "attachment; filename=project-audit.dmtlog",
                "Cache-Control": "no-store",
                "X-Audit-Manifest-SHA256": entity["payload"]["manifest_sha256"],
            },
        )

    return app
