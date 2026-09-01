from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from auth.api_oauth import ApiPrincipal, enforce_api_usage, require_api_scope, validate_api_workspace_context
from database.connection import get_db
from routes import batches, mcp_enterprise
from services.mcp_enterprise import process_webhook_deliveries


router = APIRouter(dependencies=[Depends(validate_api_workspace_context), Depends(enforce_api_usage)])

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


@router.get("/operations")
async def api_list_operations(
    principal: BatchReader,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    db=Depends(get_db),
):
    result = await batches.list_batch_jobs(
        current_user=principal.user, workspace_id=workspace_id, operation=None,
        status=status, limit=limit, db=db,
    )
    return {"operations": [_operation_view(job) for job in result["jobs"]]}


@router.get("/operations/{operation_id}")
async def api_get_operation(operation_id: str, principal: BatchReader, db=Depends(get_db)):
    job = await batches.get_batch_status(operation_id, current_user=principal.user, db=db)
    return _operation_view(job, include_items=True)


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


@router.get("/webhook-deliveries")
async def api_list_webhook_deliveries(
    principal: EventReader,
    subscription_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    rows = await db.fetch(
        """SELECT d.*,e.event_type,e.resource_type,e.resource_id,e.sequence_number
           FROM mcp_webhook_deliveries d
           JOIN mcp_event_subscriptions s ON s.id=d.subscription_id
           JOIN mcp_events e ON e.id=d.event_id
           WHERE s.user_id=$1::uuid AND ($2::uuid IS NULL OR d.subscription_id=$2::uuid)
             AND ($3::text IS NULL OR d.status=$3)
           ORDER BY d.created_at DESC LIMIT $4""",
        str(principal.user["id"]), subscription_id, status, limit,
    )
    return {"deliveries": [_serialize(row) for row in rows]}


@router.post("/webhook-deliveries/{delivery_id}/retry", status_code=202)
async def api_retry_webhook_delivery(
    delivery_id: str, background_tasks: BackgroundTasks, principal: EventWriter,
    db=Depends(get_db),
):
    delivery = await db.fetchrow(
        """UPDATE mcp_webhook_deliveries d SET status='pending',next_attempt_at=NOW(),last_error=NULL,updated_at=NOW()
           FROM mcp_event_subscriptions s WHERE d.id=$1::uuid AND s.id=d.subscription_id
             AND s.user_id=$2::uuid RETURNING d.id""",
        delivery_id, str(principal.user["id"]),
    )
    if not delivery:
        raise HTTPException(404, "Webhook delivery not found")
    background_tasks.add_task(process_webhook_deliveries, delivery_ids=[delivery_id])
    return {"delivery_id": delivery_id, "status": "pending"}


@router.post("/webhook-deliveries/process-due", status_code=202)
async def api_process_due_webhook_deliveries(
    background_tasks: BackgroundTasks,
    principal: EventWriter,
    limit: int = Query(50, ge=1, le=200),
):
    background_tasks.add_task(process_webhook_deliveries, limit=limit)
    return {"status": "accepted", "limit": limit}


@router.post("/events/{event_id}/replay", status_code=202)
async def api_replay_event(
    event_id: str, background_tasks: BackgroundTasks, principal: EventWriter,
    db=Depends(get_db),
):
    rows = await db.fetch(
        """INSERT INTO mcp_webhook_deliveries(subscription_id,event_id,status,next_attempt_at,attempt_count,last_error)
           SELECT s.id,e.id,'pending',NOW(),0,NULL FROM mcp_events e
           JOIN mcp_event_subscriptions s ON s.user_id=e.user_id AND s.status='active' AND s.webhook_url IS NOT NULL
           WHERE e.id=$1::uuid AND e.user_id=$2::uuid
             AND (s.workspace_id IS NULL OR s.workspace_id=e.workspace_id)
             AND (s.resource_type IS NULL OR s.resource_type=e.resource_type)
             AND (s.resource_id IS NULL OR s.resource_id=e.resource_id)
             AND (s.event_types='[]'::jsonb OR s.event_types ? e.event_type)
           ON CONFLICT(subscription_id,event_id) DO UPDATE SET status='pending',next_attempt_at=NOW(),
             attempt_count=0,last_error=NULL,updated_at=NOW() RETURNING id""",
        event_id, str(principal.user["id"]),
    )
    if not rows:
        exists = await db.fetchval("SELECT 1 FROM mcp_events WHERE id=$1::uuid AND user_id=$2::uuid", event_id, str(principal.user["id"]))
        if not exists:
            raise HTTPException(404, "Event not found")
    ids = [str(row["id"]) for row in rows]
    if ids:
        background_tasks.add_task(process_webhook_deliveries, delivery_ids=ids)
    return {"event_id": event_id, "delivery_ids": ids, "status": "pending" if ids else "no_matching_subscriptions"}


def _operation_view(job: dict[str, Any], *, include_items: bool = False) -> dict[str, Any]:
    result = {
        "operation_id": str(job.get("id") or job.get("batch_job_id")),
        "operation_type": job.get("operation"), "resource_type": "batch",
        "status": job.get("status"), "progress_pct": job.get("progress_pct", 0),
        "current_step": job.get("current_stage"), "workspace_id": job.get("workspace_id"),
        "total_items": job.get("total_items", 0), "succeeded_items": job.get("succeeded_items", 0),
        "failed_items": job.get("failed_items", 0), "cancel_requested": job.get("cancel_requested", False),
        "error": job.get("error_message"), "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"), "completed_at": job.get("completed_at"),
    }
    if include_items:
        result["items"] = job.get("items", [])
        result["result"] = job.get("result", {})
    return result


def _serialize(row) -> dict[str, Any]:
    value = dict(row)
    for key, item in list(value.items()):
        if hasattr(item, "isoformat"):
            value[key] = item.isoformat()
    return value


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
