from __future__ import annotations

import html
import json
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.mcp_playground.command_parser import apply_pipelines, parse_command
from services.mcp_playground.command_policy import validate_request
from services.mcp_playground.example_catalog import example_catalog
from services.mcp_playground.mcp_gateway import execute_mcp
from services.mcp_playground.oauth_session import (
    COOKIE_NAME, access_token, complete_authorization, create_authorization,
    revoke_session, session_for_request,
)
from services.mcp_playground.schemas import ExecuteRequest, OAuthStartRequest


router = APIRouter()


@router.post("/oauth/start")
async def oauth_start(body: OAuthStartRequest, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    session_token, authorization_url = await create_authorization(db, str(current_user["id"]), body.scopes)
    response = JSONResponse({"authorization_url": authorization_url})
    response.set_cookie(
        COOKIE_NAME, session_token, httponly=True, secure=request.url.scheme == "https",
        samesite="lax", max_age=3600, path="/api/mcp-playground",
    )
    return response


@router.get("/oauth/callback")
async def oauth_callback(state: str = "", code: str = "", error: str = "", db=Depends(get_db)):
    if error:
        message = f"OAuth authorization failed: {error}"
        success = False
    else:
        try:
            await complete_authorization(db, state, code)
            message = "ADAR DocIntel MCP authorization completed."
            success = True
        except HTTPException as exc:
            message = str(exc.detail)
            success = False
    payload = json.dumps({"type": "docintel-mcp-oauth", "success": success, "message": message})
    frontend_origin = urlsplit(os.getenv("APP_URL", "http://localhost:5173")).geturl().rstrip("/")
    target_origin = json.dumps(frontend_origin)
    return HTMLResponse(f"""<!doctype html><html><body style="font-family:system-ui;background:#0f1f0f;color:#e5e7eb;padding:32px">
      <h2>{html.escape(message)}</h2><p>You may close this window.</p>
      <script>if(window.opener)window.opener.postMessage({payload},{target_origin});setTimeout(()=>window.close(),800);</script>
    </body></html>""", status_code=200 if success else 400)


@router.get("/status")
async def status(request: Request, current_user: CurrentUser, db=Depends(get_db)):
    row = await session_for_request(db, request, str(current_user["id"]), require_connected=False)
    connected = bool(row and row["access_token_encrypted"] and not row["revoked_at"])
    return {
        "connected": connected,
        "scopes": row["scopes"].split() if connected else [],
        "expires_at": row["expires_at"].isoformat() if connected and row["expires_at"] else None,
    }


@router.post("/disconnect")
async def disconnect(request: Request, current_user: CurrentUser, db=Depends(get_db)):
    row = await session_for_request(db, request, str(current_user["id"]), require_connected=False)
    await revoke_session(db, row)
    response = JSONResponse({"connected": False})
    response.delete_cookie(COOKIE_NAME, path="/api/mcp-playground")
    return response


@router.post("/execute")
async def execute(body: ExecuteRequest, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    parsed = parse_command(body.command)
    if parsed.local_command:
        return {"local_command": parsed.local_command, "result": _local_result(parsed.local_command)}
    validate_request(parsed.request or {}, body.confirm)
    row = await session_for_request(db, request, str(current_user["id"]))
    token, _ = await access_token(db, row)
    raw, duration_ms = await execute_mcp(token, parsed.request or {})
    return {
        "result": apply_pipelines(raw, parsed.pipelines),
        "raw": raw,
        "duration_ms": duration_ms,
        "request_id": (parsed.request or {}).get("id"),
    }


@router.get("/examples")
async def examples(current_user: CurrentUser):
    return {"examples": _examples()}


def _local_result(command: str):
    if command == "help":
        return {"commands": ["mcp_request '<json>'", "mcp_tool <name> '<json>'", "| tool_data", "| jq '.path'", "examples", "history", "clear"]}
    if command == "examples":
        return _examples()
    return {"action": command}


def _examples() -> list[dict]:
    return example_catalog()
