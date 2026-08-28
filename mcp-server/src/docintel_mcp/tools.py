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
    async def create_batch_upload(ctx: Context, files: list[dict[str, Any]], workspace_id: str | None = None, redact_pii: bool = False, idempotency_key: str | None = None) -> dict:
        """Create one durable batch job and signed upload URL for every document manifest."""
        try:
            async with api_client(ctx, settings, "batches:write") as client:
                return await client.create_batch_upload({"files": files, "workspace_id": workspace_id, "redact_pii": redact_pii, "idempotency_key": idempotency_key})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def complete_batch_upload(ctx: Context, batch_job_id: str, document_ids: list[str] | None = None, concurrency: int = 3) -> dict:
        """Verify completed signed uploads and start bounded parallel chunking."""
        try:
            async with api_client(ctx, settings, "batches:write") as client:
                return await client.complete_batch_upload(batch_job_id, document_ids or [], concurrency)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def start_batch_embedding(ctx: Context, document_ids: list[str], workspace_id: str | None = None, concurrency: int = 3, force: bool = False, idempotency_key: str | None = None) -> dict:
        """Start resumable bulk embedding for accessible chunked documents."""
        try:
            async with api_client(ctx, settings, "batches:write") as client:
                return await client.start_document_batch("embedding", {"document_ids": document_ids, "workspace_id": workspace_id, "concurrency": concurrency, "force": force, "idempotency_key": idempotency_key})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def start_batch_classification(ctx: Context, document_ids: list[str], workspace_id: str | None = None, concurrency: int = 3, force: bool = False, idempotency_key: str | None = None) -> dict:
        """Classify accessible documents with item-level outcomes and retries."""
        try:
            async with api_client(ctx, settings, "batches:write") as client:
                return await client.start_document_batch("classification", {"document_ids": document_ids, "workspace_id": workspace_id, "concurrency": concurrency, "force": force, "idempotency_key": idempotency_key})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def start_workspace_summary(ctx: Context, workspace_id: str, document_ids: list[str] | None = None, summary_type: str = "executive", custom_prompt: str = "", redact_pii: bool = False, language: str = "en", concurrency: int = 2, idempotency_key: str | None = None) -> dict:
        """Start hierarchical map-reduce summarization across a large workspace."""
        try:
            async with api_client(ctx, settings, "batches:write") as client:
                return await client.start_workspace_summary({"workspace_id": workspace_id, "document_ids": document_ids or [], "summary_type": summary_type, "custom_prompt": custom_prompt, "redact_pii": redact_pii, "language": language, "concurrency": concurrency, "idempotency_key": idempotency_key})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_batch_jobs(ctx: Context, workspace_id: str | None = None, operation: str | None = None, status: str | None = None, limit: int = 25) -> dict:
        """List accessible batch jobs with aggregate progress."""
        try:
            async with api_client(ctx, settings, "batches:read") as client:
                return await client.list_batch_jobs({"workspace_id": workspace_id, "operation": operation, "status": status, "limit": limit})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def get_batch_status(ctx: Context, batch_job_id: str) -> dict:
        """Return job progress, current stage, counters, item attempts, and errors."""
        try:
            async with api_client(ctx, settings, "batches:read") as client: return await client.get_batch_job(batch_job_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def get_batch_results(ctx: Context, batch_job_id: str) -> dict:
        """Return aggregate and item-level outputs for a completed or partial batch."""
        try:
            async with api_client(ctx, settings, "batches:read") as client: return await client.get_batch_results(batch_job_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def retry_batch_failures(ctx: Context, batch_job_id: str) -> dict:
        """Retry only failed items from a durable batch job."""
        try:
            async with api_client(ctx, settings, "batches:write") as client: return await client.retry_batch(batch_job_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def cancel_batch_job(ctx: Context, batch_job_id: str, confirm: bool = False) -> dict:
        """Stop queued batch work while retaining completed results. Requires confirm=true."""
        if not confirm: return {"ok": False, "error": {"code": "confirmation_required", "message": "Set confirm=true to cancel this batch"}}
        try:
            async with api_client(ctx, settings, "batches:write") as client: return await client.cancel_batch(batch_job_id)
        except DocIntelMcpError as exc: return exc.as_dict()

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
    async def search_federated_knowledgebase(ctx: Context, question: str, document_ids: list[str], history: list[dict[str, Any]] | None = None, redact_pii: bool = False) -> dict:
        """Query an explicit authorized document set across multiple workspaces while preserving source citations."""
        try:
            async with api_client(ctx, settings, "knowledge:query") as client:
                result = await client.ask(question, document_ids, None, history, redact_pii)
            result["sources"] = _normalized_sources(result.get("sources"))
            result["federated"] = True
            return result
        except DocIntelMcpError as exc: return exc.as_dict()

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
            result["sources"] = _normalized_sources(result.get("sources"))
            return GroundedAnswer(**result).model_dump()
        except DocIntelMcpError as exc:
            return exc.as_dict()

    @mcp.tool()
    async def get_enterprise_capabilities(ctx: Context) -> dict:
        """Discover versioned enterprise MCP capabilities and workflow contracts."""
        try:
            async with api_client(ctx, settings, "workflows:read") as client: return await client.enterprise_catalog()
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def get_workflow_schema(ctx: Context, workflow: str) -> dict:
        """Return the versioned input, review, and packet contract for one workflow."""
        try:
            async with api_client(ctx, settings, "workflows:read") as client: return await client.workflow_schema(workflow)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def validate_workflow_inputs(ctx: Context, workflow: str, inputs: dict[str, Any]) -> dict:
        """Validate workflow inputs without starting a run."""
        try:
            async with api_client(ctx, settings, "workflows:read") as client: return await client.validate_workflow(workflow, inputs)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_operation_events(ctx: Context, after_sequence: int = 0, resource_type: str | None = None, resource_id: str | None = None, limit: int = 100) -> dict:
        """Read cursor-based job, workflow, review, and packet lifecycle events."""
        try:
            async with api_client(ctx, settings, "events:read") as client: return await client.list_events(after_sequence, resource_type, resource_id, limit)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def create_event_subscription(ctx: Context, event_types: list[str], workspace_id: str | None = None, resource_type: str | None = None, resource_id: str | None = None, webhook_url: str | None = None) -> dict:
        """Create a governed event cursor or HTTPS webhook subscription."""
        try:
            async with api_client(ctx, settings, "events:write") as client:
                return await client.create_subscription({"event_types": event_types, "workspace_id": workspace_id, "resource_type": resource_type, "resource_id": resource_id, "webhook_url": webhook_url})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_event_subscriptions(ctx: Context) -> dict:
        """List event and webhook subscriptions owned by the caller."""
        try:
            async with api_client(ctx, settings, "events:read") as client: return await client.list_subscriptions()
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def delete_event_subscription(ctx: Context, subscription_id: str) -> dict:
        """Delete an event or webhook subscription owned by the caller."""
        try:
            async with api_client(ctx, settings, "events:write") as client:
                return await client.delete_subscription(subscription_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def resume_batch_job(ctx: Context, batch_job_id: str) -> dict:
        """Resume a failed or interrupted batch from failed items only."""
        try:
            async with api_client(ctx, settings, "batches:write") as client: return await client.resume_batch(batch_job_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def create_review_task(ctx: Context, vertical: str, run_id: str, title: str, workspace_id: str | None = None, priority: str = "normal", metadata: dict[str, Any] | None = None) -> dict:
        """Create or retrieve the governed human-review task for a workflow run."""
        try:
            async with api_client(ctx, settings, "reviews:write") as client:
                return await client.create_review_task({"vertical": vertical, "run_id": run_id, "title": title, "workspace_id": workspace_id, "priority": priority, "metadata": metadata or {}})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_review_tasks(ctx: Context, status: str | None = None) -> dict:
        """List pending or completed human-review tasks accessible to the caller."""
        try:
            async with api_client(ctx, settings, "reviews:write") as client: return await client.list_review_tasks(status)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def assign_review_task(ctx: Context, task_id: str) -> dict:
        """Assign an accessible review task to the authenticated reviewer."""
        try:
            async with api_client(ctx, settings, "reviews:write") as client: return await client.assign_review_task(task_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def submit_review_decision(ctx: Context, task_id: str, decision: str, reviewer_notes: str = "") -> dict:
        """Approve, reject, or request changes for a human-review task."""
        try:
            async with api_client(ctx, settings, "reviews:approve") as client: return await client.decide_review_task(task_id, decision, reviewer_notes)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def save_knowledge_artifact(ctx: Context, artifact_type: str, title: str, content: dict[str, Any], workspace_id: str | None = None, source_document_ids: list[str] | None = None, source_trace_id: str | None = None, status: str = "draft") -> dict:
        """Save a summary, comparison, evidence map, report, or packet as reusable knowledge."""
        try:
            async with api_client(ctx, settings, "artifacts:write") as client:
                return await client.create_artifact({"artifact_type": artifact_type, "title": title, "content": content, "workspace_id": workspace_id, "source_document_ids": source_document_ids or [], "source_trace_id": source_trace_id, "status": status})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_knowledge_artifacts(ctx: Context, workspace_id: str | None = None) -> dict:
        """List reusable knowledge artifacts owned by the caller."""
        try:
            async with api_client(ctx, settings, "artifacts:read") as client: return await client.list_artifacts(workspace_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def register_document_version(ctx: Context, document_id: str, previous_document_id: str | None = None, change_summary: str = "", changed_pages: list[int] | None = None) -> dict:
        """Register document lineage and changed pages for incremental processing."""
        try:
            async with api_client(ctx, settings, "versions:write") as client:
                return await client.register_document_version(document_id, {"previous_document_id": previous_document_id, "change_summary": change_summary, "changed_pages": changed_pages or []})
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_document_versions(ctx: Context, document_id: str) -> dict:
        """List accessible versions and change metadata for a document family."""
        try:
            async with api_client(ctx, settings, "versions:read") as client: return await client.list_document_versions(document_id)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def evaluate_trace_quality(ctx: Context, trace_id: str, evaluation_type: str = "groundedness") -> dict:
        """Evaluate an owned execution trace and correlate the quality result with its evidence chain."""
        try:
            async with api_client(ctx, settings, "evaluations:run") as client: return await client.evaluate_trace(trace_id, evaluation_type)
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def list_my_traces(ctx: Context, workspace_id: str | None = None, limit: int = 50) -> dict:
        """List requester-owned execution traces for operational investigation."""
        try:
            async with api_client(ctx, settings, "events:read") as client:
                traces = await client.list_my_traces(max(1, min(limit, 100)), workspace_id)
            return {"count": len(traces), "traces": traces}
        except DocIntelMcpError as exc: return exc.as_dict()

    @mcp.tool()
    async def get_my_trace(ctx: Context, trace_id: str) -> dict:
        """Return the requester-safe flow, timeline, model activity, and evaluations for an owned trace."""
        try:
            async with api_client(ctx, settings, "events:read") as client: return await client.get_my_trace(trace_id)
        except DocIntelMcpError as exc: return exc.as_dict()


def _normalized_sources(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for index, source in enumerate(sources or []):
        result.append({
            "citation_id": source.get("citation_id") or f"citation-{index + 1}",
            "document_id": source.get("document_id") or source.get("doc_id"),
            "document_name": source.get("document_name") or source.get("filename") or source.get("source"),
            "chunk_id": source.get("chunk_id") or source.get("id"),
            "chunk_index": source.get("chunk_index", source.get("index")),
            "page_number": source.get("page_number") or source.get("page"),
            "start_seconds": source.get("start_seconds"), "end_seconds": source.get("end_seconds"),
            "retrieval_score": source.get("retrieval_score", source.get("score")),
            "rerank_score": source.get("rerank_score"), "confidence": source.get("confidence"),
            "source_url": source.get("source_url") or source.get("url"),
            "excerpt": str(source.get("excerpt") or source.get("text") or source.get("content") or "")[:600],
        })
    return result
