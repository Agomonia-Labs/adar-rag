from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .config import Settings
from .errors import DocIntelMcpError
from .runtime import api_client
from .schemas import DocumentList, GroundedAnswer, SessionResult, WorkspaceList
from .verticals import WORKFLOW_CATALOG, vertical_name, workflow_definition


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_vertical_workflows(ctx: Context) -> dict:
        """Discover supported DocIntel vertical workflows, required inputs, review gates, and packet types."""
        try:
            async with api_client(ctx, settings, "workflows:read"):
                pass
            return {"count": len(WORKFLOW_CATALOG), "workflows": WORKFLOW_CATALOG}
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def start_vertical_workflow(
        ctx: Context,
        workflow: str,
        document_ids: list[str],
        workspace_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> dict:
        """Start a supported vertical workflow against accessible, processed documents."""
        try:
            workflow_definition(workflow)
            async with api_client(ctx, settings, "workflows:write") as client:
                return await client.start_vertical_workflow(workflow, document_ids, workspace_id, inputs or {})
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_vertical_run(ctx: Context, vertical: str, run_id: str) -> dict:
        """Return current status, outputs, review packet, approval state, and errors for a vertical run."""
        try:
            normalized = vertical_name(vertical)
            async with api_client(ctx, settings, "workflows:read") as client:
                return await client.get_vertical_run(normalized, run_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def list_vertical_runs(
        ctx: Context,
        vertical: str,
        workspace_id: str | None = None,
        status: str = "all",
        limit: int = 25,
    ) -> dict:
        """List accessible finance/tax or talent workflow runs."""
        try:
            normalized = vertical_name(vertical)
            async with api_client(ctx, settings, "workflows:read") as client:
                return await client.list_vertical_runs(normalized, workspace_id, status, max(1, min(limit, 100)))
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def save_vertical_review(
        ctx: Context,
        vertical: str,
        run_id: str,
        packet: dict[str, Any],
        notes: str = "",
        persona: str | None = None,
    ) -> dict:
        """Save a human-reviewed healthcare or talent packet without approving it."""
        try:
            normalized = vertical_name(vertical)
            async with api_client(ctx, settings, "reviews:write") as client:
                return await client.save_vertical_review(normalized, run_id, packet, notes, persona)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def approve_vertical_run(
        ctx: Context,
        vertical: str,
        run_id: str,
        confirm: bool,
        packet: dict[str, Any] | None = None,
        notes: str = "",
        persona: str | None = None,
    ) -> dict:
        """Apply the explicit human approval gate to a reviewed vertical packet. Requires confirm=true."""
        if not confirm:
            return {"ok": False, "error": {"code": "confirmation_required", "message": "Set confirm=true to approve this packet"}}
        try:
            normalized = vertical_name(vertical)
            async with api_client(ctx, settings, "reviews:approve") as client:
                return await client.approve_vertical_run(normalized, run_id, packet, notes, persona)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def generate_vertical_packet(
        ctx: Context,
        vertical: str,
        run_id: str,
        packet_type: str,
        packet: dict[str, Any] | None = None,
    ) -> dict:
        """Generate or ingest an approved/review-ready PDF packet and return its document metadata or signed URL."""
        try:
            normalized = vertical_name(vertical)
            async with api_client(ctx, settings, "packets:write") as client:
                return await client.generate_vertical_packet(normalized, run_id, packet_type, packet)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def list_workspaces(ctx: Context) -> dict:
        """List DocIntel workspaces accessible to the authenticated user."""
        try:
            async with api_client(ctx, settings, "workspaces:read") as client:
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
    async def create_document_upload(
        ctx: Context,
        filename: str,
        content_type: str,
        file_size: int,
        workspace_id: str | None = None,
        redact_pii: bool = False,
    ) -> dict:
        """Create a signed PUT URL for direct document upload. Call complete_document_upload after the PUT succeeds."""
        try:
            async with api_client(ctx, settings, "documents:write") as client:
                return await client.create_upload_session(filename, content_type, file_size, workspace_id, redact_pii)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def complete_document_upload(
        ctx: Context,
        doc_id: str,
        filename: str,
        content_type: str,
        file_size: int,
        gcs_source_path: str,
        workspace_id: str | None = None,
        redact_pii: bool = False,
    ) -> dict:
        """Verify a completed direct upload and start DocIntel chunking."""
        try:
            payload = {
                "doc_id": doc_id, "filename": filename, "content_type": content_type,
                "file_size": file_size, "gcs_source_path": gcs_source_path,
                "workspace_id": workspace_id, "redact_pii": redact_pii,
            }
            async with api_client(ctx, settings, "documents:write") as client:
                return await client.complete_upload(payload)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_ingestion_status(ctx: Context, document_id: str) -> dict:
        """Return current chunking or embedding status, counts, progress metadata, and any error."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                document = await client.get_document(document_id)
            return {
                "document_id": document.get("id"),
                "status": document.get("status"),
                "chunk_count": document.get("chunk_count") or 0,
                "error_message": document.get("error_message"),
                "updated_at": document.get("updated_at"),
                "progress": (document.get("doc_metadata") or {}).get("video_progress"),
            }
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_document_chunks(ctx: Context, document_id: str) -> dict:
        """Return the chunk manifest for an accessible chunked or embedded document."""
        try:
            async with api_client(ctx, settings, "documents:read") as client:
                return await client.get_document_chunks(document_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def embed_document(ctx: Context, document_id: str) -> dict:
        """Start embedding a document that has completed chunking."""
        try:
            async with api_client(ctx, settings, "documents:write") as client:
                return await client.trigger_embedding(document_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def delete_document(ctx: Context, document_id: str, confirm: bool = False) -> dict:
        """Permanently delete a document and its stored source, chunks, and vectors. Requires confirm=true."""
        if not confirm:
            return {"ok": False, "error": {"code": "confirmation_required", "message": "Set confirm=true to permanently delete this document"}}
        try:
            async with api_client(ctx, settings, "documents:write") as client:
                return await client.delete_document(document_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def create_video_upload(
        ctx: Context,
        filename: str,
        content_type: str,
        file_size: int,
        workspace_id: str | None = None,
    ) -> dict:
        """Create a signed PUT URL for direct video upload."""
        try:
            async with api_client(ctx, settings, "video:process") as client:
                return await client.create_video_upload_session(filename, content_type, file_size, workspace_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def complete_video_upload(
        ctx: Context,
        doc_id: str,
        filename: str,
        content_type: str,
        file_size: int,
        gcs_source_path: str,
        workspace_id: str | None = None,
        process_after_upload: bool = False,
        rights_confirmed: bool = False,
        transcript_language: str = "auto",
        max_frames: int = 12,
        segment_seconds: int = 60,
        embed_after_processing: bool = True,
    ) -> dict:
        """Verify a direct video upload and optionally start processing."""
        if process_after_upload and not rights_confirmed:
            return {"ok": False, "error": {"code": "rights_confirmation_required", "message": "Confirm that you have rights to process this video"}}
        try:
            payload = {
                "doc_id": doc_id, "filename": filename, "content_type": content_type,
                "file_size": file_size, "gcs_source_path": gcs_source_path,
                "workspace_id": workspace_id, "process_after_upload": process_after_upload,
                "rights_confirmed": rights_confirmed, "transcript_language": transcript_language,
                "max_frames": max_frames, "segment_seconds": segment_seconds,
                "embed_after_processing": embed_after_processing,
            }
            async with api_client(ctx, settings, "video:process") as client:
                return await client.complete_video_upload(payload)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def list_videos(ctx: Context, workspace_id: str | None = None) -> dict:
        """List accessible video documents and their processing progress."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                videos = await client.list_videos(workspace_id)
            return {"workspace_id": workspace_id, "count": len(videos), "videos": videos}
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def process_video(
        ctx: Context,
        document_id: str,
        rights_confirmed: bool,
        transcript_language: str = "auto",
        max_frames: int = 12,
        segment_seconds: int = 60,
        embed_after_processing: bool = True,
    ) -> dict:
        """Start transcript, frame, timeline, and embedding processing for an uploaded video."""
        if not rights_confirmed:
            return {"ok": False, "error": {"code": "rights_confirmation_required", "message": "Confirm that you have rights to process this video"}}
        try:
            payload = {
                "rights_confirmed": True, "source_type": "upload",
                "transcript_language": transcript_language, "max_frames": max_frames,
                "segment_seconds": segment_seconds, "embed_after_processing": embed_after_processing,
            }
            async with api_client(ctx, settings, "video:process") as client:
                return await client.process_video(document_id, payload)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_video_status(ctx: Context, document_id: str) -> dict:
        """Return video processing stage, percentage, timestamps, metadata, and errors."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                return await client.get_video_status(document_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_video_timeline(ctx: Context, document_id: str) -> dict:
        """Return timestamped video segments and sampled frames."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                return await client.get_video_timeline(document_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_video_transcript(ctx: Context, document_id: str) -> dict:
        """Return timestamped transcript entries from the processed video timeline."""
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
                for segment in timeline.get("segments", [])
                if segment.get("transcript")
            ]
            return {"document_id": document_id, "count": len(entries), "entries": entries}
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_video_frames(ctx: Context, document_id: str) -> dict:
        """Return sampled frame metadata, timestamps, captions, and OCR text."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                timeline = await client.get_video_timeline(document_id)
            frames = timeline.get("frames", [])
            return {"document_id": document_id, "count": len(frames), "frames": frames}
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_video_frame_url(ctx: Context, document_id: str, frame_index: int) -> dict:
        """Create a short-lived view URL for one sampled video frame."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                return await client.get_video_frame_url(document_id, frame_index)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def search_video(ctx: Context, document_id: str, question: str, limit: int = 8) -> dict:
        """Ask a timestamp-grounded question against an embedded video."""
        try:
            async with api_client(ctx, settings, "video:read") as client:
                return await client.ask_video(document_id, question, limit)
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
    async def summarize_document(
        ctx: Context,
        document_id: str,
        summary_type: str = "executive",
        custom_prompt: str = "",
        chunk_indices: list[int] | None = None,
        redact_pii: bool = False,
    ) -> dict:
        """Generate an executive, detailed, bullets, sections, or custom summary for one document."""
        allowed = {"executive", "detailed", "bullets", "sections", "custom"}
        if summary_type not in allowed:
            return {"ok": False, "error": {"code": "invalid_summary_type", "message": f"summary_type must be one of {sorted(allowed)}"}}
        try:
            async with api_client(ctx, settings, "knowledge:generate") as client:
                return await client.summarize_document(
                    document_id, summary_type, custom_prompt, chunk_indices or [], redact_pii,
                )
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def summarize_documents(
        ctx: Context,
        document_ids: list[str],
        summary_type: str = "executive",
        custom_prompt: str = "",
        redact_pii: bool = False,
    ) -> dict:
        """Generate a combined summary across multiple accessible documents."""
        allowed = {"executive", "detailed", "bullets", "sections", "custom"}
        if summary_type not in allowed:
            return {"ok": False, "error": {"code": "invalid_summary_type", "message": f"summary_type must be one of {sorted(allowed)}"}}
        try:
            async with api_client(ctx, settings, "knowledge:generate") as client:
                return await client.summarize_documents(document_ids, summary_type, custom_prompt, redact_pii)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def compare_documents(
        ctx: Context,
        document_id_1: str,
        document_id_2: str,
        redact_pii: bool = False,
    ) -> dict:
        """Compare two accessible documents and return similarity, unique points, and section-level differences."""
        if document_id_1 == document_id_2:
            return {"ok": False, "error": {"code": "invalid_request", "message": "Select two different documents"}}
        try:
            async with api_client(ctx, settings, "knowledge:generate") as client:
                return await client.compare_documents(document_id_1, document_id_2, redact_pii)
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
    async def list_chat_sessions(ctx: Context, workspace_id: str | None = None) -> dict:
        """List persistent chat sessions in personal scope or one workspace."""
        try:
            async with api_client(ctx, settings, "sessions:write") as client:
                sessions = await client.list_sessions(workspace_id)
            return {"workspace_id": workspace_id, "count": len(sessions), "sessions": sessions}
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_chat_session(ctx: Context, session_id: str) -> dict:
        """Return one persistent chat session with its saved conversation history."""
        try:
            async with api_client(ctx, settings, "sessions:write") as client:
                return await client.get_session(session_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def update_chat_session(
        ctx: Context,
        session_id: str,
        title: str | None = None,
        document_ids: list[str] | None = None,
    ) -> dict:
        """Rename a chat session or change its selected documents."""
        if title is None and document_ids is None:
            return {"ok": False, "error": {"code": "invalid_request", "message": "Provide title or document_ids"}}
        try:
            payload: dict[str, Any] = {}
            if title is not None:
                payload["title"] = title
            if document_ids is not None:
                payload["document_ids"] = document_ids
            async with api_client(ctx, settings, "sessions:write") as client:
                await client.update_session(session_id, payload)
                return await client.get_session(session_id)
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def delete_chat_session(ctx: Context, session_id: str, confirm: bool = False) -> dict:
        """Permanently delete a persistent chat session. Requires confirm=true."""
        if not confirm:
            return {"ok": False, "error": {"code": "confirmation_required", "message": "Set confirm=true to delete this session"}}
        try:
            async with api_client(ctx, settings, "sessions:write") as client:
                return await client.delete_session(session_id)
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
