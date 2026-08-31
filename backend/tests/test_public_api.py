from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

import pytest
from fastapi import BackgroundTasks, HTTPException
from jose import jwt

from auth.api_oauth import API_RESOURCE_METADATA, ApiPrincipal, get_api_principal, require_api_scope
from auth.service import ALGORITHM, SECRET_KEY
from routes import public_api
from routes.documents import DirectUploadCompleteRequest, DirectUploadSessionRequest
from routes.oauth import API_RESOURCE, ISSUER, MCP_RESOURCE


class ApiAuthDb:
    def __init__(self, *, grants=("documents:read",), client_active=True):
        self.grants = grants
        self.client_active = client_active

    async def fetchrow(self, sql, *_args):
        if "FROM users" in sql:
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "email": "developer@example.com",
                "full_name": "Developer",
                "role": "user",
                "created_at": datetime.now(timezone.utc),
            }
        if "oauth_clients" in sql:
            return {"client_id": "client-1"} if self.client_active else None
        return None

    async def fetchval(self, _sql, *_args):
        return "user"

    async def fetch(self, _sql, *_args):
        return [{"scope": scope} for scope in self.grants]


def access_token(*, audience=API_RESOURCE, scope="documents:read"):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "client_id": "client-1",
            "scope": scope,
            "aud": audience,
            "iss": ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


@pytest.mark.anyio
async def test_api_accepts_api_audience_and_current_scope_grant():
    principal = await get_api_principal(access_token(), ApiAuthDb())
    assert principal.client_id == "client-1"
    assert principal.scopes == {"documents:read"}


@pytest.mark.anyio
async def test_missing_api_token_advertises_protected_resource_metadata():
    with pytest.raises(HTTPException) as exc:
        await get_api_principal(None, ApiAuthDb())
    assert exc.value.status_code == 401
    assert API_RESOURCE_METADATA in exc.value.headers["WWW-Authenticate"]


@pytest.mark.anyio
async def test_api_rejects_mcp_audience_token():
    with pytest.raises(HTTPException) as exc:
        await get_api_principal(access_token(audience=MCP_RESOURCE), ApiAuthDb())
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_api_rejects_scope_removed_after_token_was_issued():
    with pytest.raises(HTTPException) as exc:
        await get_api_principal(
            access_token(scope="documents:read knowledge:generate"),
            ApiAuthDb(grants=("documents:read",)),
        )
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_required_scope_returns_oauth_insufficient_scope_challenge():
    dependency = require_api_scope("knowledge:query")
    principal = ApiPrincipal(user={"id": "user-1"}, client_id="client-1", scopes=frozenset({"documents:read"}))
    with pytest.raises(HTTPException) as exc:
        await dependency(principal)
    assert exc.value.status_code == 403
    assert "insufficient_scope" in exc.value.headers["WWW-Authenticate"]


@pytest.mark.anyio
async def test_current_identity_reports_token_user_and_workspace_count():
    class IdentityDb:
        async def fetchval(self, sql, user_id):
            assert "workspace_members" in sql
            assert user_id == "user-2"
            return 3

    principal = ApiPrincipal(
        user={
            "id": "user-2",
            "email": "second@example.com",
            "full_name": "Second User",
            "role": "user",
        },
        client_id="client-2",
        scopes=frozenset({"workspaces:read"}),
    )
    result = await public_api.api_current_identity(principal, IdentityDb())

    assert result["data"]["user_id"] == "user-2"
    assert result["data"]["email"] == "second@example.com"
    assert result["data"]["workspace_count"] == 3


@pytest.mark.anyio
async def test_public_upload_reuses_document_upload_pipeline(monkeypatch):
    captured = {}

    async def fake_create(body, current_user, db):
        captured.update(body=body, user=current_user, db=db)
        return {"doc_id": "doc-1", "upload_url": "https://storage.example/upload"}

    monkeypatch.setattr(public_api, "create_direct_upload_session", fake_create)
    principal = ApiPrincipal(
        user={"id": "user-1"},
        client_id="client-1",
        scopes=frozenset({"documents:write"}),
    )
    body = DirectUploadSessionRequest(filename="policy.pdf", content_type="application/pdf", file_size=42)
    result = await public_api.api_create_upload(body, principal, db="db")

    assert result["doc_id"] == "doc-1"
    assert captured["body"] is body
    assert captured["user"]["id"] == "user-1"


@pytest.mark.anyio
async def test_public_upload_completion_reuses_chunking_pipeline(monkeypatch):
    captured = {}

    async def fake_complete(body, background_tasks, current_user, db):
        captured.update(body=body, tasks=background_tasks, user=current_user, db=db)
        return {"doc_id": body.doc_id, "status": "chunking"}

    monkeypatch.setattr(public_api, "complete_direct_upload", fake_complete)
    principal = ApiPrincipal(
        user={"id": "user-1"},
        client_id="client-1",
        scopes=frozenset({"documents:write"}),
    )
    body = DirectUploadCompleteRequest(
        doc_id="doc-1",
        filename="policy.pdf",
        content_type="application/pdf",
        file_size=42,
        gcs_source_path="users/user-1/documents/doc-1/source/policy.pdf",
    )
    result = await public_api.api_complete_upload(body, BackgroundTasks(), principal, db="db")

    assert result == {"doc_id": "doc-1", "status": "chunking"}
    assert captured["user"]["id"] == "user-1"
