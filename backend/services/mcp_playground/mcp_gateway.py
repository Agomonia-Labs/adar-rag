from __future__ import annotations

import os
from time import perf_counter

import httpx
from fastapi import HTTPException


MCP_URL = os.getenv("DOCINTEL_MCP_URL", "https://mcp.docintel.adar.agomoniai.com/mcp")
MAX_RESPONSE_BYTES = int(os.getenv("MCP_PLAYGROUND_MAX_RESPONSE_BYTES", "2097152"))


async def execute_mcp(access_token: str, payload: dict) -> tuple[dict, int]:
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            response = await client.post(
                MCP_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, "The MCP server is unavailable") from exc
    elapsed = int((perf_counter() - started) * 1000)
    if response.is_error:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise HTTPException(response.status_code, detail)
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise HTTPException(502, "The MCP response is too large for the browser playground")
    try:
        return response.json(), elapsed
    except ValueError as exc:
        raise HTTPException(502, "The MCP server returned an unsupported response") from exc
