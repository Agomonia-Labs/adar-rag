from __future__ import annotations

from contextlib import asynccontextmanager
import time

import httpx

from mcp.server.fastmcp import Context
from mcp.server.auth.middleware.auth_context import get_access_token

from .auth import require_capability
from .client import DocIntelApiClient
from .config import Settings
from .telemetry import current_trace_id, traced_span


def request_trace_id(ctx: Context) -> str:
    # Stable MCP SDK releases do not expose transport headers on FastMCP Context.
    # The MCP request ID still gives every upstream call a safe correlation ID.
    return str(ctx.request_id)


@asynccontextmanager
async def api_client(ctx: Context, settings: Settings, capability: str):
    with traced_span("mcp.tool.execute", attributes={
        "mcp.request.id": request_trace_id(ctx),
        "mcp.required_scope": capability,
    }):
        access_token = get_access_token()
        if access_token is None:
            from .errors import DocIntelMcpError

            raise DocIntelMcpError("unauthorized", "A verified DocIntel bearer token is required", status_code=401)
        require_capability(settings.enabled_capabilities, capability, set(access_token.scopes))
        claims = dict(access_token.claims or {})
        token_kind = str(claims.get("token_kind") or "user")
        organization_id = claims.get("organization_id")
        workspace_ids = frozenset(str(item) for item in claims.get("workspace_ids") or [])
        trace_id = current_trace_id() or request_trace_id(ctx)
        started = time.monotonic()
        usage = None
        try:
            async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=15) as governance:
                response = await governance.post(
                    "/internal/usage/reserve",
                    headers={"X-MCP-Introspection-Secret": settings.introspection_secret},
                    json={
                        "user_id": access_token.subject, "client_id": access_token.client_id,
                        "organization_id": organization_id, "principal_type": token_kind,
                        "scope": capability, "operation": f"mcp.{capability}",
                        "request_id": request_trace_id(ctx), "trace_id": trace_id,
                    },
                )
                if response.status_code == 429:
                    from .errors import DocIntelMcpError
                    raise DocIntelMcpError("quota_exceeded", "Application quota exceeded", status_code=429)
                response.raise_for_status()
                usage = response.json()
        except httpx.HTTPError:
            # Preserve MCP availability during a rolling backend deployment. A
            # real quota denial above is never swallowed.
            usage = None
        with traced_span("mcp.identity.authorized", attributes={
            "mcp.auth.token_kind": token_kind,
            "mcp.auth.client_id": access_token.client_id,
            "mcp.auth.organization_id": str(organization_id or ""),
            "mcp.auth.workspace_grant_count": len(workspace_ids),
        }):
            status_code = 200
            try:
                async with DocIntelApiClient(
                    settings.api_base_url,
                    access_token.token,
                    trace_id=trace_id,
                    timeout_seconds=settings.timeout_seconds,
                    service_client_id=access_token.client_id if token_kind == "service" else None,
                    organization_id=str(organization_id) if organization_id else None,
                    allowed_workspace_ids=workspace_ids,
                ) as client:
                    yield client
            except Exception:
                status_code = 500
                raise
            finally:
                try:
                    if usage:
                        async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=15) as governance:
                            await governance.post(
                                "/internal/usage/reconcile",
                                headers={"X-MCP-Introspection-Secret": settings.introspection_secret},
                                json={"context": usage["context"], "reservation_ids": usage.get("reservation_ids", []),
                                      "units_used": 1, "status_code": status_code,
                                      "latency_ms": round((time.monotonic() - started) * 1000),
                                      "metadata": {"transport": "mcp"}},
                            )
                except httpx.HTTPError:
                    pass
