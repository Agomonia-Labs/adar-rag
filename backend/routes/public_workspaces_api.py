from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth.api_oauth import ApiPrincipal, enforce_api_usage, require_api_scope, validate_api_workspace_context
from database.connection import get_db
from routes import workspaces
from services.audit import audit, ip_from, ua_from


router = APIRouter(dependencies=[Depends(validate_api_workspace_context), Depends(enforce_api_usage)])

WorkspaceReader = Annotated[ApiPrincipal, Depends(require_api_scope("workspaces:read"))]
WorkspaceWriter = Annotated[ApiPrincipal, Depends(require_api_scope("workspaces:write"))]


class WorkspaceContextInput(BaseModel):
    workspace_id: str | None = None


class WorkspaceCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceMemberInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = "viewer"


class WorkspaceRoleInput(BaseModel):
    role: str


async def _audit_api(
    db,
    request: Request,
    principal: ApiPrincipal,
    *,
    action: str,
    workspace_id: str | None,
    status: str = "success",
    metadata: dict | None = None,
) -> None:
    await audit(
        db,
        user_id=principal.user_id,
        action=action,
        resource_type="workspace",
        resource_id=workspace_id or "personal",
        metadata={
            "oauth_client_id": principal.client_id,
            "workspace_id": workspace_id,
            "workspace_type": "team" if workspace_id else "personal",
            "granted_scopes": sorted(principal.scopes),
            "outcome": status,
            **(metadata or {}),
        },
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )


async def _workspace_context(db, principal: ApiPrincipal, workspace_id: str | None) -> dict:
    if not workspace_id:
        count = await db.fetchval(
            """SELECT COUNT(*) FROM documents
               WHERE user_id=$1::uuid AND workspace_id IS NULL AND status!='deleted'""",
            principal.user_id,
        )
        return {
            "id": None,
            "key": "personal",
            "name": "Personal",
            "workspace_type": "personal",
            "my_role": "owner",
            "doc_count": int(count or 0),
        }

    role = await workspaces._require_role(db, workspace_id, principal.user_id, "viewer")
    row = await db.fetchrow(
        """SELECT w.id,w.name,w.owner_id,w.created_at,w.updated_at,
                  (SELECT COUNT(*) FROM workspace_members wm WHERE wm.workspace_id=w.id) member_count,
                  (SELECT COUNT(*) FROM documents d WHERE d.workspace_id=w.id AND d.status!='deleted') doc_count
           FROM workspaces w WHERE w.id=$1::uuid""",
        workspace_id,
    )
    if not row:
        raise HTTPException(404, "Workspace not found")
    return {
        "id": str(row["id"]),
        "key": str(row["id"]),
        "name": row["name"],
        "workspace_type": "owned" if str(row["owner_id"]) == principal.user_id else "shared",
        "owner_id": str(row["owner_id"]),
        "my_role": role,
        "doc_count": int(row["doc_count"] or 0),
        "member_count": int(row["member_count"] or 0),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("/me/workspaces")
async def api_my_workspaces(principal: WorkspaceReader, db=Depends(get_db)):
    personal = await _workspace_context(db, principal, None)
    rows = await db.fetch(
        """SELECT w.id FROM workspaces w
           JOIN workspace_members wm ON wm.workspace_id=w.id
           WHERE wm.user_id=$1::uuid ORDER BY w.updated_at DESC""",
        principal.user_id,
    )
    team = [await _workspace_context(db, principal, str(row["id"])) for row in rows]
    return {"data": [personal, *team], "default_workspace_key": "personal"}


@router.post("/workspace-context")
async def api_select_workspace_context(
    body: WorkspaceContextInput,
    request: Request,
    principal: WorkspaceReader,
    db=Depends(get_db),
):
    context = await _workspace_context(db, principal, body.workspace_id)
    await _audit_api(
        db, request, principal, action="api.select_workspace_context", workspace_id=body.workspace_id
    )
    return {"data": context, "header": {"X-DocIntel-Workspace-ID": body.workspace_id or "personal"}}


@router.post("/workspaces", status_code=201)
async def api_create_workspace(
    body: WorkspaceCreateInput,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.create_workspace(
        workspaces.CreateWorkspace(name=body.name), request, current_user=principal.user, db=db
    )
    await _audit_api(
        db, request, principal, action="api.create_workspace", workspace_id=str(result["id"])
    )
    return {"data": result}


@router.patch("/workspaces/{workspace_id}")
async def api_update_workspace(
    workspace_id: str,
    body: WorkspaceUpdateInput,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.update_workspace(
        workspace_id, workspaces.UpdateWorkspace(name=body.name), current_user=principal.user, db=db
    )
    await _audit_api(db, request, principal, action="api.update_workspace", workspace_id=workspace_id)
    return {"data": result}


@router.delete("/workspaces/{workspace_id}")
async def api_delete_workspace(
    workspace_id: str,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.delete_workspace(workspace_id, current_user=principal.user, db=db)
    await _audit_api(db, request, principal, action="api.delete_workspace", workspace_id=workspace_id)
    return {"data": result}


@router.post("/workspaces/{workspace_id}/members", status_code=201)
async def api_add_workspace_member(
    workspace_id: str,
    body: WorkspaceMemberInput,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.invite_member(
        workspace_id,
        workspaces.InviteMember(email=body.email, role=body.role),
        current_user=principal.user,
        db=db,
    )
    await _audit_api(
        db,
        request,
        principal,
        action="api.add_workspace_member",
        workspace_id=workspace_id,
        metadata={"member_email": body.email, "role": body.role},
    )
    return {"data": result, "membership_status": "active"}


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
async def api_update_workspace_member(
    workspace_id: str,
    user_id: str,
    body: WorkspaceRoleInput,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.update_member_role(
        workspace_id,
        user_id,
        workspaces.UpdateMemberRole(role=body.role),
        current_user=principal.user,
        db=db,
    )
    await _audit_api(
        db, request, principal, action="api.update_workspace_member", workspace_id=workspace_id,
        metadata={"member_user_id": user_id, "role": body.role},
    )
    return {"data": result}


@router.delete("/workspaces/{workspace_id}/members/{user_id}")
async def api_remove_workspace_member(
    workspace_id: str,
    user_id: str,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.remove_member(
        workspace_id, user_id, current_user=principal.user, db=db
    )
    await _audit_api(
        db, request, principal, action="api.remove_workspace_member", workspace_id=workspace_id,
        metadata={"member_user_id": user_id},
    )
    return {"data": result}


@router.post("/workspaces/{workspace_id}/leave")
async def api_leave_workspace(
    workspace_id: str,
    request: Request,
    principal: WorkspaceWriter,
    db=Depends(get_db),
):
    result = await workspaces.remove_member(
        workspace_id, principal.user_id, current_user=principal.user, db=db
    )
    await _audit_api(db, request, principal, action="api.leave_workspace", workspace_id=workspace_id)
    return {"data": result}
