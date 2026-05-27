# routes/traces.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import AdminUser
from database.connection import get_db

router = APIRouter()


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
    limit: int = 50,
):
    limit = max(1, min(limit, 200))
    rows = await db.fetch(
        """SELECT trace_id, request_type, user_id, workspace_id, session_id, status,
                  input_text_hash, input_text_preview, client_info, metadata,
                  error_message, started_at, ended_at
           FROM trace_flows
           WHERE ($1::uuid IS NULL OR user_id=$1::uuid)
             AND ($2::text IS NULL OR request_type=$2)
             AND ($3::text IS NULL OR status=$3)
           ORDER BY started_at DESC
           LIMIT $4""",
        user_id,
        request_type,
        status,
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
    return {
        "trace": dict(trace),
        "spans": [dict(r) for r in spans],
        "llm_events": [dict(r) for r in llm_events],
    }
