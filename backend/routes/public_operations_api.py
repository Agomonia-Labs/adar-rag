from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from auth.api_oauth import ApiPrincipal, require_api_scope, validate_api_workspace_context
from database.connection import get_db
from routes import batches, mcp_enterprise


router = APIRouter(dependencies=[Depends(validate_api_workspace_context)])

BatchReader = Annotated[ApiPrincipal, Depends(require_api_scope("batches:read"))]
BatchWriter = Annotated[ApiPrincipal, Depends(require_api_scope("batches:write"))]
WorkflowReader = Annotated[ApiPrincipal, Depends(require_api_scope("workflows:read"))]
EventReader = Annotated[ApiPrincipal, Depends(require_api_scope("events:read"))]
EventWriter = Annotated[ApiPrincipal, Depends(require_api_scope("events:write"))]
ReviewWriter = Annotated[ApiPrincipal, Depends(require_api_scope("reviews:write"))]
ReviewApprover = Annotated[ApiPrincipal, Depends(require_api_scope("reviews:approve"))]
ArtifactReader = Annotated[ApiPrincipal, Depends(require_api_scope("artifacts:read"))]
ArtifactWriter = Annotated[ApiPrincipal, Depends(require_api_scope("artifacts:write"))]
VersionReader = Annotated[ApiPrincipal, Depends(require_api_scope("versions:read"))]
VersionWriter = Annotated[ApiPrincipal, Depends(require_api_scope("versions:write"))]
EvaluationRunner = Annotated[ApiPrincipal, Depends(require_api_scope("evaluations:run"))]


# Durable batch operations


@router.post("/batches/uploads", status_code=201)
async def api_create_batch_upload(
    body: batches.BatchUploadRequest,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.create_batch_upload(body, current_user=principal.user, db=db)


@router.post("/batches/{job_id}/uploads/complete", status_code=202)
async def api_complete_batch_upload(
    job_id: str,
    body: batches.CompleteBatchUploadRequest,
    background_tasks: BackgroundTasks,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.complete_batch_upload(
        job_id, body, background_tasks, current_user=principal.user, db=db
    )


@router.post("/batches/embedding", status_code=202)
async def api_start_batch_embedding(
    body: batches.DocumentBatchRequest,
    background_tasks: BackgroundTasks,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.start_batch_embedding(
        body, background_tasks, current_user=principal.user, db=db
    )


@router.post("/batches/classification", status_code=202)
async def api_start_batch_classification(
    body: batches.DocumentBatchRequest,
    background_tasks: BackgroundTasks,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.start_batch_classification(
        body, background_tasks, current_user=principal.user, db=db
    )


@router.post("/batches/workspace-summary", status_code=202)
async def api_start_workspace_summary(
    body: batches.WorkspaceSummaryRequest,
    background_tasks: BackgroundTasks,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.start_workspace_summary(
        body, background_tasks, current_user=principal.user, db=db
    )


@router.get("/batches")
async def api_list_batch_jobs(
    principal: BatchReader,
    workspace_id: str | None = None,
    operation: str | None = None,
    status: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    db=Depends(get_db),
):
    return await batches.list_batch_jobs(
        current_user=principal.user,
        workspace_id=workspace_id,
        operation=operation,
        status=status,
        limit=limit,
        db=db,
    )


@router.get("/batches/{job_id}")
async def api_get_batch_status(job_id: str, principal: BatchReader, db=Depends(get_db)):
    return await batches.get_batch_status(job_id, current_user=principal.user, db=db)


@router.get("/batches/{job_id}/results")
async def api_get_batch_results(job_id: str, principal: BatchReader, db=Depends(get_db)):
    return await batches.get_batch_results(job_id, current_user=principal.user, db=db)


@router.post("/batches/{job_id}/retry", status_code=202)
async def api_retry_batch(
    job_id: str,
    background_tasks: BackgroundTasks,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.retry_batch_failures(
        job_id, background_tasks, current_user=principal.user, db=db
    )


@router.post("/batches/{job_id}/resume", status_code=202)
async def api_resume_batch(
    job_id: str,
    background_tasks: BackgroundTasks,
    principal: BatchWriter,
    db=Depends(get_db),
):
    return await batches.resume_batch_job(
        job_id, background_tasks, current_user=principal.user, db=db
    )


@router.post("/batches/{job_id}/cancel", status_code=202)
async def api_cancel_batch(job_id: str, principal: BatchWriter, db=Depends(get_db)):
    return await batches.cancel_batch_job(job_id, current_user=principal.user, db=db)


# Workflow contracts and lifecycle events


@router.get("/operations/catalog")
async def api_operations_catalog(principal: WorkflowReader):
    return await mcp_enterprise.enterprise_catalog(current_user=principal.user)


@router.get("/workflows/{workflow}/schema")
async def api_workflow_schema(workflow: str, principal: WorkflowReader):
    return await mcp_enterprise.workflow_schema(workflow, current_user=principal.user)


@router.post("/workflows/{workflow}/validate")
async def api_validate_workflow(workflow: str, payload: dict[str, Any], principal: WorkflowReader):
    return await mcp_enterprise.validate_workflow(workflow, payload, current_user=principal.user)


@router.get("/events")
async def api_list_events(
    principal: EventReader,
    after: int = 0,
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    return await mcp_enterprise.list_events(
        current_user=principal.user,
        db=db,
        after=after,
        resource_type=resource_type,
        resource_id=resource_id,
        limit=limit,
    )


@router.post("/event-subscriptions", status_code=201)
async def api_create_event_subscription(
    body: mcp_enterprise.SubscriptionInput,
    principal: EventWriter,
    db=Depends(get_db),
):
    return await mcp_enterprise.create_subscription(body, current_user=principal.user, db=db)


@router.get("/event-subscriptions")
async def api_list_event_subscriptions(principal: EventReader, db=Depends(get_db)):
    return await mcp_enterprise.list_subscriptions(current_user=principal.user, db=db)


@router.delete("/event-subscriptions/{subscription_id}")
async def api_delete_event_subscription(
    subscription_id: str,
    principal: EventWriter,
    db=Depends(get_db),
):
    return await mcp_enterprise.delete_subscription(
        subscription_id, current_user=principal.user, db=db
    )


# Human review, knowledge artifacts, versions, and evaluations


@router.post("/reviews", status_code=201)
async def api_create_review(
    body: mcp_enterprise.ReviewTaskInput,
    principal: ReviewWriter,
    db=Depends(get_db),
):
    return await mcp_enterprise.create_review_task(body, current_user=principal.user, db=db)


@router.get("/reviews")
async def api_list_reviews(
    principal: ReviewWriter,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    return await mcp_enterprise.list_review_tasks(
        current_user=principal.user, db=db, status=status, limit=limit
    )


@router.post("/reviews/{task_id}/assign")
async def api_assign_review(task_id: str, principal: ReviewWriter, db=Depends(get_db)):
    return await mcp_enterprise.assign_review_task(task_id, current_user=principal.user, db=db)


@router.post("/reviews/{task_id}/decision")
async def api_decide_review(
    task_id: str,
    body: mcp_enterprise.ReviewDecisionInput,
    principal: ReviewApprover,
    db=Depends(get_db),
):
    return await mcp_enterprise.decide_review_task(
        task_id, body, current_user=principal.user, db=db
    )


@router.post("/artifacts", status_code=201)
async def api_create_artifact(
    body: mcp_enterprise.ArtifactInput,
    principal: ArtifactWriter,
    db=Depends(get_db),
):
    return await mcp_enterprise.create_artifact(body, current_user=principal.user, db=db)


@router.get("/artifacts")
async def api_list_artifacts(
    principal: ArtifactReader,
    workspace_id: str | None = None,
    db=Depends(get_db),
):
    return await mcp_enterprise.list_artifacts(
        current_user=principal.user, db=db, workspace_id=workspace_id
    )


@router.post("/documents/{document_id}/versions", status_code=201)
async def api_register_document_version(
    document_id: str,
    body: mcp_enterprise.VersionInput,
    principal: VersionWriter,
    db=Depends(get_db),
):
    return await mcp_enterprise.register_document_version(
        document_id, body, current_user=principal.user, db=db
    )


@router.get("/documents/{document_id}/versions")
async def api_list_document_versions(
    document_id: str,
    principal: VersionReader,
    db=Depends(get_db),
):
    return await mcp_enterprise.list_document_versions(
        document_id, current_user=principal.user, db=db
    )


@router.post("/evaluations", status_code=201)
async def api_run_evaluation(
    body: mcp_enterprise.EvaluationInput,
    principal: EvaluationRunner,
    db=Depends(get_db),
):
    return await mcp_enterprise.evaluate_trace(body, current_user=principal.user, db=db)
