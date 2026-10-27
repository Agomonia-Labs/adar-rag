from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

from database.connection import get_pool


ACTIVE_VIDEO_JOB_STATUSES = ("queued", "dispatching", "running")
VIDEO_JOB_STALE_SECONDS = max(300, int(os.getenv("VIDEO_JOB_STALE_SECONDS", "1200")))
DOCUMENT_JOB_STALE_SECONDS = max(120, int(os.getenv("DOCUMENT_JOB_STALE_SECONDS", "180")))


def video_dispatch_mode() -> str:
    configured = os.getenv("VIDEO_DISPATCH_MODE", "").strip().lower()
    if configured:
        return configured
    return "inline" if os.getenv("ENVIRONMENT", "development").lower() in {"dev", "development", "local"} else "cloud_run_job"


def document_dispatch_mode() -> str:
    configured = os.getenv("DOCUMENT_DISPATCH_MODE", "").strip().lower()
    return configured or video_dispatch_mode()


def _is_document_job(job_type: str) -> bool:
    return job_type in {"process_document", "process_document_embedding"}


async def create_or_reuse_video_job(
    *, document_id: str, user_id: str, workspace_id: str | None, payload: dict[str, Any]
) -> tuple[str, bool]:
    return await create_or_reuse_processing_job(
        document_id=document_id,
        user_id=user_id,
        workspace_id=workspace_id,
        payload=payload,
        job_type="process_video",
        metadata_key="video_job",
    )


async def create_or_reuse_document_job(
    *, document_id: str, user_id: str, workspace_id: str | None, payload: dict[str, Any]
) -> tuple[str, bool]:
    return await create_or_reuse_processing_job(
        document_id=document_id,
        user_id=user_id,
        workspace_id=workspace_id,
        payload=payload,
        job_type="process_document",
        metadata_key="document_job",
    )


async def create_or_reuse_document_embedding_job(
    *, document_id: str, user_id: str, workspace_id: str | None, payload: dict[str, Any]
) -> tuple[str, bool]:
    return await create_or_reuse_processing_job(
        document_id=document_id,
        user_id=user_id,
        workspace_id=workspace_id,
        payload=payload,
        job_type="process_document_embedding",
        metadata_key="document_embedding_job",
    )


async def create_or_reuse_processing_job(
    *, document_id: str, user_id: str, workspace_id: str | None,
    payload: dict[str, Any], job_type: str, metadata_key: str,
) -> tuple[str, bool]:
    """Create one durable active job per document and processing type."""
    dispatch_mode = document_dispatch_mode() if _is_document_job(job_type) else video_dispatch_mode()
    document_status = "embedding" if job_type == "process_document_embedding" else "chunking"
    stale_seconds = DOCUMENT_JOB_STALE_SECONDS if _is_document_job(job_type) else VIDEO_JOB_STALE_SECONDS
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", document_id)
            existing = await conn.fetchrow(
                """
                SELECT id, status, input_data,
                       COALESCE(lease_expires_at, updated_at + ($2 * INTERVAL '1 second')) < NOW() AS stale
                  FROM video_processing_jobs
                 WHERE document_id=$1
                   AND job_type=$4
                   AND status = ANY($3::text[])
                 ORDER BY created_at DESC LIMIT 1
                """,
                document_id, stale_seconds,
                [*ACTIVE_VIDEO_JOB_STATUSES, "error", "dead_letter"],
                job_type,
            )
            if existing:
                if existing["status"] in ACTIVE_VIDEO_JOB_STATUSES and not existing["stale"]:
                    return str(existing["id"]), True
                existing_input = existing["input_data"] if "input_data" in existing else {}
                if isinstance(existing_input, str):
                    existing_input = json.loads(existing_input or "{}")
                same_input_profile = (
                    (existing_input or {}).get("processing_profile", job_type)
                    == payload.get("processing_profile", job_type)
                )
                checkpoint_exists = same_input_profile and await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM video_processing_checkpoints WHERE job_id=$1)",
                    existing["id"],
                )
                if checkpoint_exists:
                    await conn.execute(
                        """
                        UPDATE video_processing_jobs
                           SET status='queued', error_message=NULL, completed_at=NULL,
                               attempt_count=0, lease_owner=NULL, lease_expires_at=NULL,
                               updated_at=NOW()
                         WHERE id=$1
                        """,
                        existing["id"],
                    )
                    await conn.execute(
                        """
                        UPDATE documents
                           SET status=$2, error_message=NULL, updated_at=NOW()
                         WHERE id=$1
                        """,
                        document_id,
                        document_status,
                    )
                    return str(existing["id"]), False

            job_id = str(uuid4())
            await conn.execute(
                """
                INSERT INTO video_processing_jobs
                  (id, document_id, user_id, workspace_id, job_type, status, input_data,
                   dispatch_mode, attempt_count)
                VALUES ($1,$2,$3,$4,$5,'queued',$6::jsonb,$7,0)
                """,
                job_id, document_id, user_id, workspace_id, job_type,
                json.dumps(payload), dispatch_mode,
            )
            await conn.execute(
                """
                UPDATE documents
                   SET status=$3, error_message=NULL,
                       doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                       updated_at=NOW()
                 WHERE id=$2
                """,
                json.dumps(
                    {
                        metadata_key: {
                            "job_id": job_id,
                            "status": "queued",
                            "dispatch_mode": dispatch_mode,
                        },
                        **({
                            "document_progress": {
                                "step": "embedding_queued" if job_type == "process_document_embedding" else "queued",
                                "progress_pct": 0,
                                "message": (
                                    "Document embedding is queued."
                                    if job_type == "process_document_embedding"
                                    else "Large document processing is queued."
                                ),
                            }
                        } if _is_document_job(job_type) else {}),
                    }
                ),
                document_id,
                document_status,
            )
            return job_id, False


async def dispatch_cloud_run_video_job(job_id: str) -> str:
    """Start a long-running Cloud Run Job execution and return its operation name."""
    return await dispatch_cloud_run_processing_job(job_id, "DOCINTEL_VIDEO_JOB_ID")


async def dispatch_cloud_run_document_job(job_id: str) -> str:
    return await dispatch_cloud_run_processing_job(job_id, "DOCINTEL_DOCUMENT_JOB_ID")


async def dispatch_cloud_run_document_embedding_job(job_id: str) -> str:
    return await dispatch_cloud_run_processing_job(job_id, "DOCINTEL_DOCUMENT_EMBEDDING_JOB_ID")


async def dispatch_cloud_run_processing_job(job_id: str, env_name: str) -> str:
    """Start the shared durable processing job with a persisted job identifier."""
    try:
        from google.cloud import run_v2
    except ImportError as exc:
        raise RuntimeError("google-cloud-run is required for VIDEO_DISPATCH_MODE=cloud_run_job") from exc

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or os.getenv("PROJECT_ID")
    region = os.getenv("VIDEO_WORKER_REGION") or os.getenv("REGION", "us-central1")
    job_name = os.getenv("VIDEO_WORKER_JOB_NAME", "docintel-video-worker")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT (or PROJECT_ID) is required to dispatch video jobs")

    client = run_v2.JobsClient()
    name = f"projects/{project}/locations/{region}/jobs/{job_name}"
    override = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[run_v2.EnvVar(name=env_name, value=job_id)]
            )
        ],
        task_count=1,
    )
    operation = await asyncio.to_thread(client.run_job, request=run_v2.RunJobRequest(name=name, overrides=override))
    operation_name = getattr(getattr(operation, "operation", None), "name", "") or str(operation)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE video_processing_jobs
               SET status='dispatching', dispatch_reference=$1, updated_at=NOW()
             WHERE id=$2 AND status='queued'
            """,
            operation_name, job_id,
        )
    return operation_name


async def mark_video_dispatch_failed(job_id: str, error: Exception) -> None:
    await mark_processing_dispatch_failed(job_id, error)


async def mark_processing_dispatch_failed(job_id: str, error: Exception) -> None:
    pool = get_pool()
    message = str(error)[:500]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE video_processing_jobs
               SET status='error', error_message=$1, completed_at=NOW(), updated_at=NOW()
             WHERE id=$2
            """,
            message, job_id,
        )
        await conn.execute(
            """
            UPDATE documents d SET status='error', error_message=$1, updated_at=NOW()
              FROM video_processing_jobs j
             WHERE j.id=$2 AND d.id=j.document_id
            """,
            message, job_id,
        )
