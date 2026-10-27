from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from database.connection import get_pool
from services import storage as gcs
from services import video_checkpoints as checkpoints
from services.llm import embed
from services.mcp_enterprise import emit_event
from services.notifications import send_embed_complete
from services.text_safety import sanitize_text_for_storage
from services.vectordb import delete_document_vectors, replace_chunk

log = logging.getLogger("docintel.document.embedding.worker")

DOCUMENT_JOB_MAX_ATTEMPTS = max(1, int(os.getenv("DOCUMENT_JOB_MAX_ATTEMPTS", "3")))
DOCUMENT_EMBED_CONCURRENCY = max(1, min(10, int(os.getenv("DOCUMENT_EMBED_CONCURRENCY", "3"))))


async def process_document_embedding_job(
    *, job_id: str, document_id: str, user_id: str,
    workspace_id: str | None, processing_profile: str = "durable_embedding",
    chunk_count: int | None = None,
) -> dict[str, Any]:
    """Embed document chunks with per-chunk durable checkpoints."""
    pool = get_pool()
    owner = checkpoints.worker_id()
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
             WHERE id=$3 AND job_type='process_document_embedding'
               AND (lease_owner IS NULL OR lease_expires_at IS NULL
                    OR lease_expires_at < NOW() OR lease_owner=$1)
            RETURNING attempt_count
            """,
            owner, checkpoints.LEASE_SECONDS, job_id,
        )
        if not acquired:
            log.info("Embedding job %s is owned by another live worker", job_id)
            return {"job_id": job_id, "document_id": document_id, "status": "already_running"}

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT gcs_chunks_dir, guest_session_id, original_name
                     FROM documents WHERE id=$1""",
                document_id,
            )
        if not row:
            raise RuntimeError("Document not found")
        metadata_path = (
            row["gcs_chunks_dir"].rstrip("/") + "/_metadata.json"
            if row["gcs_chunks_dir"]
            else gcs.metadata_path(user_id, document_id)
        )
        metadata = await gcs.download_json(metadata_path)
        chunks = metadata.get("chunks") or []
        if not chunks:
            raise RuntimeError("Document has no chunks to embed")

        prepared = await checkpoints.completed_output(job_id, "document_embedding_prepare")
        if prepared is None:
            started = await checkpoints.begin(
                job_id=job_id,
                document_id=document_id,
                stage="document_embedding_prepare",
                input_data={"chunk_count": len(chunks), "processing_profile": processing_profile},
                owner=owner,
            )
            if not started:
                return {"job_id": job_id, "document_id": document_id, "status": "already_running"}
            try:
                await delete_document_vectors(document_id)
                await checkpoints.complete(
                    job_id=job_id,
                    stage="document_embedding_prepare",
                    output_data={"vectors_cleared": True, "chunk_count": len(chunks)},
                    owner=owner,
                )
            except Exception as exc:
                await checkpoints.fail(
                    job_id=job_id, stage="document_embedding_prepare", error=exc, owner=owner,
                )
                raise

        await _set_progress(document_id, 2, f"Embedding {len(chunks)} document chunks.")

        async def embed_one(chunk_info: dict[str, Any]) -> None:
            index = int(chunk_info["index"])
            item_key = f"{index:06d}"
            if await checkpoints.completed_output(job_id, "document_embedding_chunk", item_key) is not None:
                return
            started = await checkpoints.begin(
                job_id=job_id,
                document_id=document_id,
                stage="document_embedding_chunk",
                item_key=item_key,
                input_data={"chunk_index": index, "gcs_path": chunk_info.get("gcs_path")},
                owner=owner,
            )
            if not started:
                raise RuntimeError(f"Embedding chunk {index} is leased by another worker")
            try:
                content = sanitize_text_for_storage(await gcs.download_text(chunk_info["gcs_path"]))
                vector = await embed(content)
                await replace_chunk(
                    document_id=document_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    chunk_index=index,
                    chunk_total=len(chunks),
                    content=content,
                    embedding=vector,
                    chunk_metadata=chunk_info,
                )
                await checkpoints.complete(
                    job_id=job_id,
                    stage="document_embedding_chunk",
                    item_key=item_key,
                    output_data={"chunk_index": index, "stored": True},
                    owner=owner,
                )
            except Exception as exc:
                await checkpoints.fail(
                    job_id=job_id,
                    stage="document_embedding_chunk",
                    item_key=item_key,
                    error=exc,
                    owner=owner,
                )
                raise

        for offset in range(0, len(chunks), DOCUMENT_EMBED_CONCURRENCY):
            batch = chunks[offset:offset + DOCUMENT_EMBED_CONCURRENCY]
            await asyncio.gather(*(embed_one(chunk) for chunk in batch))
            completed = min(offset + len(batch), len(chunks))
            await checkpoints.heartbeat(job_id, owner)
            await _set_progress(
                document_id,
                2 + int(96 * completed / len(chunks)),
                f"Embedded {completed} of {len(chunks)} chunks.",
            )

        if row["guest_session_id"]:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE document_chunks SET guest_session_id=$1 WHERE document_id=$2",
                    row["guest_session_id"], document_id,
                )

        output = {"document_id": document_id, "chunk_count": len(chunks), "status": "embedded"}
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE video_processing_jobs
                      SET status='completed', output_data=$1::jsonb, completed_at=NOW(),
                          heartbeat_at=NOW(), lease_owner=NULL, lease_expires_at=NULL,
                          updated_at=NOW() WHERE id=$2""",
                json.dumps(output), job_id,
            )
            await conn.execute(
                """UPDATE documents SET status='embedded', error_message=NULL,
                          doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                          updated_at=NOW() WHERE id=$2""",
                json.dumps({
                    "document_embedding_job": {
                        "job_id": job_id, "status": "completed", "progress_pct": 100,
                    },
                    "document_progress": {
                        "step": "embedded", "progress_pct": 100,
                        "message": "Document embedding completed.",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }),
                document_id,
            )
            await emit_event(
                conn,
                user_id=user_id,
                workspace_id=workspace_id,
                event_type="document.embedded",
                resource_type="document",
                resource_id=document_id,
                payload={"document_id": document_id, "status": "embedded",
                         "stage": "embedding", "chunk_count": len(chunks)},
            )

        try:
            async with pool.acquire() as conn:
                user = await conn.fetchrow(
                    "SELECT email, notify_on_embed FROM users WHERE id=$1", user_id,
                )
            if user and user["notify_on_embed"]:
                await send_embed_complete(
                    user_email=user["email"],
                    doc_name=row["original_name"] or document_id,
                    chunk_count=len(chunks),
                )
        except Exception:
            log.exception("Embedding completed but notification failed for %s", document_id)
        return output
    except Exception as exc:
        error = str(exc)[:500]
        log.exception("Durable document embedding failed for %s", document_id)
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
                error,
                json.dumps({
                    "document_embedding_job": {"job_id": job_id, "status": "error"},
                    "document_progress": {
                        "step": "embedding_failed", "progress_pct": 100,
                        "message": error, "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }),
                document_id,
            )
            await emit_event(
                conn,
                user_id=user_id,
                workspace_id=workspace_id,
                event_type="document.failed",
                resource_type="document",
                resource_id=document_id,
                payload={"document_id": document_id, "status": "error",
                         "stage": "embedding", "message": error},
            )
        raise


async def _set_progress(document_id: str, progress_pct: int, message: str) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            """UPDATE documents
                  SET status='embedding',
                      doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                      updated_at=NOW()
                WHERE id=$2""",
            json.dumps({"document_progress": {
                "step": "embedding", "progress_pct": max(0, min(100, progress_pct)),
                "message": message, "updated_at": datetime.now(timezone.utc).isoformat(),
            }}),
            document_id,
        )
