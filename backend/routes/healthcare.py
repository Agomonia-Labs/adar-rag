from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.adk_workflow import WorkflowConfigError, load_workflow_config, run_multi_agent_workflow
from services.audit import audit, ip_from, ua_from
from services.healthcare_agent_tools import HEALTHCARE_AGENT_TOOLS
from services.healthcare_intelligence import HealthcareIntelligenceError, build_healthcare_context
from services.usage import check_and_log_daily_event, log_event
from services.vertical_agent_runs import (
    approve_vertical_run,
    complete_vertical_run,
    create_vertical_run,
    fail_vertical_run,
    get_accessible_vertical_run,
    latest_vertical_run,
    run_vertical_step,
    vertical_run_response,
)
import services.storage as gcs


router = APIRouter()
log = logging.getLogger("docintel.healthcare.route")

HEALTHCARE_WORKFLOW_ID = "healthcare_phase1"
HEALTHCARE_VERTICAL = "healthcare"


class HealthcareApprovalRequest(BaseModel):
    approved_packet: dict | None = None
    notes: str | None = None


@router.get("/agent-runs/{run_id}")
async def get_healthcare_agent_run(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    run = await get_accessible_vertical_run(db, run_id, str(current_user["id"]))
    if run.get("vertical") != HEALTHCARE_VERTICAL:
        raise HTTPException(404, "Healthcare agent run not found")
    return await vertical_run_response(db, run)


@router.get("/{doc_id}/agent-workflow/latest")
async def get_latest_healthcare_workflow(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _get_accessible_doc(db, doc_id, user_id)
    run = await latest_vertical_run(db, document_id=doc_id, vertical=HEALTHCARE_VERTICAL, user_id=user_id)
    if not run:
        return {"document_id": doc_id, "agent_run": None}
    return {"document_id": doc_id, "agent_run": await vertical_run_response(db, run)}


@router.post("/{doc_id}/agent-workflow")
async def run_healthcare_agent_workflow(
    doc_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    doc = await _get_accessible_doc(db, doc_id, user_id)
    if doc["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document must be chunked before running the healthcare agent workflow")

    await check_and_log_daily_event(
        db,
        user_id,
        "healthcare_ai",
        "max_healthcare_ai_day",
        metadata={"action": "healthcare_agent_workflow", "doc_id": doc_id},
    )

    config = load_workflow_config(HEALTHCARE_WORKFLOW_ID)
    run = await create_vertical_run(
        db,
        workflow_id=HEALTHCARE_WORKFLOW_ID,
        workflow_version=config.get("version") or "healthcare-adk-v1",
        vertical=HEALTHCARE_VERTICAL,
        document_id=doc_id,
        user_id=user_id,
        workspace_id=doc.get("workspace_id"),
        input_data={
            "document_name": doc["original_name"],
            "doc_type": doc.get("doc_type"),
            "doc_domain": doc.get("doc_domain"),
        },
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_healthcare_workflow_background,
        run_id,
        doc_id,
        user_id,
        ip_from(request),
        ua_from(request),
    )
    return await vertical_run_response(db, run)


@router.post("/agent-runs/{run_id}/approve")
async def approve_healthcare_agent_run(
    run_id: str,
    body: HealthcareApprovalRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL:
        raise HTTPException(404, "Healthcare agent run not found")

    result_data = _json(run.get("result_data")) or {}
    approved_packet = body.approved_packet or result_data.get("approved_packet")
    if not approved_packet:
        raise HTTPException(400, "No healthcare packet available to approve")

    await approve_vertical_run(
        db,
        run_id=run_id,
        user_id=user_id,
        approved_packet=approved_packet,
        notes=body.notes,
    )
    await audit(
        db,
        user_id=user_id,
        action="healthcare_agent_approve",
        resource_type="document",
        resource_id=str(run["document_id"]),
        metadata={"run_id": run_id},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    fresh = await get_accessible_vertical_run(db, run_id, user_id)
    return await vertical_run_response(db, fresh)


async def _execute_healthcare_workflow_background(
    run_id: str,
    doc_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        try:
            doc = await _get_accessible_doc(db, doc_id, user_id)
            document_context = build_healthcare_context(
                doc["original_name"],
                await _load_doc_chunks(db, doc, user_id),
                max_chars=32000,
            )
            workflow_context = {
                "document_id": doc_id,
                "document_name": doc["original_name"],
                "document_context": document_context,
                "doc_type": doc.get("doc_type"),
                "doc_domain": doc.get("doc_domain"),
            }
            workflow = await run_multi_agent_workflow(
                HEALTHCARE_WORKFLOW_ID,
                workflow_context,
                HEALTHCARE_AGENT_TOOLS,
                lambda agent, agent_call: run_vertical_step(
                    db,
                    run_id,
                    agent.get("name") or agent.get("id") or "Agent",
                    agent.get("input_summary") or "",
                    agent_call,
                ),
            )
            await complete_vertical_run(db, run_id, workflow["result"], status="pending_approval")
            await log_event(db, user_id, "healthcare_agent_workflow", metadata={"doc_id": doc_id, "run_id": run_id})
            await audit(
                db,
                user_id=user_id,
                action="healthcare_agent_workflow",
                resource_type="document",
                resource_id=doc_id,
                metadata={"run_id": run_id, "workflow_id": HEALTHCARE_WORKFLOW_ID},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except (HealthcareIntelligenceError, WorkflowConfigError) as exc:
            log.warning("Healthcare agent workflow failed run_id=%s doc_id=%s: %s", run_id, doc_id, exc)
            await fail_vertical_run(db, run_id, str(exc))
        except Exception as exc:
            log.exception("Healthcare agent workflow crashed run_id=%s doc_id=%s", run_id, doc_id)
            await fail_vertical_run(db, run_id, str(exc))


async def _get_accessible_doc(db, doc_id: str, user_id: str) -> dict:
    row = await db.fetchrow(
        """SELECT d.* FROM documents d
           WHERE d.id=$1
             AND d.status != 'deleted'
             AND (
               d.user_id=$2
               OR EXISTS (
                 SELECT 1 FROM workspace_members wm
                 WHERE wm.workspace_id=d.workspace_id
                   AND wm.user_id=$2
               )
             )""",
        doc_id,
        user_id,
    )
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)


async def _load_doc_chunks(db, doc: dict, user_id: str) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT chunk_index, content
        FROM document_chunks
        WHERE document_id=$1
        ORDER BY chunk_index
        """,
        doc["id"],
    )
    if rows:
        return [dict(r) for r in rows]

    try:
        owner_id = str(doc.get("user_id") or user_id)
        meta = await gcs.download_json(gcs.metadata_path(owner_id, str(doc["id"])))
        chunks = []
        for item in meta.get("chunks", []):
            content = await gcs.download_text(item["gcs_path"])
            chunks.append({"chunk_index": item["index"], "content": content})
        return chunks
    except Exception as exc:
        raise HTTPException(500, f"Could not load document chunks: {exc}")


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value
