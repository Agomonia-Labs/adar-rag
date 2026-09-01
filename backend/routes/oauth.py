from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from auth.dependencies import AdminUser, CurrentUser
from auth.router import _ensure_mfa_table, _mfa_enabled, _send_mfa_challenge, _token_hash
from auth.service import ALGORITHM, SECRET_KEY, create_access_token, decode_token, verify_password
from database.connection import get_db
from services.limiter import ip_10_per_min


router = APIRouter()
oauth_router = router

ISSUER = os.getenv("OAUTH_ISSUER_URL", "http://localhost:8000").rstrip("/")
MCP_RESOURCE = os.getenv("OAUTH_MCP_RESOURCE", "http://localhost:8081/mcp").rstrip("/")
API_RESOURCE = os.getenv("OAUTH_API_RESOURCE", "http://localhost:8000/api/v1").rstrip("/")
API_DOCUMENTATION_URL = os.getenv("OAUTH_API_DOCUMENTATION_URL", "https://labs.agomoniai.com/developers/api")
MCP_AUDIENCE = MCP_RESOURCE
SUPPORTED_RESOURCES = {MCP_RESOURCE, API_RESOURCE}
MCP_INTROSPECTION_SECRET = os.getenv("MCP_INTROSPECTION_SECRET", "")
ACCESS_MINUTES = int(os.getenv("OAUTH_ACCESS_TOKEN_MINUTES", "15"))
REFRESH_DAYS = int(os.getenv("OAUTH_REFRESH_TOKEN_DAYS", "30"))
ALLOWED_SCOPES = {
    "workspaces:read",
    "workspaces:write",
    "documents:read",
    "documents:write",
    "knowledge:query",
    "knowledge:generate",
    "sessions:write",
    "video:read",
    "video:process",
    "workflows:read",
    "workflows:write",
    "reviews:write",
    "reviews:approve",
    "packets:write",
    "batches:read",
    "batches:write",
    "events:read",
    "events:write",
    "artifacts:read",
    "artifacts:write",
    "versions:read",
    "versions:write",
    "evaluations:run",
    "service:manage",
}

SCOPE_CATALOG = {
    "workspaces:read": ("View accessible workspaces", "low"),
    "workspaces:write": ("Create and administer workspace membership", "high"),
    "documents:read": ("View document metadata and content", "medium"),
    "documents:write": ("Upload, modify, and delete documents", "high"),
    "knowledge:query": ("Ask grounded questions across knowledge", "medium"),
    "knowledge:generate": ("Generate summaries and derived knowledge", "medium"),
    "sessions:write": ("Create and update chat sessions", "medium"),
    "video:read": ("View video intelligence metadata and results", "medium"),
    "video:process": ("Start video processing workflows", "high"),
    "workflows:read": ("View vertical workflow runs", "medium"),
    "workflows:write": ("Start and modify vertical workflows", "high"),
    "reviews:write": ("Create and modify human reviews", "high"),
    "reviews:approve": ("Approve governed workflow outcomes", "critical"),
    "packets:write": ("Generate, ingest, or withdraw packets", "high"),
    "batches:read": ("View batch jobs and item results", "medium"),
    "batches:write": ("Start, retry, and cancel batch jobs", "high"),
    "events:read": ("Read operation progress and lifecycle events", "medium"),
    "events:write": ("Create event subscriptions and webhooks", "high"),
    "artifacts:read": ("Read saved knowledge artifacts", "medium"),
    "artifacts:write": ("Create and update knowledge artifacts", "high"),
    "versions:read": ("View document version history", "medium"),
    "versions:write": ("Register document versions and changes", "high"),
    "evaluations:run": ("Run quality evaluations against owned traces", "high"),
    "service:manage": ("Create unattended service clients", "critical"),
}
_OAUTH_TABLES_READY = False


class ScopeRequestBody(BaseModel):
    client_id: str
    scopes: list[str] = Field(min_length=1)
    reason: str = Field(default="", max_length=2000)


class ScopeDecisionBody(BaseModel):
    decision: str
    assignment_scope: str = "user"
    reviewer_note: str = Field(default="", max_length=2000)
    expires_at: datetime | None = None


class ScopeAssignmentBody(BaseModel):
    user_id: str
    client_id: str | None = None
    scopes: list[str] = Field(min_length=1)
    reviewer_note: str = Field(default="", max_length=2000)
    expires_at: datetime | None = None


class ServiceClientBody(BaseModel):
    client_name: str = Field(min_length=2, max_length=200)
    owner_user_id: str
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url_sha256(value: str) -> str:
    import base64

    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).rstrip(b"=").decode()


def _valid_redirect_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    if parsed.fragment or parsed.username or parsed.password:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def _redirect_uris(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return [str(item) for item in value] if isinstance(value, list) else []


async def _ensure_oauth_tables(db) -> None:
    global _OAUTH_TABLES_READY
    if _OAUTH_TABLES_READY:
        return
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id TEXT PRIMARY KEY,
            client_name TEXT NOT NULL,
            redirect_uris JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        );
        ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE;
        ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS client_type TEXT NOT NULL DEFAULT 'public';
        ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS default_scope TEXT NOT NULL DEFAULT '';
        ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
            code_hash TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            redirect_uri TEXT NOT NULL,
            scope TEXT NOT NULL,
            resource TEXT NOT NULL,
            code_challenge TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
            token_hash TEXT PRIMARY KEY,
            family_id UUID NOT NULL DEFAULT uuid_generate_v4(),
            client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            resource TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            replaced_by_hash TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_refresh_family ON oauth_refresh_tokens(family_id);
        CREATE TABLE IF NOT EXISTS oauth_scope_requests (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_id TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewer_note TEXT NOT NULL DEFAULT '',
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id,client_id,scope)
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_scope_requests_status
          ON oauth_scope_requests(status,created_at);
        CREATE TABLE IF NOT EXISTS oauth_scope_grants (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_id TEXT REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewer_note TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            revoked_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id,client_id,scope)
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_scope_grants_active
          ON oauth_scope_grants(user_id,client_id,scope);
        ALTER TABLE oauth_scope_grants ALTER COLUMN client_id DROP NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_scope_grants_user_global
          ON oauth_scope_grants(user_id,scope) WHERE client_id IS NULL;
        INSERT INTO oauth_scope_grants
          (user_id,client_id,scope,granted_by,reviewer_note,expires_at,created_at,updated_at)
        SELECT DISTINCT ON (user_id,scope)
          user_id,NULL,scope,granted_by,
          CASE WHEN reviewer_note='' THEN 'Promoted from an existing client-specific grant' ELSE reviewer_note END,
          expires_at,created_at,NOW()
        FROM oauth_scope_grants
        WHERE client_id IS NOT NULL AND revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at>NOW())
        ORDER BY user_id,scope,created_at DESC
        ON CONFLICT (user_id,scope) WHERE client_id IS NULL DO NOTHING;
        UPDATE oauth_scope_grants
           SET revoked_at=NOW(),updated_at=NOW()
         WHERE client_id IS NOT NULL AND revoked_at IS NULL
           AND EXISTS (
             SELECT 1 FROM oauth_scope_grants global_grant
              WHERE global_grant.user_id=oauth_scope_grants.user_id
                AND global_grant.scope=oauth_scope_grants.scope
                AND global_grant.client_id IS NULL
                AND global_grant.revoked_at IS NULL
           );
        CREATE TABLE IF NOT EXISTS developer_organizations (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'active',
            created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS developer_organization_members (
            organization_id UUID NOT NULL REFERENCES developer_organizations(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'developer',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(organization_id,user_id)
        );
        ALTER TABLE oauth_service_clients ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES developer_organizations(id) ON DELETE CASCADE;
        ALTER TABLE oauth_service_clients ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        CREATE TABLE IF NOT EXISTS oauth_service_workspace_grants (
            client_id TEXT NOT NULL REFERENCES oauth_service_clients(client_id) ON DELETE CASCADE,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(client_id,workspace_id)
        );
        CREATE TABLE IF NOT EXISTS oauth_service_audit_events (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            organization_id UUID REFERENCES developer_organizations(id) ON DELETE SET NULL,
            client_id TEXT,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS oauth_service_scope_requests (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            client_id TEXT NOT NULL REFERENCES oauth_service_clients(client_id) ON DELETE CASCADE,
            organization_id UUID REFERENCES developer_organizations(id) ON DELETE CASCADE,
            requested_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewer_note TEXT NOT NULL DEFAULT '',
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(client_id,scope)
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_service_scope_requests_status
          ON oauth_service_scope_requests(status,created_at);
        """
    )
    _OAUTH_TABLES_READY = True


def _normalize_scopes(scopes) -> set[str]:
    values = {str(scope).strip() for scope in scopes if str(scope).strip()}
    if not values or not values <= ALLOWED_SCOPES:
        raise HTTPException(400, "Invalid or unsupported scope")
    return values


async def _active_scopes(db, user_id: str, _client_id: str = "") -> set[str]:
    role = await db.fetchval("SELECT role FROM users WHERE id=$1::uuid", user_id)
    if role == "admin":
        return set(ALLOWED_SCOPES)
    rows = await db.fetch(
        """SELECT scope FROM oauth_scope_grants
           WHERE user_id=$1::uuid AND revoked_at IS NULL
             AND (expires_at IS NULL OR expires_at>NOW())""",
        user_id,
    )
    return {str(row["scope"]) for row in rows}


async def _queue_scope_requests(db, user_id: str, client_id: str, scopes: set[str], reason: str = "OAuth authorization request") -> None:
    for scope in sorted(scopes):
        await db.execute(
            """INSERT INTO oauth_scope_requests(user_id,client_id,scope,reason,status)
               VALUES($1::uuid,$2,$3,$4,'pending')
               ON CONFLICT(user_id,client_id,scope) DO UPDATE SET
                 reason=EXCLUDED.reason,
                 status='pending',reviewer_id=NULL,reviewer_note='',reviewed_at=NULL,
                 updated_at=NOW()""",
            user_id, client_id, scope, reason,
        )


async def _require_scope_grants(db, user_id: str, client_id: str, requested: set[str]) -> set[str]:
    granted = await _active_scopes(db, user_id, client_id)
    missing = requested - granted
    if missing:
        raise HTTPException(403, detail={
            "code": "scope_approval_required",
            "message": "One or more requested MCP scopes require administrator approval",
            "missing_scopes": sorted(missing),
        })
    return requested


def _metadata() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "revocation_endpoint": f"{ISSUER}/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": sorted(ALLOWED_SCOPES),
        "resource_parameter_supported": True,
        "resource_indicators_supported": sorted(SUPPORTED_RESOURCES),
    }


@router.get("/api/oauth/scopes/catalog")
async def scope_catalog(current_user: CurrentUser):
    return {"scopes": [
        {"scope": scope, "description": SCOPE_CATALOG[scope][0], "risk": SCOPE_CATALOG[scope][1]}
        for scope in sorted(ALLOWED_SCOPES)
    ]}


@router.get("/api/oauth/scopes/me")
async def my_scope_access(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    grants = await _active_scopes(db, str(current_user["id"]), client_id)
    grant_rows = await db.fetch(
        """SELECT id,scope,expires_at,created_at FROM oauth_scope_grants
           WHERE user_id=$1::uuid AND revoked_at IS NULL
             AND (expires_at IS NULL OR expires_at>NOW()) ORDER BY scope""",
        str(current_user["id"]),
    )
    requests = await db.fetch(
        """SELECT id,scope,status,reason,reviewer_note,reviewed_at,created_at,updated_at
           FROM oauth_scope_requests WHERE user_id=$1::uuid AND client_id=$2 ORDER BY scope""",
        str(current_user["id"]), client_id,
    )
    return {
        "client_id": client_id,
        "granted_scopes": sorted(grants),
        "grants": [_serializable(dict(row)) for row in grant_rows],
        "requests": [_serializable(dict(row)) for row in requests],
    }


@router.post("/api/oauth/scopes/requests")
async def request_scope_access(body: ScopeRequestBody, current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    requested = _normalize_scopes(body.scopes)
    client = await db.fetchrow("SELECT client_id FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL", body.client_id)
    if not client:
        raise HTTPException(404, "OAuth client not found")
    granted = await _active_scopes(db, str(current_user["id"]), body.client_id)
    await _queue_scope_requests(db, str(current_user["id"]), body.client_id, requested - granted, body.reason)
    return {
        "client_id": body.client_id,
        "already_granted": sorted(requested & granted),
        "requested_scopes": sorted(requested - granted),
        "status": "pending" if requested - granted else "already_granted",
    }


@router.get("/api/admin/oauth/scope-requests")
async def list_scope_requests(admin: AdminUser, status: str = "pending", db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    if status not in {"pending", "approved", "denied", "all"}:
        raise HTTPException(400, "Invalid request status")
    condition = "" if status == "all" else "WHERE r.status=$1"
    args = () if status == "all" else (status,)
    rows = await db.fetch(
        f"""SELECT r.*,u.email,c.client_name FROM oauth_scope_requests r
            JOIN users u ON u.id=r.user_id JOIN oauth_clients c ON c.client_id=r.client_id
            {condition} ORDER BY r.created_at""", *args,
    )
    return {"requests": [_serializable(dict(row)) for row in rows]}


@router.get("/api/admin/oauth/scope-grants")
async def list_scope_grants(admin: AdminUser, user_id: str = "", client_id: str = "", db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    conditions = ["g.revoked_at IS NULL", "(g.expires_at IS NULL OR g.expires_at>NOW())"]
    args = []
    if user_id:
        args.append(user_id)
        conditions.append(f"g.user_id=${len(args)}::uuid")
    if client_id:
        args.append(client_id)
        conditions.append(f"(g.client_id=${len(args)} OR g.client_id IS NULL)")
    rows = await db.fetch(
        f"""SELECT g.*,u.email,COALESCE(c.client_name,'Any registered MCP client') AS client_name FROM oauth_scope_grants g
            JOIN users u ON u.id=g.user_id LEFT JOIN oauth_clients c ON c.client_id=g.client_id
            WHERE {' AND '.join(conditions)} ORDER BY u.email,c.client_name,g.scope""", *args,
    )
    return {"grants": [_serializable(dict(row)) for row in rows]}


@router.get("/api/admin/oauth/clients")
async def list_oauth_clients(admin: AdminUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    rows = await db.fetch(
        """SELECT c.client_id,c.client_name,c.redirect_uris,c.created_at,
                  COUNT(g.id) FILTER (WHERE g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>NOW())) AS active_grants
             FROM oauth_clients c
             LEFT JOIN oauth_scope_grants g ON g.client_id=c.client_id
            WHERE c.revoked_at IS NULL
            GROUP BY c.client_id ORDER BY c.created_at DESC"""
    )
    return {"clients": [_serializable(dict(row)) for row in rows]}


@router.post("/api/admin/oauth/scope-requests/{request_id}/decision")
async def decide_scope_request(request_id: str, body: ScopeDecisionBody, admin: AdminUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    decision = body.decision.lower()
    if decision not in {"approved", "denied"}:
        raise HTTPException(400, "decision must be approved or denied")
    async with db.transaction():
        row = await db.fetchrow("SELECT * FROM oauth_scope_requests WHERE id=$1::uuid FOR UPDATE", request_id)
        if not row:
            raise HTTPException(404, "Scope request not found")
        await db.execute(
            """UPDATE oauth_scope_requests SET status=$2,reviewer_id=$3::uuid,reviewer_note=$4,
                 reviewed_at=NOW(),updated_at=NOW() WHERE id=$1::uuid""",
            request_id, decision, str(admin["id"]), body.reviewer_note,
        )
        if decision == "approved":
            await _upsert_scope_grant(
                db, str(row["user_id"]), None, str(row["scope"]),
                str(admin["id"]), body.reviewer_note, body.expires_at,
            )
    if decision == "approved":
        await _revoke_refresh_families(db, str(row["user_id"]), None)
    return {"request_id": request_id, "scope": row["scope"], "status": decision, "assignment_scope": "user"}


@router.post("/api/admin/oauth/scope-grants")
async def assign_scope_access(body: ScopeAssignmentBody, admin: AdminUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    scopes = _normalize_scopes(body.scopes)
    user = await db.fetchrow("SELECT id FROM users WHERE id=$1::uuid", body.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    for scope in sorted(scopes):
        await _upsert_scope_grant(db, body.user_id, None, scope, str(admin["id"]), body.reviewer_note, body.expires_at)
        await db.execute(
            """UPDATE oauth_scope_requests SET status='approved',reviewer_id=$3::uuid,
                 reviewer_note=$4,reviewed_at=NOW(),updated_at=NOW()
               WHERE user_id=$1::uuid AND scope=$2""",
            body.user_id, scope, str(admin["id"]), body.reviewer_note,
        )
    await _revoke_refresh_families(db, body.user_id, None)
    return {"user_id": body.user_id, "assignment_scope": "user", "granted_scopes": sorted(scopes)}


@router.delete("/api/admin/oauth/scope-grants/{grant_id}")
async def revoke_scope_access(grant_id: str, admin: AdminUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    row = await db.fetchrow(
        """UPDATE oauth_scope_grants SET revoked_at=NOW(),revoked_by=$2::uuid,updated_at=NOW()
           WHERE id=$1::uuid AND revoked_at IS NULL RETURNING user_id,client_id,scope""",
        grant_id, str(admin["id"]),
    )
    if not row:
        raise HTTPException(404, "Active scope grant not found")
    await _revoke_refresh_families(db, str(row["user_id"]), None)
    return {"grant_id": grant_id, "scope": row["scope"], "status": "revoked"}


async def _upsert_scope_grant(db, user_id, client_id, scope, admin_id, note, expires_at):
    conflict = "(user_id,scope) WHERE client_id IS NULL" if client_id is None else "(user_id,client_id,scope)"
    await db.execute(
        f"""INSERT INTO oauth_scope_grants(user_id,client_id,scope,granted_by,reviewer_note,expires_at)
            VALUES($1::uuid,$2,$3,$4::uuid,$5,$6)
            ON CONFLICT {conflict} DO UPDATE SET granted_by=EXCLUDED.granted_by,
              reviewer_note=EXCLUDED.reviewer_note,expires_at=EXCLUDED.expires_at,
              revoked_at=NULL,revoked_by=NULL,updated_at=NOW()""",
        user_id, client_id, scope, admin_id, note, expires_at,
    )


async def _revoke_refresh_families(db, user_id: str, client_id: str | None) -> None:
    if client_id:
        await db.execute(
            "UPDATE oauth_refresh_tokens SET revoked_at=NOW() WHERE user_id=$1::uuid AND client_id=$2 AND revoked_at IS NULL",
            user_id, client_id,
        )
    else:
        await db.execute(
            "UPDATE oauth_refresh_tokens SET revoked_at=NOW() WHERE user_id=$1::uuid AND revoked_at IS NULL",
            user_id,
        )


def _serializable(value):
    if isinstance(value, dict): return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list): return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"): return value.isoformat()
    return str(value) if value.__class__.__name__ == "UUID" else value


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata():
    return _metadata()


@router.get("/.well-known/oauth-protected-resource/api")
async def api_protected_resource_metadata():
    return {
        "resource": API_RESOURCE,
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": sorted(ALLOWED_SCOPES),
        "resource_documentation": API_DOCUMENTATION_URL,
    }


@router.post("/register")
async def register_client(request: Request, db=Depends(get_db), _rl=Depends(ip_10_per_min)):
    await _ensure_oauth_tables(db)
    body = await request.json()
    redirect_uris = body.get("redirect_uris") or []
    if not redirect_uris or not all(isinstance(uri, str) and _valid_redirect_uri(uri) for uri in redirect_uris):
        raise HTTPException(400, "redirect_uris must contain valid HTTPS or loopback callback URLs")
    if body.get("token_endpoint_auth_method", "none") != "none":
        raise HTTPException(400, "Only public PKCE clients are supported")
    client_id = secrets.token_urlsafe(24)
    client_name = str(body.get("client_name") or "MCP Client")[:200]
    await db.execute(
        "INSERT INTO oauth_clients(client_id,client_name,redirect_uris) VALUES($1,$2,$3::jsonb)",
        client_id,
        client_name,
        json.dumps(redirect_uris),
    )
    return JSONResponse(
        {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )


def _authorize_values(data) -> dict[str, str]:
    return {key: str(data.get(key, "")) for key in (
        "client_id", "redirect_uri", "response_type", "scope", "state", "code_challenge",
        "code_challenge_method", "resource",
    )}


async def _validate_authorization(values: dict[str, str], db):
    await _ensure_oauth_tables(db)
    client = await db.fetchrow(
        "SELECT client_name,redirect_uris FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL",
        values["client_id"],
    )
    if not client:
        raise HTTPException(400, "Unknown OAuth client")
    if values["redirect_uri"] not in _redirect_uris(client["redirect_uris"]):
        raise HTTPException(400, "redirect_uri is not registered")
    if values["response_type"] != "code" or values["code_challenge_method"] != "S256":
        raise HTTPException(400, "Authorization code with S256 PKCE is required")
    if len(values["code_challenge"]) < 43:
        raise HTTPException(400, "A valid PKCE code_challenge is required")
    if values["resource"].rstrip("/") not in SUPPORTED_RESOURCES:
        raise HTTPException(400, "Invalid OAuth resource")
    scopes = set(values["scope"].split())
    if not scopes or not scopes <= ALLOWED_SCOPES:
        raise HTTPException(400, "Invalid or unsupported scope")
    return client


def _page(title: str, content: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font:16px system-ui;background:#101827;color:#eaf0f7;margin:0;display:grid;place-items:center;min-height:100vh}}
main{{width:min(440px,calc(100% - 32px));background:#182334;border:1px solid #34445b;padding:24px;border-radius:8px}}
h1{{font-size:22px;margin-top:0}}label{{display:block;margin:14px 0 6px}}input{{box-sizing:border-box;width:100%;padding:11px;background:#0e1725;color:white;border:1px solid #60708a;border-radius:5px}}
button{{margin-top:18px;padding:11px 16px;border:0;border-radius:5px;background:#35b6a0;color:#071a17;font-weight:700;width:100%}}small{{color:#aebbd0}}.error{{color:#ffaaa3}}</style></head><body><main>{content}</main></body></html>""")


def _hidden(values: dict[str, str]) -> str:
    return "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in values.items())


@router.get("/authorize")
async def authorize(request: Request, db=Depends(get_db)):
    values = _authorize_values(request.query_params)
    client = await _validate_authorization(values, db)
    return _page("Authorize ADAR DocIntel", f"""
<h1>Connect {html.escape(client['client_name'])}</h1>
<p>Sign in to ADAR DocIntel. After MFA, this client may access only the scopes shown below.</p>
<small>{html.escape(values['scope'])}</small>
<form method=post action=/authorize>{_hidden(values)}
<label>Email</label><input name=email type=email required autocomplete=username>
<label>Password</label><input name=password type=password required autocomplete=current-password>
<button type=submit>Continue securely</button></form>""")


@router.post("/authorize")
async def authorize_login(request: Request, db=Depends(get_db)):
    form = await request.form()
    values = _authorize_values(form)
    client = await _validate_authorization(values, db)
    row = await db.fetchrow(
        "SELECT id,email,hashed_password,full_name,role,is_verified FROM users WHERE email=$1",
        str(form.get("email", "")),
    )
    if not row or not verify_password(str(form.get("password", "")), row["hashed_password"]):
        return _page("Authorization failed", '<h1>Authorization failed</h1><p class=error>Incorrect email or password.</p>')
    if not row["is_verified"]:
        return _page("Authorization failed", '<h1>Authorization failed</h1><p class=error>Email verification is required.</p>')
    if _mfa_enabled():
        challenge = await _send_mfa_challenge(db, row)
        fields = {**values, "mfa_token": challenge["mfa_token"]}
        return _page("Verify sign-in", f"""
<h1>Verify sign-in</h1><p>Enter the code sent to {html.escape(challenge['email_hint'])}.</p>
<form method=post action=/authorize/verify>{_hidden(fields)}
<label>Verification code</label><input name=otp inputmode=numeric pattern="[0-9]{{6}}" required autocomplete=one-time-code>
<button type=submit>Authorize {html.escape(client['client_name'])}</button></form>""")
    return await _issue_authorization_code(db, values, row["id"])


@router.post("/authorize/verify")
async def authorize_verify(request: Request, db=Depends(get_db)):
    form = await request.form()
    values = _authorize_values(form)
    await _validate_authorization(values, db)
    mfa_token, otp = str(form.get("mfa_token", "")), str(form.get("otp", "")).strip()
    try:
        payload = decode_token(mfa_token)
    except Exception:
        raise HTTPException(401, "MFA session expired")
    await _ensure_mfa_table(db)
    challenge = await db.fetchrow(
        """SELECT id,user_id,code_hash,expires_at,attempts,used FROM login_mfa_challenges
           WHERE user_id=$1::uuid AND mfa_token_hash=$2 ORDER BY created_at DESC LIMIT 1""",
        payload.get("sub"), _token_hash(mfa_token),
    )
    if not challenge or challenge["used"] or challenge["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(401, "MFA code is invalid or expired")
    if challenge["attempts"] >= 3 or _token_hash(otp) != challenge["code_hash"]:
        if challenge:
            await db.execute("UPDATE login_mfa_challenges SET attempts=attempts+1 WHERE id=$1", challenge["id"])
        raise HTTPException(401, "Invalid MFA code")
    await db.execute("UPDATE login_mfa_challenges SET used=TRUE WHERE id=$1", challenge["id"])
    return await _issue_authorization_code(db, values, challenge["user_id"])


async def _issue_authorization_code(db, values: dict[str, str], user_id):
    requested = _normalize_scopes(values["scope"].split())
    granted = await _active_scopes(db, str(user_id), values["client_id"])
    missing = requested - granted
    if missing:
        await _queue_scope_requests(db, str(user_id), values["client_id"], missing)
        listed = "".join(f"<li>{html.escape(scope)}</li>" for scope in sorted(missing))
        return _page("Scope approval required", f"""
<h1>Scope approval required</h1>
<p>Your identity was verified, but this OAuth client requested MCP permissions that have not been assigned.</p>
<ul>{listed}</ul>
<p>An administrator must approve these scopes. Retry OAuth authorization after approval.</p>""")
    approved_scope = " ".join(sorted(requested))
    code = secrets.token_urlsafe(40)
    await db.execute(
        """INSERT INTO oauth_authorization_codes
           (code_hash,client_id,user_id,redirect_uri,scope,resource,code_challenge,expires_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
        _hash(code), values["client_id"], user_id, values["redirect_uri"], approved_scope,
        values["resource"].rstrip("/"), values["code_challenge"], datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    query = {"code": code}
    if values["state"]:
        query["state"] = values["state"]
    separator = "&" if "?" in values["redirect_uri"] else "?"
    return RedirectResponse(values["redirect_uri"] + separator + urlencode(query), status_code=303)


def _access_token(
    user_id: str, client_id: str, scope: str, resource: str,
    *, extra_claims: dict | None = None,
) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ACCESS_MINUTES)
    claims = {
        "iss": ISSUER, "sub": user_id, "aud": resource, "client_id": client_id,
        "scope": scope, "iat": now, "exp": expires, "jti": secrets.token_urlsafe(16),
    }
    claims.update(extra_claims or {})
    token = jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)
    return token, int((expires - now).total_seconds())


async def _new_refresh_token(db, *, client_id, user_id, scope, resource, family_id=None):
    raw = secrets.token_urlsafe(48)
    if family_id:
        await db.execute(
            """INSERT INTO oauth_refresh_tokens(token_hash,family_id,client_id,user_id,scope,resource,expires_at)
               VALUES($1,$2,$3,$4,$5,$6,$7)""",
            _hash(raw), family_id, client_id, user_id, scope, resource,
            datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS),
        )
    else:
        await db.execute(
            """INSERT INTO oauth_refresh_tokens(token_hash,client_id,user_id,scope,resource,expires_at)
               VALUES($1,$2,$3,$4,$5,$6)""",
            _hash(raw), client_id, user_id, scope, resource,
            datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS),
        )
    row = await db.fetchrow("SELECT family_id FROM oauth_refresh_tokens WHERE token_hash=$1", _hash(raw))
    return raw, row["family_id"]


@router.post("/token")
async def token(request: Request, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    form = await request.form()
    grant_type, client_id = str(form.get("grant_type", "")), str(form.get("client_id", ""))
    if grant_type == "client_credentials":
        secret = str(form.get("client_secret", ""))
        row = await db.fetchrow(
            """SELECT s.* FROM oauth_service_clients s
               LEFT JOIN developer_organizations o ON o.id=s.organization_id
               WHERE s.client_id=$1 AND s.revoked_at IS NULL
                 AND (s.expires_at IS NULL OR s.expires_at>NOW())
                 AND (s.organization_id IS NULL OR o.status='active')""", client_id,
        )
        if not row or not secrets.compare_digest(str(row["client_secret_hash"]), _hash(secret)):
            raise HTTPException(401, "invalid_client")
        allowed = set(str(row["scope"]).split())
        requested = set(str(form.get("scope", "")).split()) or allowed
        if not requested <= allowed:
            raise HTTPException(403, "invalid_scope")
        resource = str(form.get("resource", MCP_RESOURCE)).rstrip("/")
        if resource not in SUPPORTED_RESOURCES:
            raise HTTPException(400, "invalid_target")
        scope = " ".join(sorted(requested))
        extra_claims = {"token_kind": "service"}
        if row.get("organization_id"):
            extra_claims["organization_id"] = str(row["organization_id"])
        access, expires_in = _access_token(
            str(row["owner_user_id"]), client_id, scope, resource,
            extra_claims=extra_claims,
        )
        await db.execute(
            """INSERT INTO oauth_service_audit_events
               (organization_id,client_id,actor_user_id,event_type,metadata)
               VALUES($1,$2,$3::uuid,'token.issued',$4::jsonb)""",
            row.get("organization_id"), client_id, row["owner_user_id"],
            json.dumps({"resource": resource, "scope": scope}),
        )
        return {"access_token": access, "token_type": "Bearer", "expires_in": expires_in, "scope": scope}
    if grant_type == "authorization_code":
        code_hash = _hash(str(form.get("code", "")))
        async with db.transaction():
            row = await db.fetchrow("SELECT * FROM oauth_authorization_codes WHERE code_hash=$1 FOR UPDATE", code_hash)
            if not row or row["used_at"] or row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(400, "invalid_grant")
            client = await db.fetchrow("SELECT client_id FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
            if not client:
                raise HTTPException(400, "invalid_client")
            if client_id != row["client_id"] or str(form.get("redirect_uri", "")) != row["redirect_uri"]:
                raise HTTPException(400, "invalid_grant")
            if _b64url_sha256(str(form.get("code_verifier", ""))) != row["code_challenge"]:
                raise HTTPException(400, "invalid_grant")
            await _require_scope_grants(db, str(row["user_id"]), client_id, set(str(row["scope"]).split()))
            await db.execute("UPDATE oauth_authorization_codes SET used_at=NOW() WHERE code_hash=$1", code_hash)
            refresh, _ = await _new_refresh_token(db, client_id=client_id, user_id=row["user_id"], scope=row["scope"], resource=row["resource"])
        access, expires_in = _access_token(str(row["user_id"]), client_id, row["scope"], row["resource"])
        return {"access_token": access, "token_type": "Bearer", "expires_in": expires_in, "refresh_token": refresh, "scope": row["scope"]}
    if grant_type == "refresh_token":
        old_hash = _hash(str(form.get("refresh_token", "")))
        replayed = False
        async with db.transaction():
            row = await db.fetchrow("SELECT * FROM oauth_refresh_tokens WHERE token_hash=$1 FOR UPDATE", old_hash)
            if not row or row["client_id"] != client_id or row["expires_at"] < datetime.now(timezone.utc) or row["revoked_at"]:
                raise HTTPException(400, "invalid_grant")
            client = await db.fetchrow("SELECT client_id FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL", client_id)
            if not client:
                raise HTTPException(400, "invalid_client")
            await _require_scope_grants(db, str(row["user_id"]), client_id, set(str(row["scope"]).split()))
            if row["used_at"]:
                await db.execute("UPDATE oauth_refresh_tokens SET revoked_at=NOW() WHERE family_id=$1", row["family_id"])
                replayed = True
            else:
                refresh, _ = await _new_refresh_token(db, client_id=client_id, user_id=row["user_id"], scope=row["scope"], resource=row["resource"], family_id=row["family_id"])
                await db.execute("UPDATE oauth_refresh_tokens SET used_at=NOW(),replaced_by_hash=$2 WHERE token_hash=$1", old_hash, _hash(refresh))
        if replayed:
            raise HTTPException(400, "invalid_grant")
        access, expires_in = _access_token(str(row["user_id"]), client_id, row["scope"], row["resource"])
        return {"access_token": access, "token_type": "Bearer", "expires_in": expires_in, "refresh_token": refresh, "scope": row["scope"]}
    raise HTTPException(400, "unsupported_grant_type")


@router.post("/revoke")
async def revoke(request: Request, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    form = await request.form()
    await db.execute("UPDATE oauth_refresh_tokens SET revoked_at=NOW() WHERE token_hash=$1 AND client_id=$2", _hash(str(form.get("token", ""))), str(form.get("client_id", "")))
    return JSONResponse({}, status_code=200)


@router.post("/internal/oauth/introspect")
async def introspect(request: Request, db=Depends(get_db)):
    if not MCP_INTROSPECTION_SECRET or not secrets.compare_digest(request.headers.get("X-MCP-Introspection-Secret", ""), MCP_INTROSPECTION_SECRET):
        raise HTTPException(401, "Unauthorized")
    await _ensure_oauth_tables(db)
    body = await request.json()
    try:
        claims = jwt.decode(str(body.get("token", "")), SECRET_KEY, algorithms=[ALGORITHM], audience=MCP_AUDIENCE, issuer=ISSUER)
    except JWTError:
        return {"active": False}
    scopes = set(str(claims.get("scope", "")).split())
    if not scopes <= ALLOWED_SCOPES:
        return {"active": False}
    user = await db.fetchrow("SELECT id,email,role FROM users WHERE id=$1::uuid", claims["sub"])
    client = await db.fetchrow(
        """SELECT client_id,'user'::text AS token_kind,NULL::uuid AS organization_id,
                  NULL::text AS client_scope
             FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL
           UNION ALL
           SELECT s.client_id,'service'::text AS token_kind,s.organization_id,s.scope AS client_scope
             FROM oauth_service_clients s LEFT JOIN developer_organizations o ON o.id=s.organization_id
            WHERE s.client_id=$1 AND s.revoked_at IS NULL
              AND (s.expires_at IS NULL OR s.expires_at>NOW())
              AND (s.organization_id IS NULL OR o.status='active') LIMIT 1""", claims.get("client_id"),
    )
    if not user or not client:
        return {"active": False}
    client_kind = str(client.get("token_kind") or "user")
    claim_kind = str(claims.get("token_kind") or "user")
    client_org_id = str(client["organization_id"]) if client.get("organization_id") else None
    claim_org_id = str(claims["organization_id"]) if claims.get("organization_id") else None
    if claim_kind != client_kind or claim_org_id != client_org_id:
        return {"active": False, "reason": "client_identity_changed"}
    if client_kind == "service":
        client_scopes = set(str(client.get("client_scope") or "").split())
        if not scopes <= client_scopes:
            return {"active": False, "reason": "service_scope_revoked"}
    if user["role"] != "admin":
        granted = await _active_scopes(db, str(user["id"]), str(claims["client_id"]))
        if not scopes <= granted:
            return {"active": False, "reason": "scope_grant_revoked"}
    workspace_ids: list[str] = []
    if client_kind == "service" and client_org_id:
        rows = await db.fetch(
            """SELECT g.workspace_id
                 FROM oauth_service_workspace_grants g
                 JOIN workspace_members m ON m.workspace_id=g.workspace_id
                WHERE g.client_id=$1 AND m.user_id=$2::uuid
                ORDER BY g.workspace_id""",
            str(claims["client_id"]), str(user["id"]),
        )
        workspace_ids = [str(row["workspace_id"]) for row in rows]
        if not workspace_ids:
            return {"active": False, "reason": "workspace_grants_required"}
    return {
        "active": True,
        "sub": str(user["id"]),
        "client_id": claims["client_id"],
        "scope": claims["scope"],
        "exp": claims["exp"],
        "email": user["email"],
        "role": user["role"],
        "token_kind": client_kind,
        "organization_id": client_org_id,
        "workspace_ids": workspace_ids,
        "backend_token": create_access_token(str(user["id"]), user["email"]),
    }


@router.post("/api/admin/oauth/service-clients")
async def create_service_client(body: ServiceClientBody, admin: AdminUser, db=Depends(get_db)):
    scopes = _normalize_scopes(body.scopes)
    await _require_scope_grants(db, body.owner_user_id, "", scopes)
    owner = await db.fetchval("SELECT 1 FROM users WHERE id=$1::uuid", body.owner_user_id)
    if not owner:
        raise HTTPException(404, "Owner user not found")
    client_id = "svc_" + secrets.token_urlsafe(20)
    client_secret = secrets.token_urlsafe(48)
    await db.execute(
        """INSERT INTO oauth_service_clients(client_id,client_name,client_secret_hash,owner_user_id,scope,created_by,expires_at)
           VALUES($1,$2,$3,$4::uuid,$5,$6::uuid,$7)""",
        client_id, body.client_name, _hash(client_secret), body.owner_user_id,
        " ".join(sorted(scopes)), str(admin["id"]), body.expires_at,
    )
    return {"client_id": client_id, "client_secret": client_secret, "client_name": body.client_name,
            "owner_user_id": body.owner_user_id, "scope": " ".join(sorted(scopes)), "expires_at": body.expires_at,
            "warning": "The client_secret is shown once. Store it in Secret Manager."}


@router.delete("/api/admin/oauth/service-clients/{client_id}")
async def revoke_service_client(client_id: str, admin: AdminUser, db=Depends(get_db)):
    result = await db.execute("UPDATE oauth_service_clients SET revoked_at=NOW() WHERE client_id=$1 AND revoked_at IS NULL", client_id)
    if result.endswith("0"):
        raise HTTPException(404, "Service client not found")
    return {"revoked": True, "client_id": client_id}
