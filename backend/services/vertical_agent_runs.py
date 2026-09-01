from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from services.mcp_enterprise import emit_event


async def create_vertical_run(
    db,
    *,
    workflow_id: str,
    workflow_version: str,
    vertical: str,
    document_id: str,
    user_id: str,
    workspace_id: str | None,
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        INSERT INTO vertical_agent_runs
          (workflow_id, workflow_version, vertical, document_id, user_id, workspace_id, status, input_data)
        VALUES ($1,$2,$3,$4,$5,$6,'running',$7::jsonb)
        RETURNING *
        """,
        workflow_id,
        workflow_version,
        vertical,
        document_id,
        user_id,
        workspace_id,
        json.dumps(input_data or {}),
    )
    return dict(row)


async def get_accessible_vertical_run(db, run_id: str, user_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        SELECT r.*
        FROM vertical_agent_runs r
        WHERE r.id=$1
          AND (
            r.user_id=$2
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
              WHERE wm.workspace_id=r.workspace_id
                AND wm.user_id=$2
            )
          )
        """,
        run_id,
        user_id,
    )
    if not row:
        raise HTTPException(404, "Agent run not found")
    return dict(row)


async def latest_vertical_run(
    db,
    *,
    document_id: str,
    vertical: str,
    user_id: str,
    workflow_id: str | None = None,
) -> dict[str, Any] | None:
    row = await db.fetchrow(
        """
        SELECT r.*
        FROM vertical_agent_runs r
        WHERE r.document_id=$1
          AND r.vertical=$2
          AND ($4::text IS NULL OR r.workflow_id=$4)
          AND (
            r.user_id=$3
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
              WHERE wm.workspace_id=r.workspace_id
                AND wm.user_id=$3
            )
          )
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        document_id,
        vertical,
        user_id,
        workflow_id,
    )
    return dict(row) if row else None


async def run_vertical_step(
    db,
    run_id: str,
    agent_name: str,
    input_summary: str,
    agent_call: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    step = await db.fetchrow(
        """
        INSERT INTO vertical_agent_steps (run_id, agent_name, status, input_summary)
        VALUES ($1,$2,'running',$3)
        RETURNING id
        """,
        run_id,
        agent_name,
        input_summary,
    )
    step_id = str(step["id"])
    try:
        output = await agent_call()
        await db.execute(
            """
            UPDATE vertical_agent_steps
            SET status='completed',
                output_data=$2::jsonb,
                completed_at=NOW()
            WHERE id=$1
            """,
            step_id,
            json.dumps(output),
        )
        return output
    except Exception as exc:
        await db.execute(
            """
            UPDATE vertical_agent_steps
            SET status='failed',
                error_message=$2,
                completed_at=NOW()
            WHERE id=$1
            """,
            step_id,
            str(exc)[:1000],
        )
        raise


async def complete_vertical_run(db, run_id: str, result_data: dict[str, Any], status: str = "pending_approval") -> None:
    row = await db.fetchrow(
        """
        UPDATE vertical_agent_runs
        SET status=$2,
            result_data=$3::jsonb,
            completed_at=NOW(),
            updated_at=NOW()
        WHERE id=$1 RETURNING user_id,workspace_id,vertical,workflow_id
        """,
        run_id,
        status,
        json.dumps(result_data),
    )
    if row:
        await emit_event(
            db, user_id=str(row["user_id"]),
            workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None,
            event_type="workflow.completed", resource_type="workflow_run", resource_id=run_id,
            payload={"run_id": run_id, "status": status, "stage": "completed",
                     "message": f"{row['vertical']} workflow {row['workflow_id']} completed"},
        )


async def fail_vertical_run(db, run_id: str, error_message: str) -> None:
    await db.execute(
        """
        UPDATE vertical_agent_runs
        SET status='failed',
            error_message=$2,
            completed_at=NOW(),
            updated_at=NOW()
        WHERE id=$1
        """,
        run_id,
        error_message[:1500],
    )


async def approve_vertical_run(
    db,
    *,
    run_id: str,
    user_id: str,
    approved_packet: dict[str, Any],
    notes: str | None = None,
) -> None:
    row = await db.fetchrow(
        """
        UPDATE vertical_agent_runs
        SET status='approved',
            approved_by=$2,
            approved_at=NOW(),
            approval_notes=$3,
            result_data=jsonb_set(COALESCE(result_data, '{}'::jsonb), '{approved_packet}', $4::jsonb, true),
            updated_at=NOW()
        WHERE id=$1 RETURNING user_id,workspace_id,vertical,workflow_id
        """,
        run_id,
        user_id,
        notes,
        json.dumps(approved_packet),
    )
    if row:
        await emit_event(
            db, user_id=str(row["user_id"]),
            workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None,
            event_type="review.approved", resource_type="workflow_run", resource_id=run_id,
            payload={"run_id": run_id, "review_id": run_id, "status": "approved",
                     "stage": "human_review", "message": f"{row['vertical']} workflow approved"},
        )


async def emit_packet_generated(
    db, *, run: dict[str, Any], run_id: str, document_id: str, filename: str,
) -> None:
    await emit_event(
        db, user_id=str(run["user_id"]),
        workspace_id=str(run["workspace_id"]) if run.get("workspace_id") else None,
        event_type="packet.generated", resource_type="document", resource_id=document_id,
        payload={"run_id": run_id, "packet_id": document_id, "document_id": document_id,
                 "filename": filename, "status": "generated", "stage": "packet_generation"},
    )


async def vertical_run_response(db, run: dict[str, Any]) -> dict[str, Any]:
    steps = await db.fetch(
        """
        SELECT agent_name, status, input_summary, output_data, error_message,
               started_at, completed_at
        FROM vertical_agent_steps
        WHERE run_id=$1
        ORDER BY started_at, id
        """,
        str(run["id"]),
    )
    return {
        "run_id": str(run["id"]),
        "workflow_id": run.get("workflow_id"),
        "workflow_version": run.get("workflow_version"),
        "vertical": run.get("vertical"),
        "document_id": str(run["document_id"]),
        "status": run.get("status"),
        "input": _json(run.get("input_data")) if run.get("input_data") else {},
        "result": _json(run.get("result_data")) if run.get("result_data") else None,
        "error_message": run.get("error_message"),
        "approval_notes": run.get("approval_notes"),
        "approved_by": str(run["approved_by"]) if run.get("approved_by") else None,
        "approved_at": run["approved_at"].isoformat() if run.get("approved_at") else None,
        "created_at": run["created_at"].isoformat() if run.get("created_at") else None,
        "completed_at": run["completed_at"].isoformat() if run.get("completed_at") else None,
        "steps": [
            {
                "agent_name": row["agent_name"],
                "status": row["status"],
                "input_summary": row["input_summary"],
                "output": _json(row["output_data"]) if row["output_data"] else None,
                "error_message": row["error_message"],
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
            }
            for row in steps
        ],
    }


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value
