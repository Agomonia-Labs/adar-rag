# routes/workspaces.py
from __future__ import annotations
import json, logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.audit import audit, ip_from, ua_from
from services.notifications import send_workspace_invite
import os
_APP_URL = os.getenv("APP_URL", "http://localhost:5173")

router = APIRouter()
log    = logging.getLogger("docintel.workspaces")

ROLE_ORDER = {"viewer": 0, "editor": 1, "owner": 2}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_member(db, workspace_id: str, user_id: str) -> dict | None:
    row = await db.fetchrow(
        "SELECT role FROM workspace_members WHERE workspace_id=$1 AND user_id=$2",
        workspace_id, user_id,
    )
    return dict(row) if row else None


async def _require_role(db, workspace_id: str, user_id: str, min_role: str) -> str:
    """Return user's role or raise 403."""
    m = await _get_member(db, workspace_id, user_id)
    if not m:
        raise HTTPException(403, "You are not a member of this workspace")
    if ROLE_ORDER.get(m["role"], -1) < ROLE_ORDER[min_role]:
        raise HTTPException(403, f"Requires {min_role} role (you are {m['role']})")
    return m["role"]


async def _fmt_workspace(db, ws: dict, user_id: str) -> dict:
    members = await db.fetch(
        """SELECT wm.user_id, wm.role, wm.joined_at, u.email, u.full_name
           FROM workspace_members wm
           JOIN users u ON u.id = wm.user_id
           WHERE wm.workspace_id = $1
           ORDER BY wm.joined_at""",
        str(ws["id"]),
    )
    doc_count = await db.fetchval(
        "SELECT COUNT(*) FROM documents WHERE workspace_id=$1 AND status!='deleted'",
        str(ws["id"]),
    )
    m = await _get_member(db, str(ws["id"]), user_id)
    return {
        "id":         str(ws["id"]),
        "name":       ws["name"],
        "owner_id":   str(ws["owner_id"]),
        "my_role":    m["role"] if m else None,
        "doc_count":  int(doc_count),
        "created_at": ws["created_at"].isoformat(),
        "members": [
            {
                "user_id":   str(r["user_id"]),
                "email":     r["email"],
                "full_name": r["full_name"] or "",
                "role":      r["role"],
                "joined_at": r["joined_at"].isoformat(),
            }
            for r in members
        ],
    }


# ── List my workspaces ────────────────────────────────────────────────────────
@router.get("/")
async def list_workspaces(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT w.* FROM workspaces w
           JOIN workspace_members wm ON wm.workspace_id = w.id
           WHERE wm.user_id = $1
           ORDER BY w.updated_at DESC""",
        str(current_user["id"]),
    )
    result = []
    for r in rows:
        m = await _get_member(db, str(r["id"]), str(current_user["id"]))
        doc_count = await db.fetchval(
            "SELECT COUNT(*) FROM documents WHERE workspace_id=$1 AND status!='deleted'",
            str(r["id"]),
        )
        member_count = await db.fetchval(
            "SELECT COUNT(*) FROM workspace_members WHERE workspace_id=$1", str(r["id"])
        )
        result.append({
            "id":           str(r["id"]),
            "name":         r["name"],
            "owner_id":     str(r["owner_id"]),
            "my_role":      m["role"] if m else None,
            "doc_count":    int(doc_count),
            "member_count": int(member_count),
            "created_at":   r["created_at"].isoformat(),
            "updated_at":   r["updated_at"].isoformat(),
        })
    return result


# ── Create workspace ──────────────────────────────────────────────────────────
class CreateWorkspace(BaseModel):
    name: str

@router.post("/")
async def create_workspace(
    body: CreateWorkspace,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    if not body.name.strip():
        raise HTTPException(400, "Workspace name is required")

    user_id = str(current_user["id"])
    ws = await db.fetchrow(
        "INSERT INTO workspaces (name, owner_id) VALUES ($1, $2) RETURNING *",
        body.name.strip(), user_id,
    )
    # Add owner as member
    await db.execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES ($1, $2, 'owner')",
        str(ws["id"]), user_id,
    )
    result = await _fmt_workspace(db, dict(ws), user_id)
    await audit(db, user_id=user_id, action="create_workspace",
                resource_type="workspace", resource_id=str(ws["id"]),
                metadata={"name": body.name.strip()},
                ip_address=ip_from(request), user_agent=ua_from(request))
    return result


# ── Get workspace ─────────────────────────────────────────────────────────────
@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    await _require_role(db, workspace_id, str(current_user["id"]), "viewer")
    ws = await db.fetchrow("SELECT * FROM workspaces WHERE id=$1", workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return await _fmt_workspace(db, dict(ws), str(current_user["id"]))


# ── Rename workspace ──────────────────────────────────────────────────────────
class UpdateWorkspace(BaseModel):
    name: str

@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: UpdateWorkspace,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    await _require_role(db, workspace_id, str(current_user["id"]), "owner")
    await db.execute(
        "UPDATE workspaces SET name=$1, updated_at=NOW() WHERE id=$2",
        body.name.strip(), workspace_id,
    )
    return {"ok": True}


# ── Delete workspace ──────────────────────────────────────────────────────────
@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    await _require_role(db, workspace_id, str(current_user["id"]), "owner")
    # Detach documents (don't delete them — just make them personal)
    await db.execute(
        "UPDATE documents SET workspace_id=NULL WHERE workspace_id=$1", workspace_id
    )
    await db.execute("DELETE FROM workspaces WHERE id=$1", workspace_id)
    return {"ok": True}


# ── Invite member ─────────────────────────────────────────────────────────────
class InviteMember(BaseModel):
    email: str
    role:  str = "viewer"   # viewer | editor

@router.post("/{workspace_id}/members")
async def invite_member(
    workspace_id: str,
    body: InviteMember,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    await _require_role(db, workspace_id, str(current_user["id"]), "owner")
    if body.role not in ("viewer", "editor"):
        raise HTTPException(400, "role must be viewer or editor")

    invitee = await db.fetchrow("SELECT id FROM users WHERE email=$1", body.email)
    if not invitee:
        raise HTTPException(404, f"No account found for {body.email}")

    existing = await _get_member(db, workspace_id, str(invitee["id"]))
    if existing:
        raise HTTPException(409, f"{body.email} is already a member")

    await db.execute(
        """INSERT INTO workspace_members (workspace_id, user_id, role, invited_by)
           VALUES ($1, $2, $3, $4)""",
        workspace_id, str(invitee["id"]), body.role, str(current_user["id"]),
    )
    await db.execute(
        "UPDATE workspaces SET updated_at=NOW() WHERE id=$1", workspace_id
    )
    await audit(db, user_id=str(current_user["id"]), action="invite_member",
                resource_type="workspace", resource_id=workspace_id,
                metadata={"invitee_email": body.email, "role": body.role})
    # Notify invitee
    inviter_name = current_user.get("email", "A team member")
    await send_workspace_invite(body.email, "", inviter_name, body.role, _APP_URL)
    return {"ok": True, "email": body.email, "role": body.role}


# ── Change member role ────────────────────────────────────────────────────────
class UpdateMemberRole(BaseModel):
    role: str

@router.patch("/{workspace_id}/members/{user_id}")
async def update_member_role(
    workspace_id: str,
    user_id: str,
    body: UpdateMemberRole,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    await _require_role(db, workspace_id, str(current_user["id"]), "owner")
    if body.role not in ("viewer", "editor"):
        raise HTTPException(400, "role must be viewer or editor (owner cannot be changed)")
    if user_id == str(current_user["id"]):
        raise HTTPException(400, "Cannot change your own role")

    r = await db.execute(
        "UPDATE workspace_members SET role=$1 WHERE workspace_id=$2 AND user_id=$3",
        body.role, workspace_id, user_id,
    )
    if r == "UPDATE 0":
        raise HTTPException(404, "Member not found")
    return {"ok": True}


# ── Remove member ─────────────────────────────────────────────────────────────
@router.delete("/{workspace_id}/members/{user_id}")
async def remove_member(
    workspace_id: str,
    user_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    me = str(current_user["id"])
    # Owner can remove anyone; members can remove themselves
    if user_id != me:
        await _require_role(db, workspace_id, me, "owner")
    if user_id == me:
        # Owners can't leave — must transfer or delete
        m = await _get_member(db, workspace_id, me)
        if m and m["role"] == "owner":
            raise HTTPException(400, "Owners cannot leave. Delete the workspace or transfer ownership first.")

    r = await db.execute(
        "DELETE FROM workspace_members WHERE workspace_id=$1 AND user_id=$2",
        workspace_id, user_id,
    )
    if r == "DELETE 0":
        raise HTTPException(404, "Member not found")
    return {"ok": True}


# ── Workspace documents ───────────────────────────────────────────────────────
@router.get("/{workspace_id}/documents")
async def list_workspace_documents(
    workspace_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    await _require_role(db, workspace_id, str(current_user["id"]), "viewer")
    import json as _json
    rows = await db.fetch(
        """SELECT d.id, d.original_name, d.status, d.error_message, d.file_type,
                  d.file_size, d.workspace_id, d.created_at, d.user_id,
                  d.doc_type, d.doc_domain, d.doc_language,
                  (SELECT COUNT(*) FROM document_chunks WHERE document_id=d.id) AS chunk_count,
                  COALESCE(
                    json_agg(json_build_object(
                      'id', t.id::text, 'name', t.name, 'color', t.color
                    )) FILTER (WHERE t.id IS NOT NULL),
                    '[]'::json
                  ) AS tags
           FROM documents d
           LEFT JOIN document_tag_map m ON m.document_id = d.id
           LEFT JOIN document_tags    t ON t.id = m.tag_id
           WHERE d.workspace_id=$1 AND d.status!='deleted'
           GROUP BY d.id
           ORDER BY d.created_at DESC""",
        workspace_id,
    )

    def _parse_tags(raw):
        if not raw: return []
        if isinstance(raw, list): return [t for t in raw if t.get("id")]
        try: return [t for t in _json.loads(raw) if t.get("id")]
        except: return []

    return [
        {
            "id":            str(r["id"]),
            "original_name": r["original_name"],
            "status":        r["status"],
            "error_message": r["error_message"],
            "file_type":     r["file_type"],
            "file_size":     r["file_size"],
            "chunk_count":   int(r["chunk_count"]),
            "doc_type":      r["doc_type"] or "general",
            "doc_domain":    r["doc_domain"] or "general",
            "doc_language":  r["doc_language"] or "en",
            "workspace_id":  str(r["workspace_id"]) if r["workspace_id"] else None,
            "uploaded_by":   str(r["user_id"]),
            "created_at":    r["created_at"].isoformat(),
            "tags":          _parse_tags(r["tags"]),
        }
        for r in rows
    ]
