from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .config import Settings
from .errors import DocIntelMcpError
from .runtime import api_client
from .schemas import DocumentList, GroundedAnswer, SessionResult, WorkspaceList


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_workspaces(ctx: Context) -> dict:
        """List DocIntel workspaces accessible to the authenticated user."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                workspaces = await client.list_workspaces()
            return WorkspaceList(count=len(workspaces), workspaces=workspaces).model_dump()
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def list_documents(ctx: Context, workspace_id: str | None = None) -> dict:
        """List accessible DocIntel documents, optionally scoped to one workspace."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                documents = await client.list_documents(workspace_id)
            return DocumentList(workspace_id=workspace_id, count=len(documents), documents=documents).model_dump()
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_document(ctx: Context, document_id: str) -> dict:
        """Return metadata for an accessible DocIntel document."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                return await client.get_document(document_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def search_knowledgebase(
        ctx: Context,
        question: str,
        document_ids: list[str],
        workspace_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        redact_pii: bool = False,
    ) -> dict:
        """Ask a grounded question using DocIntel hybrid retrieval, re-ranking, and citations."""
        try:
            async with api_client(ctx, settings, "knowledge:query") as client:
                result = await client.ask(question, document_ids, workspace_id, history, redact_pii)
            return GroundedAnswer(**result).model_dump()
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def create_chat_session(
        ctx: Context,
        document_ids: list[str],
        workspace_id: str | None = None,
        title: str = "New Chat",
    ) -> dict:
        """Create a persistent DocIntel chat session for selected documents."""
        try:
            async with api_client(ctx, settings, "sessions:write") as client:
                result = await client.create_session(title, document_ids, workspace_id)
            return SessionResult(**result).model_dump()
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def ask(
        ctx: Context,
        question: str,
        document_ids: list[str],
        workspace_id: str | None = None,
        session_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        redact_pii: bool = False,
    ) -> dict:
        """Ask DocIntel a grounded question; optionally use history from an existing session."""
        try:
            async with api_client(ctx, settings, "knowledge:query") as client:
                effective_history = history or []
                if session_id and not history:
                    session = await client.get_session(session_id)
                    effective_history = session.get("messages") or []
                result = await client.ask(question, document_ids, workspace_id, effective_history, redact_pii)
                if session_id:
                    updated_messages = [
                        *effective_history,
                        {"role": "user", "content": question},
                        {
                            "role": "assistant",
                            "content": result["answer"],
                            "sources": result.get("sources") or [],
                        },
                    ]
                    await client.save_session_messages(session_id, updated_messages)
            return GroundedAnswer(**result).model_dump()
        except DocIntelMcpError as exc:
            return exc.as_dict()
