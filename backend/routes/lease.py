from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.adk_workflow import WorkflowConfigError, run_multi_agent_workflow
from services.audit import audit, ip_from, ua_from
from services.lease_agent_tools import LEASE_AGENT_TOOLS
from services.lease_intelligence import (
    LeaseIntelligenceError,
    build_lease_context,
    compare_lease_documents,
)
from services.usage import check_and_log_daily_event, log_event
import services.storage as gcs


router = APIRouter()
log = logging.getLogger("docintel.lease.route")


class LeaseCompareRequest(BaseModel):
    base_document_id: str
    amendment_document_id: str


class LeaseAgentWorkflowRequest(BaseModel):
    amendment_document_id: str | None = None


class LeaseApprovalRequest(BaseModel):
    approved_abstract: dict | None = None
    notes: str | None = None


@router.get("/{doc_id}/abstract")
async def get_lease_abstract(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _get_accessible_doc(db, doc_id, str(current_user["id"]))
    row = await db.fetchrow(
        """
        SELECT abstract_data, confidence, status, created_at, updated_at
        FROM lease_abstracts
        WHERE document_id=$1
        """,
        doc_id,
    )
    if not row:
        return {"document_id": doc_id, "abstract": None}
    return {
        "document_id": doc_id,
        "abstract": _json(row["abstract_data"]),
        "confidence": row["confidence"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
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
        raise HTTPException(404, "Agent run not found")
    return await _fresh_agent_run_response(db, str(run["id"]))


@router.get("/{doc_id}/agent-workflow/latest")
async def get_latest_agent_workflow(doc_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _get_accessible_doc(db, doc_id, user_id)
    run = await db.fetchrow(
        """
        SELECT r.*
        FROM lease_agent_runs r
        WHERE r.document_id=$1
          AND (
            r.user_id=$2
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
              WHERE wm.workspace_id=r.workspace_id
                AND wm.user_id=$2
            )
          )
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        doc_id,
        user_id,
    )
    if not run:
        return {"document_id": doc_id, "agent_run": None}
    return {"document_id": doc_id, "agent_run": await _fresh_agent_run_response(db, str(run["id"]))}


@router.post("/{doc_id}/agent-workflow")
async def run_agent_workflow(
    doc_id: str,
    body: LeaseAgentWorkflowRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    doc = await _get_accessible_doc(db, doc_id, user_id)
    if doc["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document must be chunked before running the lease agent workflow")

    amendment = None
    if body.amendment_document_id:
        amendment = await _get_accessible_doc(db, body.amendment_document_id, user_id)
        if amendment["status"] not in ("chunked", "embedding", "embedded"):
            raise HTTPException(400, f"{amendment['original_name']} must be chunked before amendment comparison")
    await check_and_log_daily_event(
        db,
        user_id,
        "lease_ai",
        "max_lease_ai_day",
        metadata={
            "action": "lease_agent_workflow",
            "doc_id": doc_id,
            "amendment_document_id": body.amendment_document_id,
        },
    )

    run = await db.fetchrow(
        """
        INSERT INTO lease_agent_runs
          (document_id, amendment_document_id, user_id, workspace_id, status, workflow_version)
        VALUES ($1,$2,$3,$4,'running','phase2-adk-v1')
        RETURNING *
        """,
        doc_id,
        body.amendment_document_id,
        user_id,
        doc.get("workspace_id") or (amendment or {}).get("workspace_id"),
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_agent_workflow_background,
        run_id,
        doc_id,
        body.amendment_document_id,
        user_id,
        ip_from(request),
        ua_from(request),
    )
    return await _fresh_agent_run_response(db, run_id)


@router.post("/agent-runs/{run_id}/approve")
async def approve_agent_run(
    run_id: str,
    body: LeaseApprovalRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
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
        raise HTTPException(404, "Agent run not found")

    result_data = _json(run["result_data"]) or {}
    approved_abstract = body.approved_abstract or result_data.get("approved_abstract") or result_data.get("abstract")
    if not approved_abstract:
        raise HTTPException(400, "No abstract available to approve")

    doc = await _get_accessible_doc(db, str(run["document_id"]), user_id)
    await _save_lease_abstract(db, doc, user_id, approved_abstract, status="approved")
    await db.execute(
        """
        UPDATE lease_agent_runs
        SET status='approved',
            approved_by=$2,
            approved_at=NOW(),
            approval_notes=$3,
            result_data=jsonb_set(COALESCE(result_data, '{}'::jsonb), '{approved_abstract}', $4::jsonb, true),
            updated_at=NOW()
        WHERE id=$1
        """,
        run_id,
        user_id,
        body.notes,
        json.dumps(approved_abstract),
    )
    await db.execute("UPDATE lease_obligations SET approved=TRUE WHERE run_id=$1", run_id)
    existing_obligations = await _get_obligations(db, str(run["document_id"]), run_id=run_id, include_result_fallback=False)
    if not existing_obligations:
        checklist = result_data.get("obligation_checklist") or {}
        await _replace_obligations(db, doc, user_id, run_id, checklist.get("obligations", []), approved=True)
    await audit(
        db,
        user_id=user_id,
        action="lease_agent_approve",
        resource_type="document",
        resource_id=str(run["document_id"]),
        metadata={"run_id": run_id},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return await _fresh_agent_run_response(db, run_id)


@router.post("/{doc_id}/extract")
async def extract_lease(
    doc_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    doc = await _get_accessible_doc(db, doc_id, user_id)
    if doc["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Document must be chunked before lease extraction")
    await check_and_log_daily_event(
        db,
        user_id,
        "lease_ai",
        "max_lease_ai_day",
        metadata={"action": "lease_extract", "doc_id": doc_id},
    )

    chunks = await _load_doc_chunks(db, doc, user_id)
    context = build_lease_context(doc["original_name"], chunks)
    try:
        result = await _extract_lease_abstract_with_retries(db, doc, user_id, context)
    except LeaseIntelligenceError as exc:
        raise HTTPException(502, str(exc)) from exc

    await log_event(db, user_id, "lease_extract", metadata={"doc_id": doc_id})
    await audit(
        db,
        user_id=user_id,
        action="lease_extract",
        resource_type="document",
        resource_id=doc_id,
        metadata={"document_kind": result.get("document_kind"), "confidence": result.get("confidence")},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {"document_id": doc_id, "abstract": result}


@router.post("/compare")
async def compare_leases(
    body: LeaseCompareRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    base = await _get_accessible_doc(db, body.base_document_id, user_id)
    amendment = await _get_accessible_doc(db, body.amendment_document_id, user_id)

    for doc in (base, amendment):
        if doc["status"] not in ("chunked", "embedding", "embedded"):
            raise HTTPException(400, f"{doc['original_name']} must be chunked before lease comparison")

    await check_and_log_daily_event(
        db,
        user_id,
        "lease_ai",
        "max_lease_ai_day",
        metadata={
            "action": "lease_compare",
            "base_document_id": body.base_document_id,
            "amendment_document_id": body.amendment_document_id,
        },
    )

    base_context = build_lease_context(base["original_name"], await _load_doc_chunks(db, base, user_id), max_chars=16000)
    amendment_context = build_lease_context(
        amendment["original_name"],
        await _load_doc_chunks(db, amendment, user_id),
        max_chars=16000,
    )
    try:
        result = await compare_lease_documents(
            base["original_name"],
            base_context,
            amendment["original_name"],
            amendment_context,
        )
    except LeaseIntelligenceError as exc:
        raise HTTPException(502, str(exc)) from exc
    row = await db.fetchrow(
        """
        INSERT INTO lease_comparisons
          (base_document_id, amendment_document_id, user_id, workspace_id, comparison_data)
        VALUES ($1,$2,$3,$4,$5::jsonb)
        RETURNING id, created_at
        """,
        body.base_document_id,
        body.amendment_document_id,
        user_id,
        base.get("workspace_id") or amendment.get("workspace_id"),
        json.dumps(result),
    )
    await log_event(db, user_id, "lease_compare", metadata={
        "base_document_id": body.base_document_id,
        "amendment_document_id": body.amendment_document_id,
    })
    await audit(
        db,
        user_id=user_id,
        action="lease_compare",
        resource_type="document",
        resource_id=body.base_document_id,
        metadata={"amendment_document_id": body.amendment_document_id, "confidence": result.get("confidence")},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {
        "comparison_id": str(row["id"]),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "comparison": result,
    }


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


async def _get_saved_lease_abstract(db, doc_id: str) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT abstract_data, status, updated_at
        FROM lease_abstracts
        WHERE document_id=$1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        doc_id,
    )
    if not row:
        log.info("No saved lease abstract found for document_id=%s; agent workflow will extract fresh abstract", doc_id)
        return None
    abstract = _json(row["abstract_data"]) or {}
    abstract["agent_source"] = "saved_lease_abstract"
    abstract["agent_source_status"] = row["status"]
    abstract["agent_source_updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
    log.info(
        "Using saved lease abstract for document_id=%s status=%s updated_at=%s",
        doc_id,
        row["status"],
        row["updated_at"],
    )
    return abstract


async def _extract_lease_abstract_with_retries(db, doc: dict, user_id: str, context: str) -> dict:
    max_attempts = 3
    doc_id = str(doc["id"])
    best = await _get_saved_lease_abstract(db, doc_id)
    last_error: LeaseIntelligenceError | None = None
    tool = LEASE_AGENT_TOOLS["lease.extract_abstract"]

    for attempt in range(1, max_attempts + 1):
        workflow_context = {
            "document_id": doc_id,
            "document_name": doc["original_name"],
            "document_context": context,
            "existing_abstract": best,
        }
        agent = {
            "id": "lease_abstraction",
            "name": f"Lease Abstraction Agent (extract attempt {attempt}/{max_attempts})",
            "input_summary": "Extract structured lease abstract with citations.",
            "attempt": attempt,
            "max_attempts": max_attempts,
            "previous_output": best,
        }
        try:
            best = await tool(workflow_context, {"abstract": best} if best else {}, agent)
            quality = best.get("agent_quality") if isinstance(best.get("agent_quality"), dict) else {}
            status = "ready" if quality.get("complete", True) else "partial"
            best["extract_attempts"] = attempt
            await _save_lease_abstract(db, doc, user_id, best, status=status)
            log.info(
                "Lease abstract extract attempt %s/%s doc_id=%s status=%s missing=%s",
                attempt,
                max_attempts,
                doc_id,
                status,
                quality.get("missing") or [],
            )
            if quality.get("complete", True):
                return best
        except LeaseIntelligenceError as exc:
            last_error = exc
            if best:
                best["extract_attempts"] = attempt
                best["last_extract_error"] = str(exc)
                await _save_lease_abstract(db, doc, user_id, best, status="partial")
                log.warning("Lease abstract attempt %s/%s failed; saved partial state doc_id=%s: %s", attempt, max_attempts, doc_id, exc)
                continue
            raise

    if best:
        return best
    raise last_error or LeaseIntelligenceError("Lease abstract extraction failed")


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


async def _save_lease_abstract(db, doc: dict, user_id: str, result: dict, status: str = "ready") -> None:
    doc_id = str(doc["id"])
    workspace_id = doc.get("workspace_id")
    await db.execute("DELETE FROM lease_critical_dates WHERE document_id=$1", doc_id)
    await db.execute("DELETE FROM lease_clause_flags WHERE document_id=$1", doc_id)
    await db.execute(
        """
        INSERT INTO lease_abstracts
          (document_id, user_id, workspace_id, abstract_data, confidence, status, updated_at)
        VALUES ($1,$2,$3,$4::jsonb,$5,$6,NOW())
        ON CONFLICT (document_id) DO UPDATE SET
          abstract_data=EXCLUDED.abstract_data,
          confidence=EXCLUDED.confidence,
          status=EXCLUDED.status,
          updated_at=NOW()
        """,
        doc_id,
        user_id,
        workspace_id,
        json.dumps(result),
        result.get("confidence"),
        status,
    )
    for item in result.get("critical_dates", []):
        await db.execute(
            """
            INSERT INTO lease_critical_dates
              (document_id, user_id, workspace_id, date_type, date_value, raw_value,
               description, responsible_party, source, confidence)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
            doc_id,
            user_id,
            workspace_id,
            item.get("date_type") or "other",
            _date_or_none(item.get("date_value")),
            item.get("raw_value"),
            item.get("description"),
            item.get("responsible_party"),
            item.get("source"),
            item.get("confidence"),
        )
    for item in result.get("clause_flags", []):
        await db.execute(
            """
            INSERT INTO lease_clause_flags
              (document_id, user_id, workspace_id, clause_type, status, risk_level,
               finding, source, confidence)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            doc_id,
            user_id,
            workspace_id,
            item.get("clause_type") or "unknown",
            item.get("status") or "ambiguous",
            item.get("risk_level") or "unknown",
            item.get("finding"),
            item.get("source"),
            item.get("confidence"),
        )


def _date_or_none(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _run_agent_step(db, run_id: str, agent_name: str, input_summary: str, agent_call):
    step = await db.fetchrow(
        """
        INSERT INTO lease_agent_steps (run_id, agent_name, status, input_summary)
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
            UPDATE lease_agent_steps
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
            UPDATE lease_agent_steps
            SET status='failed',
                error_message=$2,
                completed_at=NOW()
            WHERE id=$1
            """,
            step_id,
            str(exc)[:1000],
        )
        raise


async def _execute_agent_workflow_background(
    run_id: str,
    doc_id: str,
    amendment_document_id: str | None,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        try:
            doc = await _get_accessible_doc(db, doc_id, user_id)
            document_context = build_lease_context(
                doc["original_name"],
                await _load_doc_chunks(db, doc, user_id),
                max_chars=30000,
            )
            workflow_context = {
                "document_id": doc_id,
                "document_name": doc["original_name"],
                "document_context": document_context,
                "existing_abstract": await _get_saved_lease_abstract(db, doc_id),
                "amendment_document_id": amendment_document_id,
            }
            if amendment_document_id:
                amendment = await _get_accessible_doc(db, amendment_document_id, user_id)
                workflow_context["base_compare_context"] = build_lease_context(
                    doc["original_name"],
                    await _load_doc_chunks(db, doc, user_id),
                    max_chars=16000,
                )
                workflow_context["amendment_document_name"] = amendment["original_name"]
                workflow_context["amendment_context"] = build_lease_context(
                    amendment["original_name"],
                    await _load_doc_chunks(db, amendment, user_id),
                    max_chars=16000,
                )

            workflow = await run_multi_agent_workflow(
                "lease_phase2",
                workflow_context,
                LEASE_AGENT_TOOLS,
                lambda agent, agent_call: _run_agent_step(
                    db,
                    run_id,
                    agent.get("name") or agent.get("id") or "Agent",
                    agent.get("input_summary") or "",
                    agent_call,
                ),
            )
            merged = workflow["result"]
            obligations = workflow["outputs"].get("obligation_checklist") or {}
            await db.execute(
                """
                UPDATE lease_agent_runs
                SET status='pending_approval',
                    result_data=$2::jsonb,
                    completed_at=NOW(),
                    updated_at=NOW()
                WHERE id=$1
                """,
                run_id,
                json.dumps(merged),
            )
            saved_count = await _replace_obligations(db, doc, user_id, run_id, obligations.get("obligations", []), approved=False)
            log.info("Saved %d lease obligations for run_id=%s doc_id=%s", saved_count, run_id, doc_id)
            await log_event(db, user_id, "lease_agent_workflow", metadata={"doc_id": doc_id, "run_id": run_id})
            await audit(
                db,
                user_id=user_id,
                action="lease_agent_workflow",
                resource_type="document",
                resource_id=doc_id,
                metadata={"run_id": run_id, "amendment_document_id": amendment_document_id},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except LeaseIntelligenceError as exc:
            log.warning("Lease agent workflow failed with model output error run_id=%s doc_id=%s: %s", run_id, doc_id, exc)
            await _fail_agent_run(db, run_id, str(exc))
        except WorkflowConfigError as exc:
            log.error("Lease agent workflow config error run_id=%s doc_id=%s: %s", run_id, doc_id, exc)
            await _fail_agent_run(db, run_id, str(exc))
        except Exception as exc:
            log.exception("Lease agent workflow crashed run_id=%s doc_id=%s", run_id, doc_id)
            await _fail_agent_run(db, run_id, str(exc))


async def _fail_agent_run(db, run_id: str, error_message: str) -> None:
    await db.execute(
        """
        UPDATE lease_agent_runs
        SET status='failed',
            error_message=$2,
            completed_at=NOW(),
            updated_at=NOW()
        WHERE id=$1
        """,
        run_id,
        error_message[:1500],
    )


async def _replace_obligations(
    db,
    doc: dict,
    user_id: str,
    run_id: str,
    obligations: list[dict],
    approved: bool,
) -> int:
    await db.execute("DELETE FROM lease_obligations WHERE run_id=$1", run_id)
    saved = 0
    for item in obligations:
        await db.execute(
            """
            INSERT INTO lease_obligations
              (run_id, document_id, user_id, workspace_id, title, party, category,
               priority, due_date, "trigger", source, status, notes, approved)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            """,
            run_id,
            str(doc["id"]),
            user_id,
            doc.get("workspace_id"),
            item.get("title"),
            item.get("party") or "unknown",
            item.get("category") or "other",
            item.get("priority") or "medium",
            _date_or_none(item.get("due_date")),
            item.get("trigger"),
            item.get("source"),
            item.get("status") or "open",
            item.get("notes"),
            approved,
        )
        saved += 1
    return saved


async def _get_obligations(
    db,
    doc_id: str,
    run_id: str | None = None,
    include_result_fallback: bool = True,
) -> list[dict]:
    where = "document_id=$1"
    params = [doc_id]
    if run_id:
        where += " AND run_id=$2"
        params.append(run_id)
    rows = await db.fetch(
        f"""
        SELECT id, run_id, title, party, category, priority, due_date, "trigger",
               source, status, notes, approved, created_at
        FROM lease_obligations
        WHERE {where}
        ORDER BY approved DESC, due_date NULLS LAST, priority DESC, created_at DESC
        """,
        *params,
    )
    saved = [
        {
            **dict(row),
            "id": str(row["id"]),
            "run_id": str(row["run_id"]),
            "due_date": row["due_date"].isoformat() if row["due_date"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]
    if saved or not include_result_fallback:
        return saved

    run = await db.fetchrow(
        """
        SELECT id, result_data, created_at
        FROM lease_agent_runs
        WHERE document_id=$1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        doc_id,
    )
    if not run:
        return []
    result = _json(run["result_data"]) or {}
    obligations = (result.get("obligation_checklist") or {}).get("obligations") or []
    return [
        {
            **item,
            "id": f"result-{idx}",
            "run_id": str(run["id"]),
            "approved": False,
            "created_at": run["created_at"].isoformat() if run["created_at"] else None,
        }
        for idx, item in enumerate(obligations)
        if isinstance(item, dict)
    ]


async def _fresh_agent_run_response(db, run_id: str) -> dict:
    fresh = await db.fetchrow("SELECT * FROM lease_agent_runs WHERE id=$1", run_id)
    if not fresh:
        raise HTTPException(404, "Agent run not found")
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
    return _agent_run_response(dict(fresh), steps, await _get_obligations(db, str(fresh["document_id"])))


def _agent_run_response(run: dict, steps, obligations: list[dict]) -> dict:
    return {
        "run_id": str(run["id"]),
        "document_id": str(run["document_id"]),
        "amendment_document_id": str(run["amendment_document_id"]) if run.get("amendment_document_id") else None,
        "status": run["status"],
        "workflow_version": run.get("workflow_version"),
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
        "obligations": obligations,
    }
