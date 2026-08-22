from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP

from .config import Settings
from .errors import DocIntelMcpError
from .runtime import api_client
from .verticals import WORKFLOW_CATALOG, vertical_name


def register_resources(mcp: FastMCP, settings: Settings) -> None:
    @mcp.resource("docintel://workflows/catalog")
    async def workflow_catalog(ctx: Context) -> str:
        """Supported vertical workflows and their human-review and packet capabilities."""
        try:
            async with api_client(ctx, settings, "workflows:read"):
                pass
            return json.dumps(WORKFLOW_CATALOG, ensure_ascii=True)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

    @mcp.resource("docintel://workflows/{vertical}/runs/{run_id}")
    async def workflow_run(vertical: str, run_id: str, ctx: Context) -> str:
        """Current structured state for an accessible vertical workflow run."""
        try:
            normalized = vertical_name(vertical)
            async with api_client(ctx, settings, "workflows:read") as client:
                result = await client.get_vertical_run(normalized, run_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

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

    @mcp.resource("docintel://documents/{document_id}/chunks")
    async def document_chunks(document_id: str, ctx: Context) -> str:
        """Chunk manifest for an accessible DocIntel document."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                result = await client.get_document_chunks(document_id)
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

    @mcp.resource("docintel://videos/{document_id}")
    async def video(document_id: str, ctx: Context) -> str:
        """Processing status and metadata for an accessible video."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                result = await client.get_video_status(document_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

    @mcp.resource("docintel://videos/{document_id}/timeline")
    async def video_timeline(document_id: str, ctx: Context) -> str:
        """Timestamped segments and sampled frames for an accessible video."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                result = await client.get_video_timeline(document_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

    @mcp.resource("docintel://videos/{document_id}/transcript")
    async def video_transcript(document_id: str, ctx: Context) -> str:
        """Timestamped transcript entries for an accessible video."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                timeline = await client.get_video_timeline(document_id)
            entries = [
                {
                    "segment_index": segment.get("segment_index"),
                    "start_seconds": segment.get("start_seconds"),
                    "end_seconds": segment.get("end_seconds"),
                    "transcript": segment.get("transcript") or "",
                }
                for segment in timeline.get("segments", []) if segment.get("transcript")
            ]
            return json.dumps({"document_id": document_id, "entries": entries}, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())

    @mcp.resource("docintel://videos/{document_id}/frames")
    async def video_frames(document_id: str, ctx: Context) -> str:
        """Sampled frame metadata for an accessible video."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                timeline = await client.get_video_timeline(document_id)
            return json.dumps({"document_id": document_id, "frames": timeline.get("frames", [])}, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict())
