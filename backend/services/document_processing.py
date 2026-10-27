from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from database.connection import get_pool
from services import storage as gcs
from services import video_checkpoints as checkpoints
from services.chunker import chunk_text
from services.classifier import classify_document
from services.extractor import extract_text, extract_tables_from_pdf, tables_to_text
from services.mcp_enterprise import emit_event
from services.pii import redact_text
from services.text_safety import sanitize_text_for_storage

log = logging.getLogger("docintel.document.worker")

DOCUMENT_JOB_MAX_ATTEMPTS = max(1, int(os.getenv("DOCUMENT_JOB_MAX_ATTEMPTS", "3")))
DOCUMENT_STAGE_HEARTBEAT_SECONDS = max(10, int(os.getenv("DOCUMENT_STAGE_HEARTBEAT_SECONDS", "30")))
DOCUMENT_TABLE_EXTRACTION_MAX_MB = max(1, int(os.getenv("DOCUMENT_TABLE_EXTRACTION_MAX_MB", "50")))


def extracted_text_path(user_id: str, document_id: str) -> str:
    return f"users/{user_id}/documents/{document_id}/processing/extracted.txt"


async def process_document_job(
    *, job_id: str, document_id: str, user_id: str, workspace_id: str | None,
    source_gcs_path: str, filename: str, content_type: str,
    file_type: str, redact_pii: bool = False,
    processing_profile: str = "large_document",
    split_source_gcs_path: str | None = None,
    split_start_page: int | None = None,
    split_end_page: int | None = None,
    embed_after_processing: bool = False,
) -> dict[str, Any]:
    """Durably extract, classify, and chunk a large document.

    Checkpoints use the existing processing checkpoint store shared with the
    video worker. Large text stays in GCS; checkpoint rows retain only bounded
    metadata and object paths.
    """
    pool = get_pool()
    owner = checkpoints.worker_id()
    payload = {
        "source_gcs_path": source_gcs_path,
        "filename": filename,
        "content_type": content_type,
        "file_type": file_type,
        "redact_pii": redact_pii,
        "processing_profile": processing_profile,
        "split_source_gcs_path": split_source_gcs_path,
        "split_start_page": split_start_page,
        "split_end_page": split_end_page,
        "embed_after_processing": embed_after_processing,
    }
    async with pool.acquire() as conn:
        acquired = await conn.fetchrow(
            """
            UPDATE video_processing_jobs
               SET status='running', error_message=NULL, completed_at=NULL,
                   started_at=COALESCE(started_at, NOW()),
                   attempt_count=COALESCE(attempt_count, 0) + 1,
                   heartbeat_at=NOW(), lease_owner=$1,
                   lease_expires_at=NOW() + ($2 * INTERVAL '1 second'),
                   updated_at=NOW()
             WHERE id=$3 AND job_type='process_document'
               AND (lease_owner IS NULL OR lease_expires_at IS NULL
                    OR lease_expires_at < NOW() OR lease_owner=$1)
            RETURNING attempt_count
            """,
            owner, checkpoints.LEASE_SECONDS, job_id,
        )
        if not acquired:
            log.info("Document job %s is owned by another live worker", job_id)
            return {"job_id": job_id, "document_id": document_id, "status": "already_running"}

    await _set_progress(
        document_id,
        "splitting" if split_source_gcs_path else "downloading",
        2 if split_source_gcs_path else 5,
        "Creating the requested PDF page range." if split_source_gcs_path else "Downloading source document.",
    )
    await checkpoints.heartbeat(job_id, owner)

    try:
        if split_source_gcs_path:
            split_output = await checkpoints.completed_output(job_id, "document_split")
            if split_output is None:
                started = await checkpoints.begin(
                    job_id=job_id, document_id=document_id, stage="document_split",
                    input_data={
                        "source_gcs_path": split_source_gcs_path,
                        "start_page": split_start_page,
                        "end_page": split_end_page,
                    },
                    owner=owner,
                )
                if not started:
                    return {"job_id": job_id, "document_id": document_id, "status": "already_running"}
                source_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                source_tmp.close()
                output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                output_tmp.close()
                try:
                    await gcs.download_to_file(split_source_gcs_path, source_tmp.name)
                    await checkpoints.heartbeat(job_id, owner)
                    page_count, file_size = await asyncio.to_thread(
                        _split_pdf_range,
                        source_tmp.name,
                        output_tmp.name,
                        int(split_start_page or 0),
                        int(split_end_page or 0),
                    )
                    await gcs.upload_file(source_gcs_path, output_tmp.name, "application/pdf")
                    split_output = {
                        "source_page_count": page_count,
                        "start_page": split_start_page,
                        "end_page": split_end_page,
                        "file_size": file_size,
                        "gcs_path": source_gcs_path,
                    }
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE documents SET file_size=$1,
                                      doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $2::jsonb,
                                      updated_at=NOW() WHERE id=$3""",
                            file_size,
                            json.dumps({
                                "source_page_count": page_count,
                                "page_count": int(split_end_page) - int(split_start_page) + 1,
                            }),
                            document_id,
                        )
                    await checkpoints.complete(
                        job_id=job_id, stage="document_split",
                        output_data=split_output, owner=owner,
                    )
                except Exception as exc:
                    await checkpoints.fail(job_id=job_id, stage="document_split", error=exc, owner=owner)
                    raise
                finally:
                    for path in (source_tmp.name, output_tmp.name):
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
            await _set_progress(document_id, "split_complete", 8, "PDF range created. Extracting content next.")

        extraction = await checkpoints.completed_output(job_id, "document_extract")
        if extraction is None:
            started = await checkpoints.begin(
                job_id=job_id, document_id=document_id, stage="document_extract",
                input_data={"source_gcs_path": source_gcs_path}, owner=owner,
            )
            if not started:
                return {"job_id": job_id, "document_id": document_id, "status": "already_running"}
            suffix = os.path.splitext(filename)[1]
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.close()
            converted_pdf_path = None
            try:
                await gcs.download_to_file(source_gcs_path, tmp.name)
                await _set_progress(document_id, "extracting_text", 15, "Extracting document text.")
                extraction_path = tmp.name
                extraction_filename = filename
                extraction_content_type = content_type
                extraction_file_type = file_type
                if file_type == "presentation":
                    from services.extractor import convert_to_pdf

                    await _set_progress(document_id, "converting_pdf", 12, "Converting PowerPoint to PDF.")
                    converted_pdf_path = await _with_heartbeat(
                        convert_to_pdf(tmp.name, filename), job_id, owner,
                    )
                    preview_path = gcs.pdf_preview_path(user_id, document_id)
                    await gcs.upload_file(preview_path, converted_pdf_path, "application/pdf")
                    pdf_size = os.path.getsize(converted_pdf_path)
                    page_count = await asyncio.to_thread(_pdf_page_count, converted_pdf_path)
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE documents SET gcs_pdf_path=$1,
                                      doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $2::jsonb,
                                      updated_at=NOW() WHERE id=$3""",
                            preview_path,
                            json.dumps({
                                "pdf_conversion": {"status": "completed", "file_size": pdf_size},
                                "page_count": page_count,
                            }),
                            document_id,
                        )
                    extraction_path = converted_pdf_path
                    extraction_filename = os.path.splitext(filename)[0] + ".pdf"
                    extraction_content_type = "application/pdf"
                    extraction_file_type = "pdf"
                    await _set_progress(document_id, "extracting_text", 20, "PowerPoint converted. Extracting slide content.")
                text = await _with_heartbeat(
                    extract_text(extraction_path, extraction_filename, extraction_content_type), job_id, owner,
                )
                if extraction_file_type == "pdf":
                    source_mb = os.path.getsize(extraction_path) / (1024 * 1024)
                    if source_mb <= DOCUMENT_TABLE_EXTRACTION_MAX_MB:
                        await _set_progress(document_id, "extracting_tables", 32, "Extracting PDF tables.")
                        tables = await _with_heartbeat(
                            asyncio.to_thread(extract_tables_from_pdf, extraction_path), job_id, owner,
                        )
                        if tables:
                            text += tables_to_text(tables)
                    else:
                        log.info(
                            "Skipping deep table extraction for %.1f MB document %s (limit %s MB)",
                            source_mb, document_id, DOCUMENT_TABLE_EXTRACTION_MAX_MB,
                        )
                        await _set_progress(
                            document_id, "extracting_tables", 35,
                            "Text extracted. Deep table detection was skipped for this large PDF.",
                        )
                text = sanitize_text_for_storage(text)
                redaction = redact_text(text, enabled=redact_pii)
                text = redaction.text
                if not text.strip():
                    raise RuntimeError("No text content found")
                text_path = extracted_text_path(user_id, document_id)
                await gcs.upload_text(text_path, text)
                extraction = {
                    "gcs_path": text_path,
                    "characters": len(text),
                    "pii_redaction": {
                        "enabled": bool(redact_pii),
                        "total": redaction.total,
                        "counts": redaction.counts,
                    },
                }
                await checkpoints.complete(
                    job_id=job_id, stage="document_extract", output_data=extraction, owner=owner,
                )
            except Exception as exc:
                await checkpoints.fail(job_id=job_id, stage="document_extract", error=exc, owner=owner)
                raise
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                if converted_pdf_path:
                    try:
                        os.unlink(converted_pdf_path)
                    except OSError:
                        pass

        await checkpoints.heartbeat(job_id, owner)
        text = await gcs.download_text(extraction["gcs_path"])
        classification = await checkpoints.completed_output(job_id, "document_classify")
        if classification is None:
            started = await checkpoints.begin(
                job_id=job_id, document_id=document_id, stage="document_classify",
                input_data={"filename": filename, "file_type": file_type}, owner=owner,
            )
            if not started:
                return {"job_id": job_id, "document_id": document_id, "status": "already_running"}
            try:
                await _set_progress(document_id, "classifying", 45, "Classifying document content.")
                classification = await _with_heartbeat(
                    classify_document(text, filename, file_type), job_id, owner,
                )
                async with pool.acquire() as conn:
                    await conn.execute(
                        """UPDATE documents SET doc_type=$1, doc_domain=$2, doc_language=$3,
                                  classified_at=NOW(), updated_at=NOW() WHERE id=$4""",
                        classification["doc_type"], classification["doc_domain"],
                        classification["doc_language"], document_id,
                    )
                await checkpoints.complete(
                    job_id=job_id, stage="document_classify",
                    output_data=classification, owner=owner,
                )
            except Exception as exc:
                await checkpoints.fail(job_id=job_id, stage="document_classify", error=exc, owner=owner)
                raise

        doc_meta = {
            "document_id": document_id,
            "user_id": user_id,
            "filename": filename,
            "file_type": file_type,
            "pii_redacted": redact_pii,
        }
        chunks = chunk_text(text, doc_meta=doc_meta)
        if not chunks:
            raise RuntimeError("No text content found")

        await _set_progress(document_id, "chunking", 55, f"Writing {len(chunks)} searchable chunks.")
        for position, chunk in enumerate(chunks, 1):
            item_key = f"{chunk.index:06d}"
            saved = await checkpoints.completed_output(job_id, "document_chunk", item_key)
            if saved is None:
                started = await checkpoints.begin(
                    job_id=job_id, document_id=document_id, stage="document_chunk",
                    item_key=item_key, input_data={"chunk_index": chunk.index}, owner=owner,
                )
                if not started:
                    raise RuntimeError(f"Chunk {chunk.index} is leased by another document worker")
                try:
                    path = gcs.chunk_path(user_id, document_id, chunk.index)
                    await gcs.upload_text(path, chunk.text)
                    await checkpoints.complete(
                        job_id=job_id, stage="document_chunk", item_key=item_key,
                        output_data={"gcs_path": path, "word_count": chunk.word_count,
                                     "char_count": chunk.char_count}, owner=owner,
                    )
                except Exception as exc:
                    await checkpoints.fail(
                        job_id=job_id, stage="document_chunk", item_key=item_key,
                        error=exc, owner=owner,
                    )
                    raise
            if position % 20 == 0 or position == len(chunks):
                await checkpoints.heartbeat(job_id, owner)
                progress = 55 + int(40 * position / len(chunks))
                await _set_progress(
                    document_id, "chunking", progress,
                    f"Stored {position} of {len(chunks)} chunks.",
                )

        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "document": {
                "id": document_id, "user_id": user_id, "filename": filename,
                "file_type": file_type, "total_chunks": len(chunks), "created_at": now,
                "pii_redaction": extraction.get("pii_redaction", {}),
            },
            "chunks": [
                {
                    "index": chunk.index, "word_count": chunk.word_count,
                    "char_count": chunk.char_count,
                    "gcs_path": gcs.chunk_path(user_id, document_id, chunk.index),
                }
                for chunk in chunks
            ],
        }
        await gcs.upload_json(gcs.metadata_path(user_id, document_id), metadata)
        output = {"document_id": document_id, "chunk_count": len(chunks), "status": "chunked"}
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE video_processing_jobs
                      SET status='completed', output_data=$1::jsonb, completed_at=NOW(),
                          heartbeat_at=NOW(), lease_owner=NULL, lease_expires_at=NULL, updated_at=NOW()
                    WHERE id=$2""",
                json.dumps(output), job_id,
            )
            await conn.execute(
                """UPDATE documents SET status='chunked', chunk_count=$1, error_message=NULL,
                          doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $2::jsonb,
                          updated_at=NOW() WHERE id=$3""",
                len(chunks), json.dumps({
                    "pii_redaction": extraction.get("pii_redaction", {}),
                    "document_job": {"job_id": job_id, "status": "completed", "progress_pct": 100},
                    "document_progress": {
                        "step": "completed", "progress_pct": 100,
                        "message": "Document processing completed.",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }), document_id,
            )
            await emit_event(
                conn, user_id=user_id, workspace_id=workspace_id,
                event_type="document.chunked", resource_type="document", resource_id=document_id,
                payload={"document_id": document_id, "filename": filename,
                         "status": "chunked", "stage": "chunking", "chunk_count": len(chunks)},
            )
        if embed_after_processing:
            try:
                await _queue_document_embedding(
                    document_id=document_id, user_id=user_id,
                    workspace_id=workspace_id, chunk_count=len(chunks),
                )
                output["embedding_status"] = "queued"
            except Exception as exc:
                log.exception("Split document %s was chunked but automatic embedding could not be queued", document_id)
                output["embedding_status"] = "not_queued"
                output["embedding_error"] = str(exc)[:300]
                async with pool.acquire() as conn:
                    await conn.execute(
                        """UPDATE documents
                              SET doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                                  updated_at=NOW() WHERE id=$2""",
                        json.dumps({"automatic_embedding": {
                            "status": "not_queued", "error": str(exc)[:300],
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }}),
                        document_id,
                    )
        return output
    except Exception as exc:
        error = str(exc)[:500]
        log.exception("Durable document processing failed for %s", document_id)
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE video_processing_jobs
                      SET status=CASE WHEN attempt_count >= $3 THEN 'dead_letter' ELSE 'error' END,
                          error_message=$1, completed_at=NOW(), lease_owner=NULL,
                          lease_expires_at=NULL, updated_at=NOW() WHERE id=$2""",
                error, job_id, DOCUMENT_JOB_MAX_ATTEMPTS,
            )
            await conn.execute(
                """UPDATE documents SET status='error', error_message=$1,
                          doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $2::jsonb,
                          updated_at=NOW() WHERE id=$3""",
                error, json.dumps({
                    "document_job": {"job_id": job_id, "status": "error"},
                    "document_progress": {
                        "step": "failed", "progress_pct": 100, "message": error,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }), document_id,
            )
            await emit_event(
                conn, user_id=user_id, workspace_id=workspace_id,
                event_type="document.failed", resource_type="document", resource_id=document_id,
                payload={"document_id": document_id, "filename": filename,
                         "status": "error", "stage": "large_document_processing", "message": error},
            )
        raise


def _split_pdf_range(source_path: str, output_path: str, start_page: int, end_page: int) -> tuple[int, int]:
    """Write an inclusive, one-based PDF range and return source pages + output size."""
    import fitz

    if start_page < 1 or end_page < start_page:
        raise ValueError("Page ranges must use positive page numbers with the end page after the start page")
    source = fitz.open(source_path)
    output = fitz.open()
    try:
        page_count = source.page_count
        if end_page > page_count:
            raise ValueError(f"Requested page {end_page}, but the source PDF contains {page_count} pages")
        output.insert_pdf(source, from_page=start_page - 1, to_page=end_page - 1)
        output.save(output_path, garbage=4, deflate=True)
        return page_count, os.path.getsize(output_path)
    finally:
        output.close()
        source.close()


def _pdf_page_count(path: str) -> int:
    import fitz

    document = fitz.open(path)
    try:
        return document.page_count
    finally:
        document.close()


async def _queue_document_embedding(
    *, document_id: str, user_id: str, workspace_id: str | None, chunk_count: int,
) -> None:
    """Apply normal usage governance and queue the existing durable embedding worker."""
    from services.document_embedding import process_document_embedding_job
    from services.usage import check_and_log_daily_event
    from services.video_dispatch import (
        create_or_reuse_document_embedding_job,
        dispatch_cloud_run_document_embedding_job,
        document_dispatch_mode,
        mark_processing_dispatch_failed,
    )

    async with get_pool().acquire() as conn:
        await check_and_log_daily_event(
            conn, user_id, "embedding", "max_embeds_day", quantity=chunk_count,
            metadata={"doc_id": document_id, "chunk_count": chunk_count, "automatic": True},
        )
    payload = {"processing_profile": "durable_embedding", "chunk_count": chunk_count}
    embedding_job_id, reused = await create_or_reuse_document_embedding_job(
        document_id=document_id, user_id=user_id, workspace_id=workspace_id, payload=payload,
    )
    if reused:
        return
    if document_dispatch_mode() == "inline":
        await process_document_embedding_job(
            job_id=embedding_job_id, document_id=document_id, user_id=user_id,
            workspace_id=workspace_id, **payload,
        )
        return
    try:
        await dispatch_cloud_run_document_embedding_job(embedding_job_id)
    except Exception as exc:
        await mark_processing_dispatch_failed(embedding_job_id, exc)
        raise


async def _set_progress(document_id: str, step: str, progress_pct: int, message: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """UPDATE documents
                  SET doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                      updated_at=NOW()
                WHERE id=$2""",
            json.dumps({"document_progress": {
                "step": step, "progress_pct": max(0, min(100, progress_pct)),
                "message": message, "updated_at": datetime.now(timezone.utc).isoformat(),
            }}),
            document_id,
        )


async def _with_heartbeat(awaitable, job_id: str, owner: str):
    """Keep a durable job lease alive while one long stage is awaiting work."""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=DOCUMENT_STAGE_HEARTBEAT_SECONDS)
            if done:
                return await task
            await checkpoints.heartbeat(job_id, owner)
    finally:
        if not task.done():
            task.cancel()
