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

from auth.router import _ensure_mfa_table, _mfa_enabled, _send_mfa_challenge, _token_hash
from auth.service import ALGORITHM, SECRET_KEY, create_access_token, decode_token, verify_password
from database.connection import get_db
from services.limiter import ip_10_per_min


router = APIRouter()
oauth_router = router

ISSUER = os.getenv("OAUTH_ISSUER_URL", "http://localhost:8000").rstrip("/")
MCP_RESOURCE = os.getenv("OAUTH_MCP_RESOURCE", "http://localhost:8081/mcp").rstrip("/")
MCP_AUDIENCE = MCP_RESOURCE
MCP_INTROSPECTION_SECRET = os.getenv("MCP_INTROSPECTION_SECRET", "")
ACCESS_MINUTES = int(os.getenv("OAUTH_ACCESS_TOKEN_MINUTES", "15"))
REFRESH_DAYS = int(os.getenv("OAUTH_REFRESH_TOKEN_DAYS", "30"))
ALLOWED_SCOPES = {
    "workspaces:read",
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
}


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
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id TEXT PRIMARY KEY,
            client_name TEXT NOT NULL,
            redirect_uris JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        );
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
        """
    )


def _metadata() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "revocation_endpoint": f"{ISSUER}/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted(ALLOWED_SCOPES),
        "resource_parameter_supported": True,
    }


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata():
    return _metadata()


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
    if values["resource"].rstrip("/") != MCP_RESOURCE:
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
    code = secrets.token_urlsafe(40)
    await db.execute(
        """INSERT INTO oauth_authorization_codes
           (code_hash,client_id,user_id,redirect_uri,scope,resource,code_challenge,expires_at)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
        _hash(code), values["client_id"], user_id, values["redirect_uri"], values["scope"],
        MCP_RESOURCE, values["code_challenge"], datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    query = {"code": code}
    if values["state"]:
        query["state"] = values["state"]
    separator = "&" if "?" in values["redirect_uri"] else "?"
    return RedirectResponse(values["redirect_uri"] + separator + urlencode(query), status_code=303)


def _access_token(user_id: str, client_id: str, scope: str, resource: str) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ACCESS_MINUTES)
    token = jwt.encode({
        "iss": ISSUER, "sub": user_id, "aud": resource, "client_id": client_id,
        "scope": scope, "iat": now, "exp": expires, "jti": secrets.token_urlsafe(16),
    }, SECRET_KEY, algorithm=ALGORITHM)
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
    body = await request.json()
    try:
        claims = jwt.decode(str(body.get("token", "")), SECRET_KEY, algorithms=[ALGORITHM], audience=MCP_AUDIENCE, issuer=ISSUER)
    except JWTError:
        return {"active": False}
    scopes = set(str(claims.get("scope", "")).split())
    if not scopes <= ALLOWED_SCOPES:
        return {"active": False}
    user = await db.fetchrow("SELECT id,email,role FROM users WHERE id=$1::uuid", claims["sub"])
    client = await db.fetchrow("SELECT client_id FROM oauth_clients WHERE client_id=$1 AND revoked_at IS NULL", claims.get("client_id"))
    if not user or not client:
        return {"active": False}
    return {
        "active": True,
        "sub": str(user["id"]),
        "client_id": claims["client_id"],
        "scope": claims["scope"],
        "exp": claims["exp"],
        "email": user["email"],
        "role": user["role"],
        "backend_token": create_access_token(str(user["id"]), user["email"]),
    }
