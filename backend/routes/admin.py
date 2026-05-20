# routes/admin.py
# All endpoints require role = 'admin'
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.dependencies import AdminUser
from database.connection import get_db
import services.storage as gcs
from services.vectordb import delete_document_vectors

router = APIRouter()


# ── GET /api/admin/stats ──────────────────────────────────────────────────────
@router.get("/stats")
async def system_stats(admin: AdminUser, db=Depends(get_db)):
    stats = await db.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM users)                              AS total_users,
            (SELECT COUNT(*) FROM users WHERE role = 'admin')        AS total_admins,
            (SELECT COUNT(*) FROM documents WHERE status != 'deleted') AS total_docs,
            (SELECT COUNT(*) FROM documents WHERE status = 'embedded') AS embedded_docs,
            (SELECT COUNT(*) FROM documents WHERE status = 'chunked')  AS chunked_docs,
            (SELECT COUNT(*) FROM documents WHERE status = 'error')    AS error_docs,
            (SELECT COUNT(*) FROM document_chunks)                    AS total_vectors,
            (SELECT COALESCE(SUM(file_size),0) FROM documents WHERE status != 'deleted') AS total_bytes
    """)
    return dict(stats)


# ── GET /api/admin/users ──────────────────────────────────────────────────────
@router.get("/users")
async def list_all_users(admin: AdminUser, db=Depends(get_db)):
    rows = await db.fetch("""
        SELECT
            u.id, u.email, u.full_name, u.role, u.created_at,
            COUNT(d.id) FILTER (WHERE d.status != 'deleted') AS doc_count,
            COUNT(d.id) FILTER (WHERE d.status = 'embedded') AS embedded_count
        FROM users u
        LEFT JOIN documents d ON d.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """)
    return [_fmt_user(r) for r in rows]


# ── PATCH /api/admin/users/{id}/role ─────────────────────────────────────────
class RoleUpdate(BaseModel):
    role: str   # 'user' | 'admin'

@router.patch("/users/{user_id}/role")
async def update_user_role(user_id: str, body: RoleUpdate, admin: AdminUser, db=Depends(get_db)):
    if body.role not in ("user", "admin"):
        raise HTTPException(400, "role must be 'user' or 'admin'")
    if user_id == str(admin["id"]) and body.role == "user":
        raise HTTPException(400, "You cannot demote yourself")

    result = await db.execute(
        "UPDATE users SET role = $1 WHERE id = $2", body.role, user_id
    )
    if result == "UPDATE 0":
        raise HTTPException(404, "User not found")
    return {"user_id": user_id, "role": body.role}


# ── DELETE /api/admin/users/{id} ──────────────────────────────────────────────
@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin: AdminUser, db=Depends(get_db)):
    if user_id == str(admin["id"]):
        raise HTTPException(400, "You cannot delete your own account")

    # Delete all GCS data for this user
    await gcs.delete_prefix(f"users/{user_id}/")

    # Cascade deletes document_chunks via FK, then delete user
    await db.execute("DELETE FROM users WHERE id = $1", user_id)
    return {"deleted_user_id": user_id}


# ── GET /api/admin/documents ──────────────────────────────────────────────────
@router.get("/documents")
async def list_all_documents(admin: AdminUser, db=Depends(get_db)):
    rows = await db.fetch("""
        SELECT
            d.id, d.original_name, d.file_type, d.file_size,
            d.status, d.chunk_count, d.error_message,
            d.created_at, d.updated_at,
            u.id   AS user_id,
            u.email AS user_email,
            u.full_name AS user_name
        FROM documents d
        JOIN users u ON u.id = d.user_id
        WHERE d.status != 'deleted'
        ORDER BY d.created_at DESC
    """)
    return [_fmt_doc(r) for r in rows]


# ── DELETE /api/admin/documents/{id} ─────────────────────────────────────────
@router.delete("/documents/{doc_id}")
async def admin_delete_document(doc_id: str, admin: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT user_id, gcs_source_path FROM documents WHERE id = $1 AND status != 'deleted'",
        doc_id,
    )
    if not row:
        raise HTTPException(404, "Document not found")

    user_id = str(row["user_id"])
    await gcs.delete_prefix(f"users/{user_id}/documents/{doc_id}/")
    await delete_document_vectors(doc_id)
    await db.execute("UPDATE documents SET status = 'deleted', updated_at = NOW() WHERE id = $1", doc_id)
    return {"deleted": doc_id}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt_user(r: dict) -> dict:
    d = dict(r)
    d["id"]         = str(d["id"])
    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    return d

def _fmt_doc(r: dict) -> dict:
    d = dict(r)
    d["id"]         = str(d["id"])
    d["user_id"]    = str(d["user_id"])
    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
    return d