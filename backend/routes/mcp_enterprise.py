from __future__ import annotations

import json
import ipaddress
import secrets
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.mcp_enterprise import emit_event, normalize_citations

router = APIRouter()
CONTRACT_VERSION = "2026-08-26"

WORKFLOWS = {
    "healthcare_clinical": {"version": "1.0", "vertical": "healthcare", "required": ["document_ids"], "review": True, "packets": ["after_visit_summary"]},
    "healthcare_prior_auth": {"version": "1.0", "vertical": "healthcare", "required": ["document_ids", "policy_document_ids"], "review": True, "packets": ["prior_auth", "missing_information"]},
    "finance_tax_readiness": {"version": "2.0", "vertical": "finance_tax", "required": ["document_ids"], "review": True, "packets": ["advisor"]},
    "talent_readiness": {"version": "1.0", "vertical": "talent", "required": ["resume_document_ids", "job_description_id"], "review": True, "packets": ["candidate"]},
    "employee_mobility": {"version": "1.0", "vertical": "talent", "required": ["resume_document_ids", "job_description_id"], "review": True, "packets": ["mobility"]},
    "lease_intelligence": {"version": "1.0", "vertical": "lease", "required": ["document_ids"], "review": True, "packets": []},
}


class SubscriptionInput(BaseModel):
    event_types: list[str] = Field(default_factory=list, max_length=50)
    workspace_id: str | None = None
    resource_type: str | None = Field(default=None, max_length=80)
    resource_id: str | None = Field(default=None, max_length=200)
    webhook_url: str | None = None


class ReviewTaskInput(BaseModel):
    vertical: str
    run_id: str
    title: str = Field(min_length=2, max_length=240)
    workspace_id: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    due_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewDecisionInput(BaseModel):
    decision: Literal["approved", "changes_requested", "rejected"]
    reviewer_notes: str = Field(default="", max_length=5000)


class ArtifactInput(BaseModel):
    artifact_type: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=240)
    content: dict[str, Any]
    workspace_id: str | None = None
    source_document_ids: list[str] = Field(default_factory=list, max_length=500)
    source_trace_id: str | None = None
    status: Literal["draft", "reviewed", "approved"] = "draft"


class VersionInput(BaseModel):
    previous_document_id: str | None = None
    change_summary: str = Field(default="", max_length=2000)
    changed_pages: list[int] = Field(default_factory=list, max_length=5000)


class EvaluationInput(BaseModel):
    trace_id: str
    evaluation_type: str = "groundedness"


@router.get("/catalog")
async def enterprise_catalog(current_user: CurrentUser):
    return {"contract_version": CONTRACT_VERSION, "capabilities": ["events", "idempotency", "reviews", "artifacts", "versions", "evaluations", "service_oauth"], "workflows": WORKFLOWS}


@router.get("/workflows/{workflow}")
async def workflow_schema(workflow: str, current_user: CurrentUser):
    if workflow not in WORKFLOWS:
        raise HTTPException(404, "Workflow not found")
    return {"workflow": workflow, **WORKFLOWS[workflow], "contract_version": CONTRACT_VERSION}


@router.post("/workflows/{workflow}/validate")
async def validate_workflow(workflow: str, payload: dict[str, Any], current_user: CurrentUser):
    definition = WORKFLOWS.get(workflow)
    if not definition:
        raise HTTPException(404, "Workflow not found")
    missing = [field for field in definition["required"] if not payload.get(field)]
    return {"valid": not missing, "missing": missing, "workflow": workflow, "version": definition["version"]}


@router.get("/events")
async def list_events(current_user: CurrentUser, db=Depends(get_db), after: int = 0, resource_type: str | None = None, resource_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
    rows = await db.fetch(
        """SELECT * FROM mcp_events WHERE user_id=$1::uuid AND sequence_number>$2
           AND ($3::text IS NULL OR resource_type=$3) AND ($4::text IS NULL OR resource_id=$4)
           ORDER BY sequence_number LIMIT $5""", str(current_user["id"]), after, resource_type, resource_id, limit,
    )
    return {"events": [_serialize(row) for row in rows], "next_sequence": max([after, *[int(row["sequence_number"]) for row in rows]])}


@router.post("/subscriptions")
async def create_subscription(body: SubscriptionInput, current_user: CurrentUser, db=Depends(get_db)):
    await _require_workspace_access(db, body.workspace_id, str(current_user["id"]))
    if body.webhook_url:
        parsed = urlparse(body.webhook_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HTTPException(400, "webhook_url must use HTTPS")
        if parsed.hostname.lower() in {"localhost", "metadata.google.internal"}:
            raise HTTPException(400, "webhook_url host is not allowed")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                raise HTTPException(400, "webhook_url cannot target a private address")
        except ValueError:
            pass
    webhook_secret = secrets.token_urlsafe(32) if body.webhook_url else None
    row = await db.fetchrow(
        """INSERT INTO mcp_event_subscriptions(user_id,workspace_id,event_types,resource_type,resource_id,webhook_url,webhook_secret)
           VALUES($1::uuid,$2::uuid,$3::jsonb,$4,$5,$6,$7) RETURNING *""",
        str(current_user["id"]), body.workspace_id, json.dumps(body.event_types), body.resource_type, body.resource_id, body.webhook_url, webhook_secret,
    )
    result = _serialize(row)
    if webhook_secret:
        result["webhook_secret"] = webhook_secret
        result["warning"] = "The webhook secret is shown once. Store it securely."
    return result


@router.get("/subscriptions")
async def list_subscriptions(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM mcp_event_subscriptions WHERE user_id=$1::uuid ORDER BY created_at DESC", str(current_user["id"]))
    values = []
    for row in rows:
        item = _serialize(row)
        item.pop("webhook_secret", None)
        values.append(item)
    return {"subscriptions": values}


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription(subscription_id: str, current_user: CurrentUser, db=Depends(get_db)):
    result = await db.execute("DELETE FROM mcp_event_subscriptions WHERE id=$1::uuid AND user_id=$2::uuid", subscription_id, str(current_user["id"]))
    if result.endswith("0"):
        raise HTTPException(404, "Subscription not found")
    return {"deleted": True, "subscription_id": subscription_id}


@router.post("/reviews")
async def create_review_task(body: ReviewTaskInput, current_user: CurrentUser, db=Depends(get_db)):
    await _require_workspace_access(db, body.workspace_id, str(current_user["id"]))
    row = await db.fetchrow(
        """INSERT INTO mcp_review_tasks(user_id,workspace_id,vertical,run_id,title,priority,due_at,metadata)
           VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::jsonb)
           ON CONFLICT(vertical,run_id,task_type) DO UPDATE SET title=EXCLUDED.title,priority=EXCLUDED.priority,due_at=EXCLUDED.due_at,updated_at=NOW()
           RETURNING *""", str(current_user["id"]), body.workspace_id, body.vertical, body.run_id, body.title, body.priority, body.due_at, json.dumps(body.metadata),
    )
    await emit_event(db, user_id=str(current_user["id"]), workspace_id=body.workspace_id, event_type="workflow.review_required", resource_type="review_task", resource_id=str(row["id"]), payload={"run_id": body.run_id, "status": "pending"})
    return _serialize(row)


@router.get("/reviews")
async def list_review_tasks(current_user: CurrentUser, db=Depends(get_db), status: str | None = None, limit: int = Query(100, ge=1, le=500)):
    rows = await db.fetch("""SELECT * FROM mcp_review_tasks WHERE (user_id=$1::uuid OR assigned_to=$1::uuid)
      AND ($2::text IS NULL OR status=$2) ORDER BY created_at DESC LIMIT $3""", str(current_user["id"]), status, limit)
    return {"tasks": [_serialize(row) for row in rows]}


@router.post("/reviews/{task_id}/assign")
async def assign_review_task(task_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await db.fetchrow("""UPDATE mcp_review_tasks SET assigned_to=$2::uuid,status='in_review',updated_at=NOW()
      WHERE id=$1::uuid AND (user_id=$2::uuid OR assigned_to=$2::uuid OR workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=$2::uuid)) RETURNING *""", task_id, str(current_user["id"]))
    if not row: raise HTTPException(404, "Review task not found")
    return _serialize(row)


@router.post("/reviews/{task_id}/decision")
async def decide_review_task(task_id: str, body: ReviewDecisionInput, current_user: CurrentUser, db=Depends(get_db)):
    status = "completed" if body.decision == "approved" else "changes_requested" if body.decision == "changes_requested" else "rejected"
    row = await db.fetchrow("""UPDATE mcp_review_tasks SET assigned_to=$2::uuid,status=$3,decision=$4,reviewer_notes=$5,
      completed_at=CASE WHEN $3='completed' THEN NOW() ELSE NULL END,updated_at=NOW()
      WHERE id=$1::uuid AND (user_id=$2::uuid OR assigned_to=$2::uuid OR workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=$2::uuid)) RETURNING *""",
      task_id, str(current_user["id"]), status, body.decision, body.reviewer_notes)
    if not row: raise HTTPException(404, "Review task not found")
    await emit_event(db, user_id=str(row["user_id"]), workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None, event_type=f"review.{body.decision}", resource_type="review_task", resource_id=task_id, payload={"run_id": row["run_id"], "status": status})
    return _serialize(row)


@router.post("/artifacts")
async def create_artifact(body: ArtifactInput, current_user: CurrentUser, db=Depends(get_db)):
    await _require_workspace_access(db, body.workspace_id, str(current_user["id"]))
    for document_id in body.source_document_ids:
        await _require_document_access(db, document_id, str(current_user["id"]))
    if body.source_trace_id and not await db.fetchval(
        "SELECT 1 FROM trace_flows WHERE trace_id=$1 AND user_id=$2::uuid",
        body.source_trace_id, str(current_user["id"]),
    ):
        raise HTTPException(404, "Source trace not found")
    row = await db.fetchrow("""INSERT INTO knowledge_artifacts(user_id,workspace_id,artifact_type,title,content,source_document_ids,source_trace_id,status)
      VALUES($1::uuid,$2::uuid,$3,$4,$5::jsonb,$6::jsonb,$7,$8) RETURNING *""", str(current_user["id"]), body.workspace_id, body.artifact_type, body.title, json.dumps(body.content), json.dumps(body.source_document_ids), body.source_trace_id, body.status)
    return _serialize(row)


@router.get("/artifacts")
async def list_artifacts(current_user: CurrentUser, db=Depends(get_db), workspace_id: str | None = None):
    rows = await db.fetch("""SELECT * FROM knowledge_artifacts WHERE user_id=$1::uuid AND ($2::uuid IS NULL OR workspace_id=$2::uuid) ORDER BY updated_at DESC LIMIT 200""", str(current_user["id"]), workspace_id)
    return {"artifacts": [_serialize(row) for row in rows]}


@router.post("/documents/{document_id}/versions")
async def register_document_version(document_id: str, body: VersionInput, current_user: CurrentUser, db=Depends(get_db)):
    await _require_document_access(db, document_id, str(current_user["id"]))
    if body.previous_document_id:
        await _require_document_access(db, body.previous_document_id, str(current_user["id"]))
    previous = body.previous_document_id or document_id
    root = await db.fetchval("SELECT root_document_id FROM document_versions WHERE document_id=$1::uuid", previous) or previous
    if body.previous_document_id and not await db.fetchval("SELECT 1 FROM document_versions WHERE document_id=$1::uuid", previous):
        await db.execute("""INSERT INTO document_versions(document_id,root_document_id,version_number,change_summary,created_by)
          VALUES($1::uuid,$1::uuid,1,'Baseline version',$2::uuid) ON CONFLICT(document_id) DO NOTHING""", previous, str(current_user["id"]))
    version = await db.fetchval("SELECT COALESCE(MAX(version_number),0)+1 FROM document_versions WHERE root_document_id=$1::uuid", root)
    row = await db.fetchrow("""INSERT INTO document_versions(document_id,root_document_id,previous_document_id,version_number,change_summary,changed_pages,created_by)
      VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6::jsonb,$7::uuid) ON CONFLICT(document_id) DO UPDATE SET change_summary=EXCLUDED.change_summary,changed_pages=EXCLUDED.changed_pages RETURNING *""",
      document_id, root, body.previous_document_id, version, body.change_summary, json.dumps(body.changed_pages), str(current_user["id"]))
    return _serialize(row)


@router.get("/documents/{document_id}/versions")
async def list_document_versions(document_id: str, current_user: CurrentUser, db=Depends(get_db)):
    root = await db.fetchval("SELECT COALESCE((SELECT root_document_id FROM document_versions WHERE document_id=$1::uuid),$1::uuid)", document_id)
    rows = await db.fetch("""SELECT v.* FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.root_document_id=$1::uuid
      AND (d.user_id=$2::uuid OR d.workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=$2::uuid)) ORDER BY version_number""", root, str(current_user["id"]))
    return {"root_document_id": str(root), "versions": [_serialize(row) for row in rows]}


@router.post("/evaluations")
async def evaluate_trace(body: EvaluationInput, current_user: CurrentUser, db=Depends(get_db)):
    trace = await db.fetchrow("SELECT * FROM trace_flows WHERE trace_id=$1 AND user_id=$2::uuid", body.trace_id, str(current_user["id"]))
    if not trace: raise HTTPException(404, "Trace not found")
    spans = await db.fetch("SELECT status,name FROM trace_spans WHERE trace_id=$1", body.trace_id)
    events = await db.fetch("SELECT tool_response_json,llm_response,error FROM trace_llm_events WHERE trace_id=$1", body.trace_id)
    successful = sum(1 for span in spans if span["status"] == "success")
    score = successful / len(spans) if spans else (1.0 if trace["status"] == "success" else 0.0)
    has_response = any(event["llm_response"] for event in events)
    sources = []
    for event in events:
        response = event["tool_response_json"] if isinstance(event["tool_response_json"], dict) else {}
        sources.extend(response.get("candidates") or response.get("sources") or [])
    result = {"evaluation_type": body.evaluation_type, "score": score, "passed": score >= .8 and has_response,
              "trace_id": body.trace_id, "criteria": {"span_success_rate": score, "response_present": has_response, "citation_count": len(sources)},
              "citations": normalize_citations(sources)}
    await db.execute("""INSERT INTO trace_evaluation_correlations(trace_id,evaluation_type,evaluation_source,score,outcome,reviewer_id,metadata)
      VALUES($1,$2,'mcp_evaluation',$3,$4,$5::uuid,$6::jsonb)""", body.trace_id, body.evaluation_type, score, "passed" if result["passed"] else "needs_review", str(current_user["id"]), json.dumps(result["criteria"]))
    return result


def _serialize(row) -> dict:
    result = dict(row)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"): result[key] = value.isoformat()
        elif key in {"id", "user_id", "workspace_id", "assigned_to", "created_by", "document_id", "root_document_id", "previous_document_id"} and value is not None: result[key] = str(value)
        elif isinstance(value, str) and key in {"payload", "metadata", "content", "event_types", "source_document_ids", "changed_pages"}:
            try: result[key] = json.loads(value)
            except ValueError: pass
    return result


async def _require_workspace_access(db, workspace_id: str | None, user_id: str) -> None:
    if not workspace_id:
        return
    allowed = await db.fetchval(
        """SELECT 1 FROM workspaces w WHERE w.id=$1::uuid AND
           (w.owner_id=$2::uuid OR EXISTS (SELECT 1 FROM workspace_members m WHERE m.workspace_id=w.id AND m.user_id=$2::uuid))""",
        workspace_id, user_id,
    )
    if not allowed:
        raise HTTPException(404, "Workspace not found")


async def _require_document_access(db, document_id: str, user_id: str) -> None:
    allowed = await db.fetchval(
        """SELECT 1 FROM documents d WHERE d.id=$1::uuid AND
           (d.user_id=$2::uuid OR d.workspace_id IN
             (SELECT workspace_id FROM workspace_members WHERE user_id=$2::uuid))""",
        document_id, user_id,
    )
    if not allowed:
        raise HTTPException(404, "Document not found")
