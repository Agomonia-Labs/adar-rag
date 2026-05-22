# routes/documents.py
from __future__ import annotations
import os, asyncio, json, tempfile
from uuid import uuid4
from datetime import datetime, timezone

from typing import Optional
from fastapi import Request, APIRouter, BackgroundTasks, UploadFile, File, HTTPException, Depends

from auth.dependencies import CurrentUser, get_current_user
from database.connection import get_db
from services.usage         import check_document_limit, check_daily_limit, log_event
from services.audit         import audit, ip_from, ua_from
from services.notifications import send_embed_complete
from services.extractor import extract_text, detect_type
from services.chunker   import chunk_text
from services.llm       import embed
from services.vectordb  import store_chunk, delete_document_vectors
import services.storage as gcs


router = APIRouter()

MAX_FILES    = int(os.getenv("MAX_UPLOAD_FILES", "10"))
MAX_FILE_MB  = int(os.getenv("MAX_FILE_SIZE_MB", "50"))


# ══════════════════════════════════════════════════════════════════════════════
#  Upload — saves to GCS and immediately starts chunking as a background task
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/upload")
async def upload_documents(
    background_tasks: BackgroundTasks,
    request:          Request,
    current_user:     CurrentUser,
    files:        list[UploadFile] = File(...),
    workspace_id: Optional[str]   = None,   # if set, doc belongs to a workspace
    db=Depends(get_db),
):
    # Enforce per-user tier document limit (replaces global MAX_FILES env var)
    await check_document_limit(db, str(current_user["id"]))

    # If workspace upload — verify editor/owner membership
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, str(current_user["id"]), "editor")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"You can upload at most {MAX_FILES} files at once")

    created = []
    for upload in files:
        content = await upload.read()
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"{upload.filename} exceeds {MAX_FILE_MB} MB")

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

        # Save temp file for processing
        suffix = os.path.splitext(filename)[1]
        tmp    = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(content); tmp.close()

        # Upload source to GCS
        await gcs.upload_bytes(src_path, content, upload.content_type or "application/octet-stream")

        # Update status to chunking, then kick off chunking in background
        await db.execute("UPDATE documents SET status='chunking' WHERE id=$1", doc_id)
        background_tasks.add_task(
            _chunk_document, doc_id, user_id, tmp.name, filename, upload.content_type or "", ftype, workspace_id
        )
        created.append({"doc_id": doc_id, "filename": filename})
        await log_event(db, user_id, "upload", metadata={
            "doc_id":    doc_id,
            "filename":  filename,
            "file_size": len(content),
            "file_type": ftype,
        })
        await audit(db, user_id=user_id, action="upload_document",
                    resource_type="document", resource_id=doc_id,
                    metadata={"filename": filename, "workspace_id": workspace_id},
                    ip_address=ip_from(request), user_agent=ua_from(request))

    return {"uploaded": created}


# ══════════════════════════════════════════════════════════════════════════════
#  List — user's documents
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/")
async def list_documents(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """
        SELECT d.id, d.original_name, d.file_type, d.file_size, d.status,
               d.chunk_count, d.error_message, d.doc_metadata, d.workspace_id,
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


# ══════════════════════════════════════════════════════════════════════════════
#  View chunks — reads metadata + content from GCS
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{doc_id}/chunks")
async def get_chunks(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await _get_owned(doc_id, current_user["id"], db)
    if row["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document has not been chunked yet")

    user_id  = str(current_user["id"])
    meta     = await gcs.download_json(gcs.metadata_path(user_id, doc_id))
    return {"document": meta["document"], "chunks": meta["chunks"]}


@router.get("/{doc_id}/chunks/{chunk_index}")
async def get_chunk_content(
    doc_id: str, chunk_index: int, current_user: CurrentUser, db=Depends(get_db)
):
    row = await _get_owned(doc_id, current_user["id"], db)
    if row["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document has not been chunked yet")

    user_id  = str(current_user["id"])
    path     = gcs.chunk_path(user_id, doc_id, chunk_index)
    content  = await gcs.download_text(path)
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
    if row["status"] != "chunked":
        raise HTTPException(400, f"Document must be in 'chunked' state (current: {row['status']})")

    user_id      = str(current_user["id"])
    workspace_id = row.get("workspace_id")   # propagate workspace scope to chunks
    await db.execute("UPDATE documents SET status='embedding', updated_at=NOW() WHERE id=$1", doc_id)
    background_tasks.add_task(_embed_document, doc_id, user_id, workspace_id)
    return {"message": "Embedding started", "doc_id": doc_id}


# ══════════════════════════════════════════════════════════════════════════════
#  Delete — removes GCS files + pgvector rows + DB record
# ══════════════════════════════════════════════════════════════════════════════
@router.delete("/{doc_id}")
async def delete_document(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row     = await _get_owned(doc_id, str(current_user["id"]), db)
    user_id = str(current_user["id"])
    warnings = []

    # Delete GCS files — non-fatal
    try:
        await gcs.delete_prefix(f"users/{user_id}/documents/{doc_id}/")
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
async def _chunk_document(doc_id, user_id, file_path, filename, content_type, ftype, workspace_id=None):
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

    try:
        # Extract text
        text = await extract_text(file_path, filename, content_type)

        # Build document-level metadata attached to every chunk
        doc_meta = {
            "document_id":  doc_id,
            "user_id":      user_id,
            "filename":     filename,
            "file_type":    ftype,
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

    try:
        # Read chunks from GCS metadata
        meta   = await gcs.download_json(gcs.metadata_path(user_id, doc_id))
        chunks = meta["chunks"]

        # Delete any existing vectors for this document (re-embed support)
        await delete_document_vectors(doc_id)

        for chunk_info in chunks:
            content   = await gcs.download_text(chunk_info["gcs_path"])
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

        await _set_status("embedded")
        # Log embedding event + notify user
        async with pool.acquire() as _c:
            await log_event(_c, user_id, "embedding",
                            quantity=len(chunks),
                            metadata={"doc_id": doc_id, "chunk_count": len(chunks)})
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


def _doc_row(row: dict) -> dict:
    r = dict(row)
    r["id"]           = str(r["id"])
    r["user_id"]      = str(r.get("user_id", ""))
    r["workspace_id"] = str(r["workspace_id"]) if r.get("workspace_id") else None
    # Tags: may arrive as JSON string or list
    raw_tags = r.get("tags", [])
    if isinstance(raw_tags, str):
        import json as _json
        raw_tags = _json.loads(raw_tags)
    r["tags"] = [t for t in (raw_tags or []) if t.get("id")]
    r["created_at"]   = r["created_at"].isoformat() if r.get("created_at") else None
    r["updated_at"]   = r["updated_at"].isoformat() if r.get("updated_at") else None
    return r