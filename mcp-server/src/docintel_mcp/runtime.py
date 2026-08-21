from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.server.auth.middleware.auth_context import get_access_token

from .auth import require_capability
from .client import DocIntelApiClient
from .config import Settings


def request_trace_id(ctx: Context) -> str:
    # Stable MCP SDK releases do not expose transport headers on FastMCP Context.
    # The MCP request ID still gives every upstream call a safe correlation ID.
    return str(ctx.request_id)


def api_client(ctx: Context, settings: Settings, capability: str) -> DocIntelApiClient:
    access_token = get_access_token()
    if access_token is None:
        from .errors import DocIntelMcpError

        raise DocIntelMcpError("unauthorized", "A verified DocIntel bearer token is required", status_code=401)
    require_capability(settings.enabled_capabilities, capability, set(access_token.scopes))
    return DocIntelApiClient(
        settings.api_base_url,
        access_token.token,
        trace_id=request_trace_id(ctx),
        timeout_seconds=settings.timeout_seconds,
    )
