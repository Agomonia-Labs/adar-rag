from __future__ import annotations

import json
from typing import Any


def _tool(category: str, name: str, arguments: dict[str, Any], description: str) -> dict:
    return {"category": category, "name": name.replace("_", " ").title(), "tool": name,
            "description": description,
            "command": f"mcp_tool {name} '{json.dumps(arguments, separators=(',', ':'))}' | tool_data | jq '.'"}


def _request(category: str, name: str, method: str, params: dict | None, description: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return {"category": category, "name": name, "tool": method, "description": description,
            "command": f"mcp_request '{json.dumps(payload, separators=(',', ':'))}'"}


def example_catalog() -> list[dict]:
    ws, doc, doc2, session, run = ("YOUR_WORKSPACE_ID", "YOUR_DOCUMENT_ID",
                                    "YOUR_SECOND_DOCUMENT_ID", "YOUR_SESSION_ID", "YOUR_RUN_ID")
    conversation = "YOUR_CONVERSATION_SESSION_ID"
    examples = [
        _request("Discovery", "Initialize server", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "docintel-playground", "version": "1.0"}}, "Negotiate MCP protocol capabilities."),
        _request("Discovery", "List all tools", "tools/list", {}, "Inspect live tools and input schemas."),
        _request("Discovery", "List resources", "resources/list", {}, "List concrete MCP resources."),
        _request("Discovery", "List resource templates", "resources/templates/list", {}, "List parameterized DocIntel resources."),
        _tool("Workspaces", "list_workspaces", {}, "List every accessible workspace."),
        _tool("Documents", "list_documents", {"workspace_id": ws}, "List workspace documents; use null for personal scope."),
        _tool("Documents", "get_document", {"document_id": doc}, "Read document metadata and processing state."),
        _tool("Documents", "get_ingestion_status", {"document_id": doc}, "Check chunking and embedding progress."),
        _tool("Documents", "get_document_chunks", {"document_id": doc}, "Read the document chunk manifest."),
        _tool("Documents", "create_document_upload", {"filename": "sample.pdf", "content_type": "application/pdf", "file_size": 12345, "workspace_id": ws, "redact_pii": False}, "Create a signed document upload URL."),
        _tool("Documents", "complete_document_upload", {"doc_id": doc, "filename": "sample.pdf", "content_type": "application/pdf", "file_size": 12345, "gcs_source_path": "PATH_FROM_CREATE_UPLOAD", "workspace_id": ws, "redact_pii": False}, "Verify upload and start chunking."),
        _tool("Documents", "embed_document", {"document_id": doc}, "Embed a chunked document."),
        _tool("Documents", "delete_document", {"document_id": doc, "confirm": True}, "Delete a document and owned artifacts."),
        _tool("Knowledge", "search_knowledgebase", {"question": "Summarize the important facts, risks, and next actions.", "document_ids": [doc], "workspace_id": ws, "history": [], "redact_pii": False}, "Ask with hybrid retrieval and citations."),
        _tool("Knowledge", "summarize_document", {"document_id": doc, "summary_type": "executive", "custom_prompt": "", "chunk_indices": [], "redact_pii": False}, "Generate executive, detailed, bullets, sections, or custom summary."),
        _tool("Knowledge", "summarize_documents", {"document_ids": [doc, doc2], "summary_type": "executive", "custom_prompt": "", "redact_pii": False}, "Summarize multiple documents together."),
        _tool("Knowledge", "compare_documents", {"document_id_1": doc, "document_id_2": doc2, "redact_pii": False}, "Compare two documents."),
        _tool("Chat Sessions", "create_chat_session", {"document_ids": [doc], "workspace_id": ws, "title": "MCP Research Session"}, "Create a persistent chat session."),
        _tool("Chat Sessions", "list_chat_sessions", {"workspace_id": ws}, "List persistent sessions."),
        _tool("Chat Sessions", "get_chat_session", {"session_id": session}, "Read a session and message history."),
        _tool("Chat Sessions", "update_chat_session", {"session_id": session, "title": "Updated Session", "document_ids": [doc]}, "Rename a session or change documents."),
        _tool("Chat Sessions", "ask", {"question": "What changed and what action is required?", "document_ids": [doc], "workspace_id": ws, "session_id": session, "history": [], "redact_pii": False}, "Ask and optionally persist a grounded answer."),
        _tool("Chat Sessions", "delete_chat_session", {"session_id": session, "confirm": True}, "Delete a persistent session."),
        _tool("Conversation Recording", "start_conversation_recording", {"workspace_id": ws, "language_code": "en-US"}, "Start a governed conversation recording session; use bn-BD for Bangla."),
        _tool("Conversation Recording", "confirm_conversation_consent", {"session_id": conversation, "confirmed": True}, "Record participant consent before collecting turns."),
        _tool("Conversation Recording", "add_conversation_turn", {"session_id": conversation, "transcript": "I would like to add this information to the knowledgebase."}, "Add one transcribed participant turn and receive the assistant follow-up."),
        _tool("Conversation Recording", "finish_conversation_recording", {"session_id": conversation}, "Finish collection and move the editable transcript into review."),
        _tool("Conversation Recording", "get_conversation_recording", {"session_id": conversation}, "Read turns, review state, processing progress, and published document linkage."),
        _tool("Conversation Recording", "list_conversation_recordings", {"workspace_id": ws}, "List conversation recordings visible in a workspace."),
        _tool("Conversation Recording", "approve_conversation_transcript", {"session_id": conversation, "transcript": "Customer: Reviewed transcript text.", "confirm": True}, "Approve edited text and publish it for chunking and embedding."),
        _tool("Conversation Recording", "delete_conversation_recording", {"session_id": conversation, "confirm": True}, "Delete the conversation and all owned derived records."),
        _tool("Video", "create_video_upload", {"filename": "sample.mp4", "content_type": "video/mp4", "file_size": 104857600, "workspace_id": ws}, "Create a signed video upload URL."),
        _tool("Video", "complete_video_upload", {"doc_id": doc, "filename": "sample.mp4", "content_type": "video/mp4", "file_size": 104857600, "gcs_source_path": "PATH_FROM_CREATE_UPLOAD", "workspace_id": ws, "process_after_upload": False, "rights_confirmed": True, "transcript_language": "auto", "max_frames": 12, "segment_seconds": 60, "embed_after_processing": True}, "Verify video upload and optionally process."),
        _tool("Video", "list_videos", {"workspace_id": ws}, "List videos and processing progress."),
        _tool("Video", "process_video", {"document_id": doc, "rights_confirmed": True, "transcript_language": "auto", "max_frames": 12, "segment_seconds": 60, "embed_after_processing": True}, "Start video intelligence processing."),
        _tool("Video", "get_video_status", {"document_id": doc}, "Check video processing status."),
        _tool("Video", "get_video_timeline", {"document_id": doc}, "Read timestamped segments and frames."),
        _tool("Video", "get_video_transcript", {"document_id": doc}, "Read timestamped transcript entries."),
        _tool("Video", "get_video_frames", {"document_id": doc}, "Read sampled frame metadata."),
        _tool("Video", "get_video_frame_url", {"document_id": doc, "frame_index": 0}, "Create a short-lived frame URL."),
        _tool("Video", "search_video", {"document_id": doc, "question": "What happens between 1:00 and 3:00?", "limit": 8}, "Ask a timestamp-grounded video question."),
        _tool("Vertical Workflows", "list_vertical_workflows", {}, "Discover workflows, inputs, review gates, and packets."),
        _tool("Vertical Workflows", "start_vertical_workflow", {"workflow": "healthcare_clinical", "document_ids": [doc], "workspace_id": ws, "inputs": {}}, "Start a supported vertical workflow."),
        _tool("Vertical Workflows", "get_vertical_run", {"vertical": "healthcare", "run_id": run}, "Read workflow status and output."),
        _tool("Vertical Workflows", "list_vertical_runs", {"vertical": "finance_tax", "workspace_id": ws, "status": "all", "limit": 25}, "List finance/tax or talent runs."),
        _tool("Human Review", "save_vertical_review", {"vertical": "healthcare", "run_id": run, "packet": {}, "notes": "Reviewed in MCP Playground", "persona": "reviewer"}, "Save reviewer edits without approval."),
        _tool("Human Review", "approve_vertical_run", {"vertical": "healthcare", "run_id": run, "confirm": True, "packet": {}, "notes": "Approved after review", "persona": "reviewer"}, "Apply the human approval gate."),
        _tool("Packets", "generate_vertical_packet", {"vertical": "healthcare", "run_id": run, "packet_type": "clinical_summary", "packet": {}}, "Generate or ingest a PDF packet."),
        _tool("Batch Operations", "create_batch_upload", {"files": [{"filename": "first.pdf", "content_type": "application/pdf", "file_size": 12345}, {"filename": "second.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "file_size": 23456}], "workspace_id": ws, "redact_pii": False}, "Create signed URLs for multiple document uploads."),
        _tool("Batch Operations", "complete_batch_upload", {"batch_job_id": "YOUR_BATCH_JOB_ID", "document_ids": [], "concurrency": 3}, "Verify uploaded files and start bounded chunking."),
        _tool("Batch Operations", "start_batch_embedding", {"document_ids": [doc, doc2], "workspace_id": ws, "concurrency": 3, "force": False}, "Embed many chunked documents with partial-failure tracking."),
        _tool("Batch Operations", "start_batch_classification", {"document_ids": [doc, doc2], "workspace_id": ws, "concurrency": 3, "force": False}, "Classify many documents while preserving reviewed values."),
        _tool("Batch Operations", "start_workspace_summary", {"workspace_id": ws, "document_ids": [], "summary_type": "executive", "custom_prompt": "", "redact_pii": False, "language": "en", "concurrency": 2}, "Create a hierarchical workspace summary."),
        _tool("Batch Operations", "list_batch_jobs", {"workspace_id": ws, "operation": None, "status": None, "limit": 25}, "List batch jobs and aggregate progress."),
        _tool("Batch Operations", "get_batch_status", {"batch_job_id": "YOUR_BATCH_JOB_ID"}, "Read current stage, percentage, counts, and item errors."),
        _tool("Batch Operations", "get_batch_results", {"batch_job_id": "YOUR_BATCH_JOB_ID"}, "Read aggregate and item-level batch outputs."),
        _tool("Batch Operations", "retry_batch_failures", {"batch_job_id": "YOUR_BATCH_JOB_ID"}, "Retry only failed items."),
        _tool("Batch Operations", "cancel_batch_job", {"batch_job_id": "YOUR_BATCH_JOB_ID", "confirm": True}, "Cancel queued work and retain completed results."),
    ]
    resources = [
        ("Workflow catalog", "docintel://workflows/catalog", "Read the workflow catalog."),
        ("Workflow run", "docintel://workflows/healthcare/runs/YOUR_RUN_ID", "Read one workflow run."),
        ("Workspace documents", "docintel://workspaces/YOUR_WORKSPACE_ID/documents", "Read workspace documents."),
        ("Document", "docintel://documents/YOUR_DOCUMENT_ID", "Read document metadata."),
        ("Document chunks", "docintel://documents/YOUR_DOCUMENT_ID/chunks", "Read document chunks."),
        ("Chat session", "docintel://sessions/YOUR_SESSION_ID", "Read a chat session."),
        ("Conversation recording", "docintel://conversations/YOUR_CONVERSATION_SESSION_ID", "Read conversation turns and lifecycle state."),
        ("Conversation transcript", "docintel://conversations/YOUR_CONVERSATION_SESSION_ID/transcript", "Read the editable transcript draft or published segments."),
        ("Video", "docintel://videos/YOUR_DOCUMENT_ID", "Read video metadata."),
        ("Video timeline", "docintel://videos/YOUR_DOCUMENT_ID/timeline", "Read the video timeline."),
        ("Video transcript", "docintel://videos/YOUR_DOCUMENT_ID/transcript", "Read the video transcript."),
        ("Video frames", "docintel://videos/YOUR_DOCUMENT_ID/frames", "Read video frame metadata."),
        ("Batch job", "docintel://batches/YOUR_BATCH_JOB_ID", "Read batch progress and item states."),
        ("Batch results", "docintel://batches/YOUR_BATCH_JOB_ID/results", "Read batch outputs and errors."),
    ]
    examples.extend(_request("Resources", f"{name} resource", "resources/read", {"uri": uri}, description)
                    for name, uri, description in resources)
    return examples
