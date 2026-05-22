# routes/tags.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from auth.dependencies import CurrentUser
from database.connection import get_db

router = APIRouter()

TAG_COLORS = ["#4ade80","#60a5fa","#c084fc","#fbbf24","#f87171",
              "#34d399","#fb923c","#a78bfa","#38bdf8","#e879f9"]


# ── Tag CRUD ──────────────────────────────────────────────────────────────────

@router.get("/")
async def list_tags(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT t.id, t.name, t.color,
                  COUNT(m.document_id) AS doc_count
           FROM document_tags t
           LEFT JOIN document_tag_map m ON m.tag_id = t.id
           WHERE t.user_id = $1
           GROUP BY t.id
           ORDER BY t.name""",
        str(current_user["id"]),
    )
    return [{"id": str(r["id"]), "name": r["name"], "color": r["color"],
             "doc_count": int(r["doc_count"])} for r in rows]


class CreateTag(BaseModel):
    name:  str
    color: Optional[str] = None

@router.post("/", status_code=201)
async def create_tag(body: CreateTag, current_user: CurrentUser, db=Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "Tag name is required")
    color = body.color or TAG_COLORS[0]
    try:
        row = await db.fetchrow(
            "INSERT INTO document_tags (user_id, name, color) VALUES ($1,$2,$3) RETURNING id, name, color",
            str(current_user["id"]), body.name.strip()[:40], color,
        )
        return {"id": str(row["id"]), "name": row["name"], "color": row["color"], "doc_count": 0}
    except Exception:
        raise HTTPException(409, f"Tag '{body.name}' already exists")


# ── Tag assignments — MUST come before /{tag_id} to avoid routing conflict ───

class AssignBody(BaseModel):
    document_id: str
    tag_id:      str

@router.post("/assign")
async def assign_tag(body: AssignBody, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    doc = await db.fetchrow(
        """SELECT d.id FROM documents d
           WHERE d.id=$1 AND d.status!='deleted'
             AND (d.user_id=$2 OR EXISTS (
               SELECT 1 FROM workspace_members wm
               WHERE wm.workspace_id=d.workspace_id AND wm.user_id=$2))""",
        body.document_id, user_id,
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    tag = await db.fetchrow(
        "SELECT id FROM document_tags WHERE id=$1 AND user_id=$2",
        body.tag_id, user_id,
    )
    if not tag:
        raise HTTPException(404, "Tag not found")
    await db.execute(
        "INSERT INTO document_tag_map (document_id, tag_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        body.document_id, body.tag_id,
    )
    return {"ok": True}


@router.delete("/assign")
async def remove_tag_assignment(
    document_id: str,
    tag_id:      str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    """Remove a tag — uses query params: ?document_id=...&tag_id=..."""
    await db.execute(
        "DELETE FROM document_tag_map WHERE document_id=$1 AND tag_id=$2",
        document_id, tag_id,
    )
    return {"ok": True}


# ── Tag CRUD continued (after /assign to avoid conflict) ─────────────────────

@router.patch("/{tag_id}")
async def update_tag(tag_id: str, body: CreateTag, current_user: CurrentUser, db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT id FROM document_tags WHERE id=$1 AND user_id=$2",
        tag_id, str(current_user["id"]),
    )
    if not row:
        raise HTTPException(404, "Tag not found")
    updates, params = [], [tag_id]
    if body.name.strip():
        params.append(body.name.strip()[:40]); updates.append(f"name=${len(params)}")
    if body.color:
        params.append(body.color); updates.append(f"color=${len(params)}")
    if updates:
        await db.execute(f"UPDATE document_tags SET {chr(44).join(updates)} WHERE id=$1", *params)
    return {"ok": True}


@router.delete("/{tag_id}")
async def delete_tag(tag_id: str, current_user: CurrentUser, db=Depends(get_db)):
    r = await db.execute(
        "DELETE FROM document_tags WHERE id=$1 AND user_id=$2",
        tag_id, str(current_user["id"]),
    )
    if r == "DELETE 0":
        raise HTTPException(404, "Tag not found")
    return {"ok": True}


@router.get("/{tag_id}/documents")
async def documents_by_tag(tag_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    rows = await db.fetch(
        """SELECT d.id, d.original_name, d.status, d.file_type, d.chunk_count,
                  d.file_size, d.workspace_id
           FROM documents d
           JOIN document_tag_map m ON m.document_id = d.id
           JOIN document_tags    t ON t.id = m.tag_id
           WHERE m.tag_id=$1 AND t.user_id=$2 AND d.status!='deleted'
           ORDER BY d.created_at DESC""",
        tag_id, user_id,
    )
    return [{"id": str(r["id"]), "original_name": r["original_name"],
             "status": r["status"], "file_type": r["file_type"],
             "chunk_count": int(r["chunk_count"] or 0), "file_size": r["file_size"],
             "workspace_id": str(r["workspace_id"]) if r["workspace_id"] else None,
             } for r in rows]