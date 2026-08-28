from __future__ import annotations

import asyncio
import json
import logging
import os

from database.connection import close_pool, get_pool, init_pool
from database.models import create_additional_tables, create_tables
from services.video_intelligence import process_video_document

log = logging.getLogger("docintel.video.worker")


async def run(job_id: str) -> None:
    await init_pool()
    try:
        await create_tables()
        await create_additional_tables()
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, document_id, user_id, workspace_id, status, input_data, output_data
                  FROM video_processing_jobs WHERE id=$1
                """,
                job_id,
            )
        if not row:
            raise RuntimeError(f"Video processing job {job_id} was not found")
        if row["status"] == "completed":
            log.info("Video job %s is already complete; no work required", job_id)
            return

        payload = row["input_data"] if isinstance(row["input_data"], dict) else json.loads(row["input_data"] or "{}")
        await process_video_document(
            job_id=job_id,
            document_id=str(row["document_id"]),
            user_id=str(row["user_id"]),
            workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None,
            source_gcs_path=payload["source_gcs_path"],
            filename=payload["filename"],
            rights_confirmed=bool(payload.get("rights_confirmed")),
            source_type=payload.get("source_type", "upload"),
            source_url=payload.get("source_url"),
            max_frames=int(payload.get("max_frames", 12)),
            segment_seconds=int(payload.get("segment_seconds", 60)),
            embed_after_processing=bool(payload.get("embed_after_processing", True)),
            transcript_language=payload.get("transcript_language", "auto"),
        )
    finally:
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    current_job_id = os.getenv("DOCINTEL_VIDEO_JOB_ID", "").strip()
    if not current_job_id:
        raise SystemExit("DOCINTEL_VIDEO_JOB_ID is required")
    asyncio.run(run(current_job_id))
