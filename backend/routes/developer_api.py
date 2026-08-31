from __future__ import annotations

import hashlib
import json
import secrets
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser
from database.connection import get_db
from routes.oauth import (
    _active_scopes,
    _ensure_oauth_tables,
    _normalize_scopes,
    _valid_redirect_uri,
)


router = APIRouter()


class DeveloperAppCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    client_type: Literal["public", "confidential"] = "public"
    redirect_uris: list[str] = Field(default_factory=list, max_length=20)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None
    organization_id: str | None = None
    workspace_ids: list[str] = Field(default_factory=list, max_length=100)


class DeveloperAppUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    redirect_uris: list[str] = Field(default_factory=list, max_length=20)


class DeveloperOrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, max_length=100)


class DeveloperScopeUpdate(BaseModel):
    scopes: list[str] = Field(min_length=1)


class DeveloperWorkspaceUpdate(BaseModel):
    workspace_ids: list[str] = Field(default_factory=list, max_length=100)


class DeveloperOrganizationMemberInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "admin", "developer", "viewer"] = "developer"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _require_service_management(db, user_id: str) -> None:
    granted = await _active_scopes(db, user_id)
    if "service:manage" not in granted:
        raise HTTPException(403, "The user is not approved for service:manage")


async def _require_org_role(db, organization_id: str, user_id: str, roles=("owner", "admin")) -> str:
    role = await db.fetchval(
        """SELECT role FROM developer_organization_members m
           JOIN developer_organizations o ON o.id=m.organization_id
           WHERE m.organization_id=$1::uuid AND m.user_id=$2::uuid AND o.status='active'""",
        organization_id, user_id,
    )
    if role not in roles:
        raise HTTPException(403, "Organization administrator access is required")
    return str(role)


async def _require_workspace_management(db, workspace_id: str, user_id: str) -> None:
    role = await db.fetchval(
        "SELECT role FROM workspace_members WHERE workspace_id=$1::uuid AND user_id=$2::uuid",
        workspace_id, user_id,
    )
    if role not in {"owner", "admin", "editor"}:
        raise HTTPException(403, f"Workspace management access is required for {workspace_id}")


async def _audit(db, *, event_type: str, actor_user_id: str, client_id: str | None = None,
                 organization_id: str | None = None, metadata: dict | None = None) -> None:
    await db.execute(
        """INSERT INTO oauth_service_audit_events
           (organization_id,client_id,actor_user_id,event_type,metadata)
           VALUES($1::uuid,$2,$3::uuid,$4,$5::jsonb)""",
        organization_id, client_id, actor_user_id, event_type, json.dumps(metadata or {}),
    )


@router.get("/organizations")
async def list_developer_organizations(current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    rows = await db.fetch(
        """SELECT o.id,o.name,o.slug,o.status,m.role,o.created_at,o.updated_at
           FROM developer_organizations o JOIN developer_organization_members m ON m.organization_id=o.id
           WHERE m.user_id=$1::uuid ORDER BY o.name""", str(current_user["id"]),
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.post("/organizations", status_code=201)
async def create_developer_organization(body: DeveloperOrganizationCreate, current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    user_id = str(current_user["id"])
    base = body.slug or body.name
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if len(slug) < 2:
        raise HTTPException(400, "Organization slug must contain at least two letters or numbers")
    row = await db.fetchrow(
        """INSERT INTO developer_organizations(name,slug,created_by)
           VALUES($1,$2,$3::uuid) RETURNING *""", body.name.strip(), slug, user_id,
    )
    await db.execute(
        """INSERT INTO developer_organization_members(organization_id,user_id,role)
           VALUES($1,$2::uuid,'owner')""", row["id"], user_id,
    )
    await _audit(db, event_type="organization.created", actor_user_id=user_id,
                 organization_id=str(row["id"]), metadata={"name": body.name.strip(), "slug": slug})
    return {"data": _serialize_app(row)}


@router.get("/organizations/{organization_id}/members")
async def list_developer_organization_members(organization_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _require_org_role(db, organization_id, str(current_user["id"]), ("owner", "admin", "developer", "viewer"))
    rows = await db.fetch(
        """SELECT u.id AS user_id,u.email,u.full_name,m.role,m.created_at
           FROM developer_organization_members m JOIN users u ON u.id=m.user_id
           WHERE m.organization_id=$1::uuid ORDER BY u.email""", organization_id,
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.put("/organizations/{organization_id}/members")
async def put_developer_organization_member(
    organization_id: str, body: DeveloperOrganizationMemberInput,
    current_user: CurrentUser, db=Depends(get_db),
):
    actor_id = str(current_user["id"])
    await _require_org_role(db, organization_id, actor_id)
    user = await db.fetchrow("SELECT id,email FROM users WHERE LOWER(email)=LOWER($1)", body.email.strip())
    if not user:
        raise HTTPException(404, "The user must register with DocIntel before joining an organization")
    await db.execute(
        """INSERT INTO developer_organization_members(organization_id,user_id,role)
           VALUES($1::uuid,$2,$3) ON CONFLICT(organization_id,user_id)
           DO UPDATE SET role=EXCLUDED.role""", organization_id, user["id"], body.role,
    )
    await _audit(db, event_type="organization.member_updated", actor_user_id=actor_id,
                 organization_id=organization_id,
                 metadata={"member_user_id": str(user["id"]), "email": user["email"], "role": body.role})
    return {"organization_id": organization_id, "user_id": str(user["id"]), "email": user["email"], "role": body.role}


@router.delete("/organizations/{organization_id}/members/{member_user_id}")
async def delete_developer_organization_member(
    organization_id: str, member_user_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    actor_id = str(current_user["id"])
    await _require_org_role(db, organization_id, actor_id)
    if member_user_id == actor_id:
        owners = await db.fetchval(
            """SELECT COUNT(*) FROM developer_organization_members
               WHERE organization_id=$1::uuid AND role='owner'""", organization_id,
        )
        current_role = await db.fetchval(
            """SELECT role FROM developer_organization_members
               WHERE organization_id=$1::uuid AND user_id=$2::uuid""", organization_id, actor_id,
        )
        if current_role == "owner" and int(owners or 0) <= 1:
            raise HTTPException(400, "The final organization owner cannot be removed")
    result = await db.execute(
        """DELETE FROM developer_organization_members
           WHERE organization_id=$1::uuid AND user_id=$2::uuid""", organization_id, member_user_id,
    )
    if result.endswith("0"):
        raise HTTPException(404, "Organization member not found")
    await _audit(db, event_type="organization.member_removed", actor_user_id=actor_id,
                 organization_id=organization_id, metadata={"member_user_id": member_user_id})
    return {"organization_id": organization_id, "user_id": member_user_id, "removed": True}


@router.get("/apps")
async def list_developer_apps(current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    rows = await db.fetch(
        """SELECT client_id,client_name,client_type,redirect_uris,default_scope AS scope,
                  created_at,updated_at,NULL::timestamptz AS expires_at,revoked_at,
                  NULL::uuid AS organization_id,NULL::text AS organization_name
             FROM oauth_clients WHERE owner_user_id=$1::uuid
            UNION ALL
           SELECT s.client_id,s.client_name,'confidential',NULL::jsonb,s.scope,
                  s.created_at,s.updated_at,s.expires_at,s.revoked_at,
                  s.organization_id,o.name AS organization_name
             FROM oauth_service_clients s
             LEFT JOIN developer_organizations o ON o.id=s.organization_id
             LEFT JOIN developer_organization_members m ON m.organization_id=s.organization_id AND m.user_id=$1::uuid
            WHERE s.owner_user_id=$1::uuid OR m.user_id IS NOT NULL
            ORDER BY created_at DESC""",
        str(current_user["id"]),
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.post("/apps", status_code=201)
async def create_developer_app(body: DeveloperAppCreate, current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    scopes = _normalize_scopes(body.scopes)
    user_id = str(current_user["id"])
    granted = await _active_scopes(db, user_id)
    missing = scopes - granted
    if missing:
        raise HTTPException(403, detail={
            "code": "scope_approval_required",
            "missing_scopes": sorted(missing),
        })

    if body.client_type == "public":
        if not body.redirect_uris or not all(_valid_redirect_uri(uri) for uri in body.redirect_uris):
            raise HTTPException(400, "Public clients require valid HTTPS or loopback redirect_uris")
        client_id = secrets.token_urlsafe(24)
        await db.execute(
            """INSERT INTO oauth_clients
               (client_id,client_name,redirect_uris,owner_user_id,client_type,default_scope)
               VALUES($1,$2,$3::jsonb,$4::uuid,'public',$5)""",
            client_id, body.name.strip(), json.dumps(body.redirect_uris), user_id,
            " ".join(sorted(scopes)),
        )
        return {
            "client_id": client_id,
            "client_type": "public",
            "name": body.name.strip(),
            "redirect_uris": body.redirect_uris,
            "scopes": sorted(scopes),
            "token_endpoint_auth_method": "none",
        }

    await _require_service_management(db, user_id)
    if body.organization_id:
        await _require_org_role(db, body.organization_id, user_id)
    for workspace_id in body.workspace_ids:
        await _require_workspace_management(db, workspace_id, user_id)
    client_id = "svc_" + secrets.token_urlsafe(20)
    client_secret = secrets.token_urlsafe(48)
    await db.execute(
        """INSERT INTO oauth_service_clients
           (client_id,client_name,client_secret_hash,owner_user_id,scope,created_by,expires_at,organization_id)
           VALUES($1,$2,$3,$4::uuid,$5,$4::uuid,$6,$7::uuid)""",
        client_id, body.name.strip(), _hash_secret(client_secret), user_id,
        " ".join(sorted(scopes)), body.expires_at, body.organization_id,
    )
    for workspace_id in sorted(set(body.workspace_ids)):
        await db.execute(
            """INSERT INTO oauth_service_workspace_grants(client_id,workspace_id,granted_by)
               VALUES($1,$2::uuid,$3::uuid)""", client_id, workspace_id, user_id,
        )
    await _audit(db, event_type="application.created", actor_user_id=user_id,
                 client_id=client_id, organization_id=body.organization_id,
                 metadata={"scopes": sorted(scopes), "workspace_ids": sorted(set(body.workspace_ids))})
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_type": "confidential",
        "name": body.name.strip(),
        "scopes": sorted(scopes),
        "expires_at": body.expires_at,
        "organization_id": body.organization_id,
        "workspace_ids": sorted(set(body.workspace_ids)),
        "warning": "The client_secret is shown once. Store it in Secret Manager.",
    }


@router.get("/apps/{client_id}")
async def get_developer_app(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    row = await db.fetchrow(
        """SELECT s.client_id,s.client_name,'confidential' AS client_type,s.scope,
                  s.organization_id,o.name AS organization_name,s.expires_at,s.revoked_at,
                  s.created_at,s.updated_at
           FROM oauth_service_clients s
           LEFT JOIN developer_organizations o ON o.id=s.organization_id
           WHERE s.client_id=$1""", client_id,
    )
    if not row:
        raise HTTPException(404, "Developer application not found")
    if row["organization_id"]:
        await _require_org_role(db, str(row["organization_id"]), user_id, ("owner", "admin", "developer", "viewer"))
    else:
        owner = await db.fetchval("SELECT owner_user_id FROM oauth_service_clients WHERE client_id=$1", client_id)
        if str(owner) != user_id:
            raise HTTPException(403, "Application owner access is required")
    workspaces = await db.fetch(
        """SELECT g.workspace_id,w.name,g.created_at FROM oauth_service_workspace_grants g
           JOIN workspaces w ON w.id=g.workspace_id WHERE g.client_id=$1 ORDER BY w.name""", client_id,
    )
    result = _serialize_app(row)
    result["workspaces"] = [_serialize_app(item) for item in workspaces]
    return {"data": result}


@router.patch("/apps/{client_id}")
async def update_developer_app(client_id: str, body: DeveloperAppUpdate, current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    user_id = str(current_user["id"])
    if body.redirect_uris and not all(_valid_redirect_uri(uri) for uri in body.redirect_uris):
        raise HTTPException(400, "redirect_uris must use HTTPS or a loopback callback")
    row = await db.fetchrow(
        """UPDATE oauth_clients SET client_name=$3,redirect_uris=$4::jsonb,updated_at=NOW()
            WHERE client_id=$1 AND owner_user_id=$2::uuid AND revoked_at IS NULL
            RETURNING client_id,client_name,client_type,redirect_uris,created_at,updated_at,revoked_at""",
        client_id, user_id, body.name.strip(), json.dumps(body.redirect_uris),
    )
    if row:
        return {"data": _serialize_app(row)}
    row = await db.fetchrow(
        """UPDATE oauth_service_clients SET client_name=$3
            WHERE client_id=$1 AND owner_user_id=$2::uuid AND revoked_at IS NULL
            RETURNING client_id,client_name,'confidential' AS client_type,scope,created_at,expires_at,revoked_at""",
        client_id, user_id, body.name.strip(),
    )
    if not row:
        raise HTTPException(404, "Developer application not found")
    return {"data": _serialize_app(row)}


@router.post("/apps/{client_id}/rotate-secret")
async def rotate_developer_secret(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _require_service_management(db, user_id)
    secret = secrets.token_urlsafe(48)
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if app and app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id)
    elif app and str(app["owner_user_id"]) != user_id:
        app = None
    row = None
    if app:
        row = await db.fetchrow(
            """UPDATE oauth_service_clients SET client_secret_hash=$2,updated_at=NOW()
               WHERE client_id=$1 RETURNING client_id,client_name,organization_id""",
            client_id, _hash_secret(secret),
        )
    if not row:
        raise HTTPException(404, "Active confidential application not found")
    await _audit(db, event_type="secret.rotated", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(row["organization_id"]) if row["organization_id"] else None)
    return {
        "client_id": client_id,
        "client_secret": secret,
        "warning": "The previous secret is no longer valid. This secret is shown once.",
    }


@router.put("/apps/{client_id}/scopes")
async def update_developer_scopes(client_id: str, body: DeveloperScopeUpdate, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _require_service_management(db, user_id)
    scopes = _normalize_scopes(body.scopes)
    granted = await _active_scopes(db, user_id)
    if not scopes <= granted:
        raise HTTPException(403, detail={"code": "scope_approval_required", "missing_scopes": sorted(scopes - granted)})
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if not app:
        raise HTTPException(404, "Active confidential application not found")
    if app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id)
    elif str(app["owner_user_id"]) != user_id:
        raise HTTPException(403, "Application owner access is required")
    await db.execute("UPDATE oauth_service_clients SET scope=$2,updated_at=NOW() WHERE client_id=$1", client_id, " ".join(sorted(scopes)))
    await _audit(db, event_type="scopes.updated", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"scopes": sorted(scopes)})
    return {"client_id": client_id, "scopes": sorted(scopes)}


@router.put("/apps/{client_id}/workspaces")
async def update_developer_workspaces(client_id: str, body: DeveloperWorkspaceUpdate, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if not app:
        raise HTTPException(404, "Active confidential application not found")
    if app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id)
    elif str(app["owner_user_id"]) != user_id:
        raise HTTPException(403, "Application owner access is required")
    workspace_ids = sorted(set(body.workspace_ids))
    for workspace_id in workspace_ids:
        await _require_workspace_management(db, workspace_id, user_id)
    async with db.transaction():
        await db.execute("DELETE FROM oauth_service_workspace_grants WHERE client_id=$1", client_id)
        for workspace_id in workspace_ids:
            await db.execute(
                """INSERT INTO oauth_service_workspace_grants(client_id,workspace_id,granted_by)
                   VALUES($1,$2::uuid,$3::uuid)""", client_id, workspace_id, user_id,
            )
    await _audit(db, event_type="workspaces.updated", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"workspace_ids": workspace_ids})
    return {"client_id": client_id, "workspace_ids": workspace_ids}


@router.get("/apps/{client_id}/audit")
async def list_developer_app_audit(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1", client_id)
    if not app:
        raise HTTPException(404, "Developer application not found")
    if app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id, ("owner", "admin", "developer", "viewer"))
    elif str(app["owner_user_id"]) != user_id:
        raise HTTPException(403, "Application owner access is required")
    rows = await db.fetch(
        """SELECT id,event_type,actor_user_id,metadata,created_at
           FROM oauth_service_audit_events WHERE client_id=$1 ORDER BY created_at DESC LIMIT 200""", client_id,
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.delete("/apps/{client_id}")
async def revoke_developer_app(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    public_result = await db.execute(
        "UPDATE oauth_clients SET revoked_at=NOW(),updated_at=NOW() WHERE client_id=$1 AND owner_user_id=$2::uuid AND revoked_at IS NULL",
        client_id, user_id,
    )
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    service_result = "UPDATE 0"
    if app:
        if app["organization_id"]:
            await _require_org_role(db, str(app["organization_id"]), user_id)
        elif str(app["owner_user_id"]) != user_id:
            app = None
        if app:
            service_result = await db.execute(
                "UPDATE oauth_service_clients SET revoked_at=NOW(),updated_at=NOW() WHERE client_id=$1 AND revoked_at IS NULL",
                client_id,
            )
            await _audit(db, event_type="application.revoked", actor_user_id=user_id, client_id=client_id,
                         organization_id=str(app["organization_id"]) if app["organization_id"] else None)
    if public_result.endswith("0") and service_result.endswith("0"):
        raise HTTPException(404, "Active developer application not found")
    return {"client_id": client_id, "revoked": True}


def _serialize_app(row) -> dict:
    value = dict(row)
    redirect_uris = value.get("redirect_uris")
    if isinstance(redirect_uris, str):
        redirect_uris = json.loads(redirect_uris)
    scope = value.pop("scope", None)
    value["redirect_uris"] = redirect_uris or []
    value["scopes"] = str(scope or "").split()
    for key, item in list(value.items()):
        if hasattr(item, "isoformat"):
            value[key] = item.isoformat()
    return value
