from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.agent_workflow_evaluator import evaluate_agent_workflow
from services.audit import audit, ip_from, ua_from
from services.usage import check_and_log_daily_event
from services.tracing import finish_trace, span, start_trace


router = APIRouter()
log = logging.getLogger("docintel.agent_evals")


class AgentEvalRequest(BaseModel):
    persist: bool = True


@router.post("/{vertical}/runs/{run_id}")
async def evaluate_agent_run(
    vertical: str,
    run_id: str,
    body: AgentEvalRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await _load_accessible_run(db, vertical, run_id, user_id)
    trace_id = await start_trace(
        request_type="workflow_evaluation",
        trace_id=getattr(request.state, "trace_id", None),
        user_id=user_id,
        workspace_id=run.get("workspace_id"),
        input_text=f"Evaluate {vertical} workflow run",
        metadata={"vertical": vertical, "run_id": run_id},
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "eval",
        "max_evals_day",
        metadata={"mode": "agent_workflow", "vertical": vertical, "run_id": run_id},
    )
    async with span("workflow_quality_evaluation", trace_id=trace_id, metadata={"vertical": vertical}):
        evaluation = evaluate_agent_workflow(vertical, run)
    saved = None
    if body.persist:
        saved = await _save_evaluation(db, run, evaluation, user_id)
        await audit(
            db,
            user_id=user_id,
            action="agent_workflow_evaluate",
            resource_type="agent_run",
            resource_id=run_id,
            metadata={
                "vertical": vertical,
                "document_id": run.get("document_id"),
                "overall_score": evaluation["overall_score"],
                "gate_status": evaluation["gate_status"],
                "evaluation_id": saved["id"],
            },
            ip_address=ip_from(request),
            user_agent=ua_from(request),
        )
    await finish_trace(trace_id, "success")
    return _evaluation_response(evaluation, saved)


@router.get("/{vertical}/runs/{run_id}")
async def get_agent_run_evaluations(
    vertical: str,
    run_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _load_accessible_run(db, vertical, run_id, user_id)
    rows = await db.fetch(
        """
        SELECT *
        FROM agent_workflow_evaluations
        WHERE vertical=$1 AND run_id=$2
        ORDER BY created_at DESC
        """,
        _normalize_vertical(vertical),
        run_id,
    )
    return {"vertical": _normalize_vertical(vertical), "run_id": run_id, "evaluations": [_row_response(row) for row in rows]}


@router.get("/{vertical}/runs/{run_id}/latest")
async def get_latest_agent_run_evaluation(
    vertical: str,
    run_id: str,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _load_accessible_run(db, vertical, run_id, user_id)
    row = await db.fetchrow(
        """
        SELECT *
        FROM agent_workflow_evaluations
        WHERE vertical=$1 AND run_id=$2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        _normalize_vertical(vertical),
        run_id,
    )
    return {"vertical": _normalize_vertical(vertical), "run_id": run_id, "evaluation": _row_response(row) if row else None}


async def _load_accessible_run(db, vertical: str, run_id: str, user_id: str) -> dict[str, Any]:
    vertical = _normalize_vertical(vertical)
    if vertical == "lease":
        return await _load_lease_run(db, run_id, user_id)
    if vertical == "healthcare":
        return await _load_vertical_run(db, vertical, run_id, user_id)
    raise HTTPException(400, "vertical must be one of: lease, healthcare")


async def _load_lease_run(db, run_id: str, user_id: str) -> dict[str, Any]:
    run = await db.fetchrow(
        """
        SELECT r.*
        FROM lease_agent_runs r
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
    if not run:
        raise HTTPException(404, "Lease agent run not found")
    steps = await db.fetch(
        """
        SELECT agent_name, status, input_summary, output_data, error_message,
               started_at, completed_at
        FROM lease_agent_steps
        WHERE run_id=$1
        ORDER BY started_at, id
        """,
        run_id,
    )
    obligations = await db.fetch(
        """
        SELECT title, party, category, priority, due_date, "trigger", source,
               status, notes, approved
        FROM lease_obligations
        WHERE run_id=$1
        ORDER BY due_date NULLS LAST, created_at
        """,
        run_id,
    )
    result = _json(run["result_data"]) if run["result_data"] else {}
    return {
        "vertical": "lease",
        "run_id": str(run["id"]),
        "document_id": str(run["document_id"]),
        "user_id": str(run["user_id"]),
        "workspace_id": str(run["workspace_id"]) if run["workspace_id"] else None,
        "status": run["status"],
        "workflow_version": run["workflow_version"],
        "result": result,
        "steps": [_step(row) for row in steps],
        "obligations": [_obligation(row) for row in obligations],
    }


async def _load_vertical_run(db, vertical: str, run_id: str, user_id: str) -> dict[str, Any]:
    run = await db.fetchrow(
        """
        SELECT r.*
        FROM vertical_agent_runs r
        WHERE r.id=$1
          AND r.vertical=$2
          AND (
            r.user_id=$3
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
              WHERE wm.workspace_id=r.workspace_id
                AND wm.user_id=$3
            )
          )
        """,
        run_id,
        vertical,
        user_id,
    )
    if not run:
        raise HTTPException(404, "Agent run not found")
    steps = await db.fetch(
        """
        SELECT agent_name, status, input_summary, output_data, error_message,
               started_at, completed_at
        FROM vertical_agent_steps
        WHERE run_id=$1
        ORDER BY started_at, id
        """,
        run_id,
    )
    return {
        "vertical": vertical,
        "run_id": str(run["id"]),
        "document_id": str(run["document_id"]),
        "user_id": str(run["user_id"]),
        "workspace_id": str(run["workspace_id"]) if run["workspace_id"] else None,
        "status": run["status"],
        "workflow_version": run["workflow_version"],
        "result": _json(run["result_data"]) if run["result_data"] else {},
        "steps": [_step(row) for row in steps],
    }


async def _save_evaluation(db, run: dict[str, Any], evaluation: dict[str, Any], user_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        INSERT INTO agent_workflow_evaluations
          (vertical, run_id, document_id, user_id, workspace_id, evaluator_version,
           overall_score, passed, gate_status, metrics, recommendations, policy, metadata)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb)
        RETURNING id, created_at
        """,
        run["vertical"],
        run["run_id"],
        run["document_id"],
        user_id,
        run.get("workspace_id"),
        evaluation["evaluator_version"],
        evaluation["overall_score"],
        evaluation["passed"],
        evaluation["gate_status"],
        json.dumps(evaluation["metrics"]),
        json.dumps(evaluation["recommendations"]),
        json.dumps(evaluation["policy"]),
        json.dumps(evaluation["metadata"]),
    )
    from services.tracing import current_trace_id
    trace_id = current_trace_id.get()
    if trace_id:
        trace_exists = await db.fetchval("SELECT 1 FROM trace_flows WHERE trace_id=$1", trace_id)
        if trace_exists:
            await db.execute(
                """INSERT INTO trace_evaluation_correlations
                   (trace_id,evaluation_type,evaluation_source,evaluation_id,score,outcome,reviewer_id,metadata)
                   VALUES($1,'agent_workflow',$2,$3,$4,$5,$6,$7::jsonb)
                   ON CONFLICT(trace_id,evaluation_type,evaluation_source,evaluation_id) DO UPDATE SET
                     score=EXCLUDED.score,outcome=EXCLUDED.outcome,metadata=EXCLUDED.metadata""",
                trace_id, run["vertical"], row["id"], evaluation["overall_score"],
                evaluation["gate_status"], user_id,
                json.dumps({"run_id": str(run["run_id"]), "passed": bool(evaluation["passed"]),
                            "metric_count": len(evaluation.get("metrics") or [])}),
            )
    return {"id": str(row["id"]), "created_at": row["created_at"].isoformat()}


def _evaluation_response(evaluation: dict[str, Any], saved: dict[str, Any] | None) -> dict[str, Any]:
    return {
        **evaluation,
        "evaluation_id": saved["id"] if saved else None,
        "created_at": saved["created_at"] if saved else None,
        "persisted": bool(saved),
    }


def _row_response(row) -> dict[str, Any]:
    return {
        "evaluation_id": str(row["id"]),
        "vertical": row["vertical"],
        "run_id": str(row["run_id"]),
        "document_id": str(row["document_id"]),
        "evaluator_version": row["evaluator_version"],
        "overall_score": row["overall_score"],
        "passed": row["passed"],
        "gate_status": row["gate_status"],
        "metrics": _json(row["metrics"]) or [],
        "recommendations": _json(row["recommendations"]) or [],
        "policy": _json(row["policy"]) or {},
        "metadata": _json(row["metadata"]) or {},
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def _step(row) -> dict[str, Any]:
    return {
        "agent_name": row["agent_name"],
        "status": row["status"],
        "input_summary": row["input_summary"],
        "output": _json(row["output_data"]) if row["output_data"] else None,
        "error_message": row["error_message"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }


def _obligation(row) -> dict[str, Any]:
    return {
        **dict(row),
        "due_date": row["due_date"].isoformat() if row["due_date"] else None,
    }


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _normalize_vertical(vertical: str) -> str:
    return (vertical or "").strip().lower()
