from __future__ import annotations

import json
import os
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.batch_operations import execute_job, refresh_job
from services.extractor import detect_type
from services.usage import check_and_log_daily_event, check_document_limit, get_user_limits
from services.mcp_enterprise import emit_event, idempotent_result, save_idempotent_result
import services.storage as gcs


router = APIRouter()
MAX_ITEMS = int(os.getenv("BATCH_MAX_ITEMS", "500"))


class DocumentBatchRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1)
    workspace_id: str | None = None
    concurrency: int = Field(default=3, ge=1, le=10)
    force: bool = False
    idempotency_key: str | None = Field(default=None, max_length=200)


class WorkspaceSummaryRequest(BaseModel):
    workspace_id: str
    document_ids: list[str] = []
    summary_type: str = "executive"
    custom_prompt: str = ""
    redact_pii: bool = False
    language: str = "en"
    concurrency: int = Field(default=2, ge=1, le=6)
    idempotency_key: str | None = Field(default=None, max_length=200)


class UploadFileManifest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    file_size: int = Field(gt=0)


class BatchUploadRequest(BaseModel):
    files: list[UploadFileManifest] = Field(min_length=1)
    workspace_id: str | None = None
    redact_pii: bool = False
    idempotency_key: str | None = Field(default=None, max_length=200)


class CompleteBatchUploadRequest(BaseModel):
    document_ids: list[str] = []
    concurrency: int = Field(default=3, ge=1, le=10)


@router.post("/uploads")
async def create_batch_upload(body: BatchUploadRequest, current_user: CurrentUser, db=Depends(get_db)):
    _limit(body.files)
    user_id = str(current_user["id"])
    replay = await idempotent_result(db, user_id, "batch_upload", body.idempotency_key, body.model_dump(exclude={"idempotency_key"}))
    if replay: return replay
    await check_document_limit(db, user_id, quantity=len(body.files))
    limits = await get_user_limits(db, user_id)
    max_mb = limits.get("max_file_mb", 10)
    oversized = [item.filename for item in body.files if max_mb != -1 and item.file_size > max_mb * 1024 * 1024]
    if oversized:
        raise HTTPException(413, f"Files exceed the {max_mb} MB plan limit: {', '.join(oversized[:5])}")
    await _require_workspace(db, body.workspace_id, user_id, "editor")
    job_id = str(uuid4())
    await db.execute("""INSERT INTO batch_jobs(id,user_id,workspace_id,operation,status,current_stage,configuration,total_items,queued_items)
      VALUES($1,$2,$3,'upload','awaiting_upload','awaiting_upload',$4::jsonb,$5,$5)""",
      job_id, user_id, body.workspace_id, json.dumps({"redact_pii": body.redact_pii}), len(body.files))
    files = []
    for manifest in body.files:
        if detect_type(manifest.filename, manifest.content_type) == "video":
            raise HTTPException(400, f"Use the video upload workflow for {manifest.filename}")
        doc_id = str(uuid4())
        filename = os.path.basename(manifest.filename) or "document"
        path = gcs.source_path(user_id, doc_id, filename)
        url = await gcs.get_signed_upload_url(path, content_type=manifest.content_type)
        data = {**manifest.model_dump(), "document_id": doc_id, "filename": filename, "gcs_source_path": path, "user_id": user_id,
                "workspace_id": body.workspace_id, "redact_pii": body.redact_pii}
        # The document row is created only after GCS confirms the upload. Keep
        # the reserved UUID in input_data until it can satisfy the FK safely.
        await db.execute("""INSERT INTO batch_job_items(job_id,item_key,status,stage,input_data)
          VALUES($1,$2,'queued','awaiting_upload',$3::jsonb)""", job_id, doc_id, json.dumps(data))
        files.append({"document_id": doc_id, "filename": filename, "upload_url": url, "gcs_source_path": path,
                      "method": "PUT", "headers": {"Content-Type": manifest.content_type}})
    response = {"batch_job_id": job_id, "status": "awaiting_upload", "files": files}
    await save_idempotent_result(db, user_id, "batch_upload", body.idempotency_key, body.model_dump(exclude={"idempotency_key"}), response, resource_type="batch", resource_id=job_id)
    await emit_event(db, user_id=user_id, workspace_id=body.workspace_id, event_type="batch.created", resource_type="batch", resource_id=job_id, payload={"batch_job_id": job_id, "operation": "upload", "status": "awaiting_upload"})
    return response


@router.post("/{job_id}/uploads/complete")
async def complete_batch_upload(job_id: str, body: CompleteBatchUploadRequest, background_tasks: BackgroundTasks,
                                current_user: CurrentUser, db=Depends(get_db)):
    job = await _job(db, job_id, str(current_user["id"]), write=True)
    if job["operation"] != "upload": raise HTTPException(400, "This is not an upload batch")
    if body.document_ids:
        await db.execute("UPDATE batch_job_items SET status='skipped',stage='skipped',completed_at=NOW() WHERE job_id=$1 AND NOT(item_key=ANY($2::text[]))", job_id, body.document_ids)
    await db.execute("UPDATE batch_jobs SET status='queued',current_stage='verifying_uploads',updated_at=NOW() WHERE id=$1", job_id)
    background_tasks.add_task(execute_job, job_id, "upload", body.concurrency)
    await emit_event(db, user_id=str(current_user["id"]), workspace_id=str(job["workspace_id"]) if job["workspace_id"] else None, event_type="batch.started", resource_type="batch", resource_id=job_id, payload={"batch_job_id": job_id, "operation": "upload", "status": "queued", "stage": "verifying_uploads"})
    return {"batch_job_id": job_id, "status": "queued"}


@router.post("/embedding")
async def start_batch_embedding(body: DocumentBatchRequest, background_tasks: BackgroundTasks,
                                current_user: CurrentUser, db=Depends(get_db)):
    return await _start_document_job("embedding", body, background_tasks, current_user, db)


@router.post("/classification")
async def start_batch_classification(body: DocumentBatchRequest, background_tasks: BackgroundTasks,
                                     current_user: CurrentUser, db=Depends(get_db)):
    return await _start_document_job("classification", body, background_tasks, current_user, db)


@router.post("/workspace-summary")
async def start_workspace_summary(body: WorkspaceSummaryRequest, background_tasks: BackgroundTasks,
                                  current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    replay = await idempotent_result(db, user_id, "workspace_summary", body.idempotency_key, body.model_dump(exclude={"idempotency_key"}))
    if replay: return replay
    await _require_workspace(db, body.workspace_id, user_id, "viewer")
    rows = await db.fetch("""SELECT d.id FROM documents d JOIN workspace_members wm ON wm.workspace_id=d.workspace_id
      WHERE d.workspace_id=$1 AND wm.user_id=$2 AND d.status IN ('chunked','embedding','embedded')
        AND ($3::uuid[] IS NULL OR d.id=ANY($3::uuid[])) ORDER BY d.created_at""",
      body.workspace_id, user_id, body.document_ids or None)
    ids = [str(row["id"]) for row in rows]
    if not ids: raise HTTPException(400, "No accessible chunked documents found")
    _limit(ids)
    await check_and_log_daily_event(db, user_id, "summarize", "max_summaries_day", quantity=len(ids),
                                    metadata={"batch": True, "workspace_id": body.workspace_id, "document_ids": ids})
    config = body.model_dump()
    job_id = await _create_job(db, user_id, body.workspace_id, "workspace_summary", ids, config)
    background_tasks.add_task(execute_job, job_id, "workspace_summary", body.concurrency)
    response = {"batch_job_id": job_id, "status": "queued", "total_items": len(ids)}
    await save_idempotent_result(db, user_id, "workspace_summary", body.idempotency_key, body.model_dump(exclude={"idempotency_key"}), response, resource_type="batch", resource_id=job_id)
    await emit_event(db, user_id=user_id, workspace_id=body.workspace_id, event_type="batch.started", resource_type="batch", resource_id=job_id, payload={"batch_job_id": job_id, "operation": "workspace_summary", "status": "queued"})
    return response


@router.get("")
async def list_batch_jobs(current_user: CurrentUser, workspace_id: str | None = None, operation: str | None = None,
                          status: str | None = None, limit: int = Query(25, ge=1, le=100), db=Depends(get_db)):
    rows = await db.fetch("""SELECT * FROM batch_jobs j WHERE
      (j.user_id=$1 OR EXISTS(SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=j.workspace_id AND wm.user_id=$1))
      AND ($2::uuid IS NULL OR j.workspace_id=$2) AND ($3::text IS NULL OR j.operation=$3)
      AND ($4::text IS NULL OR j.status=$4) ORDER BY created_at DESC LIMIT $5""",
      str(current_user["id"]), workspace_id, operation, status, limit)
    return {"jobs": [_serialize(row) for row in rows]}


@router.get("/{job_id}")
async def get_batch_status(job_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await refresh_job(job_id)
    job = await _job(db, job_id, str(current_user["id"]))
    items = await db.fetch("SELECT * FROM batch_job_items WHERE job_id=$1 ORDER BY created_at", job_id)
    return {**_serialize(job), "items": [_serialize(row) for row in items]}


@router.get("/{job_id}/results")
async def get_batch_results(job_id: str, current_user: CurrentUser, db=Depends(get_db)):
    job = await _job(db, job_id, str(current_user["id"]))
    items = await db.fetch("SELECT document_id,item_key,status,attempts,output_data,error_message FROM batch_job_items WHERE job_id=$1 ORDER BY created_at", job_id)
    return {"batch_job_id": job_id, "operation": job["operation"], "status": job["status"],
            "result": _json(job["result"]), "items": [_serialize(row) for row in items]}


@router.post("/{job_id}/retry")
async def retry_batch_failures(job_id: str, background_tasks: BackgroundTasks, current_user: CurrentUser, db=Depends(get_db)):
    job = await _job(db, job_id, str(current_user["id"]), write=True)
    await db.execute("UPDATE batch_job_items SET status='queued',stage='retry_queued',error_message=NULL,completed_at=NULL WHERE job_id=$1 AND status='failed'", job_id)
    await db.execute("UPDATE batch_jobs SET status='queued',cancel_requested=FALSE,completed_at=NULL,current_stage='retry_queued',updated_at=NOW() WHERE id=$1", job_id)
    background_tasks.add_task(execute_job, job_id, job["operation"], int(_json(job["configuration"]).get("concurrency", 3)))
    await emit_event(db, user_id=str(current_user["id"]), workspace_id=str(job["workspace_id"]) if job["workspace_id"] else None, event_type="batch.resumed", resource_type="batch", resource_id=job_id, payload={"batch_job_id": job_id, "operation": job["operation"], "status": "queued", "stage": "retry_queued"})
    return {"batch_job_id": job_id, "status": "queued"}


@router.post("/{job_id}/resume")
async def resume_batch_job(job_id: str, background_tasks: BackgroundTasks, current_user: CurrentUser, db=Depends(get_db)):
    return await retry_batch_failures(job_id, background_tasks, current_user, db)


@router.post("/{job_id}/cancel")
async def cancel_batch_job(job_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _job(db, job_id, str(current_user["id"]), write=True)
    await db.execute("UPDATE batch_jobs SET cancel_requested=TRUE,status='cancelling',current_stage='cancelling',updated_at=NOW() WHERE id=$1", job_id)
    await db.execute("UPDATE batch_job_items SET status='skipped',stage='cancelled',completed_at=NOW(),updated_at=NOW() WHERE job_id=$1 AND status='queued'", job_id)
    await emit_event(db, user_id=str(current_user["id"]), event_type="batch.cancelled", resource_type="batch", resource_id=job_id, payload={"batch_job_id": job_id, "status": "cancelling"})
    return {"batch_job_id": job_id, "status": "cancelling"}


async def _start_document_job(operation, body, background_tasks, current_user, db):
    _limit(body.document_ids)
    user_id = str(current_user["id"])
    replay = await idempotent_result(db, user_id, f"batch_{operation}", body.idempotency_key, body.model_dump(exclude={"idempotency_key"}))
    if replay: return replay
    await _require_workspace(db, body.workspace_id, user_id, "editor")
    rows = await db.fetch("""SELECT d.id,d.chunk_count FROM documents d WHERE d.id=ANY($1::uuid[]) AND
      (d.user_id=$2 OR EXISTS(SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=d.workspace_id AND wm.user_id=$2))""", body.document_ids, user_id)
    ids = [str(row["id"]) for row in rows]
    if len(ids) != len(set(body.document_ids)): raise HTTPException(403, "One or more documents are inaccessible")
    if operation == "embedding":
        chunk_count = sum(max(1, int(row.get("chunk_count") or 0)) for row in rows)
        await check_and_log_daily_event(db, user_id, "embedding", "max_embeds_day", quantity=chunk_count,
                                        metadata={"batch": True, "document_ids": ids, "chunk_count": chunk_count})
    config = body.model_dump()
    job_id = await _create_job(db, user_id, body.workspace_id, operation, ids, config)
    background_tasks.add_task(execute_job, job_id, operation, body.concurrency)
    response = {"batch_job_id": job_id, "status": "queued", "total_items": len(ids)}
    await save_idempotent_result(db, user_id, f"batch_{operation}", body.idempotency_key, body.model_dump(exclude={"idempotency_key"}), response, resource_type="batch", resource_id=job_id)
    await emit_event(db, user_id=user_id, workspace_id=body.workspace_id, event_type="batch.started", resource_type="batch", resource_id=job_id, payload={"batch_job_id": job_id, "operation": operation, "status": "queued"})
    return response


async def _create_job(db, user_id, workspace_id, operation, ids, config):
    job_id = str(uuid4())
    await db.execute("""INSERT INTO batch_jobs(id,user_id,workspace_id,operation,status,configuration,total_items,queued_items)
      VALUES($1,$2,$3,$4,'queued',$5::jsonb,$6,$6)""", job_id, user_id, workspace_id, operation, json.dumps(config), len(ids))
    for doc_id in ids:
        await db.execute("INSERT INTO batch_job_items(job_id,document_id,item_key,input_data) VALUES($1,$2,$3,$4::jsonb)",
                         job_id, doc_id, doc_id, json.dumps(config))
    return job_id


async def _job(db, job_id, user_id, write=False):
    row = await db.fetchrow("""SELECT j.* FROM batch_jobs j WHERE j.id=$1 AND
      (j.user_id=$2 OR EXISTS(SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=j.workspace_id AND wm.user_id=$2))""", job_id, user_id)
    if not row: raise HTTPException(404, "Batch job not found")
    if write and row["workspace_id"]: await _require_workspace(db, str(row["workspace_id"]), user_id, "editor")
    return dict(row)


async def _require_workspace(db, workspace_id, user_id, role):
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, role)


def _limit(items):
    if len(items) > MAX_ITEMS: raise HTTPException(400, f"Batch supports at most {MAX_ITEMS} items")


def _json(value):
    if isinstance(value, dict): return value
    if isinstance(value, str): return json.loads(value or "{}")
    return dict(value or {})


def _serialize(row):
    result = dict(row)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"): result[key] = value.isoformat()
        elif key in {"configuration", "result", "input_data", "output_data"}: result[key] = _json(value)
        elif key in {"id", "user_id", "workspace_id", "document_id", "job_id"} and value is not None: result[key] = str(value)
    return result
