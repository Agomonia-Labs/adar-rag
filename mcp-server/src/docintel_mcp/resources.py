from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP

from .config import Settings
from .errors import DocIntelMcpError
from .runtime import api_client
from .verticals import WORKFLOW_CATALOG, vertical_name


def register_resources(mcp: FastMCP, settings: Settings) -> None:
    @mcp.resource("docintel://batches/{batch_job_id}")
    async def batch_job(batch_job_id: str, ctx: Context) -> str:
        """Status and item-level progress for an accessible batch job."""
        try:
            async with api_client(ctx, settings, "batches:read") as client:
                result = await client.get_batch_job(batch_job_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://batches/{batch_job_id}/results")
    async def batch_results(batch_job_id: str, ctx: Context) -> str:
        """Aggregate and item-level results for an accessible batch job."""
        try:
            async with api_client(ctx, settings, "batches:read") as client:
                result = await client.get_batch_results(batch_job_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

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

    @mcp.resource("docintel://conversations/{session_id}")
    async def conversation(session_id: str, ctx: Context) -> str:
        """Conversation turns, consent, review state, processing status, and knowledgebase document linkage."""
        try:
            async with api_client(ctx, settings, "sessions:write") as client:
                result = await client.get_conversation_recording(session_id)
            return json.dumps(result, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict(), ensure_ascii=False)

    @mcp.resource("docintel://conversations/{session_id}/transcript")
    async def conversation_transcript(session_id: str, ctx: Context) -> str:
        """Editable transcript draft before approval, or processed transcript segments after publication."""
        try:
            async with api_client(ctx, settings, "sessions:write") as client:
                result = await client.get_conversation_recording(session_id)
            payload = {
                "session_id": session_id, "review_status": result.get("review_status"),
                "processing_status": result.get("processing_status"), "document_id": result.get("document_id"),
                "editable_transcript": result.get("editable_transcript") or "",
                "segments": result.get("segments") or [],
            }
            return json.dumps(payload, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc:
            return json.dumps(exc.as_dict(), ensure_ascii=False)

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

    @mcp.resource("docintel://enterprise/catalog")
    async def enterprise_catalog(ctx: Context) -> str:
        """Versioned enterprise capabilities, workflow schemas, and governance contracts."""
        try:
            async with api_client(ctx, settings, "workflows:read") as client:
                result = await client.enterprise_catalog()
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://events/{after_sequence}")
    async def operation_events(after_sequence: str, ctx: Context) -> str:
        """Cursor-based operation events after a sequence number."""
        try:
            async with api_client(ctx, settings, "events:read") as client:
                result = await client.list_events(int(after_sequence or 0))
            return json.dumps(result, ensure_ascii=True, default=str)
        except (DocIntelMcpError, ValueError) as exc:
            return json.dumps(exc.as_dict() if isinstance(exc, DocIntelMcpError) else {"error": "after_sequence must be an integer"})

    @mcp.resource("docintel://reviews/queue/{status}")
    async def review_queue(status: str, ctx: Context) -> str:
        """Human-review queue filtered by status or 'all'."""
        try:
            async with api_client(ctx, settings, "reviews:write") as client:
                result = await client.list_review_tasks(None if status == "all" else status)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://artifacts/{workspace_id}")
    async def knowledge_artifacts(workspace_id: str, ctx: Context) -> str:
        """Reusable knowledge artifacts in a workspace or personal scope."""
        try:
            async with api_client(ctx, settings, "artifacts:read") as client:
                result = await client.list_artifacts(None if workspace_id == "personal" else workspace_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://documents/{document_id}/versions")
    async def document_versions(document_id: str, ctx: Context) -> str:
        """Version lineage and changed-page metadata for an accessible document."""
        try:
            async with api_client(ctx, settings, "versions:read") as client:
                result = await client.list_document_versions(document_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://traces/{trace_id}")
    async def requester_trace(trace_id: str, ctx: Context) -> str:
        """Requester-safe execution and evaluation details for an owned trace."""
        try:
            async with api_client(ctx, settings, "events:read") as client:
                result = await client.get_my_trace(trace_id)
            return json.dumps(result, ensure_ascii=True, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/workspaces/{workspace_id}/courses")
    async def learning_courses(workspace_id: str, ctx: Context) -> str:
        """Knowledge Academy courses accessible in a workspace."""
        try:
            async with api_client(ctx, settings, "learning:read") as client:
                result = await client.list_learning_courses(workspace_id)
            return json.dumps({"workspace_id": workspace_id, "courses": result}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}")
    async def learning_course(course_id: str, ctx: Context) -> str:
        """Complete governed course workspace for the authenticated learner or educator."""
        try:
            async with api_client(ctx, settings, "learning:read") as client: result = await client.get_learning_course(course_id)
            return json.dumps(result, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/curriculum")
    async def learning_curriculum(course_id: str, ctx: Context) -> str:
        """Ordered modules, lessons, objectives, and course metadata."""
        try:
            async with api_client(ctx, settings, "learning:read") as client: course = await client.get_learning_course(course_id)
            fields = ("id", "title", "course_code", "semester", "description", "objectives", "domain", "domain_pack", "publication_status", "modules")
            return json.dumps({key: course.get(key) for key in fields}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/directory")
    async def learning_directory(course_id: str, ctx: Context) -> str:
        """Classmate profiles and coarse locations, filtered by caller role and profile visibility."""
        try:
            async with api_client(ctx, settings, "learning:read") as client:
                result = await client.get_learning_directory(course_id)
            return json.dumps(result, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/content")
    async def learning_content(course_id: str, ctx: Context) -> str:
        """Documents, recordings, and videos mapped to course, module, and lesson scopes."""
        try:
            async with api_client(ctx, settings, "learning:read") as client: course = await client.get_learning_course(course_id)
            return json.dumps({"course_id": course_id, "assets": course.get("assets") or []}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/artifacts")
    async def learning_artifacts(course_id: str, ctx: Context) -> str:
        """Caller-owned summaries, study guides, concepts, flashcards, and quizzes."""
        try:
            async with api_client(ctx, settings, "learning:read") as client: course = await client.get_learning_course(course_id)
            return json.dumps({"course_id": course_id, "artifacts": course.get("artifacts") or []}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/questions")
    async def learning_questions(course_id: str, ctx: Context) -> str:
        """Human questions visible to the learner, assigned teacher, advisor, or course manager."""
        try:
            async with api_client(ctx, settings, "learning:read") as client: course = await client.get_learning_course(course_id)
            return json.dumps({"course_id": course_id, "questions": course.get("questions") or []}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/progress")
    async def learning_progress(course_id: str, ctx: Context) -> str:
        """Persisted quiz progress scoped by learner or educator visibility."""
        try:
            async with api_client(ctx, settings, "learning:read") as client: attempts = await client.get_learning_progress(course_id)
            return json.dumps({"course_id": course_id, "attempts": attempts}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/mastery")
    async def learning_mastery(course_id: str, ctx: Context) -> str:
        """Evidence-backed mastery projection and adaptive next actions for the caller."""
        try:
            async with api_client(ctx, settings, "learning:read") as client:
                result = await client.get_learning_mastery(course_id)
            return json.dumps(result, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/assignments")
    async def learning_assignments(course_id: str, ctx: Context) -> str:
        """Role-filtered assignments and submissions visible to the authenticated course member."""
        try:
            async with api_client(ctx, settings, "learning:read") as client:
                course = await client.get_learning_course(course_id)
            return json.dumps({"course_id": course_id, "assignments": course.get("assignments") or [], "submissions": course.get("submissions") or []}, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())

    @mcp.resource("docintel://learning/courses/{course_id}/instructor-dashboard")
    async def learning_instructor_dashboard(course_id: str, ctx: Context) -> str:
        """Teacher-only cohort progress, assessment, risk, question, and content-quality signals."""
        try:
            async with api_client(ctx, settings, "learning:manage") as client:
                result = await client.get_learning_instructor_dashboard(course_id)
            return json.dumps(result, ensure_ascii=False, default=str)
        except DocIntelMcpError as exc: return json.dumps(exc.as_dict())
