# routes/documents.py
from __future__ import annotations
import logging
import os, asyncio, json, tempfile
from uuid import uuid4
from datetime import datetime, timezone

from typing import Optional
from fastapi import Request, APIRouter, BackgroundTasks, UploadFile, File, Form, HTTPException, Depends
from pydantic import BaseModel, Field

log = logging.getLogger('docintel.documents')

from auth.dependencies import CurrentUser, get_current_user
from database.connection import get_db
from services.usage         import check_and_log_daily_event, check_document_limit, log_event
from services.audit         import audit, ip_from, ua_from
from services.notifications import send_embed_complete
from services.extractor import extract_text, detect_type, extract_tables_from_pdf, tables_to_text, convert_to_pdf
from services.classifier import classify_document
from services.chunker   import chunk_text
from services.llm       import embed
from services.pii       import redact_text
from services.text_safety import sanitize_text_for_storage
from services.vectordb  import store_chunk, delete_document_vectors
from services.mcp_enterprise import emit_event
from services.document_processing import process_document_job
from services.document_embedding import process_document_embedding_job
from services.video_checkpoints import summary as processing_checkpoint_summary
from services.video_dispatch import (
    create_or_reuse_document_job,
    create_or_reuse_document_embedding_job,
    dispatch_cloud_run_document_job,
    dispatch_cloud_run_document_embedding_job,
    document_dispatch_mode,
    DOCUMENT_JOB_STALE_SECONDS,
    mark_processing_dispatch_failed,
)
import services.storage as gcs


router = APIRouter()

MAX_FILES    = int(os.getenv("MAX_UPLOAD_FILES", "10"))
MAX_FILE_MB  = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
LARGE_DOCUMENT_MB = max(8, int(os.getenv("LARGE_DOCUMENT_PROCESSING_THRESHOLD_MB", "25")))


class DirectUploadSessionRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    file_size: int
    workspace_id: Optional[str] = None
    redact_pii: bool = False


class DirectUploadCompleteRequest(DirectUploadSessionRequest):
    doc_id: str
    gcs_source_path: str


class PdfSplitPart(BaseModel):
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    filename: str = Field(min_length=1, max_length=240)


class PdfSplitRequest(BaseModel):
    parts: list[PdfSplitPart] = Field(min_length=1, max_length=25)
    embed_after_processing: bool = True
    redact_pii: bool = False


async def _chunk_direct_upload(
    doc_id: str,
    user_id: str,
    source_path: str,
    filename: str,
    content_type: str,
    ftype: str,
    workspace_id: Optional[str],
    redact_pii: bool,
):
    suffix = os.path.splitext(filename)[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    try:
        await gcs.download_to_file(source_path, tmp.name)
        await _chunk_document(
            doc_id, user_id, tmp.name, filename, content_type, ftype,
            workspace_id, redact_pii,
        )
    except Exception as exc:
        log.exception("Direct-upload chunking failed for %s", doc_id)
        from database.connection import get_pool
        async with get_pool().acquire() as connection:
            await connection.execute(
                "UPDATE documents SET status='error', error_message=$2, updated_at=NOW() WHERE id=$1",
                doc_id, str(exc)[:2000],
            )
            await emit_event(
                connection, user_id=user_id, workspace_id=workspace_id,
                event_type="document.failed", resource_type="document", resource_id=doc_id,
                payload={"document_id": doc_id, "filename": filename, "status": "error",
                         "stage": "download", "message": str(exc)[:500]},
            )
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


@router.post("/upload-session")
async def create_direct_upload_session(
    body: DirectUploadSessionRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Create a signed GCS PUT URL without proxying file bytes through the API."""
    user_id = str(current_user["id"])
    filename = os.path.basename((body.filename or "document").strip()) or "document"
    content_type = (body.content_type or "application/octet-stream").strip()
    file_size = int(body.file_size or 0)
    if file_size <= 0:
        raise HTTPException(400, "file_size is required")
    if detect_type(filename, content_type) == "video":
        raise HTTPException(400, "Use the video direct-upload workflow for video files")

    await check_document_limit(db, user_id, quantity=1)
    from services.usage import get_user_limits
    limits = await get_user_limits(db, user_id)
    max_mb = limits.get("max_file_mb", 10)
    if max_mb != -1 and file_size > max_mb * 1024 * 1024:
        raise HTTPException(413, f"File '{filename}' exceeds your {max_mb} MB file size limit")
    if body.workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, body.workspace_id, user_id, "editor")

    doc_id = str(uuid4())
    source_path = gcs.source_path(user_id, doc_id, filename)
    upload_url = await gcs.get_signed_upload_url(source_path, content_type=content_type)
    return {
        "doc_id": doc_id,
        "upload_url": upload_url,
        "gcs_source_path": source_path,
        "expires_in_seconds": int(os.getenv("GCS_SIGNED_URL_EXPIRY_SECONDS", "3600")),
        "method": "PUT",
        "headers": {"Content-Type": content_type},
    }


@router.post("/upload-complete")
async def complete_direct_upload(
    body: DirectUploadCompleteRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Verify a direct upload, create its document record, and start chunking."""
    user_id = str(current_user["id"])
    filename = os.path.basename((body.filename or "document").strip()) or "document"
    content_type = (body.content_type or "application/octet-stream").strip()
    expected_path = gcs.source_path(user_id, body.doc_id, filename)
    if body.gcs_source_path != expected_path:
        raise HTTPException(400, "Upload path does not match the authenticated user and document")
    if body.workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, body.workspace_id, user_id, "editor")

    meta = await gcs.blob_metadata(body.gcs_source_path)
    if not meta:
        raise HTTPException(400, "Uploaded document was not found in storage")
    uploaded_size = int(meta.get("size") or 0)
    if body.file_size and uploaded_size and abs(uploaded_size - body.file_size) > 1024:
        raise HTTPException(400, f"Uploaded size mismatch. Expected {body.file_size} bytes but found {uploaded_size} bytes")

    ftype = detect_type(filename, content_type)
    chk_dir = gcs.chunks_dir(user_id, body.doc_id)
    result = await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_metadata)
        VALUES ($1,$2,$3,$4,$4,$5,$6,$7,$8,'chunking',$9::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        body.doc_id, user_id, body.workspace_id, filename, ftype,
        uploaded_size or body.file_size, body.gcs_source_path, chk_dir,
        json.dumps({"direct_upload": True, "upload_content_type": meta.get("content_type") or content_type}),
    )
    if result == "INSERT 0 0":
        raise HTTPException(409, "Document upload was already completed")
    await emit_event(
        db, user_id=user_id, workspace_id=body.workspace_id,
        event_type="document.uploaded", resource_type="document", resource_id=body.doc_id,
        payload={"document_id": body.doc_id, "filename": filename, "status": "chunking", "stage": "uploaded"},
    )
    durable = (uploaded_size or body.file_size) >= LARGE_DOCUMENT_MB * 1024 * 1024 or ftype == "presentation"
    job_id = None
    if durable:
        payload = {
            "source_gcs_path": body.gcs_source_path,
            "filename": filename,
            "content_type": content_type,
            "file_type": ftype,
            "redact_pii": body.redact_pii,
            "processing_profile": "large_document",
            "embed_after_processing": ftype == "presentation",
        }
        job_id, reused = await create_or_reuse_document_job(
            document_id=body.doc_id,
            user_id=user_id,
            workspace_id=body.workspace_id,
            payload=payload,
        )
        if not reused:
            if document_dispatch_mode() == "inline":
                background_tasks.add_task(
                    process_document_job,
                    job_id=job_id,
                    document_id=body.doc_id,
                    user_id=user_id,
                    workspace_id=body.workspace_id,
                    **payload,
                )
            else:
                try:
                    await dispatch_cloud_run_document_job(job_id)
                except Exception as exc:
                    await mark_processing_dispatch_failed(job_id, exc)
                    raise HTTPException(503, f"Document worker dispatch failed: {str(exc)[:240]}") from exc
    else:
        background_tasks.add_task(
            _chunk_direct_upload, body.doc_id, user_id, body.gcs_source_path,
            filename, content_type, ftype, body.workspace_id, body.redact_pii,
        )
    return {
        "doc_id": body.doc_id,
        "filename": filename,
        "status": "chunking",
        "file_size": uploaded_size or body.file_size,
        "processing_mode": "durable_job" if durable else "inline",
        "job_id": job_id,
    }


@router.post("/{doc_id}/retry")
async def retry_document_processing(
    doc_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Resume a failed durable document job from its completed checkpoints."""
    row = await _get_owned(doc_id, str(current_user["id"]), db)
    if row.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(row["workspace_id"]), str(current_user["id"]), "editor")
    job = await db.fetchrow(
        """SELECT id, job_type, status, input_data,
                  (status = ANY($3::text[]) AND
                   COALESCE(heartbeat_at, updated_at) < NOW() - ($2 * INTERVAL '1 second')) AS stalled
             FROM video_processing_jobs
             WHERE document_id=$1
               AND job_type = ANY($4::text[])
             ORDER BY created_at DESC LIMIT 1""",
        doc_id, DOCUMENT_JOB_STALE_SECONDS, ["queued", "dispatching", "running"],
        ["process_document", "process_document_embedding"],
    )
    stalled = bool(job and job["stalled"])
    if row["status"] != "error" and not stalled:
        raise HTTPException(
            400,
            f"Only failed or stalled document jobs can be retried (current: {row['status']})",
        )
    if stalled:
        await db.execute(
            """UPDATE video_processing_jobs SET status='error', error_message='Worker heartbeat expired',
                      lease_owner=NULL, lease_expires_at=NULL, updated_at=NOW() WHERE id=$1""",
            job["id"],
        )
        await db.execute(
            """UPDATE video_processing_checkpoints
                  SET status='failed', error_message='Worker heartbeat expired',
                      lease_owner=NULL, lease_expires_at=NULL, updated_at=NOW()
                WHERE job_id=$1 AND status='running'""",
            job["id"],
        )
    if job and job["job_type"] == "process_document_embedding":
        payload = job["input_data"] if isinstance(job["input_data"], dict) else json.loads(job["input_data"] or "{}")
        job_id, reused = await create_or_reuse_document_embedding_job(
            document_id=doc_id,
            user_id=str(row["user_id"]),
            workspace_id=str(row["workspace_id"]) if row.get("workspace_id") else None,
            payload=payload,
        )
        if not reused:
            if document_dispatch_mode() == "inline":
                background_tasks.add_task(
                    process_document_embedding_job,
                    job_id=job_id,
                    document_id=doc_id,
                    user_id=str(row["user_id"]),
                    workspace_id=str(row["workspace_id"]) if row.get("workspace_id") else None,
                    **payload,
                )
            else:
                try:
                    await dispatch_cloud_run_document_embedding_job(job_id)
                except Exception as exc:
                    await mark_processing_dispatch_failed(job_id, exc)
                    raise HTTPException(503, f"Embedding worker dispatch failed: {str(exc)[:240]}") from exc
        return {
            "doc_id": doc_id,
            "job_id": job_id,
            "job_reused": reused,
            "status": "embedding",
            "processing_mode": "durable_job",
        }
    if not job:
        if int(row.get("file_size") or 0) < LARGE_DOCUMENT_MB * 1024 * 1024:
            background_tasks.add_task(
                _chunk_direct_upload,
                doc_id, str(row["user_id"]), row["gcs_source_path"], row["original_name"],
                "application/octet-stream", row["file_type"],
                str(row["workspace_id"]) if row.get("workspace_id") else None, False,
            )
            await db.execute("UPDATE documents SET status='chunking', error_message=NULL WHERE id=$1", doc_id)
            return {"doc_id": doc_id, "status": "chunking", "processing_mode": "inline"}
        payload = {
            "source_gcs_path": row["gcs_source_path"],
            "filename": row["original_name"],
            "content_type": "application/pdf" if row["file_type"] == "pdf" else "application/octet-stream",
            "file_type": row["file_type"],
            "redact_pii": bool((row.get("doc_metadata") or {}).get("pii_redaction", {}).get("enabled"))
                if isinstance(row.get("doc_metadata"), dict) else False,
            "processing_profile": "large_document",
        }
    else:
        payload = job["input_data"] if isinstance(job["input_data"], dict) else json.loads(job["input_data"] or "{}")

    job_id, reused = await create_or_reuse_document_job(
        document_id=doc_id,
        user_id=str(row["user_id"]),
        workspace_id=str(row["workspace_id"]) if row.get("workspace_id") else None,
        payload=payload,
    )
    if not reused:
        if document_dispatch_mode() == "inline":
            background_tasks.add_task(
                process_document_job,
                job_id=job_id,
                document_id=doc_id,
                user_id=str(row["user_id"]),
                workspace_id=str(row["workspace_id"]) if row.get("workspace_id") else None,
                **payload,
            )
        else:
            try:
                await dispatch_cloud_run_document_job(job_id)
            except Exception as exc:
                await mark_processing_dispatch_failed(job_id, exc)
                raise HTTPException(503, f"Document worker dispatch failed: {str(exc)[:240]}") from exc
    return {
        "doc_id": doc_id,
        "job_id": job_id,
        "job_reused": reused,
        "status": "chunking",
        "processing_mode": "durable_job",
    }


@router.get("/{doc_id}/processing-status")
async def document_processing_status(
    doc_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row = await _get_owned(doc_id, str(current_user["id"]), db)
    job = await db.fetchrow(
        """SELECT id, job_type, status, dispatch_mode, dispatch_reference, attempt_count,
                  error_message, heartbeat_at, lease_owner, lease_expires_at,
                  created_at, started_at, completed_at, updated_at,
                  (status = ANY($3::text[]) AND
                   COALESCE(heartbeat_at, updated_at) < NOW() - ($2 * INTERVAL '1 second')) AS stalled
             FROM video_processing_jobs
            WHERE document_id=$1
              AND job_type = ANY($4::text[])
            ORDER BY created_at DESC LIMIT 1""",
        doc_id, DOCUMENT_JOB_STALE_SECONDS, ["queued", "dispatching", "running"],
        ["process_document", "process_document_embedding"],
    )
    metadata = row.get("doc_metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    if not job:
        return {
            "doc_id": doc_id,
            "document_status": row["status"],
            "progress": metadata.get("document_progress"),
            "processing_job": None,
            "processing_stalled": False,
            "checkpoint_summary": None,
        }
    job_data = dict(job)
    job_data["id"] = str(job_data["id"])
    return {
        "doc_id": doc_id,
        "document_status": row["status"],
        "progress": metadata.get("document_progress"),
        "processing_job": job_data,
        "processing_stalled": bool(job_data.pop("stalled", False)),
        "checkpoint_summary": await processing_checkpoint_summary(str(job["id"])),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Upload — saves to GCS and immediately starts chunking as a background task
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/upload")
async def upload_documents(
    background_tasks: BackgroundTasks,
    request:          Request,
    current_user:     CurrentUser,
    files:        list[UploadFile] = File(...),
    redact_pii:   bool             = Form(False),
    workspace_id: Optional[str]   = None,   # if set, doc belongs to a workspace
    db=Depends(get_db),
):
    # Enforce per-user tier document limit (replaces global MAX_FILES env var)
    user_id = str(current_user["id"])
    await check_document_limit(db, user_id, quantity=len(files))

    # Enforce per-file size limit from tier
    from services.usage import get_user_limits
    limits   = await get_user_limits(db, user_id)
    max_mb   = limits.get("max_file_mb", 10)
    for upload in files:
        content_peek = await upload.read(1)
        await upload.seek(0)
        # Check Content-Length header if present
        cl = upload.size  # may be None on some clients
        if cl and max_mb != -1 and cl > max_mb * 1024 * 1024:
            raise HTTPException(
                413,
                f"File '{upload.filename}' exceeds your {max_mb} MB file size limit "
                f"({limits.get('label','Free')} plan). Upgrade to upload larger files."
            )

    # If workspace upload — verify editor/owner membership
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "editor")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"You can upload at most {MAX_FILES} files at once")

    created = []
    for upload in files:
        content = await upload.read()
        if max_mb != -1 and len(content) > max_mb * 1024 * 1024:
            raise HTTPException(
                413,
                f"File '{upload.filename}' exceeds your {max_mb} MB file size limit "
                f"({limits.get('label','Free')} plan). Upgrade to upload larger files.",
            )

        doc_id    = str(uuid4())
        user_id   = str(current_user["id"])
        filename  = upload.filename or "unnamed"
        ftype     = detect_type(filename, upload.content_type or "")
        src_path  = gcs.source_path(user_id, doc_id, filename)
        chk_dir   = gcs.chunks_dir(user_id, doc_id)

        # Insert document record (status = uploading)
        await db.execute(
            """
            INSERT INTO documents
              (id, user_id, workspace_id, filename, original_name, file_type, file_size,
               gcs_source_path, gcs_chunks_dir, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'uploading')
            """,
            doc_id, user_id, workspace_id, filename, filename,
            ftype, len(content), src_path, chk_dir,
        )

        # Save temp file for inline processing. Presentations always use the
        # durable worker so LibreOffice conversion, chunking, and embedding
        # are resumable and do not hold the upload request open.
        suffix = os.path.splitext(filename)[1]
        tmp    = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(content); tmp.close()

        # Upload source to GCS
        await gcs.upload_bytes(src_path, content, upload.content_type or "application/octet-stream")

        # Update status to chunking, then kick off chunking in background
        await db.execute("UPDATE documents SET status='chunking' WHERE id=$1", doc_id)
        await emit_event(
            db, user_id=user_id, workspace_id=workspace_id,
            event_type="document.uploaded", resource_type="document", resource_id=doc_id,
            payload={"document_id": doc_id, "filename": filename, "status": "chunking", "stage": "uploaded"},
        )
        if ftype == "presentation":
            payload = {
                "source_gcs_path": src_path,
                "filename": filename,
                "content_type": upload.content_type or "application/octet-stream",
                "file_type": ftype,
                "redact_pii": redact_pii,
                "processing_profile": "presentation_pdf",
                "embed_after_processing": True,
            }
            job_id, reused = await create_or_reuse_document_job(
                document_id=doc_id, user_id=user_id, workspace_id=workspace_id, payload=payload,
            )
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            if not reused:
                if document_dispatch_mode() == "inline":
                    background_tasks.add_task(
                        process_document_job,
                        job_id=job_id, document_id=doc_id, user_id=user_id,
                        workspace_id=workspace_id, **payload,
                    )
                else:
                    try:
                        await dispatch_cloud_run_document_job(job_id)
                    except Exception as exc:
                        await mark_processing_dispatch_failed(job_id, exc)
                        raise HTTPException(503, f"Presentation worker dispatch failed: {str(exc)[:240]}") from exc
        else:
            background_tasks.add_task(
                _chunk_document, doc_id, user_id, tmp.name, filename,
                upload.content_type or "", ftype, workspace_id, redact_pii,
            )
        created.append({"doc_id": doc_id, "filename": filename})
        await log_event(db, user_id, "upload", metadata={
            "doc_id":    doc_id,
            "filename":  filename,
            "file_size": len(content),
            "file_type": ftype,
            "redact_pii": redact_pii,
        })
        await audit(db, user_id=user_id, action="upload_document",
                    resource_type="document", resource_id=doc_id,
                    metadata={"filename": filename, "workspace_id": workspace_id, "redact_pii": redact_pii},
                    ip_address=ip_from(request), user_agent=ua_from(request))

    return {"uploaded": created}


@router.get("/{doc_id}/pdf-info")
async def get_pdf_info(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    """Return a PDF page count, caching it for future split operations."""
    row = await _get_owned(doc_id, str(current_user["id"]), db)
    if row.get("file_type") != "pdf":
        raise HTTPException(400, "Only PDF documents can be split by page range")
    metadata = row.get("doc_metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    if metadata.get("page_count"):
        return {"doc_id": doc_id, "page_count": int(metadata["page_count"]), "cached": True}

    import fitz
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    try:
        await gcs.download_to_file(row["gcs_source_path"], tmp.name)
        def _count_pages():
            document = fitz.open(tmp.name)
            try:
                return document.page_count
            finally:
                document.close()
        page_count = await asyncio.to_thread(_count_pages)
    except Exception as exc:
        raise HTTPException(502, f"Could not inspect PDF pages: {str(exc)[:300]}") from exc
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    await db.execute(
        """UPDATE documents
              SET doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                  updated_at=NOW() WHERE id=$2""",
        json.dumps({"page_count": page_count}), doc_id,
    )
    return {"doc_id": doc_id, "page_count": page_count, "cached": False}


@router.post("/{doc_id}/split")
async def split_pdf_document(
    doc_id: str,
    body: PdfSplitRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Create independently processed documents from inclusive PDF page ranges."""
    source = await _get_owned(doc_id, str(current_user["id"]), db)
    if source.get("file_type") != "pdf":
        raise HTTPException(400, "Only PDF documents can be split by page range")
    if source.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(source["workspace_id"]), str(current_user["id"]), "editor")

    pdf_info = await get_pdf_info(doc_id, current_user, db)
    source_page_count = int(pdf_info["page_count"])

    normalized = []
    filenames = set()
    for part in body.parts:
        if part.end_page < part.start_page:
            raise HTTPException(422, f"Invalid page range {part.start_page}-{part.end_page}")
        if part.end_page > source_page_count:
            raise HTTPException(
                422,
                f"Requested page {part.end_page}, but the source PDF contains {source_page_count} pages",
            )
        filename = os.path.basename(part.filename.strip())
        if not filename:
            raise HTTPException(422, "Every split document needs a filename")
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        key = filename.casefold()
        if key in filenames:
            raise HTTPException(422, f"Duplicate output filename: {filename}")
        filenames.add(key)
        normalized.append((part.start_page, part.end_page, filename))
    ordered = sorted(normalized, key=lambda item: (item[0], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] <= previous[1]:
            raise HTTPException(
                422,
                f"Page ranges overlap: {previous[0]}-{previous[1]} and {current[0]}-{current[1]}",
            )

    user_id = str(current_user["id"])
    workspace_id = str(source["workspace_id"]) if source.get("workspace_id") else None
    await check_document_limit(db, user_id, quantity=len(normalized))
    results = []
    for start_page, end_page, filename in normalized:
        child_id = str(uuid4())
        child_source_path = gcs.source_path(user_id, child_id, filename)
        metadata = {
            "derived_from": {
                "document_id": doc_id,
                "filename": source.get("original_name"),
                "start_page": start_page,
                "end_page": end_page,
            },
            "automatic_embedding": {"requested": body.embed_after_processing},
            "document_progress": {
                "step": "split_queued", "progress_pct": 0,
                "message": f"Pages {start_page}-{end_page} are queued for splitting.",
            },
        }
        await db.execute(
            """INSERT INTO documents
                 (id,user_id,workspace_id,filename,original_name,file_type,file_size,
                  gcs_source_path,gcs_chunks_dir,status,doc_metadata)
               VALUES ($1,$2,$3,$4,$4,'pdf',0,$5,$6,'chunking',$7::jsonb)""",
            child_id, user_id, workspace_id, filename, child_source_path,
            gcs.chunks_dir(user_id, child_id), json.dumps(metadata),
        )
        payload = {
            "source_gcs_path": child_source_path,
            "filename": filename,
            "content_type": "application/pdf",
            "file_type": "pdf",
            "redact_pii": body.redact_pii,
            "processing_profile": "split_pdf",
            "split_source_gcs_path": source["gcs_source_path"],
            "split_start_page": start_page,
            "split_end_page": end_page,
            "embed_after_processing": body.embed_after_processing,
        }
        job_id, reused = await create_or_reuse_document_job(
            document_id=child_id, user_id=user_id, workspace_id=workspace_id, payload=payload,
        )
        dispatch_error = None
        if not reused:
            if document_dispatch_mode() == "inline":
                background_tasks.add_task(
                    process_document_job,
                    job_id=job_id, document_id=child_id, user_id=user_id,
                    workspace_id=workspace_id, **payload,
                )
            else:
                try:
                    await dispatch_cloud_run_document_job(job_id)
                except Exception as exc:
                    dispatch_error = str(exc)[:240]
                    await mark_processing_dispatch_failed(job_id, exc)
        await emit_event(
            db, user_id=user_id, workspace_id=workspace_id,
            event_type="document.uploaded", resource_type="document", resource_id=child_id,
            payload={
                "document_id": child_id, "filename": filename, "status": "chunking",
                "stage": "pdf_split", "source_document_id": doc_id,
                "start_page": start_page, "end_page": end_page,
            },
        )
        results.append({
            "doc_id": child_id, "filename": filename,
            "start_page": start_page, "end_page": end_page,
            "status": "error" if dispatch_error else "chunking",
            "job_id": job_id, "dispatch_error": dispatch_error,
        })
    await audit(
        db, user_id=user_id, action="split_pdf_document",
        resource_type="document", resource_id=doc_id,
        metadata={"workspace_id": workspace_id, "outputs": results},
        ip_address=ip_from(request), user_agent=ua_from(request),
    )
    return {
        "source_document_id": doc_id,
        "created": results,
        "embed_after_processing": body.embed_after_processing,
    }


@router.post("/{doc_id}/convert-pdf")
async def convert_presentation_to_pdf(
    doc_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Queue PDF conversion, extraction, chunking, and embedding for PowerPoint."""
    row = await _get_owned(doc_id, str(current_user["id"]), db)
    if row.get("file_type") != "presentation":
        raise HTTPException(400, "Only PowerPoint documents can use this conversion")
    if row.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(row["workspace_id"]), str(current_user["id"]), "editor")
    user_id = str(row["user_id"])
    workspace_id = str(row["workspace_id"]) if row.get("workspace_id") else None
    payload = {
        "source_gcs_path": row["gcs_source_path"],
        "filename": row["original_name"],
        "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "file_type": "presentation", "redact_pii": False,
        "processing_profile": "presentation_pdf", "embed_after_processing": True,
    }
    job_id, reused = await create_or_reuse_document_job(
        document_id=doc_id, user_id=user_id, workspace_id=workspace_id, payload=payload,
    )
    if not reused:
        if document_dispatch_mode() == "inline":
            background_tasks.add_task(
                process_document_job, job_id=job_id, document_id=doc_id,
                user_id=user_id, workspace_id=workspace_id, **payload,
            )
        else:
            try:
                await dispatch_cloud_run_document_job(job_id)
            except Exception as exc:
                await mark_processing_dispatch_failed(job_id, exc)
                raise HTTPException(503, f"Presentation worker dispatch failed: {str(exc)[:240]}") from exc
    return {"doc_id": doc_id, "job_id": job_id, "job_reused": reused, "status": "chunking"}


# ══════════════════════════════════════════════════════════════════════════════
#  List — user's documents
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/")
async def list_documents(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """
        SELECT d.id, d.original_name, d.file_type, d.file_size, d.status,
               d.chunk_count, d.error_message, d.doc_metadata, d.workspace_id,
               d.doc_type, d.doc_domain, d.doc_language,
               d.created_at, d.updated_at,
               COALESCE(
                 json_agg(json_build_object(
                   'id', t.id::text, 'name', t.name, 'color', t.color
                 )) FILTER (WHERE t.id IS NOT NULL),
                 '[]'::json
               ) AS tags
        FROM documents d
        LEFT JOIN document_tag_map m ON m.document_id = d.id
        LEFT JOIN document_tags    t ON t.id = m.tag_id
        WHERE d.user_id = $1
          AND d.status != 'deleted'
          AND d.workspace_id IS NULL
        GROUP BY d.id
        ORDER BY d.created_at DESC
        """,
        current_user["id"],
    )
    return [_doc_row(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
#  Single document
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{doc_id}")
async def get_document(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await _get_owned(doc_id, current_user["id"], db)
    return _doc_row(row)


# ══════════════════════════════════════════════════════════════════════════════
#  View source — signed GCS URL
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{doc_id}/view-url")
async def view_source_url(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await _get_owned(doc_id, current_user["id"], db)
    url = await gcs.get_signed_url(row["gcs_source_path"])
    return {"url": url, "expires_in_seconds": int(os.getenv("GCS_SIGNED_URL_EXPIRY_SECONDS", "3600"))}


@router.get("/{doc_id}/source-text")
async def view_source_text(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    """Same authorization as view-url above, but returns the source text
    directly instead of a signed GCS URL -- used by ADAR Eats' owner
    "Documents" tab (voice-scribe transcripts). A signed URL has to be
    fetched straight from storage.googleapis.com by the browser, which
    only works if that bucket's CORS config lists the calling origin;
    that's an extra piece of GCP infra this app's deploy doesn't own or
    verify, and nothing had ever actually exercised that path in
    production before (the DocIntel web app's own view-url is defined
    but never called from its UI either). Reading the bytes server-side
    and handing back plain text sidesteps that entirely, the same way
    get_chunk_content below already does for chunk content."""
    row = await _get_owned(doc_id, current_user["id"], db)
    try:
        text = await gcs.download_text(row["gcs_source_path"])
    except Exception as exc:
        raise HTTPException(502, f"Could not read source file: {exc}")
    return {"text": text}


# ══════════════════════════════════════════════════════════════════════════════
#  View as PDF — on-demand docx/doc -> PDF conversion, cached in GCS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{doc_id}/pdf-view-url")
async def view_as_pdf_url(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    """Browsers can't render a Word document inline -- there is no built-in
    engine for Office formats the way there is for PDF. This converts a
    docx/doc to a real PDF once via headless LibreOffice (services.extractor
    .convert_to_pdf) and caches the result in GCS (documents.gcs_pdf_path),
    so the student-view inline viewer can hand the SAME iframe PDF viewer
    already used for native PDFs a real PDF, and later views of the same
    document skip reconversion entirely."""
    row = await _get_owned(doc_id, current_user["id"], db)
    if row.get("gcs_pdf_path"):
        return {"url": await gcs.get_signed_url(row["gcs_pdf_path"]), "cached": True}

    tmp_src = tempfile.NamedTemporaryFile(delete=False)
    tmp_src.close()
    pdf_local_path = None
    try:
        try:
            await gcs.download_to_file(row["gcs_source_path"], tmp_src.name)
        except Exception as exc:
            raise HTTPException(502, f"Could not read source file: {exc}")
        try:
            pdf_local_path = await convert_to_pdf(tmp_src.name, row["original_name"])
        except Exception as exc:
            raise HTTPException(502, f"Could not convert this document to PDF: {exc}")
        with open(pdf_local_path, "rb") as f:
            pdf_bytes = f.read()
        pdf_path = gcs.pdf_preview_path(str(row["user_id"]), doc_id)
        await gcs.upload_bytes(pdf_path, pdf_bytes, "application/pdf")
        await db.execute("UPDATE documents SET gcs_pdf_path=$2, updated_at=NOW() WHERE id=$1", doc_id, pdf_path)
    finally:
        try: os.unlink(tmp_src.name)
        except OSError: pass
        if pdf_local_path:
            try: os.unlink(pdf_local_path)
            except OSError: pass

    return {"url": await gcs.get_signed_url(pdf_path), "cached": False}


# ══════════════════════════════════════════════════════════════════════════════
#  View chunks — reads metadata + content from GCS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{doc_id}/chunks")
async def get_chunks(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await _get_owned(doc_id, current_user["id"], db)
    if row["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document has not been chunked yet")

    meta     = await gcs.download_json(_metadata_path_from_row(row, doc_id))
    return {"document": meta["document"], "chunks": meta["chunks"]}


@router.get("/{doc_id}/chunks/{chunk_index}")
async def get_chunk_content(
    doc_id: str, chunk_index: int, current_user: CurrentUser, redact_pii: bool = False, db=Depends(get_db)
):
    row = await _get_owned(doc_id, current_user["id"], db)
    if row["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document has not been chunked yet")

    meta     = await gcs.download_json(_metadata_path_from_row(row, doc_id))
    chunk    = next((c for c in meta.get("chunks", []) if int(c.get("index")) == chunk_index), None)
    if not chunk or not chunk.get("gcs_path"):
        raise HTTPException(404, "Chunk not found")
    path     = chunk["gcs_path"]
    content  = await gcs.download_text(path)
    content  = redact_text(content, redact_pii).text
    return {"chunk_index": chunk_index, "content": content}


# ══════════════════════════════════════════════════════════════════════════════
#  Embed — user-triggered; reads chunks from GCS, stores vectors in pgvector
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{doc_id}/embed")
async def trigger_embedding(
    doc_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row = await _get_owned(doc_id, current_user["id"], db)
    if row.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(row["workspace_id"]), str(current_user["id"]), "editor")
    _require_restaurant_doc_creator(row, str(current_user["id"]))
    retrying = False
    if row["status"] == "error":
        latest = await db.fetchrow(
            """SELECT job_type, status FROM video_processing_jobs
                 WHERE document_id=$1 ORDER BY created_at DESC LIMIT 1""",
            doc_id,
        )
        retrying = bool(
            latest
            and latest["job_type"] == "process_document_embedding"
            and latest["status"] in {"error", "dead_letter"}
        )
    if row["status"] not in {"chunked", "embedded"} and not retrying:
        raise HTTPException(400, f"Document must be chunked before embedding (current: {row['status']})")

    user_id      = str(current_user["id"])
    workspace_id = row.get("workspace_id")   # propagate workspace scope to chunks
    chunk_count = int(row.get("chunk_count") or 1)
    if not retrying:
        await check_and_log_daily_event(
            db,
            user_id,
            "embedding",
            "max_embeds_day",
            quantity=chunk_count,
            metadata={"doc_id": doc_id, "chunk_count": chunk_count},
        )
    payload = {"processing_profile": "durable_embedding", "chunk_count": chunk_count}
    job_id, reused = await create_or_reuse_document_embedding_job(
        document_id=doc_id,
        user_id=user_id,
        workspace_id=str(workspace_id) if workspace_id else None,
        payload=payload,
    )
    if not reused:
        if document_dispatch_mode() == "inline":
            background_tasks.add_task(
                process_document_embedding_job,
                job_id=job_id,
                document_id=doc_id,
                user_id=user_id,
                workspace_id=str(workspace_id) if workspace_id else None,
                **payload,
            )
        else:
            try:
                await dispatch_cloud_run_document_embedding_job(job_id)
            except Exception as exc:
                await mark_processing_dispatch_failed(job_id, exc)
                raise HTTPException(503, f"Embedding worker dispatch failed: {str(exc)[:240]}") from exc
    return {
        "message": "Embedding queued" if not reused else "Embedding already queued",
        "doc_id": doc_id,
        "job_id": job_id,
        "job_reused": reused,
        "processing_mode": "durable_job",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Delete — removes GCS files + pgvector rows + DB record
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/{doc_id}")
async def delete_document(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row     = await _get_owned(doc_id, str(current_user["id"]), db)
    if row.get("workspace_id"):
        from routes.workspaces import _require_role
        await _require_role(db, str(row["workspace_id"]), str(current_user["id"]), "editor")
    _require_restaurant_doc_creator(row, str(current_user["id"]))
    user_id = str(row["user_id"])
    warnings = []

    # Delete GCS files — non-fatal
    try:
        await gcs.delete_prefix(_document_prefix_from_row(row, doc_id, user_id))
    except Exception as e:
        warnings.append(f"GCS cleanup skipped: {e}")
        print(f"[delete] GCS warning for doc {doc_id}: {e}")

    # Delete pgvector rows — non-fatal
    try:
        await delete_document_vectors(doc_id)
    except Exception as e:
        warnings.append(f"Vector cleanup skipped: {e}")
        print(f"[delete] Vector warning for doc {doc_id}: {e}")

    # Hard-delete from DB — always runs
    await db.execute("DELETE FROM documents WHERE id = $1", doc_id)
    return {"deleted": doc_id, "warnings": warnings}


# ══════════════════════════════════════════════════════════════════════════════
#  Background: chunking pipeline
# ══════════════════════════════════════════════════════════════════════════════
async def _chunk_document(doc_id, user_id, file_path, filename, content_type, ftype, workspace_id=None, redact_pii=False):
    from database.connection import get_pool
    pool = get_pool()

    async def _set_status(s, error=None, chunk_count=None):
        async with pool.acquire() as c:
            if error:
                await c.execute(
                    "UPDATE documents SET status=$1, error_message=$2, updated_at=NOW() WHERE id=$3",
                    s, error, doc_id,
                )
            elif chunk_count is not None:
                await c.execute(
                    "UPDATE documents SET status=$1, chunk_count=$2, updated_at=NOW() WHERE id=$3",
                    s, chunk_count, doc_id,
                )
            else:
                await c.execute("UPDATE documents SET status=$1, updated_at=NOW() WHERE id=$2", s, doc_id)
            if s == "chunked":
                await emit_event(
                    c, user_id=user_id, workspace_id=workspace_id,
                    event_type="document.chunked", resource_type="document", resource_id=doc_id,
                    payload={"document_id": doc_id, "filename": filename, "status": s,
                             "stage": "chunking", "chunk_count": chunk_count or 0},
                )
            elif s == "error":
                await emit_event(
                    c, user_id=user_id, workspace_id=workspace_id,
                    event_type="document.failed", resource_type="document", resource_id=doc_id,
                    payload={"document_id": doc_id, "filename": filename, "status": s,
                             "stage": "chunking", "message": error or "Document chunking failed"},
                )

    try:
        # Extract text
        text = await extract_text(file_path, filename, content_type)

        # ── Table extraction (PDF only) ────────────────────────────────────
        if ftype == "pdf":
            tables = extract_tables_from_pdf(file_path)
            if tables:
                text = text + tables_to_text(tables)
                log.info(f"[{doc_id}] Extracted {len(tables)} tables from {filename}")

        text = sanitize_text_for_storage(text)
        redaction = redact_text(text, enabled=redact_pii)
        text = redaction.text
        if redact_pii:
            log.info("[%s] PII redaction applied to %s: %s redactions", doc_id, filename, redaction.total)
            async with get_pool().acquire() as _pii_conn:
                await _pii_conn.execute(
                    """
                    UPDATE documents
                       SET doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                           updated_at = NOW()
                     WHERE id = $2
                    """,
                    json.dumps({
                        "pii_redaction": {
                            "enabled": True,
                            "total": redaction.total,
                            "counts": redaction.counts,
                        }
                    }),
                    doc_id,
                )

        # ── Document classification ────────────────────────────────────────
        classification = await classify_document(
            text_sample=text,
            filename=filename,
            file_type=ftype,
        )
        async with get_pool().acquire() as _cls_conn:
            await _cls_conn.execute(
                "UPDATE documents SET doc_type=$1, doc_domain=$2, doc_language=$3, classified_at=NOW() WHERE id=$4",
                classification["doc_type"],
                classification["doc_domain"],
                classification["doc_language"],
                doc_id,
            )
        log.info(
            "[%s] Classified as %s/%s confidence=%.0f%% source=%s sample_chars=%s reason=%s",
            doc_id,
            classification["doc_type"],
            classification["doc_domain"],
            classification.get("confidence", 0.0) * 100,
            classification.get("source", "unknown"),
            classification.get("sample_chars", "unknown"),
            classification.get("reasoning", ""),
        )

        # Build document-level metadata attached to every chunk
        doc_meta = {
            "document_id":  doc_id,
            "user_id":      user_id,
            "filename":     filename,
            "file_type":    ftype,
            "pii_redacted": redact_pii,
        }

        # Chunk
        chunks = chunk_text(text, doc_meta=doc_meta)
        if not chunks:
            await _set_status("error", error="No text content found")
            return

        # Save each chunk to GCS
        for chunk in chunks:
            await gcs.upload_text(
                gcs.chunk_path(user_id, doc_id, chunk.index),
                chunk.text,
            )

        # Save metadata.json to GCS
        now = datetime.now(timezone.utc).isoformat()
        meta_obj = {
            "document": {
                "id":           doc_id,
                "user_id":      user_id,
                "filename":     filename,
                "file_type":    ftype,
                "total_chunks": len(chunks),
                "created_at":   now,
                "pii_redaction": {
                    "enabled": bool(redact_pii),
                    "total": redaction.total,
                    "counts": redaction.counts,
                },
            },
            "chunks": [
                {
                    "index":      c.index,
                    "word_count": c.word_count,
                    "char_count": c.char_count,
                    "gcs_path":   gcs.chunk_path(user_id, doc_id, c.index),
                }
                for c in chunks
            ],
        }
        await gcs.upload_json(gcs.metadata_path(user_id, doc_id), meta_obj)

        # Mark complete
        await _set_status("chunked", chunk_count=len(chunks))

    except Exception as exc:
        await _set_status("error", error=str(exc)[:500])
    finally:
        try: os.unlink(file_path)
        except OSError: pass


# ══════════════════════════════════════════════════════════════════════════════
#  Background: embedding pipeline
# ══════════════════════════════════════════════════════════════════════════════
async def _embed_document(doc_id: str, user_id: str, workspace_id: str | None = None):
    from database.connection import get_pool
    pool = get_pool()

    async def _set_status(s, error=None):
        async with pool.acquire() as c:
            if error:
                await c.execute(
                    "UPDATE documents SET status=$1, error_message=$2, updated_at=NOW() WHERE id=$3",
                    s, error, doc_id,
                )
            else:
                await c.execute("UPDATE documents SET status=$1, updated_at=NOW() WHERE id=$2", s, doc_id)
            await emit_event(
                c, user_id=user_id, workspace_id=workspace_id,
                event_type="document.embedded" if s == "embedded" else "document.failed",
                resource_type="document", resource_id=doc_id,
                payload={"document_id": doc_id, "status": s, "stage": "embedding",
                         "message": error or ("Document embedding completed" if s == "embedded" else "Document embedding failed")},
            )

    try:
        # Read chunks from GCS metadata
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT gcs_chunks_dir, guest_session_id FROM documents WHERE id=$1", doc_id)
        row_data = dict(row) if row else {}
        meta_path = _metadata_path_from_row(row_data, doc_id, user_id)
        meta   = await gcs.download_json(meta_path)
        chunks = meta["chunks"]

        # Delete any existing vectors for this document (re-embed support)
        await delete_document_vectors(doc_id)

        for chunk_info in chunks:
            content   = sanitize_text_for_storage(await gcs.download_text(chunk_info["gcs_path"]))
            embedding = await embed(content)
            await store_chunk(
                document_id    = doc_id,
                user_id        = user_id,
                workspace_id   = workspace_id,
                chunk_index    = chunk_info["index"],
                chunk_total    = len(chunks),
                content        = content,
                embedding      = embedding,
                chunk_metadata = chunk_info,
            )
            await asyncio.sleep(0.08)   # rate limit buffer

        if row_data.get("guest_session_id"):
            async with pool.acquire() as c:
                await c.execute(
                    "UPDATE document_chunks SET guest_session_id=$1 WHERE document_id=$2",
                    row_data["guest_session_id"],
                    doc_id,
                )

        await _set_status("embedded")
        async with pool.acquire() as _c:
            # Fetch user email + notification preference
            user_row = await _c.fetchrow(
                "SELECT email, notify_on_embed FROM users WHERE id=$1", user_id
            )
            if user_row and user_row["notify_on_embed"]:
                doc_row = await _c.fetchrow(
                    "SELECT original_name FROM documents WHERE id=$1", doc_id
                )
                await send_embed_complete(
                    user_email  = user_row["email"],
                    doc_name    = doc_row["original_name"] if doc_row else doc_id,
                    chunk_count = len(chunks),
                )
    except Exception as exc:
        await _set_status("error", error=str(exc)[:500])


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def _require_restaurant_doc_creator(row: dict, user_id: str) -> None:
    """Restaurant-vertical documents (menu-scribe recordings/transcripts)
    stay creator-editable only, even inside a shared workspace -- a
    workspace editor can see every restaurant document (handled by
    _get_owned/list_workspace_documents, unchanged), but can mutate
    (delete, re-embed, reclassify) only the ones they personally
    recorded. This mirrors the same rule already enforced for the scribe
    transcript itself (get_own_vertical_run in
    services/vertical_agent_runs.py) and is intentionally scoped to
    doc_domain='restaurant' only -- every other vertical keeps DocIntel's
    original any-workspace-editor-can-mutate model, which callers of this
    function have relied on since before this rule existed."""
    if row.get("doc_domain") == "restaurant" and str(row.get("user_id")) != user_id:
        raise HTTPException(403, "Only the person who recorded this can edit it.")


async def _get_owned(doc_id: str, user_id: str, db) -> dict:
    """Return doc if owned by user OR if user is a member of the doc's workspace."""
    row = await db.fetchrow(
        """SELECT d.* FROM documents d
           WHERE d.id = $1
             AND d.status != 'deleted'
             AND (
               d.user_id = $2
               OR EXISTS (
                 SELECT 1 FROM workspace_members wm
                 WHERE wm.workspace_id = d.workspace_id
                   AND wm.user_id = $2
               )
             )""",
        doc_id, user_id,
    )
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)


def _metadata_path_from_row(row: dict, doc_id: str, user_id: str | None = None) -> str:
    chunks_dir = (row or {}).get("gcs_chunks_dir")
    if chunks_dir:
        return chunks_dir.rstrip("/") + "/_metadata.json"
    if not user_id:
        user_id = str((row or {}).get("user_id") or "")
    return gcs.metadata_path(user_id, doc_id)


def _document_prefix_from_row(row: dict, doc_id: str, user_id: str) -> str:
    src = (row or {}).get("gcs_source_path") or ""
    marker = f"/documents/{doc_id}/"
    if marker in src:
        return src.split(marker, 1)[0] + marker
    chunks_dir = (row or {}).get("gcs_chunks_dir") or ""
    if marker in chunks_dir:
        return chunks_dir.split(marker, 1)[0] + marker
    return f"users/{user_id}/documents/{doc_id}/"


def _doc_row(row: dict) -> dict:
    r = dict(row)
    r["id"]           = str(r["id"])
    r["user_id"]      = str(r.get("user_id", ""))
    r["workspace_id"] = str(r["workspace_id"]) if r.get("workspace_id") else None
    r["doc_type"]     = r.get("doc_type") or "general"
    r["doc_domain"]   = r.get("doc_domain") or "general"
    r["doc_language"] = r.get("doc_language") or "en"
    raw_meta = r.get("doc_metadata") or {}
    if isinstance(raw_meta, str):
        import json as _json
        raw_meta = _json.loads(raw_meta)
    r["doc_metadata"] = raw_meta
    # Tags: may arrive as JSON string or list
    raw_tags = r.get("tags", [])
    if isinstance(raw_tags, str):
        import json as _json
        raw_tags = _json.loads(raw_tags)
    r["tags"] = [t for t in (raw_tags or []) if t.get("id")]
    r["created_at"]   = r["created_at"].isoformat() if r.get("created_at") else None
    r["updated_at"]   = r["updated_at"].isoformat() if r.get("updated_at") else None
    return r

@router.post("/{doc_id}/classify")
async def reclassify_document(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    """Re-run classification on an existing document — useful if it was previously stored as general."""
    row = await _get_owned(doc_id, str(current_user["id"]), db)
    if not row:
        raise HTTPException(404, "Document not found")
    _require_restaurant_doc_creator(row, str(current_user["id"]))

    # Download text from GCS and re-classify
    import tempfile, os
    from services.extractor import extract_text as _extract

    ftype = row["file_type"] or "pdf"
    tmp_path = None

    try:
        file_bytes = await gcs.download_bytes(row["gcs_source_path"])
        with tempfile.NamedTemporaryFile(suffix=f".{ftype}", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        text = await _extract(tmp_path, row["original_name"], f"application/{ftype}")
    except Exception as e:
        raise HTTPException(500, f"Could not download document for re-classification: {e}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    result = await classify_document(
        text_sample=text,
        filename=row["original_name"],
        file_type=ftype,
    )
    await db.execute(
        "UPDATE documents SET doc_type=$1, doc_domain=$2, doc_language=$3, classified_at=NOW() WHERE id=$4",
        result["doc_type"], result["doc_domain"], result["doc_language"], doc_id
    )
    log.info(f"Re-classified {doc_id}: {result['doc_type']}/{result['doc_domain']}")
    return {**result, "doc_id": doc_id}
