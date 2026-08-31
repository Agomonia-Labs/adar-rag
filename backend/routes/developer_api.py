from __future__ import annotations

import hashlib
import json
import secrets
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


class DeveloperAppUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    redirect_uris: list[str] = Field(default_factory=list, max_length=20)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _require_service_management(db, user_id: str) -> None:
    granted = await _active_scopes(db, user_id)
    if "service:manage" not in granted:
        raise HTTPException(403, "The user is not approved for service:manage")


@router.get("/apps")
async def list_developer_apps(current_user: CurrentUser, db=Depends(get_db)):
    await _ensure_oauth_tables(db)
    rows = await db.fetch(
        """SELECT client_id,client_name,client_type,redirect_uris,default_scope AS scope,
                  created_at,updated_at,NULL::timestamptz AS expires_at,revoked_at
             FROM oauth_clients WHERE owner_user_id=$1::uuid
            UNION ALL
           SELECT client_id,client_name,'confidential',NULL::jsonb,scope,
                  created_at,created_at,expires_at,revoked_at
             FROM oauth_service_clients WHERE owner_user_id=$1::uuid
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
    client_id = "svc_" + secrets.token_urlsafe(20)
    client_secret = secrets.token_urlsafe(48)
    await db.execute(
        """INSERT INTO oauth_service_clients
           (client_id,client_name,client_secret_hash,owner_user_id,scope,created_by,expires_at)
           VALUES($1,$2,$3,$4::uuid,$5,$4::uuid,$6)""",
        client_id, body.name.strip(), _hash_secret(client_secret), user_id,
        " ".join(sorted(scopes)), body.expires_at,
    )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_type": "confidential",
        "name": body.name.strip(),
        "scopes": sorted(scopes),
        "expires_at": body.expires_at,
        "warning": "The client_secret is shown once. Store it in Secret Manager.",
    }


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
    row = await db.fetchrow(
        """UPDATE oauth_service_clients SET client_secret_hash=$3
            WHERE client_id=$1 AND owner_user_id=$2::uuid AND revoked_at IS NULL
            RETURNING client_id,client_name""",
        client_id, user_id, _hash_secret(secret),
    )
    if not row:
        raise HTTPException(404, "Active confidential application not found")
    return {
        "client_id": client_id,
        "client_secret": secret,
        "warning": "The previous secret is no longer valid. This secret is shown once.",
    }


@router.delete("/apps/{client_id}")
async def revoke_developer_app(client_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    public_result = await db.execute(
        "UPDATE oauth_clients SET revoked_at=NOW(),updated_at=NOW() WHERE client_id=$1 AND owner_user_id=$2::uuid AND revoked_at IS NULL",
        client_id, user_id,
    )
    service_result = await db.execute(
        "UPDATE oauth_service_clients SET revoked_at=NOW() WHERE client_id=$1 AND owner_user_id=$2::uuid AND revoked_at IS NULL",
        client_id, user_id,
    )
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
