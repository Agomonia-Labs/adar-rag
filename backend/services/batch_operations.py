from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from database.connection import get_pool
from routes.documents import _embed_document, _metadata_path_from_row
from routes.summarize import _load_chunk_texts, _stream_summary
from services.classifier import classify_document
from services.extractor import extract_text
import services.storage as gcs
from services.mcp_enterprise import emit_event


MAX_CONCURRENCY = int(os.getenv("BATCH_MAX_CONCURRENCY", "4"))


async def refresh_job(job_id: str) -> dict:
    async with get_pool().acquire() as db:
        counts = await db.fetchrow("""
            SELECT COUNT(*) total,
                   COUNT(*) FILTER (WHERE status='queued') queued,
                   COUNT(*) FILTER (WHERE status='running') running,
                   COUNT(*) FILTER (WHERE status='succeeded') succeeded,
                   COUNT(*) FILTER (WHERE status='failed') failed,
                   COUNT(*) FILTER (WHERE status='skipped') skipped
              FROM batch_job_items WHERE job_id=$1
        """, job_id)
        c = dict(counts or {})
        done = int(c.get("succeeded") or 0) + int(c.get("failed") or 0) + int(c.get("skipped") or 0)
        total = int(c.get("total") or 0)
        progress = round(done * 100 / total) if total else 0
        job_state = await db.fetchrow("SELECT cancel_requested,status,user_id,workspace_id,operation FROM batch_jobs WHERE id=$1", job_id)
        status = "running"
        completed_at = None
        if total and done == total:
            status = "cancelled" if job_state and job_state["cancel_requested"] else ("completed_with_errors" if c.get("failed") else "completed")
            completed_at = datetime.now(timezone.utc)
            progress = 100
        current_stage = status if status in {"completed", "completed_with_errors", "cancelled"} else "processing"
        row = await db.fetchrow("""
            UPDATE batch_jobs SET total_items=$2, queued_items=$3, running_items=$4,
              succeeded_items=$5, failed_items=$6, skipped_items=$7, progress_pct=$8,
              status=$9, completed_at=COALESCE($10,completed_at), current_stage=$11, updated_at=NOW()
            WHERE id=$1 RETURNING *
        """, job_id, total, c.get("queued", 0), c.get("running", 0), c.get("succeeded", 0),
             c.get("failed", 0), c.get("skipped", 0), progress, status, completed_at, current_stage)
        if status in {"completed", "completed_with_errors", "cancelled"} and job_state and job_state["status"] not in {"completed", "completed_with_errors", "cancelled"}:
            await emit_event(
                db, user_id=str(job_state["user_id"]),
                workspace_id=str(job_state["workspace_id"]) if job_state["workspace_id"] else None,
                event_type="batch.completed", resource_type="batch", resource_id=job_id,
                payload={"batch_job_id": job_id, "operation": job_state["operation"], "status": status, "progress_pct": progress, "stage": current_stage},
            )
        return dict(row)


async def execute_job(job_id: str, operation: str, concurrency: int = 3) -> None:
    async with get_pool().acquire() as db:
        await db.execute("UPDATE batch_jobs SET status='running',current_stage=$2,started_at=COALESCE(started_at,NOW()),updated_at=NOW() WHERE id=$1", job_id, operation)
        rows = await db.fetch("SELECT * FROM batch_job_items WHERE job_id=$1 AND status='queued' ORDER BY created_at", job_id)
    handler = {"embedding": _embed_item, "classification": _classify_item, "workspace_summary": _summarize_item,
               "upload": _complete_upload_item}[operation]
    semaphore = asyncio.Semaphore(max(1, min(concurrency, MAX_CONCURRENCY)))

    async def run(row):
        async with semaphore:
            if await _cancelled(job_id):
                await _finish_item(str(row["id"]), "skipped", {}, "Batch cancellation requested")
                return
            await _start_item(str(row["id"]), operation)
            try:
                output = await handler(dict(row))
                await _finish_item(str(row["id"]), output.pop("_status", "succeeded"), output)
            except Exception as exc:
                await _finish_item(str(row["id"]), "failed", {}, str(exc)[:2000])
            await refresh_job(job_id)

    await asyncio.gather(*(run(row) for row in rows))
    if operation == "workspace_summary":
        await _reduce_workspace_summary(job_id)
    await refresh_job(job_id)


async def _embed_item(item: dict) -> dict:
    data = _json(item["input_data"])
    doc_id = str(item["document_id"])
    async with get_pool().acquire() as db:
        row = await db.fetchrow("SELECT * FROM documents WHERE id=$1", doc_id)
        if not row:
            raise RuntimeError("Document not found")
        row = dict(row)
        if row["status"] == "embedded" and not data.get("force"):
            return {"_status": "skipped", "reason": "already_embedded"}
        if row["status"] not in {"chunked", "embedded"}:
            raise RuntimeError(f"Document must be chunked before embedding (status: {row['status']})")
        await db.execute("UPDATE documents SET status='embedding',updated_at=NOW() WHERE id=$1", doc_id)
    await _embed_document(doc_id, str(row["user_id"]), str(row["workspace_id"]) if row.get("workspace_id") else None)
    async with get_pool().acquire() as db:
        final = await db.fetchrow("SELECT status,error_message,chunk_count FROM documents WHERE id=$1", doc_id)
    if final["status"] != "embedded":
        raise RuntimeError(final["error_message"] or f"Embedding ended in {final['status']}")
    return {"document_id": doc_id, "status": "embedded", "chunk_count": final["chunk_count"]}


async def _classify_item(item: dict) -> dict:
    data = _json(item["input_data"])
    doc_id = str(item["document_id"])
    async with get_pool().acquire() as db:
        row = await db.fetchrow("SELECT * FROM documents WHERE id=$1", doc_id)
    if not row:
        raise RuntimeError("Document not found")
    row = dict(row)
    if row.get("classified_at") and not data.get("force"):
        return {"_status": "skipped", "reason": "already_classified", "doc_type": row.get("doc_type"), "doc_domain": row.get("doc_domain")}
    suffix = os.path.splitext(row["original_name"] or "document")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        path = tmp.name
    try:
        await gcs.download_to_file(row["gcs_source_path"], path)
        text = await extract_text(path, row["original_name"], "application/octet-stream")
        result = await classify_document(text_sample=text, filename=row["original_name"], file_type=row["file_type"])
        async with get_pool().acquire() as db:
            await db.execute("UPDATE documents SET doc_type=$2,doc_domain=$3,doc_language=$4,classified_at=NOW(),updated_at=NOW() WHERE id=$1",
                             doc_id, result["doc_type"], result["doc_domain"], result["doc_language"])
        return {"document_id": doc_id, **result}
    finally:
        try: os.unlink(path)
        except OSError: pass


async def _summarize_item(item: dict) -> dict:
    data = _json(item["input_data"])
    doc_id = str(item["document_id"])
    async with get_pool().acquire() as db:
        row = await db.fetchrow("SELECT * FROM documents WHERE id=$1", doc_id)
    if not row or row["status"] not in {"chunked", "embedding", "embedded"}:
        raise RuntimeError("Document must be chunked before summarization")
    row = dict(row)
    meta = await gcs.download_json(_metadata_path_from_row(row, doc_id, str(row["user_id"])))
    texts = await _load_chunk_texts(meta.get("chunks") or [])
    summary = await _collect_summary(texts, data.get("summary_type", "executive"), data.get("custom_prompt", ""), row["original_name"], row.get("doc_language") or "en", bool(data.get("redact_pii")))
    return {"document_id": doc_id, "filename": row["original_name"], "summary": summary}


async def _complete_upload_item(item: dict) -> dict:
    data = _json(item["input_data"])
    doc_id = str(data["document_id"])
    meta = await gcs.blob_metadata(data["gcs_source_path"])
    if not meta:
        raise RuntimeError("Uploaded object was not found")
    from services.extractor import detect_type
    from routes.documents import _chunk_direct_upload
    ftype = detect_type(data["filename"], data["content_type"])
    async with get_pool().acquire() as db:
        await db.execute("""INSERT INTO documents
          (id,user_id,workspace_id,filename,original_name,file_type,file_size,gcs_source_path,gcs_chunks_dir,status,doc_metadata)
          VALUES($1,$2,$3,$4,$4,$5,$6,$7,$8,'chunking',$9::jsonb) ON CONFLICT(id) DO NOTHING""",
          doc_id, data["user_id"], data.get("workspace_id"), data["filename"], ftype,
          int(meta.get("size") or data["file_size"]), data["gcs_source_path"], gcs.chunks_dir(data["user_id"], doc_id),
          json.dumps({"direct_upload": True, "batch_job_id": str(item["job_id"])}))
        await db.execute(
            "UPDATE batch_job_items SET document_id=$2 WHERE id=$1",
            str(item["id"]), doc_id,
        )
    await _chunk_direct_upload(doc_id, data["user_id"], data["gcs_source_path"], data["filename"], data["content_type"], ftype, data.get("workspace_id"), bool(data.get("redact_pii")))
    async with get_pool().acquire() as db:
        final = await db.fetchrow("SELECT status,chunk_count,error_message FROM documents WHERE id=$1", doc_id)
    if not final or final["status"] != "chunked":
        raise RuntimeError((final and final["error_message"]) or "Chunking failed")
    return {"document_id": doc_id, "status": "chunked", "chunk_count": final["chunk_count"]}


async def _reduce_workspace_summary(job_id: str) -> None:
    async with get_pool().acquire() as db:
        job = await db.fetchrow("SELECT * FROM batch_jobs WHERE id=$1", job_id)
        rows = await db.fetch("SELECT output_data FROM batch_job_items WHERE job_id=$1 AND status='succeeded' ORDER BY created_at", job_id)
    summaries = [_json(row["output_data"]).get("summary", "") for row in rows]
    summaries = [s for s in summaries if s]
    if not summaries:
        return
    config = _json(job["configuration"])
    final = await _collect_summary(summaries, config.get("summary_type", "executive"), config.get("custom_prompt", ""), f"workspace with {len(summaries)} documents", config.get("language", "en"), bool(config.get("redact_pii")))
    async with get_pool().acquire() as db:
        await db.execute("UPDATE batch_jobs SET result=$2::jsonb,current_stage='completed',updated_at=NOW() WHERE id=$1", job_id,
                         json.dumps({"summary": final, "documents_summarized": len(summaries)}))


async def _collect_summary(texts: list[str], summary_type: str, custom_prompt: str, label: str, language: str, redact_pii: bool) -> str:
    tokens = []
    async for event in _stream_summary(texts, summary_type, custom_prompt, label, language, redact_pii):
        if not event.startswith("data: "):
            continue
        payload = json.loads(event[6:].strip())
        if payload.get("type") == "token": tokens.append(payload.get("text", ""))
        if payload.get("type") == "error": raise RuntimeError(payload.get("error") or "Summary failed")
    return "".join(tokens).strip()


async def _cancelled(job_id: str) -> bool:
    async with get_pool().acquire() as db:
        return bool(await db.fetchval("SELECT cancel_requested FROM batch_jobs WHERE id=$1", job_id))


async def _start_item(item_id: str, stage: str) -> None:
    async with get_pool().acquire() as db:
        await db.execute("UPDATE batch_job_items SET status='running',stage=$2,attempts=attempts+1,started_at=NOW(),updated_at=NOW(),error_message=NULL WHERE id=$1", item_id, stage)


async def _finish_item(item_id: str, status: str, output: dict, error: str | None = None) -> None:
    async with get_pool().acquire() as db:
        await db.execute("UPDATE batch_job_items SET status=$2,stage=$2,output_data=$3::jsonb,error_message=$4,completed_at=NOW(),updated_at=NOW() WHERE id=$1",
                         item_id, status, json.dumps(output, default=str), error)


def _json(value: Any) -> dict:
    if isinstance(value, dict): return value
    if isinstance(value, str): return json.loads(value or "{}")
    return dict(value or {})
