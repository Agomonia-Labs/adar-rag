# routes/traces.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import AdminUser, CurrentUser
from database.connection import get_db

router = APIRouter()


@router.get("/mine/summary")
async def my_trace_summary(
    current_user: CurrentUser,
    db=Depends(get_db),
    workspace_id: str | None = None,
    personal_only: bool = False,
):
    await _validate_user_trace_scope(db, str(current_user["id"]), workspace_id)
    row = await db.fetchrow(
        """SELECT COUNT(*) AS trace_count, MAX(started_at) AS latest_trace_at
           FROM trace_flows
           WHERE user_id=$1::uuid
             AND ($2::uuid IS NULL OR workspace_id=$2::uuid)
             AND (NOT $3::boolean OR workspace_id IS NULL)""",
        str(current_user["id"]), workspace_id, personal_only,
    )
    return {
        "trace_count": int(row["trace_count"] or 0),
        "latest_trace_at": row["latest_trace_at"],
        "scope": "personal" if personal_only else (workspace_id or "all"),
    }


@router.get("/mine")
async def list_my_traces(
    current_user: CurrentUser,
    db=Depends(get_db),
    workspace_id: str | None = None,
    personal_only: bool = False,
    request_type: str | None = None,
    status: str | None = None,
    operation: str | None = None,
    search: str | None = None,
    min_duration_ms: int | None = None,
    limit: int = 50,
):
    user_id = str(current_user["id"])
    await _validate_user_trace_scope(db, user_id, workspace_id)
    limit = max(1, min(limit, 100))
    rows = await db.fetch(
        """SELECT f.trace_id, f.request_type, f.workspace_id, f.session_id, f.status,
                  f.input_text_preview, f.error_message, f.started_at, f.ended_at,
                  COALESCE(EXTRACT(EPOCH FROM (f.ended_at-f.started_at))*1000, 0)::bigint AS duration_ms,
                  COUNT(DISTINCT s.span_id)::int AS span_count,
                  COALESCE(array_agg(DISTINCT s.name) FILTER (WHERE s.name IS NOT NULL), '{}') AS operations
           FROM trace_flows f
           LEFT JOIN trace_spans s ON s.trace_id=f.trace_id
           WHERE f.user_id=$1::uuid
             AND ($2::uuid IS NULL OR f.workspace_id=$2::uuid)
             AND (NOT $3::boolean OR f.workspace_id IS NULL)
             AND ($4::text IS NULL OR f.request_type=$4)
             AND ($5::text IS NULL OR f.status=$5)
             AND ($6::text IS NULL OR EXISTS (
                   SELECT 1 FROM trace_spans os WHERE os.trace_id=f.trace_id AND os.name ILIKE '%' || $6 || '%'))
             AND ($7::text IS NULL OR f.input_text_preview ILIKE '%' || $7 || '%')
             AND ($8::int IS NULL OR COALESCE(EXTRACT(EPOCH FROM (f.ended_at-f.started_at))*1000, 0) >= $8)
           GROUP BY f.id
           ORDER BY f.started_at DESC
           LIMIT $9""",
        user_id, workspace_id, personal_only, request_type, status,
        operation, search, min_duration_ms, limit,
    )
    return [_public_trace_row(dict(row)) for row in rows]


@router.get("/mine/{trace_id}")
async def get_my_trace(
    trace_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    trace = await db.fetchrow(
        "SELECT * FROM trace_flows WHERE trace_id=$1 AND user_id=$2::uuid",
        trace_id, user_id,
    )
    if not trace:
        raise HTTPException(404, "Trace not found")
    trace_data = dict(trace)
    if trace_data.get("workspace_id"):
        await _validate_user_trace_scope(db, user_id, str(trace_data["workspace_id"]))

    spans = await db.fetch(
        "SELECT * FROM trace_spans WHERE trace_id=$1 ORDER BY started_at ASC",
        trace_id,
    )
    events = await db.fetch(
        "SELECT * FROM trace_llm_events WHERE trace_id=$1 ORDER BY created_at ASC",
        trace_id,
    )
    evaluations = await _trace_evaluations(db, trace_id)
    response = build_user_trace_response(trace_data, [dict(row) for row in spans], [dict(row) for row in events])
    response["evaluations"] = [_public_evaluation(row) for row in evaluations]
    return response


@router.get("/summary")
async def trace_summary(admin: AdminUser, db=Depends(get_db)):
    tables = await db.fetch(
        """SELECT table_name
           FROM information_schema.tables
           WHERE table_schema='public'
             AND table_name = ANY($1::text[])
           ORDER BY table_name""",
        ["trace_flows", "trace_spans", "trace_llm_events"],
    )
    existing = {r["table_name"] for r in tables}
    if "trace_flows" not in existing:
        return {
            "tables": sorted(existing),
            "trace_count": 0,
            "latest_trace_at": None,
            "ready": False,
            "message": "Trace tables are missing. Restart the backend or run schema creation.",
        }
    row = await db.fetchrow(
        "SELECT COUNT(*) AS trace_count, MAX(started_at) AS latest_trace_at FROM trace_flows"
    )
    return {
        "tables": sorted(existing),
        "trace_count": row["trace_count"],
        "latest_trace_at": row["latest_trace_at"],
        "ready": existing == {"trace_flows", "trace_spans", "trace_llm_events"},
        "message": None,
    }


@router.get("/")
async def list_traces(
    admin: AdminUser,
    db=Depends(get_db),
    user_id: str | None = None,
    request_type: str | None = None,
    status: str | None = None,
    workspace_id: str | None = None,
    operation: str | None = None,
    search: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    min_duration_ms: int | None = None,
    limit: int = 50,
):
    limit = max(1, min(limit, 200))
    rows = await db.fetch(
        """SELECT f.trace_id, f.request_type, f.user_id, f.workspace_id, f.session_id, f.status,
                  f.input_text_hash, f.input_text_preview, f.client_info, f.metadata,
                  f.error_message, f.started_at, f.ended_at,
                  COALESCE(EXTRACT(EPOCH FROM (f.ended_at-f.started_at))*1000, 0)::bigint AS duration_ms,
                  COUNT(DISTINCT s.span_id)::int AS span_count,
                  COALESCE(array_agg(DISTINCT s.name) FILTER (WHERE s.name IS NOT NULL), '{}') AS operations
           FROM trace_flows f
           LEFT JOIN trace_spans s ON s.trace_id=f.trace_id
           WHERE ($1::uuid IS NULL OR f.user_id=$1::uuid)
             AND ($2::text IS NULL OR f.request_type=$2)
             AND ($3::text IS NULL OR f.status=$3)
             AND ($4::uuid IS NULL OR f.workspace_id=$4::uuid)
             AND ($5::text IS NULL OR EXISTS (
                   SELECT 1 FROM trace_spans os WHERE os.trace_id=f.trace_id AND os.name ILIKE '%' || $5 || '%'))
             AND ($6::text IS NULL OR f.input_text_preview ILIKE '%' || $6 || '%' OR f.trace_id ILIKE '%' || $6 || '%')
             AND ($7::timestamptz IS NULL OR f.started_at >= $7)
             AND ($8::timestamptz IS NULL OR f.started_at <= $8)
             AND ($9::int IS NULL OR COALESCE(EXTRACT(EPOCH FROM (f.ended_at-f.started_at))*1000, 0) >= $9)
           GROUP BY f.id
           ORDER BY f.started_at DESC
           LIMIT $10""",
        user_id,
        request_type,
        status,
        workspace_id,
        operation,
        search,
        started_after,
        started_before,
        min_duration_ms,
        limit,
    )
    return [dict(r) for r in rows]


@router.get("/{trace_id}")
async def get_trace(trace_id: str, admin: AdminUser, db=Depends(get_db)):
    trace = await db.fetchrow("SELECT * FROM trace_flows WHERE trace_id=$1", trace_id)
    if not trace:
        raise HTTPException(404, "Trace not found")

    spans = await db.fetch(
        """SELECT * FROM trace_spans
           WHERE trace_id=$1
           ORDER BY started_at ASC""",
        trace_id,
    )
    llm_events = await db.fetch(
        """SELECT * FROM trace_llm_events
           WHERE trace_id=$1
           ORDER BY created_at ASC""",
        trace_id,
    )
    trace_data = dict(trace)
    span_data = [dict(r) for r in spans]
    event_data = [dict(r) for r in llm_events]
    evaluations = await _trace_evaluations(db, trace_id)
    return {
        "trace": trace_data,
        "spans": span_data,
        "llm_events": event_data,
        "workflow": build_trace_workflow(trace_data, span_data, event_data),
        "evaluations": evaluations,
    }


async def _trace_evaluations(db, trace_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """SELECT id,evaluation_type,evaluation_source,evaluation_id,score,outcome,reviewer_id,metadata,created_at
           FROM trace_evaluation_correlations WHERE trace_id=$1 ORDER BY created_at DESC""",
        trace_id,
    )
    return [dict(row) for row in rows]


def _public_evaluation(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in (
        "evaluation_type", "evaluation_source", "score", "outcome", "metadata", "created_at"
    )}


async def _validate_user_trace_scope(db, user_id: str, workspace_id: str | None) -> None:
    if not workspace_id:
        return
    member = await db.fetchval(
        "SELECT 1 FROM workspace_members WHERE workspace_id=$1::uuid AND user_id=$2::uuid",
        workspace_id, user_id,
    )
    if not member:
        raise HTTPException(403, "You are not a member of this workspace")


def _public_trace_row(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        key: trace.get(key)
        for key in (
            "trace_id", "request_type", "workspace_id", "session_id", "status",
            "input_text_preview", "error_message", "started_at", "ended_at",
            "duration_ms", "span_count", "operations",
        )
    }


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return model telemetry useful to the requester without internal instructions or tool payloads."""
    return {
        "span_id": event.get("span_id"),
        "operation": event.get("operation"),
        "provider": event.get("provider"),
        "model": event.get("model"),
        "system_prompt": None,
        "user_prompt": event.get("user_prompt"),
        "tool_request_json": {},
        "tool_response_json": {},
        "llm_response": event.get("llm_response"),
        "input_tokens": event.get("input_tokens"),
        "output_tokens": event.get("output_tokens"),
        "latency_ms": event.get("latency_ms"),
        "finish_reason": event.get("finish_reason"),
        "redaction_status": event.get("redaction_status"),
        "error": "This step failed." if event.get("error") else None,
        "created_at": event.get("created_at"),
    }


def build_user_trace_response(
    trace: dict[str, Any],
    spans: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the requester-safe projection used by My Traces."""
    workflow = build_trace_workflow(trace, spans, events)
    for node in workflow.get("nodes", []):
        details = node.get("details") or {}
        details["metadata"] = {}
        details["error"] = {"message": "This step failed."} if details.get("error") else {}
        details["events"] = [
            _event_detail(_public_event(event))
            for event in events
            if str(event.get("span_id") or "") == str(node.get("id") or "")
        ]
        if node.get("type") == "user_input":
            details = {
                "request_type": trace.get("request_type"),
                "workspace_id": str(trace.get("workspace_id") or "personal"),
                "session_id": str(trace.get("session_id") or ""),
            }
        node["details"] = details

    public_spans = [{
        "span_id": span.get("span_id"),
        "trace_id": span.get("trace_id"),
        "parent_span_id": span.get("parent_span_id"),
        "name": span.get("name"),
        "status": span.get("status"),
        "metadata": {},
        "error": {"message": "This step failed."} if span.get("error") else {},
        "started_at": span.get("started_at"),
        "ended_at": span.get("ended_at"),
        "duration_ms": span.get("duration_ms"),
    } for span in spans]
    public_trace = _public_trace_row({
        **trace,
        "duration_ms": _duration_ms(trace.get("started_at"), trace.get("ended_at")),
        "span_count": len(spans),
        "operations": [span.get("name") for span in spans if span.get("name")],
    })
    return {
        "trace": public_trace,
        "spans": public_spans,
        "llm_events": [_public_event(event) for event in events],
        "workflow": workflow,
        "visibility": "requester",
    }


_STAGE_TYPES = {
    "usage_limit": "context",
    "query_embedding": "embedding",
    "hybrid_retrieval": "retrieval",
    "gemini_rerank": "rerank",
    "prompt_build": "prompt",
    "agentic_context": "agent",
    "restaurant_db_context": "tool",
    "llm_generate": "llm",
}

_STAGE_LABELS = {
    "usage_limit": "Request and usage context",
    "query_embedding": "Question embedding",
    "hybrid_retrieval": "Hybrid knowledge retrieval",
    "gemini_rerank": "Evidence reranking",
    "prompt_build": "Grounded prompt assembly",
    "agentic_context": "Agent workflow context",
    "restaurant_db_context": "Domain tool call",
    "llm_generate": "LLM response generation",
}


def build_trace_workflow(trace: dict[str, Any], spans: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a UI-oriented execution graph from the durable trace projection."""
    started_at = trace.get("started_at")
    ended_at = trace.get("ended_at")
    total_ms = _duration_ms(started_at, ended_at)
    nodes: list[dict[str, Any]] = [{
        "id": "request",
        "parent_id": None,
        "type": "user_input",
        "name": "User question",
        "status": trace.get("status") or "running",
        "started_at": started_at,
        "duration_ms": total_ms,
        "summary": trace.get("input_text_preview") or "Request received",
        "details": {
            "request_type": trace.get("request_type"),
            "user_id": str(trace.get("user_id") or ""),
            "workspace_id": str(trace.get("workspace_id") or ""),
            "session_id": str(trace.get("session_id") or ""),
            "client_info": trace.get("client_info") or {},
        },
    }]
    event_by_span: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        event_by_span.setdefault(str(event.get("span_id") or ""), []).append(event)

    previous_id = "request"
    for index, span in enumerate(spans):
        span_id = str(span.get("span_id") or f"span-{index}")
        name = str(span.get("name") or "operation")
        related = event_by_span.get(span_id, [])
        details = {
            "metadata": span.get("metadata") or {},
            "error": span.get("error") or {},
            "events": [_event_detail(event) for event in related],
        }
        metrics = _node_metrics(name, span, related)
        parent = str(span.get("parent_span_id") or "") or previous_id
        nodes.append({
            "id": span_id,
            "parent_id": parent,
            "type": _stage_type(name, related),
            "name": _STAGE_LABELS.get(name, name.replace("_", " ").title()),
            "operation": name,
            "service": _service_name(span),
            "status": span.get("status") or "running",
            "started_at": span.get("started_at"),
            "ended_at": span.get("ended_at"),
            "offset_ms": _duration_ms(started_at, span.get("started_at")),
            "duration_ms": span.get("duration_ms") or 0,
            "summary": _node_summary(name, metrics, related),
            "metrics": metrics,
            "details": details,
        })
        previous_id = span_id

    response_event = next((event for event in reversed(events) if event.get("llm_response")), None)
    if response_event:
        response = response_event.get("llm_response") or ""
        nodes.append({
            "id": "response",
            "parent_id": str(response_event.get("span_id") or previous_id),
            "type": "response",
            "name": "Grounded response",
            "status": "success" if not response_event.get("error") else "error",
            "started_at": ended_at,
            "offset_ms": total_ms,
            "duration_ms": 0,
            "summary": _preview(response, 180),
            "metrics": {"response_characters": len(response)},
            "details": _event_detail(response_event),
        })

    node_ids = {node["id"] for node in nodes}
    edges = [{
        "source": node["parent_id"] if node.get("parent_id") in node_ids else "request",
        "target": node["id"],
        "relationship": "child",
    } for node in nodes if node["id"] != "request"]
    retrieval = next((node for node in nodes if node.get("type") == "retrieval"), None)
    rerank = next((node for node in nodes if node.get("type") == "rerank"), None)
    llm = next((node for node in nodes if node.get("type") == "llm"), None)
    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "duration_ms": total_ms,
            "step_count": len(nodes),
            "tool_call_count": sum(1 for node in nodes if node.get("type") == "tool"),
            "llm_call_count": sum(1 for node in nodes if node.get("type") == "llm"),
            "candidate_chunk_count": (retrieval or {}).get("metrics", {}).get("candidate_chunks", 0),
            "selected_chunk_count": (rerank or {}).get("metrics", {}).get("selected_chunks", 0),
            "error_count": sum(1 for node in nodes if node.get("status") == "error"),
        },
        "story": _request_story(trace, retrieval, rerank, llm, total_ms),
    }


def _event_detail(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": event.get("operation"),
        "provider": event.get("provider"),
        "model": event.get("model"),
        "system_prompt": event.get("system_prompt"),
        "user_prompt": event.get("user_prompt"),
        "tool_request": event.get("tool_request_json") or {},
        "tool_response": event.get("tool_response_json") or {},
        "llm_response": event.get("llm_response"),
        "input_tokens": event.get("input_tokens"),
        "output_tokens": event.get("output_tokens"),
        "latency_ms": event.get("latency_ms"),
        "finish_reason": event.get("finish_reason"),
        "redaction_status": event.get("redaction_status"),
        "error": event.get("error"),
    }


def _node_metrics(name: str, span: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = _json_object(span.get("metadata"))
    metrics: dict[str, Any] = {key: value for key, value in metadata.items() if isinstance(value, (str, int, float, bool)) and not key.startswith("otel_")}
    for event in events:
        request = _json_object(event.get("tool_request_json"))
        response = _json_object(event.get("tool_response_json"))
        if name == "hybrid_retrieval":
            metrics["candidate_chunks"] = len(response.get("candidates") or [])
            metrics["requested_limit"] = request.get("limit")
        if name == "gemini_rerank":
            metrics["candidate_chunks"] = len(request.get("candidates") or [])
            metrics["selected_chunks"] = len(response.get("ranked") or [])
        if name == "query_embedding":
            metrics["embedding_dimensions"] = response.get("embedding_dim")
        if name == "llm_generate":
            metrics.update({
                "provider": event.get("provider"), "model": event.get("model"),
                "input_tokens": event.get("input_tokens"), "output_tokens": event.get("output_tokens"),
            })
    return {key: value for key, value in metrics.items() if value is not None}


def _stage_type(name: str, events: list[dict[str, Any]]) -> str:
    if name in _STAGE_TYPES:
        return _STAGE_TYPES[name]
    operations = " ".join(str(event.get("operation") or "") for event in events).lower()
    if "tool" in operations or any((event.get("tool_request_json") or {}) for event in events):
        return "tool"
    return "operation"


def _service_name(span: dict[str, Any]) -> str:
    metadata = _json_object(span.get("metadata"))
    return metadata.get("service.name") or metadata.get("service_name") or "docintel-backend"


def _node_summary(name: str, metrics: dict[str, Any], events: list[dict[str, Any]]) -> str:
    if name == "hybrid_retrieval":
        return f"Found {metrics.get('candidate_chunks', 0)} candidate chunks"
    if name == "gemini_rerank":
        return f"Selected {metrics.get('selected_chunks', 0)} of {metrics.get('candidate_chunks', 0)} chunks"
    if name == "query_embedding":
        return f"Created a {metrics.get('embedding_dimensions', '—')}-dimension query vector"
    if name == "llm_generate" and events:
        event = events[-1]
        return f"Generated with {event.get('provider') or 'LLM'} · {event.get('model') or 'default model'}"
    return _STAGE_LABELS.get(name, name.replace("_", " ").title())


def _request_story(trace, retrieval, rerank, llm, total_ms: int) -> str:
    question = _preview(trace.get("input_text_preview") or "the request", 90)
    candidates = (retrieval or {}).get("metrics", {}).get("candidate_chunks", 0)
    selected = (rerank or {}).get("metrics", {}).get("selected_chunks", 0)
    model = (llm or {}).get("metrics", {}).get("model") or "the configured model"
    return (f'DocIntel received "{question}", retrieved {candidates} candidate chunks, '
            f"retained {selected} after reranking, and generated a grounded response with {model} "
            f"in {total_ms / 1000:.2f} seconds.")


def _duration_ms(start, end) -> int:
    if not start or not end:
        return 0
    try:
        return max(0, int((end - start).total_seconds() * 1000))
    except (AttributeError, TypeError):
        return 0


def _preview(value: str, limit: int) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[:limit - 3] + "..."


def _json_object(value: Any) -> dict[str, Any]:
    """Normalize legacy JSONB payload shapes before reading named fields."""
    return value if isinstance(value, dict) else {}
