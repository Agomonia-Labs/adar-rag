# routes/guest.py
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth.dependencies import AdminUser, CurrentUser
from auth.service import hash_password
from database.connection import get_db, get_pool
from routes.documents import _chunk_document, _doc_row, _embed_document
from services.audit import audit, ip_from, ua_from
from services.chunker import chunk_text
from services.extractor import detect_type
from services.llm import chat_stream, embed_query, rag_system
from services.pii import redact_text
from services.reranker import rerank, RERANK_ENABLED
from services.text_safety import sanitize_text_for_storage
from services.vectordb import TOP_K, RERANK_FETCH_K, find_similar
from routes.summarize import _load_chunk_texts, _stream_summary
import services.storage as gcs


router = APIRouter()

GUEST_EMAIL = os.getenv("GUEST_USER_EMAIL", "guest@docintel.local")
GUEST_TTL_HOURS = int(os.getenv("GUEST_SESSION_TTL_HOURS", "24"))
GUEST_MAX_UPLOADS = int(os.getenv("GUEST_MAX_UPLOADS", "3"))
GUEST_MAX_QUERIES = int(os.getenv("GUEST_MAX_QUERIES", "5"))
GUEST_MAX_FILE_MB = int(os.getenv("GUEST_MAX_FILE_MB", "20"))
_FETCH_K = RERANK_FETCH_K if RERANK_ENABLED else TOP_K


class GuestSessionResponse(BaseModel):
    guest_token: str
    guest_session_id: str
    expires_at: str
    max_uploads: int
    max_queries: int
    max_file_mb: int


class GuestChatRequest(BaseModel):
    question: str
    document_ids: list[str]
    history: list[dict] = []
    redact_pii: bool = False


class GuestSummarizeRequest(BaseModel):
    document_ids: list[str]
    summary_type: str = "executive"
    redact_pii: bool = False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _guest_user_id(db) -> str:
    row = await db.fetchrow("SELECT id FROM users WHERE email=$1", GUEST_EMAIL)
    if row:
        return str(row["id"])
    row = await db.fetchrow(
        """
        INSERT INTO users (email, hashed_password, full_name, role, is_verified)
        VALUES ($1,$2,'DocIntel Guest','guest',TRUE)
        ON CONFLICT (email) DO UPDATE SET email=EXCLUDED.email
        RETURNING id
        """,
        GUEST_EMAIL,
        hash_password(secrets.token_urlsafe(24)),
    )
    return str(row["id"])


async def _require_guest(db, guest_token: str | None) -> dict:
    if not guest_token:
        raise HTTPException(401, "Guest session is required")
    row = await db.fetchrow(
        """
        SELECT *
          FROM guest_sessions
         WHERE token_hash=$1
           AND claimed_by_user_id IS NULL
           AND expires_at > NOW()
        """,
        _token_hash(guest_token),
    )
    if not row:
        raise HTTPException(401, "Guest session expired or invalid")
    return dict(row)


async def _guest_doc_rows(db, session_id: str, document_ids: list[str] | None = None) -> list[dict]:
    params: list = [session_id]
    doc_filter = ""
    if document_ids:
        params.append(document_ids)
        doc_filter = "AND d.id = ANY($2::uuid[])"
    rows = await db.fetch(
        f"""
        SELECT d.id, d.user_id, d.original_name, d.file_type, d.file_size, d.status,
               d.chunk_count, d.error_message, d.doc_metadata, d.workspace_id,
               d.doc_type, d.doc_domain, d.doc_language,
               d.created_at, d.updated_at,
               '[]'::json AS tags
          FROM documents d
         WHERE d.guest_session_id=$1
           AND d.status != 'deleted'
           {doc_filter}
         ORDER BY d.created_at DESC
        """,
        *params,
    )
    return [dict(r) for r in rows]


@router.post("/session", response_model=GuestSessionResponse)
async def create_guest_session(request: Request, db=Depends(get_db)):
    guest_user_id = await _guest_user_id(db)
    token = secrets.token_urlsafe(36)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=GUEST_TTL_HOURS)
    row = await db.fetchrow(
        """
        INSERT INTO guest_sessions (token_hash, guest_user_id, expires_at)
        VALUES ($1,$2,$3)
        RETURNING id, expires_at
        """,
        _token_hash(token),
        guest_user_id,
        expires_at,
    )
    await audit(
        db,
        user_id=guest_user_id,
        action="guest_session_create",
        resource_type="guest_session",
        resource_id=str(row["id"]),
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {
        "guest_token": token,
        "guest_session_id": str(row["id"]),
        "expires_at": row["expires_at"].isoformat(),
        "max_uploads": GUEST_MAX_UPLOADS,
        "max_queries": GUEST_MAX_QUERIES,
        "max_file_mb": GUEST_MAX_FILE_MB,
    }


@router.post("/upload")
async def upload_guest_documents(
    background_tasks: BackgroundTasks,
    request: Request,
    files: list[UploadFile] = File(...),
    redact_pii: bool = Form(False),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest(db, x_guest_token)
    if len(files) + int(session["upload_count"] or 0) > GUEST_MAX_UPLOADS:
        raise HTTPException(400, f"Guest preview allows {GUEST_MAX_UPLOADS} uploads. Create an account to continue.")

    created = []
    guest_user_id = str(session["guest_user_id"])
    session_id = str(session["id"])
    max_bytes = GUEST_MAX_FILE_MB * 1024 * 1024
    for upload in files:
        filename = upload.filename or "unnamed"
        if (upload.content_type or "").startswith("video/"):
            raise HTTPException(400, "Video guest preview needs the video processing router. Sign in to use the full Video Intelligence workflow.")
        content = await upload.read()
        if len(content) > max_bytes:
            raise HTTPException(413, f"Guest preview file limit is {GUEST_MAX_FILE_MB} MB. Create an account for larger uploads.")

        doc_id = str(uuid4())
        ftype = detect_type(filename, upload.content_type or "")
        src_path = gcs.source_path(guest_user_id, doc_id, filename)
        chk_dir = gcs.chunks_dir(guest_user_id, doc_id)

        await db.execute(
            """
            INSERT INTO documents
              (id, user_id, guest_session_id, filename, original_name, file_type, file_size,
               gcs_source_path, gcs_chunks_dir, status, doc_metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'uploading',$10::jsonb)
            """,
            doc_id,
            guest_user_id,
            session_id,
            filename,
            filename,
            ftype,
            len(content),
            src_path,
            chk_dir,
            json.dumps({"guest_preview": True}),
        )
        suffix = os.path.splitext(filename)[1]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(content)
        tmp.close()
        await gcs.upload_bytes(src_path, content, upload.content_type or "application/octet-stream")
        await db.execute("UPDATE documents SET status='chunking', updated_at=NOW() WHERE id=$1", doc_id)
        await db.execute(
            "UPDATE guest_sessions SET upload_count=upload_count+1, updated_at=NOW() WHERE id=$1",
            session_id,
        )
        background_tasks.add_task(
            _chunk_document,
            doc_id,
            guest_user_id,
            tmp.name,
            filename,
            upload.content_type or "",
            ftype,
            None,
            redact_pii,
        )
        created.append({"doc_id": doc_id, "filename": filename})
        await audit(
            db,
            user_id=guest_user_id,
            action="guest_upload_document",
            resource_type="document",
            resource_id=doc_id,
            metadata={"filename": filename, "guest_session_id": session_id},
            ip_address=ip_from(request),
            user_agent=ua_from(request),
        )
    return {"uploaded": created}


@router.get("/documents")
async def list_guest_documents(
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest(db, x_guest_token)
    rows = await _guest_doc_rows(db, str(session["id"]))
    return [_doc_row(r) for r in rows]


@router.post("/documents/{doc_id}/embed")
async def embed_guest_document(
    doc_id: str,
    background_tasks: BackgroundTasks,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest(db, x_guest_token)
    rows = await _guest_doc_rows(db, str(session["id"]), [doc_id])
    if not rows:
        raise HTTPException(404, "Document not found")
    row = rows[0]
    if row["status"] != "chunked":
        raise HTTPException(400, f"Document must be chunked before embedding. Current status: {row['status']}")
    await db.execute("UPDATE documents SET status='embedding', updated_at=NOW() WHERE id=$1", doc_id)
    background_tasks.add_task(_embed_document, doc_id, str(row["user_id"]), None)
    return {"message": "Embedding started", "doc_id": doc_id}


@router.post("/chat/stream")
async def guest_chat_stream(
    req: GuestChatRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest(db, x_guest_token)
    if not req.question.strip():
        raise HTTPException(400, "question must not be empty")
    if not req.document_ids:
        raise HTTPException(400, "Select at least one embedded document to query")
    if int(session["query_count"] or 0) >= GUEST_MAX_QUERIES:
        raise HTTPException(429, "Guest question limit reached. Create an account to continue.")

    rows = await _guest_doc_rows(db, str(session["id"]), req.document_ids)
    found_ids = {str(r["id"]) for r in rows}
    not_found = set(req.document_ids) - found_ids
    not_embedded = {str(r["id"]) for r in rows if r["status"] != "embedded"}
    if not_found:
        raise HTTPException(403, f"Documents not found or not accessible: {not_found}")
    if not_embedded:
        raise HTTPException(400, f"Documents not yet embedded: {not_embedded}")

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()
        output_tokens: list[str] = []

        async def on_token(t: str):
            output_tokens.append(t)
            await queue.put(("token", t))

        async def run():
            try:
                question = redact_text(req.question, req.redact_pii).text
                query_vec = await embed_query(question)
                candidates = await find_similar(
                    query_embedding=query_vec,
                    query_text=question,
                    user_id=str(session["guest_user_id"]),
                    document_ids=req.document_ids,
                    limit=_FETCH_K,
                )
                chunks = await rerank(query=question, chunks=candidates, top_k=TOP_K)
                context = "\n\n".join(
                    f"[Source {idx+1}] {redact_text(c.get('content',''), req.redact_pii).text}"
                    for idx, c in enumerate(chunks)
                )
                system_prompt = rag_system(
                    context,
                    "Guest preview mode: answer only from the provided sources. Mention that creating an account saves this workspace.",
                )
                messages = [
                    {"role": m["role"], "content": redact_text(m["content"], req.redact_pii).text}
                    for m in (req.history or [])[-6:]
                    if m.get("role") and m.get("content")
                ] + [{"role": "user", "content": question}]
                await chat_stream(messages, system_prompt, on_token)
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        "UPDATE guest_sessions SET query_count=query_count+1, updated_at=NOW() WHERE id=$1",
                        session["id"],
                    )
                await queue.put(("done", {"sources": chunks}))
            except Exception as exc:
                await queue.put(("error", str(exc)))

        task = asyncio.create_task(run())
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "token":
                    yield f"data: {json.dumps({'type': 'token', 'text': payload})}\n\n"
                elif kind == "done":
                    yield f"data: {json.dumps({'type': 'done', 'sources': []})}\n\n"
                    break
                elif kind == "error":
                    yield f"data: {json.dumps({'type': 'error', 'error': payload})}\n\n"
                    break
            await task
        except asyncio.CancelledError:
            task.cancel()
            raise

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/summarize/stream")
async def guest_summarize_stream(
    body: GuestSummarizeRequest,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest(db, x_guest_token)
    if not body.document_ids:
        raise HTTPException(400, "Select at least one document to summarize")
    if body.summary_type not in {"executive", "bullets", "sections", "detailed"}:
        raise HTTPException(400, "Guest preview supports executive, bullets, sections, and detailed summaries")

    rows = await _guest_doc_rows(db, str(session["id"]), body.document_ids)
    found_ids = {str(r["id"]) for r in rows}
    missing = set(body.document_ids) - found_ids
    not_ready = {str(r["id"]) for r in rows if r["status"] not in ("chunked", "embedding", "embedded")}
    if missing:
        raise HTTPException(403, f"Documents not found or not accessible: {missing}")
    if not_ready:
        raise HTTPException(400, f"Documents not yet processed: {not_ready}")

    async def generate():
        try:
            all_texts: list[str] = []
            for row in rows:
                doc_id = str(row["id"])
                meta = await gcs.download_json(gcs.metadata_path(str(row["user_id"]), doc_id))
                texts = await _load_chunk_texts(meta["chunks"])
                all_texts.append(f"=== {row['original_name']} ===\n" + "\n\n".join(texts))
            label = rows[0]["original_name"] if len(rows) == 1 else f"{len(rows)} guest documents"
            async for event in _stream_summary(
                all_texts,
                body.summary_type,
                "",
                label,
                rows[0].get("doc_language") or "en",
                body.redact_pii,
            ):
                yield event
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/claim")
async def claim_guest_session(
    current_user: CurrentUser,
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db=Depends(get_db),
):
    session = await _require_guest(db, x_guest_token)
    session_id = str(session["id"])
    user_id = str(current_user["id"])
    async with db.transaction():
        await db.execute(
            """
            UPDATE documents
               SET user_id=$1,
                   guest_session_id=NULL,
                   doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $2::jsonb,
                   updated_at=NOW()
             WHERE guest_session_id=$3
            """,
            user_id,
            json.dumps({"guest_preview": False, "claimed_from_guest_session": session_id}),
            session_id,
        )
        await db.execute(
            """
            UPDATE document_chunks
               SET user_id=$1,
                   guest_session_id=NULL
             WHERE guest_session_id=$2
                OR document_id IN (
                     SELECT id FROM documents
                      WHERE doc_metadata->>'claimed_from_guest_session' = $2::text
                   )
            """,
            user_id,
            session_id,
        )
        await db.execute(
            """
            UPDATE guest_sessions
               SET claimed_by_user_id=$1, claimed_at=NOW(), updated_at=NOW()
             WHERE id=$2
            """,
            user_id,
            session_id,
        )
    return {"claimed": True, "guest_session_id": session_id}


@router.post("/cleanup-expired")
async def cleanup_expired_guest_sessions(
    admin: AdminUser,
    db=Depends(get_db),
):
    sessions = await db.fetch(
        """
        SELECT id
          FROM guest_sessions
         WHERE claimed_by_user_id IS NULL
           AND expires_at <= NOW()
         LIMIT 100
        """
    )
    deleted_docs = 0
    deleted_sessions = 0
    warnings: list[str] = []
    for session in sessions:
        session_id = str(session["id"])
        docs = await db.fetch(
            """
            SELECT id, user_id, gcs_source_path, gcs_chunks_dir
              FROM documents
             WHERE guest_session_id=$1
            """,
            session_id,
        )
        for doc in docs:
            doc_data = dict(doc)
            doc_id = str(doc["id"])
            user_id = str(doc["user_id"])
            try:
                prefix = _document_prefix(doc_data.get("gcs_source_path"), doc_data.get("gcs_chunks_dir"), doc_id, user_id)
                await gcs.delete_prefix(prefix)
            except Exception as exc:
                warnings.append(f"{doc_id}: GCS cleanup skipped: {exc}")
            await db.execute("DELETE FROM documents WHERE id=$1", doc_id)
            deleted_docs += 1
        await db.execute("DELETE FROM guest_sessions WHERE id=$1", session_id)
        deleted_sessions += 1
    return {"deleted_sessions": deleted_sessions, "deleted_documents": deleted_docs, "warnings": warnings}


def _document_prefix(source_path: str | None, chunks_dir: str | None, doc_id: str, user_id: str) -> str:
    marker = f"/documents/{doc_id}/"
    for value in (source_path or "", chunks_dir or ""):
        if marker in value:
            return value.split(marker, 1)[0] + marker
    return f"users/{user_id}/documents/{doc_id}/"
