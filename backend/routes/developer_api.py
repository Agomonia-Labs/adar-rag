from __future__ import annotations

import hashlib
import json
import secrets
import re
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import AdminUser, CurrentUser
from database.connection import get_db
from routes.oauth import (
    _active_scopes,
    _ensure_oauth_tables,
    _normalize_scopes,
    _upsert_scope_grant,
    _valid_redirect_uri,
)
from services.mcp_enterprise import dispatch_webhook_deliveries
from services.service_credentials import register_secret, rotate_secret
from services.usage_governance import application_usage_summary


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


class DeveloperOrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    status: Literal["active", "suspended"] | None = None


class DeveloperScopeUpdate(BaseModel):
    scopes: list[str] = Field(min_length=1)


class DeveloperWorkspaceUpdate(BaseModel):
    workspace_ids: list[str] = Field(default_factory=list, max_length=100)


class DeveloperOrganizationMemberInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "admin", "developer", "viewer"] = "developer"


class DeveloperScopeRequestCreate(BaseModel):
    scopes: list[str] = Field(min_length=1)
    reason: str = Field(default="", max_length=2000)


class DeveloperScopeRequestDecision(BaseModel):
    decision: Literal["approved", "denied"]
    reviewer_note: str = Field(default="", max_length=2000)
    expires_at: datetime | None = None


WEBHOOK_EVENT_TYPES = (
    "document.uploaded", "document.chunked", "document.embedded", "document.failed",
    "batch.completed", "video.processing.completed", "workflow.completed",
    "review.approved", "packet.generated",
)


class DeveloperWebhookCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    endpoint_url: str = Field(min_length=10, max_length=2000)
    event_types: list[str] = Field(min_length=1, max_length=20)
    workspace_id: str | None = None
    description: str = Field(default="", max_length=500)
    timeout_seconds: int = Field(default=10, ge=2, le=30)


class DeveloperWebhookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    endpoint_url: str | None = Field(default=None, min_length=10, max_length=2000)
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=20)
    workspace_id: str | None = None
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=2, le=30)


class DeveloperQuotaPolicyInput(BaseModel):
    policy_name: str = Field(min_length=2, max_length=160)
    workspace_id: str | None = None
    scope: str | None = Field(default=None, max_length=120)
    operation: str | None = Field(default=None, max_length=240)
    window_seconds: int = Field(ge=1, le=2678400)
    limit_value: int = Field(ge=0, le=10_000_000_000)
    expires_at: datetime | None = None


class DeveloperSecretRotateInput(BaseModel):
    name: str = Field(default="Rotated secret", min_length=2, max_length=120)
    overlap_hours: int = Field(default=24, ge=1, le=168)


class DeveloperIpAllowlistInput(BaseModel):
    cidrs: list[str] = Field(default_factory=list, max_length=100)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _validate_webhook_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Webhook endpoints must be public HTTPS URLs")
    host = parsed.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        raise HTTPException(400, "Webhook endpoints cannot target local or metadata hosts")
    try:
        address = ipaddress.ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise HTTPException(400, "Webhook endpoints cannot target private addresses")
    except ValueError:
        pass
    return value.strip()


def _validate_event_types(values: list[str]) -> list[str]:
    normalized = sorted(set(values))
    unsupported = sorted(set(normalized) - set(WEBHOOK_EVENT_TYPES))
    if unsupported:
        raise HTTPException(400, detail={"code": "unsupported_event_types", "event_types": unsupported})
    return normalized


async def _managed_service_app(db, client_id: str, user_id: str, *, readonly: bool = False):
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if not app:
        raise HTTPException(404, "Active confidential application not found")
    if app["organization_id"]:
        roles = ("owner", "admin", "developer", "viewer") if readonly else ("owner", "admin")
        await _require_org_role(db, str(app["organization_id"]), user_id, roles)
    elif str(app["owner_user_id"]) != user_id:
        raise HTTPException(403, "Application owner access is required")
    return app


async def _validate_webhook_workspace(db, client_id: str, workspace_id: str | None) -> None:
    if not workspace_id:
        return
    exists = await db.fetchval(
        "SELECT 1 FROM oauth_service_workspace_grants WHERE client_id=$1 AND workspace_id=$2::uuid",
        client_id, workspace_id,
    )
    if not exists:
        raise HTTPException(400, "The webhook workspace must be granted to the application")


async def _require_service_management(db, user_id: str) -> None:
    granted = await _active_scopes(db, user_id)
    if "service:manage" not in granted:
        raise HTTPException(403, "The user is not approved for service:manage")


async def _require_org_role(
    db, organization_id: str, user_id: str,
    roles=("owner", "admin"), *, require_active: bool = True,
) -> str:
    status_clause = "AND o.status='active'" if require_active else ""
    role = await db.fetchval(
        f"""SELECT role FROM developer_organization_members m
           JOIN developer_organizations o ON o.id=m.organization_id
           WHERE m.organization_id=$1::uuid AND m.user_id=$2::uuid {status_clause}""",
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


@router.patch("/organizations/{organization_id}")
async def update_developer_organization(
    organization_id: str, body: DeveloperOrganizationUpdate,
    current_user: CurrentUser, db=Depends(get_db),
):
    actor_id = str(current_user["id"])
    actor_role = await _require_org_role(db, organization_id, actor_id, require_active=False)
    if body.name is None and body.status is None:
        raise HTTPException(400, "Provide an organization name or status")
    if body.status is not None and actor_role != "owner":
        raise HTTPException(403, "Only an organization owner can change organization status")
    row = await db.fetchrow(
        """UPDATE developer_organizations
              SET name=COALESCE($2,name),status=COALESCE($3,status),updated_at=NOW()
            WHERE id=$1::uuid RETURNING *""",
        organization_id, body.name.strip() if body.name else None, body.status,
    )
    if not row:
        raise HTTPException(404, "Developer organization not found")
    await _audit(
        db, event_type="organization.updated", actor_user_id=actor_id,
        organization_id=organization_id,
        metadata={key: value for key, value in {"name": body.name, "status": body.status}.items() if value is not None},
    )
    return {"data": _serialize_app(row)}


@router.get("/organizations/{organization_id}/members")
async def list_developer_organization_members(organization_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _require_org_role(
        db, organization_id, str(current_user["id"]),
        ("owner", "admin", "developer", "viewer"), require_active=False,
    )
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
    actor_role = await _require_org_role(db, organization_id, actor_id)
    user = await db.fetchrow("SELECT id,email FROM users WHERE LOWER(email)=LOWER($1)", body.email.strip())
    if not user:
        raise HTTPException(404, "The user must register with DocIntel before joining an organization")
    current_role = await db.fetchval(
        """SELECT role FROM developer_organization_members
           WHERE organization_id=$1::uuid AND user_id=$2::uuid""",
        organization_id, user["id"],
    )
    if (body.role == "owner" or current_role == "owner") and actor_role != "owner":
        raise HTTPException(403, "Only an organization owner can change ownership")
    if current_role == "owner" and body.role != "owner":
        owners = await db.fetchval(
            "SELECT COUNT(*) FROM developer_organization_members WHERE organization_id=$1::uuid AND role='owner'",
            organization_id,
        )
        if int(owners or 0) <= 1:
            raise HTTPException(400, "Promote another owner before changing the final owner's role")
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
    actor_role = await _require_org_role(db, organization_id, actor_id)
    member_role = await db.fetchval(
        """SELECT role FROM developer_organization_members
           WHERE organization_id=$1::uuid AND user_id=$2::uuid""", organization_id, member_user_id,
    )
    if not member_role:
        raise HTTPException(404, "Organization member not found")
    if member_role == "owner":
        if actor_role != "owner":
            raise HTTPException(403, "Only an organization owner can remove another owner")
        owners = await db.fetchval(
            "SELECT COUNT(*) FROM developer_organization_members WHERE organization_id=$1::uuid AND role='owner'",
            organization_id,
        )
        if int(owners or 0) <= 1:
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
                  NULL::uuid AS organization_id,NULL::text AS organization_name,
                  NULL::text AS organization_role
             FROM oauth_clients WHERE owner_user_id=$1::uuid
            UNION ALL
           SELECT s.client_id,s.client_name,'confidential',NULL::jsonb,s.scope,
                  s.created_at,s.updated_at,s.expires_at,s.revoked_at,
                  s.organization_id,o.name AS organization_name,m.role AS organization_role
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
    await register_secret(db, client_id, client_secret, name="Initial secret", created_by=user_id)
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
async def rotate_developer_secret(
    client_id: str, current_user: CurrentUser,
    body: DeveloperSecretRotateInput = DeveloperSecretRotateInput(), db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _require_service_management(db, user_id)
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if app and app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id)
    elif app and str(app["owner_user_id"]) != user_id:
        app = None
    if not app:
        raise HTTPException(404, "Active confidential application not found")
    secret_id, secret, previous_expires_at = await rotate_secret(
        db, client_id, created_by=user_id, overlap_hours=body.overlap_hours, name=body.name.strip(),
    )
    await _audit(db, event_type="secret.rotated", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"secret_id": secret_id, "overlap_hours": body.overlap_hours})
    return {
        "client_id": client_id, "secret_id": secret_id,
        "client_secret": secret,
        "previous_secrets_expire_at": previous_expires_at.isoformat(),
        "warning": "The new secret is shown once. Previous active secrets remain valid during the overlap window.",
    }


@router.get("/apps/{client_id}/credentials")
async def list_developer_credentials(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _managed_service_app(db, client_id, str(current_user["id"]), readonly=True)
    rows = await db.fetch(
        """SELECT id,name,secret_hint,created_at,expires_at,last_used_at,revoked_at
           FROM oauth_service_client_secrets WHERE client_id=$1 ORDER BY created_at DESC""", client_id,
    )
    allowlist = await db.fetch(
        "SELECT id,cidr::text AS cidr,description,created_at FROM oauth_service_ip_allowlists WHERE client_id=$1 ORDER BY cidr",
        client_id,
    )
    return {"data": [_serialize_app(row) for row in rows],
            "ip_allowlist": [_serialize_app(row) for row in allowlist]}


@router.delete("/apps/{client_id}/credentials/{secret_id}")
async def revoke_developer_credential(
    client_id: str, secret_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    app = await _managed_service_app(db, client_id, str(current_user["id"]))
    active = await db.fetchval(
        """SELECT COUNT(*) FROM oauth_service_client_secrets WHERE client_id=$1
           AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>NOW())""", client_id,
    )
    if int(active or 0) <= 1:
        raise HTTPException(400, "The final active application secret cannot be revoked")
    row = await db.fetchrow(
        """UPDATE oauth_service_client_secrets SET revoked_at=NOW()
           WHERE id=$1::uuid AND client_id=$2 AND revoked_at IS NULL RETURNING id""", secret_id, client_id,
    )
    if not row:
        raise HTTPException(404, "Active credential not found")
    await _audit(db, event_type="secret.revoked", actor_user_id=str(current_user["id"]), client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"secret_id": secret_id})
    return {"secret_id": secret_id, "revoked": True}


@router.put("/apps/{client_id}/ip-allowlist")
async def update_developer_ip_allowlist(
    client_id: str, body: DeveloperIpAllowlistInput, current_user: CurrentUser, db=Depends(get_db),
):
    import ipaddress
    app = await _managed_service_app(db, client_id, str(current_user["id"]))
    try:
        networks = sorted({str(ipaddress.ip_network(value.strip(), strict=False)) for value in body.cidrs})
    except ValueError as exc:
        raise HTTPException(400, f"Invalid CIDR: {exc}") from exc
    async with db.transaction():
        await db.execute("DELETE FROM oauth_service_ip_allowlists WHERE client_id=$1", client_id)
        for cidr in networks:
            await db.execute(
                """INSERT INTO oauth_service_ip_allowlists(client_id,cidr,created_by)
                   VALUES($1,$2::cidr,$3::uuid)""", client_id, cidr, str(current_user["id"]),
            )
    await _audit(db, event_type="ip_allowlist.updated", actor_user_id=str(current_user["id"]), client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"cidrs": networks})
    return {"client_id": client_id, "cidrs": networks}


@router.get("/apps/{client_id}/usage")
async def get_developer_usage(
    client_id: str, current_user: CurrentUser, days: int = 30, db=Depends(get_db),
):
    await _managed_service_app(db, client_id, str(current_user["id"]), readonly=True)
    return {"data": await application_usage_summary(db, client_id, days)}


@router.get("/apps/{client_id}/quota-policies")
async def list_developer_quota_policies(
    client_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    await _managed_service_app(db, client_id, str(current_user["id"]), readonly=True)
    rows = await db.fetch(
        """SELECT id,policy_name,workspace_id,scope,operation,window_seconds,limit_value,
                  status,effective_from,expires_at,created_at,updated_at
             FROM usage_quota_policies WHERE client_id=$1 ORDER BY created_at DESC""",
        client_id,
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.post("/apps/{client_id}/quota-policies", status_code=201)
async def create_developer_quota_policy(
    client_id: str, body: DeveloperQuotaPolicyInput,
    current_user: CurrentUser, db=Depends(get_db),
):
    app = await _managed_service_app(db, client_id, str(current_user["id"]))
    if body.workspace_id:
        await _validate_webhook_workspace(db, client_id, body.workspace_id)
    if body.scope and body.scope not in set(str(app["scope"] or "").split()):
        raise HTTPException(400, "Quota scope must be granted to the application")
    row = await db.fetchrow(
        """INSERT INTO usage_quota_policies
           (policy_name,organization_id,client_id,workspace_id,scope,operation,
            window_seconds,limit_value,created_by,expires_at)
           VALUES($1,$2::uuid,$3,$4::uuid,$5,$6,$7,$8,$9::uuid,$10)
           RETURNING *""",
        body.policy_name.strip(), app["organization_id"], client_id, body.workspace_id,
        body.scope, body.operation, body.window_seconds, body.limit_value,
        str(current_user["id"]), body.expires_at,
    )
    await _audit(
        db, event_type="quota.created", actor_user_id=str(current_user["id"]),
        client_id=client_id,
        organization_id=str(app["organization_id"]) if app["organization_id"] else None,
        metadata={"policy_id": str(row["id"]), "limit": body.limit_value,
                  "window_seconds": body.window_seconds},
    )
    return {"data": _serialize_app(row)}


@router.delete("/apps/{client_id}/quota-policies/{policy_id}")
async def delete_developer_quota_policy(
    client_id: str, policy_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    app = await _managed_service_app(db, client_id, str(current_user["id"]))
    row = await db.fetchrow(
        """UPDATE usage_quota_policies SET status='revoked',updated_at=NOW()
           WHERE id=$1::uuid AND client_id=$2 AND status='active' RETURNING id""",
        policy_id, client_id,
    )
    if not row:
        raise HTTPException(404, "Active quota policy not found")
    await _audit(
        db, event_type="quota.revoked", actor_user_id=str(current_user["id"]),
        client_id=client_id,
        organization_id=str(app["organization_id"]) if app["organization_id"] else None,
        metadata={"policy_id": policy_id},
    )
    return {"policy_id": policy_id, "revoked": True}


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


@router.get("/apps/{client_id}/scope-requests")
async def list_developer_scope_requests(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    user_id = str(current_user["id"])
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1", client_id)
    if not app:
        raise HTTPException(404, "Developer application not found")
    if app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id, ("owner", "admin", "developer", "viewer"))
    elif str(app["owner_user_id"]) != user_id:
        raise HTTPException(403, "Application owner access is required")
    rows = await db.fetch(
        """SELECT id,scope,reason,status,reviewer_note,reviewed_at,created_at,updated_at
             FROM oauth_service_scope_requests WHERE client_id=$1 ORDER BY created_at DESC""",
        client_id,
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.post("/apps/{client_id}/scope-requests", status_code=201)
async def create_developer_scope_request(
    client_id: str, body: DeveloperScopeRequestCreate,
    current_user: CurrentUser, db=Depends(get_db),
):
    await _ensure_oauth_tables(db)
    user_id = str(current_user["id"])
    await _require_service_management(db, user_id)
    requested = _normalize_scopes(body.scopes)
    app = await db.fetchrow("SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if not app:
        raise HTTPException(404, "Active confidential application not found")
    if app["organization_id"]:
        await _require_org_role(db, str(app["organization_id"]), user_id)
    elif str(app["owner_user_id"]) != user_id:
        raise HTTPException(403, "Application owner access is required")
    current_app_scopes = set(str(app["scope"] or "").split())
    user_scopes = await _active_scopes(db, user_id)
    immediately_available = (requested - current_app_scopes) & user_scopes
    pending = (requested - current_app_scopes) - user_scopes
    if immediately_available:
        updated = current_app_scopes | immediately_available
        await db.execute(
            "UPDATE oauth_service_clients SET scope=$2,updated_at=NOW() WHERE client_id=$1",
            client_id, " ".join(sorted(updated)),
        )
    for scope in sorted(pending):
        await db.execute(
            """INSERT INTO oauth_service_scope_requests
               (client_id,organization_id,requested_by,scope,reason,status)
               VALUES($1,$2::uuid,$3::uuid,$4,$5,'pending')
               ON CONFLICT(client_id,scope) DO UPDATE SET
                 requested_by=EXCLUDED.requested_by,reason=EXCLUDED.reason,status='pending',
                 reviewer_id=NULL,reviewer_note='',reviewed_at=NULL,updated_at=NOW()""",
            client_id, app["organization_id"], user_id, scope, body.reason.strip(),
        )
    await _audit(
        db, event_type="scopes.requested", actor_user_id=user_id, client_id=client_id,
        organization_id=str(app["organization_id"]) if app["organization_id"] else None,
        metadata={"pending_scopes": sorted(pending), "immediately_added": sorted(immediately_available), "reason": body.reason.strip()},
    )
    return {
        "client_id": client_id,
        "already_enabled": sorted(requested & current_app_scopes),
        "immediately_added": sorted(immediately_available),
        "requested_scopes": sorted(pending),
        "status": "pending" if pending else "updated",
    }


@router.get("/admin/scope-requests")
async def list_admin_developer_scope_requests(
    admin: AdminUser, status: str = "pending", db=Depends(get_db),
):
    await _ensure_oauth_tables(db)
    if status not in {"pending", "approved", "denied", "all"}:
        raise HTTPException(400, "Invalid request status")
    condition = "" if status == "all" else "WHERE r.status=$1"
    args = () if status == "all" else (status,)
    rows = await db.fetch(
        f"""SELECT r.*,u.email,s.client_name,o.name AS organization_name
              FROM oauth_service_scope_requests r
              JOIN users u ON u.id=r.requested_by
              JOIN oauth_service_clients s ON s.client_id=r.client_id
              LEFT JOIN developer_organizations o ON o.id=r.organization_id
              {condition} ORDER BY r.created_at""", *args,
    )
    return {"data": [_serialize_app(row) for row in rows]}


@router.post("/admin/scope-requests/{request_id}/decision")
async def decide_admin_developer_scope_request(
    request_id: str, body: DeveloperScopeRequestDecision,
    admin: AdminUser, db=Depends(get_db),
):
    await _ensure_oauth_tables(db)
    admin_id = str(admin["id"])
    async with db.transaction():
        request = await db.fetchrow(
            "SELECT * FROM oauth_service_scope_requests WHERE id=$1::uuid FOR UPDATE", request_id,
        )
        if not request:
            raise HTTPException(404, "Service application scope request not found")
        if request["status"] != "pending":
            raise HTTPException(409, "Scope request has already been reviewed")
        app = await db.fetchrow(
            "SELECT * FROM oauth_service_clients WHERE client_id=$1 AND revoked_at IS NULL FOR UPDATE",
            request["client_id"],
        )
        if not app:
            raise HTTPException(404, "Active confidential application not found")
        await db.execute(
            """UPDATE oauth_service_scope_requests
                  SET status=$2,reviewer_id=$3::uuid,reviewer_note=$4,
                      reviewed_at=NOW(),updated_at=NOW()
                WHERE id=$1::uuid""",
            request_id, body.decision, admin_id, body.reviewer_note.strip(),
        )
        if body.decision == "approved":
            await _upsert_scope_grant(
                db, str(app["owner_user_id"]), None, str(request["scope"]),
                admin_id, body.reviewer_note.strip(), body.expires_at,
            )
            scopes = set(str(app["scope"] or "").split()) | {str(request["scope"])}
            await db.execute(
                "UPDATE oauth_service_clients SET scope=$2,updated_at=NOW() WHERE client_id=$1",
                request["client_id"], " ".join(sorted(scopes)),
            )
        await _audit(
            db, event_type=f"scope_request.{body.decision}", actor_user_id=admin_id,
            client_id=str(request["client_id"]),
            organization_id=str(request["organization_id"]) if request["organization_id"] else None,
            metadata={"scope": request["scope"], "reviewer_note": body.reviewer_note.strip()},
        )
    return {"request_id": request_id, "client_id": request["client_id"], "scope": request["scope"], "status": body.decision}


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


@router.get("/apps/{client_id}/webhooks")
async def list_developer_webhooks(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _managed_service_app(db, client_id, str(current_user["id"]), readonly=True)
    rows = await db.fetch(
        """SELECT s.id,s.client_id,s.organization_id,s.workspace_id,s.name,s.description,
                  s.webhook_url AS endpoint_url,s.event_types,s.status,s.timeout_seconds,
                  s.last_sequence,s.created_at,s.updated_at,
                  COUNT(d.id) FILTER (WHERE d.status='delivered') AS delivered_count,
                  COUNT(d.id) FILTER (WHERE d.status IN ('retrying','dead_letter')) AS failed_count,
                  MAX(d.created_at) AS last_delivery_at
             FROM mcp_event_subscriptions s
             LEFT JOIN mcp_webhook_deliveries d ON d.subscription_id=s.id
            WHERE s.client_id=$1
            GROUP BY s.id ORDER BY s.created_at DESC""", client_id,
    )
    return {"data": [_serialize_webhook(row) for row in rows], "event_types": list(WEBHOOK_EVENT_TYPES)}


@router.post("/apps/{client_id}/webhooks", status_code=201)
async def create_developer_webhook(
    client_id: str, body: DeveloperWebhookCreate, current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    app = await _managed_service_app(db, client_id, user_id)
    if "events:write" not in str(app["scope"] or "").split():
        raise HTTPException(403, "The application must have events:write before registering webhooks")
    await _validate_webhook_workspace(db, client_id, body.workspace_id)
    secret = "whsec_" + secrets.token_urlsafe(36)
    row = await db.fetchrow(
        """INSERT INTO mcp_event_subscriptions
           (user_id,workspace_id,event_types,webhook_url,webhook_secret,client_id,organization_id,
            name,description,timeout_seconds,status)
           VALUES($1::uuid,$2::uuid,$3::jsonb,$4,$5,$6,$7::uuid,$8,$9,$10,'active') RETURNING *""",
        user_id, body.workspace_id, json.dumps(_validate_event_types(body.event_types)),
        _validate_webhook_url(body.endpoint_url), secret, client_id, app["organization_id"],
        body.name.strip(), body.description.strip(), body.timeout_seconds,
    )
    await _audit(db, event_type="webhook.created", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"subscription_id": str(row["id"]), "event_types": _validate_event_types(body.event_types)})
    result = _serialize_webhook(row)
    result["signing_secret"] = secret
    result["warning"] = "The signing secret is shown once. Store it securely."
    return {"data": result}


@router.patch("/apps/{client_id}/webhooks/{subscription_id}")
async def update_developer_webhook(
    client_id: str, subscription_id: str, body: DeveloperWebhookUpdate,
    current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    app = await _managed_service_app(db, client_id, user_id)
    await _validate_webhook_workspace(db, client_id, body.workspace_id)
    event_types = _validate_event_types(body.event_types) if body.event_types is not None else None
    endpoint = _validate_webhook_url(body.endpoint_url) if body.endpoint_url is not None else None
    status = None if body.enabled is None else ("active" if body.enabled else "disabled")
    row = await db.fetchrow(
        """UPDATE mcp_event_subscriptions SET
             name=COALESCE($4,name),description=COALESCE($5,description),
             webhook_url=COALESCE($6,webhook_url),event_types=COALESCE($7::jsonb,event_types),
             workspace_id=CASE WHEN $8::boolean THEN $9::uuid ELSE workspace_id END,
             status=COALESCE($10,status),timeout_seconds=COALESCE($11,timeout_seconds),updated_at=NOW()
           WHERE id=$1::uuid AND client_id=$2 AND organization_id IS NOT DISTINCT FROM $3::uuid
           RETURNING *""",
        subscription_id, client_id, app["organization_id"], body.name.strip() if body.name else None,
        body.description.strip() if body.description is not None else None, endpoint,
        json.dumps(event_types) if event_types is not None else None,
        "workspace_id" in body.model_fields_set, body.workspace_id, status, body.timeout_seconds,
    )
    if not row:
        raise HTTPException(404, "Webhook subscription not found")
    await _audit(db, event_type="webhook.updated", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"subscription_id": subscription_id, "status": status})
    return {"data": _serialize_webhook(row)}


@router.delete("/apps/{client_id}/webhooks/{subscription_id}")
async def delete_developer_webhook(
    client_id: str, subscription_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    app = await _managed_service_app(db, client_id, user_id)
    result = await db.execute(
        "DELETE FROM mcp_event_subscriptions WHERE id=$1::uuid AND client_id=$2",
        subscription_id, client_id,
    )
    if result.endswith("0"):
        raise HTTPException(404, "Webhook subscription not found")
    await _audit(db, event_type="webhook.deleted", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"subscription_id": subscription_id})
    return {"subscription_id": subscription_id, "deleted": True}


@router.post("/apps/{client_id}/webhooks/{subscription_id}/rotate-secret")
async def rotate_developer_webhook_secret(
    client_id: str, subscription_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    app = await _managed_service_app(db, client_id, user_id)
    secret = "whsec_" + secrets.token_urlsafe(36)
    row = await db.fetchrow(
        """UPDATE mcp_event_subscriptions SET previous_webhook_secret=webhook_secret,
             previous_secret_expires_at=NOW()+INTERVAL '24 hours',webhook_secret=$3,updated_at=NOW()
           WHERE id=$1::uuid AND client_id=$2 RETURNING id""",
        subscription_id, client_id, secret,
    )
    if not row:
        raise HTTPException(404, "Webhook subscription not found")
    await _audit(db, event_type="webhook.secret_rotated", actor_user_id=user_id, client_id=client_id,
                 organization_id=str(app["organization_id"]) if app["organization_id"] else None,
                 metadata={"subscription_id": subscription_id, "previous_secret_valid_hours": 24})
    return {"subscription_id": subscription_id, "signing_secret": secret,
            "previous_secret_expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
            "warning": "The new signing secret is shown once. The previous secret remains valid for 24 hours."}


@router.post("/apps/{client_id}/webhooks/{subscription_id}/test", status_code=202)
async def test_developer_webhook(
    client_id: str, subscription_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _managed_service_app(db, client_id, user_id)
    subscription = await db.fetchrow(
        "SELECT * FROM mcp_event_subscriptions WHERE id=$1::uuid AND client_id=$2", subscription_id, client_id,
    )
    if not subscription:
        raise HTTPException(404, "Webhook subscription not found")
    event = await db.fetchrow(
        """INSERT INTO mcp_events(user_id,workspace_id,event_type,resource_type,resource_id,payload)
           VALUES($1::uuid,$2::uuid,'webhook.test','webhook',$3,$4::jsonb) RETURNING id""",
        user_id, subscription["workspace_id"], subscription_id,
        json.dumps({"status": "test", "message": "DocIntel webhook endpoint test", "event_source": "developer_ui"}),
    )
    delivery_id = await db.fetchval(
        """INSERT INTO mcp_webhook_deliveries(subscription_id,event_id)
           VALUES($1,$2) RETURNING id""", subscription_id, event["id"],
    )
    await dispatch_webhook_deliveries([str(delivery_id)])
    return {"delivery_id": str(delivery_id), "status": "pending"}


@router.get("/apps/{client_id}/webhook-deliveries")
async def list_developer_webhook_deliveries(
    client_id: str, current_user: CurrentUser, subscription_id: str | None = None,
    limit: int = 100, db=Depends(get_db),
):
    await _managed_service_app(db, client_id, str(current_user["id"]), readonly=True)
    limit = max(1, min(limit, 250))
    rows = await db.fetch(
        """SELECT d.*,e.event_type,e.resource_type,e.resource_id,e.created_at AS event_created_at,
                  COALESCE((SELECT jsonb_agg(jsonb_build_object(
                    'attempt_number',a.attempt_number,'request_timestamp',a.request_timestamp,
                    'http_status',a.http_status,'duration_ms',a.duration_ms,
                    'response_preview',a.response_preview,'error_message',a.error_message,
                    'created_at',a.created_at) ORDER BY a.attempt_number DESC)
                    FROM mcp_webhook_delivery_attempts a WHERE a.delivery_id=d.id),'[]'::jsonb) AS attempts
             FROM mcp_webhook_deliveries d
             JOIN mcp_event_subscriptions s ON s.id=d.subscription_id
             JOIN mcp_events e ON e.id=d.event_id
            WHERE s.client_id=$1 AND ($2::uuid IS NULL OR s.id=$2::uuid)
            ORDER BY d.created_at DESC LIMIT $3""", client_id, subscription_id, limit,
    )
    return {"data": [_serialize_webhook(row) for row in rows]}


@router.post("/apps/{client_id}/webhook-deliveries/{delivery_id}/replay", status_code=202)
async def replay_developer_webhook_delivery(
    client_id: str, delivery_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    await _managed_service_app(db, client_id, str(current_user["id"]))
    row = await db.fetchrow(
        """UPDATE mcp_webhook_deliveries d SET status='pending',attempt_count=0,next_attempt_at=NOW(),
             last_http_status=NULL,last_error=NULL,response_preview=NULL,delivered_at=NULL,updated_at=NOW()
           FROM mcp_event_subscriptions s WHERE d.id=$1::uuid AND s.id=d.subscription_id AND s.client_id=$2
           RETURNING d.id""", delivery_id, client_id,
    )
    if not row:
        raise HTTPException(404, "Webhook delivery not found")
    await dispatch_webhook_deliveries([delivery_id])
    return {"delivery_id": delivery_id, "status": "pending"}


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


def _serialize_webhook(row) -> dict[str, Any]:
    value = dict(row)
    value.pop("webhook_secret", None)
    value.pop("previous_webhook_secret", None)
    if isinstance(value.get("event_types"), str):
        value["event_types"] = json.loads(value["event_types"] or "[]")
    if isinstance(value.get("attempts"), str):
        try:
            value["attempts"] = json.loads(value["attempts"] or "[]")
        except json.JSONDecodeError:
            value["attempts"] = []
    elif "attempts" in value and not isinstance(value["attempts"], list):
        value["attempts"] = list(value["attempts"] or []) if isinstance(value["attempts"], (tuple, set)) else []
    for key, item in list(value.items()):
        if hasattr(item, "isoformat"):
            value[key] = item.isoformat()
    return value
