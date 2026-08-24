from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request
from jose import JWTError, jwt

from auth.service import ALGORITHM, SECRET_KEY
from routes.oauth import ISSUER, MCP_AUDIENCE


COOKIE_NAME = "docintel_mcp_playground"
MCP_URL = os.getenv("DOCINTEL_MCP_URL", "https://mcp.docintel.adar.agomoniai.com/mcp")
OAUTH_ISSUER = os.getenv("DOCINTEL_MCP_ISSUER_URL", ISSUER).rstrip("/")
CALLBACK_URL = os.getenv(
    "MCP_PLAYGROUND_CALLBACK_URL",
    f"{os.getenv('APP_URL', 'http://localhost:8000').rstrip('/')}/api/mcp-playground/oauth/callback",
)
DEFAULT_SCOPES = os.getenv(
    "MCP_PLAYGROUND_SCOPES",
    "workspaces:read documents:read documents:write knowledge:query knowledge:generate sessions:write "
    "video:read video:process workflows:read workflows:write reviews:write reviews:approve packets:write "
    "batches:read batches:write",
).split()


async def ensure_table(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS mcp_playground_sessions (
            session_hash TEXT PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            oauth_state TEXT UNIQUE NOT NULL,
            client_id TEXT NOT NULL,
            code_verifier_encrypted TEXT NOT NULL,
            access_token_encrypted TEXT,
            refresh_token_encrypted TEXT,
            scopes TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ,
            connected_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_mcp_playground_user ON mcp_playground_sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_mcp_playground_state ON mcp_playground_sessions(oauth_state);
    """)


async def create_authorization(db, user_id: str, scopes: list[str]) -> tuple[str, str]:
    await ensure_table(db)
    metadata = await _get_json(f"{OAUTH_ISSUER}/.well-known/oauth-authorization-server")
    requested = [scope for scope in scopes if scope in DEFAULT_SCOPES] or DEFAULT_SCOPES
    registration = await _post_json(metadata["registration_endpoint"], {
        "client_name": "ADAR DocIntel MCP Playground",
        "redirect_uris": [CALLBACK_URL],
        "token_endpoint_auth_method": "none",
    })
    client_id = registration.get("client_id")
    if not client_id:
        raise HTTPException(502, "OAuth registration did not return a client ID")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    session_token = secrets.token_urlsafe(48)
    await db.execute(
        """INSERT INTO mcp_playground_sessions
           (session_hash,user_id,oauth_state,client_id,code_verifier_encrypted,scopes)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        _hash(session_token), user_id, state, client_id, encrypt(verifier), " ".join(requested),
    )
    from urllib.parse import urlencode
    url = metadata["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK_URL,
        "scope": " ".join(requested), "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": MCP_URL,
    })
    return session_token, url


async def complete_authorization(db, state: str, code: str) -> dict[str, Any]:
    await ensure_table(db)
    row = await db.fetchrow(
        "SELECT * FROM mcp_playground_sessions WHERE oauth_state=$1 AND revoked_at IS NULL", state,
    )
    if not row or row["created_at"] < datetime.now(timezone.utc) - timedelta(minutes=10):
        raise HTTPException(400, "OAuth state is invalid or expired")
    metadata = await _get_json(f"{OAUTH_ISSUER}/.well-known/oauth-authorization-server")
    token = await _post_form(metadata["token_endpoint"], {
        "grant_type": "authorization_code", "client_id": row["client_id"], "code": code,
        "redirect_uri": CALLBACK_URL, "code_verifier": decrypt(row["code_verifier_encrypted"]),
        "resource": MCP_URL,
    })
    access = token.get("access_token")
    refresh = token.get("refresh_token")
    if not access or not refresh:
        raise HTTPException(502, "OAuth token exchange returned an incomplete response")
    try:
        claims = jwt.decode(access, SECRET_KEY, algorithms=[ALGORITHM], audience=MCP_AUDIENCE, issuer=ISSUER)
    except JWTError as exc:
        raise HTTPException(401, "OAuth server returned an invalid access token") from exc
    if str(claims.get("sub")) != str(row["user_id"]):
        raise HTTPException(403, "OAuth user does not match the signed-in DocIntel user")
    expires_in = int(token.get("expires_in") or 900)
    await db.execute(
        """UPDATE mcp_playground_sessions SET access_token_encrypted=$2,
           refresh_token_encrypted=$3, scopes=$4, expires_at=$5, connected_at=NOW(), updated_at=NOW()
           WHERE session_hash=$1""",
        row["session_hash"], encrypt(access), encrypt(refresh), token.get("scope") or row["scopes"],
        datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )
    return {"connected": True, "scopes": (token.get("scope") or row["scopes"]).split()}


async def session_for_request(db, request: Request, user_id: str, require_connected: bool = True):
    await ensure_table(db)
    raw = request.cookies.get(COOKIE_NAME, "")
    if not raw:
        if require_connected:
            raise HTTPException(401, "MCP Playground is not connected")
        return None
    row = await db.fetchrow(
        "SELECT * FROM mcp_playground_sessions WHERE session_hash=$1 AND user_id=$2 AND revoked_at IS NULL",
        _hash(raw), user_id,
    )
    if not row or (require_connected and not row["access_token_encrypted"]):
        if require_connected:
            raise HTTPException(401, "MCP Playground OAuth session is unavailable")
        return None
    return row


async def access_token(db, row) -> tuple[str, Any]:
    if row["expires_at"] and row["expires_at"] > datetime.now(timezone.utc) + timedelta(seconds=45):
        return decrypt(row["access_token_encrypted"]), row
    metadata = await _get_json(f"{OAUTH_ISSUER}/.well-known/oauth-authorization-server")
    token = await _post_form(metadata["token_endpoint"], {
        "grant_type": "refresh_token", "client_id": row["client_id"],
        "refresh_token": decrypt(row["refresh_token_encrypted"]), "resource": MCP_URL,
    })
    if not token.get("access_token") or not token.get("refresh_token"):
        raise HTTPException(401, "MCP OAuth session could not be refreshed")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in") or 900))
    await db.execute(
        """UPDATE mcp_playground_sessions SET access_token_encrypted=$2,
           refresh_token_encrypted=$3, scopes=$4, expires_at=$5, updated_at=NOW()
           WHERE session_hash=$1""",
        row["session_hash"], encrypt(token["access_token"]), encrypt(token["refresh_token"]),
        token.get("scope") or row["scopes"], expires_at,
    )
    fresh = await db.fetchrow("SELECT * FROM mcp_playground_sessions WHERE session_hash=$1", row["session_hash"])
    return token["access_token"], fresh


async def revoke_session(db, row) -> None:
    if row and row["refresh_token_encrypted"]:
        try:
            metadata = await _get_json(f"{OAUTH_ISSUER}/.well-known/oauth-authorization-server")
            await _post_form(metadata["revocation_endpoint"], {
                "token": decrypt(row["refresh_token_encrypted"]), "client_id": row["client_id"],
            })
        except Exception:
            pass
    if row:
        await db.execute("UPDATE mcp_playground_sessions SET revoked_at=NOW(), updated_at=NOW() WHERE session_hash=$1", row["session_hash"])


def encrypt(value: str) -> str:
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_encryption_key()).encrypt(nonce, value.encode(), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt(value: str) -> str:
    raw = base64.urlsafe_b64decode(value.encode())
    return AESGCM(_encryption_key()).decrypt(raw[:12], raw[12:], None).decode()


def _encryption_key() -> bytes:
    material = os.getenv("MCP_PLAYGROUND_ENCRYPTION_KEY") or SECRET_KEY
    return hashlib.sha256(("mcp-playground:" + material).encode()).digest()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _get_json(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _post_json(url: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def _post_form(url: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.post(url, data=payload)
        if response.is_error:
            raise HTTPException(502, f"OAuth request failed: {response.text[:300]}")
        return response.json()
