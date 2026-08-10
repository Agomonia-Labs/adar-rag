from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.usage import check_document_limit, get_user_limits, log_event
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
    transcript_language: str = "auto"


class VideoUploadSessionRequest(BaseModel):
    filename: str
    content_type: str = "video/mp4"
    file_size: int
    workspace_id: Optional[str] = None


class VideoUploadCompleteRequest(BaseModel):
    doc_id: str
    filename: str
    content_type: str = "video/mp4"
    file_size: int
    gcs_source_path: str
    workspace_id: Optional[str] = None
    process_after_upload: bool = False
    rights_confirmed: bool = False
    max_frames: int = 12
    segment_seconds: int = 60
    embed_after_processing: bool = True
    transcript_language: str = "auto"


class VideoQuestionRequest(BaseModel):
    question: str
    limit: int = 8


@router.post("/upload-session")
async def create_video_upload_session(
    body: VideoUploadSessionRequest,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    filename = os.path.basename((body.filename or "video.mp4").strip()) or "video.mp4"
    content_type = (body.content_type or "application/octet-stream").strip()
    file_size = int(body.file_size or 0)

    if not is_video_file(filename, "video", content_type):
        raise HTTPException(400, "Direct upload is currently supported for video files only")
    if file_size <= 0:
        raise HTTPException(400, "file_size is required")

    await check_document_limit(db, user_id, quantity=1)
    limits = await get_user_limits(db, user_id)
    max_mb = limits.get("max_file_mb", 10)
    if max_mb != -1 and file_size > max_mb * 1024 * 1024:
        raise HTTPException(
            413,
            f"File '{filename}' exceeds your {max_mb} MB file size limit ({limits.get('label','Free')} plan).",
        )

    workspace_id = body.workspace_id
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "editor")

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
async def complete_video_upload(
    body: VideoUploadCompleteRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    doc_id = body.doc_id
    filename = os.path.basename((body.filename or "video.mp4").strip()) or "video.mp4"
    content_type = (body.content_type or "application/octet-stream").strip()
    file_size = int(body.file_size or 0)
    expected_path = gcs.source_path(user_id, doc_id, filename)

    if body.gcs_source_path != expected_path:
        raise HTTPException(400, "Upload path does not match the authenticated user and document")
    if not is_video_file(filename, "video", content_type):
        raise HTTPException(400, "Uploaded file is not a supported video")

    workspace_id = body.workspace_id
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "editor")

    meta = await gcs.blob_metadata(body.gcs_source_path)
    if not meta:
        raise HTTPException(400, "Uploaded video was not found in storage")
    uploaded_size = int(meta.get("size") or 0)
    if file_size and uploaded_size and abs(uploaded_size - file_size) > 1024:
        raise HTTPException(400, f"Uploaded size mismatch. Expected {file_size} bytes but found {uploaded_size} bytes")

    chk_dir = gcs.chunks_dir(user_id, doc_id)
    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'video',$6,$7,$8,'uploaded','video','general',$9::jsonb)
        ON CONFLICT (id) DO UPDATE SET
           workspace_id = EXCLUDED.workspace_id,
           file_size = EXCLUDED.file_size,
           gcs_source_path = EXCLUDED.gcs_source_path,
           gcs_chunks_dir = EXCLUDED.gcs_chunks_dir,
           status = EXCLUDED.status,
           doc_type = 'video',
           doc_domain = 'general',
           doc_metadata = COALESCE(documents.doc_metadata, '{}'::jsonb) || EXCLUDED.doc_metadata,
           updated_at = NOW()
        """,
        doc_id,
        user_id,
        workspace_id,
        filename,
        filename,
        uploaded_size or file_size,
        body.gcs_source_path,
        chk_dir,
        json.dumps({
            "direct_upload": True,
            "upload_content_type": meta.get("content_type") or content_type,
            "upload_generation": meta.get("generation"),
        }),
    )
    await log_event(db, user_id, "upload", metadata={
        "doc_id": doc_id,
        "filename": filename,
        "file_size": uploaded_size or file_size,
        "file_type": "video",
        "direct_upload": True,
    })

    if body.process_after_upload:
        background_tasks.add_task(
            process_video_document,
            document_id=doc_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source_gcs_path=body.gcs_source_path,
            filename=filename,
            rights_confirmed=body.rights_confirmed,
            source_type="upload",
            source_url=None,
            max_frames=body.max_frames,
            segment_seconds=body.segment_seconds,
            embed_after_processing=body.embed_after_processing,
            transcript_language=body.transcript_language,
        )

    return {
        "doc_id": doc_id,
        "filename": filename,
        "status": "processing" if body.process_after_upload else "uploaded",
        "file_size": uploaded_size or file_size,
        "gcs_source_path": body.gcs_source_path,
    }


@router.get("/documents")
async def list_video_documents(
    current_user: CurrentUser,
    workspace_id: Optional[str] = Query(default=None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "viewer")

    rows = await db.fetch(
        """
        SELECT d.id, d.original_name, d.file_type, d.status, d.chunk_count,
               d.error_message, d.doc_type, d.doc_domain, d.workspace_id, d.doc_metadata,
               vd.id AS video_id, vd.processing_status, vd.duration_seconds,
               vd.width, vd.height, vd.frame_count, vd.updated_at AS video_updated_at,
               vd.metadata AS video_metadata
        FROM documents d
        LEFT JOIN video_documents vd ON vd.document_id = d.id
        WHERE d.status != 'deleted'
          AND (
            ($2::uuid IS NULL AND d.workspace_id IS NULL AND d.user_id = $1)
            OR ($2::uuid IS NOT NULL AND d.workspace_id = $2::uuid)
          )
          AND (
            d.file_type = 'video'
            OR d.doc_type = 'video'
            OR LOWER(d.original_name) ~ '\\.(mp4|mov|m4v|avi|mkv|webm)$'
          )
        ORDER BY d.created_at DESC
        """,
        user_id,
        workspace_id,
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
        transcript_language=body.transcript_language,
    )
    return {"message": "Video processing started", "doc_id": doc_id}


@router.get("/{doc_id}/status")
async def video_status(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _get_accessible_document(db, doc_id, str(current_user["id"]))
    row = await db.fetchrow(
        """
        SELECT
               d.id AS document_id,
               d.status AS document_status,
               d.chunk_count,
               d.error_message AS document_error,
               d.doc_metadata AS document_metadata,
               vd.id,
               vd.processing_status,
               vd.duration_seconds,
               vd.fps,
               vd.width,
               vd.height,
               vd.codec,
               vd.audio_codec,
               vd.bitrate,
               vd.frame_count,
               vd.error_message,
               vd.metadata,
               vd.updated_at
        FROM documents d
        LEFT JOIN video_documents vd ON vd.document_id = d.id
        WHERE d.id = $1
        """,
        doc_id,
    )
    if not row:
        return {"doc_id": doc_id, "processing_status": "not_processed"}
    data = _with_video_progress(_jsonable(dict(row)))
    data["doc_id"] = str(data.get("document_id") or doc_id)
    if not data.get("processing_status"):
        data["processing_status"] = data.get("progress_step") or "not_processed"
    return data


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
    return _with_video_progress(data)


def _with_video_progress(data: dict) -> dict:
    video_meta = _parse_meta(data.get("video_metadata") or data.get("metadata") or {})
    doc_meta = _parse_meta(data.get("document_metadata") or data.get("doc_metadata") or {})
    progress = {}
    if isinstance(video_meta, dict):
        progress = video_meta.get("progress") or {}
    if not progress and isinstance(doc_meta, dict):
        progress = doc_meta.get("video_progress") or {}
    if isinstance(progress, dict):
        data["progress_step"] = progress.get("step")
        data["progress_pct"] = progress.get("progress_pct")
        data["progress_message"] = progress.get("message")
        data["progress_updated_at"] = progress.get("updated_at")
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
