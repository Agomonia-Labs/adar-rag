from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

from database.connection import get_pool


ACTIVE_VIDEO_JOB_STATUSES = ("queued", "dispatching", "running")


def video_dispatch_mode() -> str:
    configured = os.getenv("VIDEO_DISPATCH_MODE", "").strip().lower()
    if configured:
        return configured
    return "inline" if os.getenv("ENVIRONMENT", "development").lower() in {"dev", "development", "local"} else "cloud_run_job"


async def create_or_reuse_video_job(
    *, document_id: str, user_id: str, workspace_id: str | None, payload: dict[str, Any]
) -> tuple[str, bool]:
    """Create one durable active job per document; repeated clicks reuse it."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", document_id)
            existing = await conn.fetchrow(
                """
                SELECT id FROM video_processing_jobs
                 WHERE document_id=$1 AND status = ANY($2::text[])
                 ORDER BY created_at DESC LIMIT 1
                """,
                document_id, list(ACTIVE_VIDEO_JOB_STATUSES),
            )
            if existing:
                return str(existing["id"]), True

            job_id = str(uuid4())
            await conn.execute(
                """
                INSERT INTO video_processing_jobs
                  (id, document_id, user_id, workspace_id, job_type, status, input_data,
                   dispatch_mode, attempt_count)
                VALUES ($1,$2,$3,$4,'process_video','queued',$5::jsonb,$6,0)
                """,
                job_id, document_id, user_id, workspace_id, json.dumps(payload), video_dispatch_mode(),
            )
            await conn.execute(
                """
                UPDATE documents
                   SET status='chunking', error_message=NULL,
                       doc_metadata=COALESCE(doc_metadata, '{}'::jsonb) || $1::jsonb,
                       updated_at=NOW()
                 WHERE id=$2
                """,
                json.dumps({"video_job": {"job_id": job_id, "status": "queued", "dispatch_mode": video_dispatch_mode()}}),
                document_id,
            )
            return job_id, False


async def dispatch_cloud_run_video_job(job_id: str) -> str:
    """Start a long-running Cloud Run Job execution and return its operation name."""
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
                env=[run_v2.EnvVar(name="DOCINTEL_VIDEO_JOB_ID", value=job_id)]
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
