from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP

from .config import Settings
from .errors import DocIntelMcpError
from .runtime import api_client


def register_resources(mcp: FastMCP, settings: Settings) -> None:
    @mcp.resource("docintel://workspaces/{workspace_id}/documents")
    async def workspace_documents(workspace_id: str, ctx: Context) -> str:
        """Accessible documents in a DocIntel workspace."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                result = await client.list_documents(workspace_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

    @mcp.resource("docintel://documents/{document_id}")
    async def document(document_id: str, ctx: Context) -> str:
        """Metadata for an accessible DocIntel document."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                result = await client.get_document(document_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

    @mcp.resource("docintel://sessions/{session_id}")
    async def session(session_id: str, ctx: Context) -> str:
        """A DocIntel chat session owned by the authenticated user."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                result = await client.get_session(session_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

