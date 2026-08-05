from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.video_intelligence import is_video_file, process_video_document
from services.vectordb import find_similar
import services.storage as gcs

router = APIRouter()


class ProcessVideoRequest(BaseModel):
    rights_confirmed: bool = False
    source_type: str = "upload"
    source_url: Optional[str] = None
    max_frames: int = 12
    segment_seconds: int = 60
    embed_after_processing: bool = True


class VideoQuestionRequest(BaseModel):
    question: str
    limit: int = 8


@router.get("/documents")
async def list_video_documents(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """
        SELECT d.id, d.original_name, d.file_type, d.status, d.chunk_count,
               d.error_message, d.doc_type, d.doc_domain, d.workspace_id,
               vd.id AS video_id, vd.processing_status, vd.duration_seconds,
               vd.width, vd.height, vd.frame_count, vd.updated_at AS video_updated_at
        FROM documents d
        LEFT JOIN video_documents vd ON vd.document_id = d.id
        WHERE d.status != 'deleted'
          AND (
            d.user_id = $1
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
               WHERE wm.workspace_id = d.workspace_id
                 AND wm.user_id = $1
            )
          )
          AND (
            d.file_type = 'video'
            OR d.doc_type = 'video'
            OR LOWER(d.original_name) ~ '\\.(mp4|mov|m4v|avi|mkv|webm)$'
          )
        ORDER BY d.created_at DESC
        """,
        current_user["id"],
    )
    return [_video_doc_row(r) for r in rows]


@router.post("/{doc_id}/process")
async def process_video(
    doc_id: str,
    body: ProcessVideoRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    row = await _get_accessible_document(db, doc_id, str(current_user["id"]))
    if not is_video_file(row.get("original_name"), row.get("file_type")):
        raise HTTPException(400, "Selected document is not a supported video file")
    if body.max_frames < 1 or body.max_frames > 60:
        raise HTTPException(400, "max_frames must be between 1 and 60")
    if body.segment_seconds < 15 or body.segment_seconds > 600:
        raise HTTPException(400, "segment_seconds must be between 15 and 600")

    background_tasks.add_task(
        process_video_document,
        document_id=doc_id,
        user_id=str(row["user_id"]),
        workspace_id=str(row["workspace_id"]) if row.get("workspace_id") else None,
        source_gcs_path=row["gcs_source_path"],
        filename=row["original_name"],
        rights_confirmed=body.rights_confirmed,
        source_type=body.source_type,
        source_url=body.source_url,
        max_frames=body.max_frames,
        segment_seconds=body.segment_seconds,
        embed_after_processing=body.embed_after_processing,
    )
    return {"message": "Video processing started", "doc_id": doc_id}


@router.get("/{doc_id}/status")
async def video_status(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _get_accessible_document(db, doc_id, str(current_user["id"]))
    row = await db.fetchrow(
        """
        SELECT vd.*, d.status AS document_status, d.chunk_count, d.error_message AS document_error
        FROM video_documents vd
        JOIN documents d ON d.id = vd.document_id
        WHERE vd.document_id = $1
        """,
        doc_id,
    )
    if not row:
        return {"doc_id": doc_id, "processing_status": "not_processed"}
    return _jsonable(dict(row))


@router.get("/{doc_id}/timeline")
async def video_timeline(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _get_accessible_document(db, doc_id, str(current_user["id"]))
    video = await db.fetchrow("SELECT * FROM video_documents WHERE document_id=$1", doc_id)
    if not video:
        raise HTTPException(404, "Video has not been processed yet")
    segments = await db.fetch(
        """
        SELECT id, segment_index, start_seconds, end_seconds, segment_type, title,
               summary, transcript, ocr_text, thumbnail_path, confidence, metadata
        FROM video_segments
        WHERE video_document_id=$1
        ORDER BY segment_index
        """,
        video["id"],
    )
    frames = await db.fetch(
        """
        SELECT id, segment_id, frame_index, timestamp_seconds, frame_path, thumbnail_path,
               caption, ocr_text, metadata
        FROM video_frames
        WHERE video_document_id=$1
        ORDER BY frame_index
        """,
        video["id"],
    )
    return {
        "video": _jsonable(dict(video)),
        "segments": [_jsonable(dict(r)) for r in segments],
        "frames": [_jsonable(dict(r)) for r in frames],
    }


@router.get("/{doc_id}/frames/{frame_index}/view-url")
async def frame_view_url(doc_id: str, frame_index: int, current_user: CurrentUser, db=Depends(get_db)):
    await _get_accessible_document(db, doc_id, str(current_user["id"]))
    row = await db.fetchrow(
        """
        SELECT vf.frame_path
        FROM video_frames vf
        JOIN video_documents vd ON vd.id = vf.video_document_id
        WHERE vd.document_id=$1 AND vf.frame_index=$2
        """,
        doc_id,
        frame_index,
    )
    if not row:
        raise HTTPException(404, "Frame not found")
    return {"url": await gcs.get_signed_url(row["frame_path"])}


@router.post("/{doc_id}/ask")
async def ask_video(doc_id: str, body: VideoQuestionRequest, current_user: CurrentUser, db=Depends(get_db)):
    from services.llm import chat_stream, embed_query, rag_system

    doc = await _get_accessible_document(db, doc_id, str(current_user["id"]))
    if doc.get("status") != "embedded":
        raise HTTPException(400, f"Video document must be embedded before Q&A. Current status: {doc.get('status')}")
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")

    qvec = await embed_query(question)
    matches = await find_similar(qvec, str(current_user["id"]), [doc_id], limit=max(1, min(body.limit, 12)), query_text=question)
    context_parts = []
    sources = []
    for idx, match in enumerate(matches, 1):
        meta = _parse_meta(match.get("chunk_metadata"))
        time_label = meta.get("start_time") or _format_seconds(meta.get("start_seconds"))
        source_name = f"{match.get('doc_name')} @ {time_label}" if time_label else match.get("doc_name")
        context_parts.append(f"[Source {idx}] {source_name}\n{match.get('content')}")
        sources.append({
            "source": idx,
            "document_id": str(match.get("document_id")),
            "doc_name": match.get("doc_name"),
            "chunk_index": match.get("chunk_index"),
            "start_seconds": meta.get("start_seconds"),
            "end_seconds": meta.get("end_seconds"),
            "start_time": meta.get("start_time"),
            "end_time": meta.get("end_time"),
            "match_type": match.get("match_type"),
            "similarity": float(match.get("similarity") or 0),
        })

    system = rag_system(
        "\n\n".join(context_parts),
        "Answer in English. For video answers, cite the timestamp when available and keep the answer grounded in the retrieved timeline chunks.",
    )
    tokens: list[str] = []

    async def on_token(text: str):
        tokens.append(text)

    await chat_stream([{"role": "user", "content": question}], system, on_token)
    return {"answer": "".join(tokens).strip(), "sources": sources}


async def _get_accessible_document(db, doc_id: str, user_id: str) -> dict:
    row = await db.fetchrow(
        """
        SELECT d.*
        FROM documents d
        WHERE d.id = $1
          AND d.status != 'deleted'
          AND (
            d.user_id = $2
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
               WHERE wm.workspace_id = d.workspace_id
                 AND wm.user_id = $2
            )
          )
        """,
        doc_id,
        user_id,
    )
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)


def _video_doc_row(row) -> dict:
    data = _jsonable(dict(row))
    data["id"] = str(data["id"])
    data["workspace_id"] = str(data["workspace_id"]) if data.get("workspace_id") else None
    data["video_id"] = str(data["video_id"]) if data.get("video_id") else None
    return data


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _parse_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _format_seconds(value) -> str:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return ""
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
