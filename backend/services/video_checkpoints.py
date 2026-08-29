from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from database.connection import get_pool


LEASE_SECONDS = max(60, int(os.getenv("VIDEO_CHECKPOINT_LEASE_SECONDS", "900")))


def worker_id() -> str:
    return (
        os.getenv("CLOUD_RUN_EXECUTION")
        or os.getenv("HOSTNAME")
        or f"video-worker-{uuid4()}"
    )


async def completed_output(job_id: str, stage: str, item_key: str = "stage") -> Any | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT output_data
              FROM video_processing_checkpoints
             WHERE job_id=$1 AND stage=$2 AND item_key=$3 AND status='completed'
            """,
            job_id, stage, item_key,
        )
    if not row:
        return None
    value = row["output_data"]
    return value if isinstance(value, (dict, list)) else json.loads(value or "null")


async def begin(
    *, job_id: str, document_id: str, stage: str, item_key: str = "stage",
    input_data: dict[str, Any] | None = None, owner: str,
) -> bool:
    """Acquire an incomplete checkpoint. Returns False when another live worker owns it."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO video_processing_checkpoints
              (job_id, document_id, stage, item_key, status, input_data, attempt_count,
               lease_owner, lease_expires_at, started_at, updated_at)
            VALUES ($1,$2,$3,$4,'running',$5::jsonb,1,$6,
                    NOW() + ($7 * INTERVAL '1 second'),NOW(),NOW())
            ON CONFLICT (job_id, stage, item_key) DO UPDATE SET
               status='running', input_data=EXCLUDED.input_data,
               attempt_count=video_processing_checkpoints.attempt_count + 1,
               lease_owner=EXCLUDED.lease_owner,
               lease_expires_at=EXCLUDED.lease_expires_at,
               error_message=NULL,
               started_at=NOW(), updated_at=NOW()
            WHERE video_processing_checkpoints.status <> 'completed'
              AND (
                video_processing_checkpoints.lease_expires_at IS NULL
                OR video_processing_checkpoints.lease_expires_at < NOW()
                OR video_processing_checkpoints.lease_owner = EXCLUDED.lease_owner
              )
            RETURNING status
            """,
            job_id, document_id, stage, item_key,
            json.dumps(input_data or {}), owner, LEASE_SECONDS,
        )
    return bool(row)


async def complete(
    *, job_id: str, stage: str, item_key: str = "stage",
    output_data: Any = None, owner: str,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE video_processing_checkpoints
               SET status='completed', output_data=$1::jsonb, error_message=NULL,
                   lease_owner=NULL, lease_expires_at=NULL, completed_at=NOW(), updated_at=NOW()
             WHERE job_id=$2 AND stage=$3 AND item_key=$4 AND lease_owner=$5
            """,
            json.dumps(output_data), job_id, stage, item_key, owner,
        )


async def fail(
    *, job_id: str, stage: str, item_key: str = "stage",
    error: Exception | str, owner: str,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE video_processing_checkpoints
               SET status='failed', error_message=$1, lease_owner=NULL,
                   lease_expires_at=NULL, updated_at=NOW()
             WHERE job_id=$2 AND stage=$3 AND item_key=$4 AND lease_owner=$5
            """,
            str(error)[:1000], job_id, stage, item_key, owner,
        )


async def heartbeat(job_id: str, owner: str) -> None:
    pool = get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE video_processing_jobs
               SET heartbeat_at=$1, lease_owner=$2,
                   lease_expires_at=NOW() + ($3 * INTERVAL '1 second'), updated_at=NOW()
             WHERE id=$4
            """,
            now, owner, LEASE_SECONDS, job_id,
        )
        await conn.execute(
            """
            UPDATE video_processing_checkpoints
               SET lease_expires_at=NOW() + ($1 * INTERVAL '1 second'), updated_at=NOW()
             WHERE job_id=$2 AND lease_owner=$3 AND status='running'
            """,
            LEASE_SECONDS, job_id, owner,
        )


async def summary(job_id: str) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT stage, status, COUNT(*) AS item_count,
                   COALESCE(SUM(attempt_count), 0) AS attempts,
                   MAX(updated_at) AS updated_at
              FROM video_processing_checkpoints
             WHERE job_id=$1
             GROUP BY stage, status
             ORDER BY MIN(created_at), stage, status
            """,
            job_id,
        )
    stages: dict[str, dict[str, Any]] = {}
    for row in rows:
        stage = stages.setdefault(row["stage"], {"stage": row["stage"], "counts": {}, "attempts": 0})
        stage["counts"][row["status"]] = int(row["item_count"] or 0)
        stage["attempts"] += int(row["attempts"] or 0)
        updated = row["updated_at"]
        if updated and (not stage.get("updated_at") or updated > stage["updated_at"]):
            stage["updated_at"] = updated
    return {"job_id": job_id, "stages": list(stages.values())}
