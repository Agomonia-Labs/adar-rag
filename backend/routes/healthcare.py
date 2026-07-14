from __future__ import annotations

import json
import logging
import os
import re
import textwrap
import uuid
from io import BytesIO
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.adk_workflow import WorkflowConfigError, load_workflow_config, run_multi_agent_workflow
from services.audit import audit, ip_from, ua_from
from services.healthcare_agent_tools import HEALTHCARE_AGENT_TOOLS
from services.healthcare_workflow_audit import (
    assigned_personas,
    can_persona_approve,
    diff_packets,
    get_workspace_role,
    persona_catalog,
    persona_config,
    record_field_changes,
    resolve_persona,
    unauthorized_changes,
)
from services.healthcare_intelligence import HealthcareIntelligenceError, build_healthcare_context, generate_after_visit_summary
from services.chunker import chunk_text
from services.llm import embed
from services.text_safety import sanitize_text_for_storage
from services.usage import check_and_log_daily_event, log_event
from services.vectordb import delete_document_vectors, store_chunk
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
PRIOR_AUTH_WORKFLOW_ID = "healthcare_prior_auth_phase1"
TRANSCRIPTION_WORKFLOW_ID = "healthcare_transcription_phase1"
HEALTHCARE_VERTICAL = "healthcare"
MAX_CLINICAL_AUDIO_BYTES = int(os.getenv("CLINICAL_TRANSCRIPTION_MAX_MB", "25")) * 1024 * 1024
SUPPORTED_CLINICAL_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
}


@router.get("/personas")
async def get_healthcare_personas(current_user: CurrentUser):
    return {"vertical": HEALTHCARE_VERTICAL, "personas": persona_catalog()}


@router.get("/agent-runs/{run_id}/access-context")
async def get_healthcare_run_access_context(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL:
        raise HTTPException(404, "Healthcare agent run not found")
    workspace_role = await get_workspace_role(
        db,
        str(run["workspace_id"]) if run.get("workspace_id") else None,
        user_id,
        str(run["user_id"]) if run.get("user_id") else None,
    )
    assigned = await assigned_personas(db, str(run["workspace_id"]) if run.get("workspace_id") else None, user_id)
    default_persona = resolve_persona(assigned[0] if assigned else None, workspace_role)
    personas = sorted(set(assigned or [default_persona]))
    return {
        "run_id": run_id,
        "workspace_id": str(run["workspace_id"]) if run.get("workspace_id") else None,
        "workspace_role": workspace_role,
        "personas": personas,
        "default_persona": default_persona,
        "persona_scopes": {persona: persona_config(persona) for persona in personas},
    }


@router.get("/agent-runs/{run_id}/change-history")
async def get_healthcare_run_change_history(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL:
        raise HTTPException(404, "Healthcare agent run not found")
    rows = await db.fetch(
        """
        SELECT c.id, c.action_type, c.field_path, c.old_value, c.new_value,
               c.workspace_role, c.persona, c.created_at,
               u.email AS user_email, u.full_name AS user_name
        FROM vertical_agent_field_changes c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE c.run_id=$1
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT 500
        """,
        run_id,
    )
    return {
        "run_id": run_id,
        "changes": [
            {
                "id": str(row["id"]),
                "action_type": row["action_type"],
                "field_path": row["field_path"],
                "old_value": _json(row["old_value"]),
                "new_value": _json(row["new_value"]),
                "workspace_role": row["workspace_role"],
                "persona": row["persona"],
                "user_email": row["user_email"],
                "user_name": row["user_name"] or "",
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ],
    }


@router.post("/agent-runs/{run_id}/after-visit-summary/pdf")
async def generate_after_visit_summary_pdf_artifact(
    run_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL or run.get("workflow_id") != TRANSCRIPTION_WORKFLOW_ID:
        raise HTTPException(404, "Clinical scribe run not found")
    if run.get("status") not in ("pending_approval", "approved"):
        raise HTTPException(400, f"Run is not ready for AVS generation: {run.get('status')}")

    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    workspace_role = await get_workspace_role(
        db,
        workspace_id,
        user_id,
        str(run["user_id"]) if run.get("user_id") else None,
    )
    if workspace_role not in ("owner", "editor"):
        raise HTTPException(403, "Requires owner or editor role to generate an after visit summary PDF")

    result = _json(run.get("result_data")) or {}
    packet = result.get("review_packet") or result.get("approved_packet") or result
    packet = await _ensure_after_visit_summary_for_run(db, run_id, run, result, packet)
    avs = packet.get("after_visit_summary") or {}
    if not avs or not (avs.get("summary") or avs.get("visit_reason") or avs.get("follow_up_plan")):
        raise HTTPException(400, "No after visit summary is available. Run the clinical scribe workflow first.")

    doc_id = str(uuid.uuid4())
    owner_id = user_id
    title = _avs_title(packet, run_id)
    filename = _safe_filename(title, suffix=".pdf")
    source_path = gcs.source_path(owner_id, doc_id, filename)
    avs_text = _format_after_visit_summary_text(packet, run_id)
    pdf_bytes = _render_after_visit_summary_pdf(title, avs_text)

    await gcs.upload_bytes(source_path, pdf_bytes, "application/pdf")
    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_language, classified_at, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'pdf',$6,$7,$8,'chunking','after_visit_summary','medical',$9,NOW(),$10::jsonb)
        """,
        doc_id,
        owner_id,
        workspace_id,
        filename,
        title,
        len(pdf_bytes),
        source_path,
        gcs.chunks_dir(owner_id, doc_id),
        _language_code(((packet.get("conversation_transcript") or {}).get("language") or "")),
        json.dumps({
            "source_kind": "healthcare_after_visit_summary_pdf",
            "source_run_id": run_id,
            "source_document_id": str(run["document_id"]),
            "generated_from": "clinical_scribe_after_visit_summary",
        }),
    )
    await log_event(db, owner_id, "upload", metadata={
        "doc_id": doc_id,
        "filename": filename,
        "file_size": len(pdf_bytes),
        "file_type": "healthcare_after_visit_summary_pdf",
        "source_kind": "healthcare_after_visit_summary_pdf",
        "run_id": run_id,
    })
    await _persist_after_visit_summary_document(
        db,
        doc_id=doc_id,
        user_id=owner_id,
        workspace_id=workspace_id,
        run_id=run_id,
        source_document_id=str(run["document_id"]),
        filename=filename,
        title=title,
        source_path=source_path,
        text=avs_text,
    )
    await audit(
        db,
        user_id=user_id,
        action="healthcare_after_visit_summary_pdf_generate",
        resource_type="document",
        resource_id=doc_id,
        metadata={"run_id": run_id, "source_document_id": str(run["document_id"]), "workspace_role": workspace_role},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {
        "ok": True,
        "run_id": run_id,
        "document": {
            "doc_id": doc_id,
            "filename": filename,
            "original_name": title,
            "file_type": "pdf",
            "status": "embedded",
            "gcs_source_path": source_path,
        },
    }


@router.post("/agent-runs/{run_id}/prior-auth-packet/pdf")
async def generate_prior_auth_packet_pdf_artifact(
    run_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL or run.get("workflow_id") != PRIOR_AUTH_WORKFLOW_ID:
        raise HTTPException(404, "Prior authorization run not found")
    if run.get("status") not in ("pending_approval", "approved"):
        raise HTTPException(400, f"Run is not ready for packet generation: {run.get('status')}")

    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    workspace_role = await get_workspace_role(
        db,
        workspace_id,
        user_id,
        str(run["user_id"]) if run.get("user_id") else None,
    )
    if workspace_role not in ("owner", "editor"):
        raise HTTPException(403, "Requires owner or editor role to generate a prior authorization packet PDF")

    result = _json(run.get("result_data")) or {}
    packet = result.get("review_packet") or result.get("approved_packet") or result
    prior_packet = packet.get("prior_auth_packet") or {}
    if not prior_packet or not (prior_packet.get("packet_summary") or prior_packet.get("medical_necessity_narrative")):
        raise HTTPException(400, "No prior authorization packet is available. Run the prior authorization workflow first.")

    doc_id = str(uuid.uuid4())
    owner_id = user_id
    title = _prior_auth_title(packet, run_id)
    filename = _safe_filename(title, suffix=".pdf")
    source_path = gcs.source_path(owner_id, doc_id, filename)
    packet_text = _format_prior_auth_packet_text(packet, run_id)
    pdf_bytes = _render_prior_auth_packet_pdf(title, packet_text)

    await gcs.upload_bytes(source_path, pdf_bytes, "application/pdf")
    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_language, classified_at, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'pdf',$6,$7,$8,'chunking','prior_authorization','medical','en',NOW(),$9::jsonb)
        """,
        doc_id,
        owner_id,
        workspace_id,
        filename,
        title,
        len(pdf_bytes),
        source_path,
        gcs.chunks_dir(owner_id, doc_id),
        json.dumps({
            "source_kind": "healthcare_prior_auth_packet_pdf",
            "source_run_id": run_id,
            "source_document_id": str(run["document_id"]),
            "generated_from": "prior_authorization_packet",
        }),
    )
    await log_event(db, owner_id, "upload", metadata={
        "doc_id": doc_id,
        "filename": filename,
        "file_size": len(pdf_bytes),
        "file_type": "healthcare_prior_auth_packet_pdf",
        "source_kind": "healthcare_prior_auth_packet_pdf",
        "run_id": run_id,
    })
    await _persist_prior_auth_packet_document(
        db,
        doc_id=doc_id,
        user_id=owner_id,
        workspace_id=workspace_id,
        run_id=run_id,
        source_document_id=str(run["document_id"]),
        filename=filename,
        source_path=source_path,
        text=packet_text,
    )
    await audit(
        db,
        user_id=user_id,
        action="healthcare_prior_auth_packet_pdf_generate",
        resource_type="document",
        resource_id=doc_id,
        metadata={"run_id": run_id, "source_document_id": str(run["document_id"]), "workspace_role": workspace_role},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    download_url = None
    try:
        download_url = await gcs.get_signed_url(source_path)
    except Exception:
        log.warning("Could not create signed URL for prior auth packet doc_id=%s", doc_id, exc_info=True)
    return {
        "ok": True,
        "run_id": run_id,
        "download_url": download_url,
        "document": {
            "doc_id": doc_id,
            "filename": filename,
            "original_name": title,
            "file_type": "pdf",
            "status": "embedded",
            "gcs_source_path": source_path,
        },
    }


@router.post("/agent-runs/{run_id}/missing-info-request/pdf")
async def generate_prior_auth_missing_info_pdf_artifact(
    run_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL or run.get("workflow_id") != PRIOR_AUTH_WORKFLOW_ID:
        raise HTTPException(404, "Prior authorization run not found")
    if run.get("status") not in ("pending_approval", "approved"):
        raise HTTPException(400, f"Run is not ready for missing information request generation: {run.get('status')}")

    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    workspace_role = await get_workspace_role(
        db,
        workspace_id,
        user_id,
        str(run["user_id"]) if run.get("user_id") else None,
    )
    if workspace_role not in ("owner", "editor"):
        raise HTTPException(403, "Requires owner or editor role to generate a missing information request PDF")

    result = _json(run.get("result_data")) or {}
    packet = result.get("review_packet") or result.get("approved_packet") or result
    gaps = packet.get("gap_detection") or {}
    next_actions = (packet.get("prior_auth_packet") or {}).get("next_actions") or []
    if not gaps.get("missing_items") and not next_actions:
        raise HTTPException(400, "No missing information or next actions are available for this run.")

    doc_id = str(uuid.uuid4())
    owner_id = user_id
    title = _missing_info_request_title(packet, run_id)
    filename = _safe_filename(title, suffix=".pdf")
    source_path = gcs.source_path(owner_id, doc_id, filename)
    request_text = _format_missing_info_request_text(packet, run_id)
    pdf_bytes = _render_missing_info_request_pdf(title, request_text)

    await gcs.upload_bytes(source_path, pdf_bytes, "application/pdf")
    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_language, classified_at, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'pdf',$6,$7,$8,'chunking','prior_authorization','medical','en',NOW(),$9::jsonb)
        """,
        doc_id,
        owner_id,
        workspace_id,
        filename,
        title,
        len(pdf_bytes),
        source_path,
        gcs.chunks_dir(owner_id, doc_id),
        json.dumps({
            "source_kind": "healthcare_prior_auth_missing_info_request_pdf",
            "source_run_id": run_id,
            "source_document_id": str(run["document_id"]),
            "generated_from": "prior_authorization_missing_info_request",
        }),
    )
    await log_event(db, owner_id, "upload", metadata={
        "doc_id": doc_id,
        "filename": filename,
        "file_size": len(pdf_bytes),
        "file_type": "healthcare_prior_auth_missing_info_request_pdf",
        "source_kind": "healthcare_prior_auth_missing_info_request_pdf",
        "run_id": run_id,
    })
    await _persist_generated_prior_auth_document(
        db,
        doc_id=doc_id,
        user_id=owner_id,
        workspace_id=workspace_id,
        run_id=run_id,
        source_document_id=str(run["document_id"]),
        filename=filename,
        source_path=source_path,
        text=request_text,
        source_kind="healthcare_prior_auth_missing_info_request_pdf",
        artifact_key="prior_auth_missing_info_request_artifact",
    )
    await audit(
        db,
        user_id=user_id,
        action="healthcare_prior_auth_missing_info_request_pdf_generate",
        resource_type="document",
        resource_id=doc_id,
        metadata={"run_id": run_id, "source_document_id": str(run["document_id"]), "workspace_role": workspace_role},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    download_url = None
    try:
        download_url = await gcs.get_signed_url(source_path)
    except Exception:
        log.warning("Could not create signed URL for missing info request doc_id=%s", doc_id, exc_info=True)
    return {
        "ok": True,
        "run_id": run_id,
        "download_url": download_url,
        "document": {
            "doc_id": doc_id,
            "filename": filename,
            "original_name": title,
            "file_type": "pdf",
            "status": "embedded",
            "gcs_source_path": source_path,
        },
    }


@router.post("/workspaces/{workspace_id}/personas")
async def set_healthcare_workspace_personas(
    workspace_id: str,
    body: WorkspacePersonaRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    from routes.workspaces import _require_role

    user_id = str(current_user["id"])
    await _require_role(db, workspace_id, user_id, "owner")
    member = await db.fetchrow(
        "SELECT 1 FROM workspace_members WHERE workspace_id=$1 AND user_id=$2",
        workspace_id,
        body.user_id,
    )
    if not member:
        raise HTTPException(404, "Workspace member not found")
    valid = {item["id"] for item in persona_catalog()}
    personas = sorted(set(body.personas))
    unknown = [persona for persona in personas if persona not in valid]
    if unknown:
        raise HTTPException(400, f"Unknown healthcare persona(s): {', '.join(unknown)}")
    await db.execute(
        "DELETE FROM workspace_member_personas WHERE workspace_id=$1 AND user_id=$2 AND vertical=$3",
        workspace_id,
        body.user_id,
        HEALTHCARE_VERTICAL,
    )
    for persona in personas:
        await db.execute(
            """
            INSERT INTO workspace_member_personas (workspace_id, user_id, vertical, persona, assigned_by)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT DO NOTHING
            """,
            workspace_id,
            body.user_id,
            HEALTHCARE_VERTICAL,
            persona,
            user_id,
        )
    await audit(
        db,
        user_id=user_id,
        action="healthcare_personas_assign",
        resource_type="workspace",
        resource_id=workspace_id,
        metadata={"target_user_id": body.user_id, "personas": personas},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return {"ok": True, "workspace_id": workspace_id, "user_id": body.user_id, "personas": personas}


class HealthcareApprovalRequest(BaseModel):
    approved_packet: dict | None = None
    notes: str | None = None
    persona: str | None = None


class HealthcareReviewDraftRequest(BaseModel):
    review_packet: dict
    notes: str | None = None
    persona: str | None = None


class PriorAuthWorkflowRequest(BaseModel):
    policy_document_ids: list[str] = []


class WorkspacePersonaRequest(BaseModel):
    user_id: str
    personas: list[str]


@router.get("/agent-runs/{run_id}")
async def get_healthcare_agent_run(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    run = await get_accessible_vertical_run(db, run_id, str(current_user["id"]))
    if run.get("vertical") != HEALTHCARE_VERTICAL:
        raise HTTPException(404, "Healthcare agent run not found")
    return await vertical_run_response(db, run)


@router.get("/{doc_id}/agent-workflow/latest")
async def get_latest_healthcare_workflow(
    doc_id: str,
    current_user: CurrentUser,
    workflow_id: str | None = Query(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    await _get_accessible_doc(db, doc_id, user_id)
    run = await latest_vertical_run(
        db,
        document_id=doc_id,
        vertical=HEALTHCARE_VERTICAL,
        user_id=user_id,
        workflow_id=workflow_id,
    )
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


@router.post("/{doc_id}/prior-auth-workflow")
async def run_prior_auth_workflow(
    doc_id: str,
    body: PriorAuthWorkflowRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    doc = await _get_accessible_doc(db, doc_id, user_id)
    if doc["status"] not in ("chunked", "embedding", "embedded"):
        raise HTTPException(400, "Patient document must be chunked before running prior authorization workflow")

    policy_docs = await _get_policy_docs(db, doc, user_id, body.policy_document_ids)
    if not policy_docs:
        raise HTTPException(
            400,
            "No payer policy/prior authorization guide documents found. Upload and embed a payer_policy, medical_policy, or prior_authorization document first.",
        )

    await check_and_log_daily_event(
        db,
        user_id,
        "healthcare_ai",
        "max_healthcare_ai_day",
        metadata={
            "action": "healthcare_prior_auth_workflow",
            "doc_id": doc_id,
            "policy_document_ids": [str(p["id"]) for p in policy_docs],
        },
    )

    config = load_workflow_config(PRIOR_AUTH_WORKFLOW_ID)
    run = await create_vertical_run(
        db,
        workflow_id=PRIOR_AUTH_WORKFLOW_ID,
        workflow_version=config.get("version") or "healthcare-prior-auth-v1",
        vertical=HEALTHCARE_VERTICAL,
        document_id=doc_id,
        user_id=user_id,
        workspace_id=doc.get("workspace_id"),
        input_data={
            "document_name": doc["original_name"],
            "doc_type": doc.get("doc_type"),
            "doc_domain": doc.get("doc_domain"),
            "policy_documents": [
                {"document_id": str(p["id"]), "document_name": p["original_name"], "doc_type": p.get("doc_type")}
                for p in policy_docs
            ],
        },
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_prior_auth_workflow_background,
        run_id,
        doc_id,
        [str(p["id"]) for p in policy_docs],
        user_id,
        ip_from(request),
        ua_from(request),
    )
    return await vertical_run_response(db, run)


@router.post("/{doc_id}/transcription-workflow")
async def run_healthcare_transcription_workflow(
    doc_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    audio: UploadFile = File(...),
    consent_confirmed: bool = Form(False),
    language: str = Form(""),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if not consent_confirmed:
        raise HTTPException(400, "Consent is required before recording or uploading a clinical conversation")
    doc = await _get_accessible_doc(db, doc_id, user_id)
    content_type = (audio.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if content_type not in SUPPORTED_CLINICAL_AUDIO_TYPES:
        raise HTTPException(400, f"Unsupported audio format: {content_type}")
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "No audio received")
    if len(audio_bytes) > MAX_CLINICAL_AUDIO_BYTES:
        raise HTTPException(413, f"Audio is too large. Max {MAX_CLINICAL_AUDIO_BYTES // 1024 // 1024} MB")

    await check_and_log_daily_event(
        db,
        user_id,
        "voice_transcription",
        "max_voice_transcriptions_day",
        metadata={"action": "healthcare_transcription", "doc_id": doc_id, "audio_bytes": len(audio_bytes), "language": language},
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "healthcare_ai",
        "max_healthcare_ai_day",
        metadata={"action": "healthcare_transcription_workflow", "doc_id": doc_id, "audio_bytes": len(audio_bytes), "language": language},
    )

    visit_id = str(uuid.uuid4())
    filename = audio.filename or "clinical-conversation.webm"
    safe_name = filename.replace("/", "_").replace("\\", "_")
    audio_gcs_path = f"users/{user_id}/documents/{doc_id}/healthcare/transcriptions/{visit_id}/{safe_name}"
    await gcs.upload_bytes(audio_gcs_path, audio_bytes, content_type)

    config = load_workflow_config(TRANSCRIPTION_WORKFLOW_ID)
    run = await create_vertical_run(
        db,
        workflow_id=TRANSCRIPTION_WORKFLOW_ID,
        workflow_version=config.get("version") or "healthcare-transcription-v1",
        vertical=HEALTHCARE_VERTICAL,
        document_id=doc_id,
        user_id=user_id,
        workspace_id=doc.get("workspace_id"),
        input_data={
            "document_name": doc["original_name"],
            "doc_type": doc.get("doc_type"),
            "doc_domain": doc.get("doc_domain"),
            "audio_filename": safe_name,
            "audio_gcs_path": audio_gcs_path,
            "audio_mime_type": content_type,
            "audio_size": len(audio_bytes),
            "language": language,
            "consent_confirmed": True,
            "visit_id": visit_id,
        },
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_transcription_workflow_background,
        run_id,
        doc_id,
        user_id,
        audio_bytes,
        audio_gcs_path,
        safe_name,
        content_type,
        len(audio_bytes),
        language,
        ip_from(request),
        ua_from(request),
    )
    return await vertical_run_response(db, run)


@router.post("/transcription-workflow")
async def run_new_visit_transcription_workflow(
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    audio: UploadFile = File(...),
    consent_confirmed: bool = Form(False),
    language: str = Form(""),
    visit_title: str = Form(""),
    workspace_id: str | None = Form(None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if not consent_confirmed:
        raise HTTPException(400, "Consent is required before recording or uploading a clinical conversation")
    if workspace_id:
        from routes.workspaces import _require_role
        await _require_role(db, workspace_id, user_id, "editor")

    from services.usage import check_document_limit
    await check_document_limit(db, user_id, quantity=1)

    content_type = (audio.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if content_type not in SUPPORTED_CLINICAL_AUDIO_TYPES:
        raise HTTPException(400, f"Unsupported audio format: {content_type}")
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "No audio received")
    if len(audio_bytes) > MAX_CLINICAL_AUDIO_BYTES:
        raise HTTPException(413, f"Audio is too large. Max {MAX_CLINICAL_AUDIO_BYTES // 1024 // 1024} MB")

    await check_and_log_daily_event(
        db,
        user_id,
        "voice_transcription",
        "max_voice_transcriptions_day",
        metadata={"action": "healthcare_new_visit_transcription", "audio_bytes": len(audio_bytes), "language": language},
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "healthcare_ai",
        "max_healthcare_ai_day",
        metadata={"action": "healthcare_new_visit_transcription_workflow", "audio_bytes": len(audio_bytes), "language": language},
    )

    doc_id = str(uuid.uuid4())
    visit_id = str(uuid.uuid4())
    base_title = (visit_title or "").strip() or f"Clinical Visit Transcript {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    transcript_filename = _safe_filename(base_title, suffix=".txt")
    transcript_gcs_path = gcs.source_path(user_id, doc_id, transcript_filename)
    audio_filename = audio.filename or "clinical-conversation.webm"
    safe_audio_name = audio_filename.replace("/", "_").replace("\\", "_")
    audio_gcs_path = f"users/{user_id}/documents/{doc_id}/healthcare/transcriptions/{visit_id}/{safe_audio_name}"
    await gcs.upload_bytes(audio_gcs_path, audio_bytes, content_type)

    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_language, classified_at, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'text',$6,$7,$8,'chunking','clinical_notes','medical',$9,NOW(),$10::jsonb)
        """,
        doc_id,
        user_id,
        workspace_id,
        transcript_filename,
        base_title,
        len(audio_bytes),
        transcript_gcs_path,
        gcs.chunks_dir(user_id, doc_id),
        _language_code(language),
        json.dumps({
            "source_kind": "healthcare_visit_transcription",
            "audio_gcs_path": audio_gcs_path,
            "audio_filename": safe_audio_name,
            "audio_mime_type": content_type,
            "consent_confirmed": True,
            "visit_id": visit_id,
        }),
    )
    await log_event(db, user_id, "upload", metadata={
        "doc_id": doc_id,
        "filename": transcript_filename,
        "file_size": len(audio_bytes),
        "file_type": "healthcare_visit_audio",
        "source_kind": "healthcare_visit_transcription",
    })

    config = load_workflow_config(TRANSCRIPTION_WORKFLOW_ID)
    run = await create_vertical_run(
        db,
        workflow_id=TRANSCRIPTION_WORKFLOW_ID,
        workflow_version=config.get("version") or "healthcare-transcription-v1",
        vertical=HEALTHCARE_VERTICAL,
        document_id=doc_id,
        user_id=user_id,
        workspace_id=workspace_id,
        input_data={
            "document_name": base_title,
            "doc_type": "clinical_notes",
            "doc_domain": "medical",
            "audio_filename": safe_audio_name,
            "audio_gcs_path": audio_gcs_path,
            "audio_mime_type": content_type,
            "audio_size": len(audio_bytes),
            "language": language,
            "consent_confirmed": True,
            "visit_id": visit_id,
            "new_visit": True,
        },
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_transcription_workflow_background,
        run_id,
        doc_id,
        user_id,
        audio_bytes,
        audio_gcs_path,
        safe_audio_name,
        content_type,
        len(audio_bytes),
        language,
        ip_from(request),
        ua_from(request),
        True,
        transcript_gcs_path,
        transcript_filename,
        workspace_id,
    )
    response = await vertical_run_response(db, run)
    response["created_document"] = {"doc_id": doc_id, "filename": transcript_filename, "original_name": base_title}
    return response


@router.post("/agent-runs/{run_id}/transcription-rerun")
async def rerun_healthcare_transcription_workflow(
    run_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    previous = await get_accessible_vertical_run(db, run_id, user_id)
    if previous.get("vertical") != HEALTHCARE_VERTICAL or previous.get("workflow_id") != TRANSCRIPTION_WORKFLOW_ID:
        raise HTTPException(404, "Clinical scribe run not found")
    doc = await _get_accessible_doc(db, str(previous["document_id"]), user_id)
    workspace_id = str(previous["workspace_id"]) if previous.get("workspace_id") else None
    workspace_role = await get_workspace_role(db, workspace_id, user_id, str(previous["user_id"]))
    if workspace_id and workspace_role not in ("owner", "editor"):
        raise HTTPException(403, "Requires owner or editor role to re-run clinical scribe")

    input_data = _json(previous.get("input_data")) or {}
    doc_metadata = _json(doc.get("doc_metadata")) or {}
    audio_gcs_path = input_data.get("audio_gcs_path") or (doc_metadata.get("audio_gcs_path") if isinstance(doc_metadata, dict) else None)
    if not audio_gcs_path:
        raise HTTPException(400, "This clinical scribe run does not have saved audio. Please upload or record audio again.")
    audio_bytes = await gcs.download_bytes(audio_gcs_path)
    if not audio_bytes:
        raise HTTPException(400, "Saved audio is empty or unavailable. Please upload or record audio again.")
    content_type = input_data.get("audio_mime_type") or "application/octet-stream"
    language = input_data.get("language") or ""
    safe_audio_name = input_data.get("audio_filename") or "clinical-conversation.webm"
    is_new_visit = bool(input_data.get("new_visit"))

    await check_and_log_daily_event(
        db,
        user_id,
        "voice_transcription",
        "max_voice_transcriptions_day",
        metadata={"action": "healthcare_transcription_rerun", "source_run_id": run_id, "audio_bytes": len(audio_bytes), "language": language},
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "healthcare_ai",
        "max_healthcare_ai_day",
        metadata={"action": "healthcare_transcription_workflow_rerun", "source_run_id": run_id, "audio_bytes": len(audio_bytes), "language": language},
    )

    config = load_workflow_config(TRANSCRIPTION_WORKFLOW_ID)
    new_run = await create_vertical_run(
        db,
        workflow_id=TRANSCRIPTION_WORKFLOW_ID,
        workflow_version=config.get("version") or "healthcare-transcription-v1",
        vertical=HEALTHCARE_VERTICAL,
        document_id=str(previous["document_id"]),
        user_id=str(previous["user_id"]),
        workspace_id=previous.get("workspace_id"),
        input_data={
            **input_data,
            "audio_size": len(audio_bytes),
            "rerun_from_run_id": run_id,
            "rerun_requested_by": user_id,
        },
    )
    new_run_id = str(new_run["id"])
    background_tasks.add_task(
        _execute_transcription_workflow_background,
        new_run_id,
        str(previous["document_id"]),
        str(previous["user_id"]),
        audio_bytes,
        audio_gcs_path,
        safe_audio_name,
        content_type,
        len(audio_bytes),
        language,
        ip_from(request),
        ua_from(request),
        is_new_visit,
        str(doc["gcs_source_path"]) if is_new_visit else None,
        str(doc["filename"]) if is_new_visit else "",
        workspace_id,
    )
    return await vertical_run_response(db, new_run)


@router.patch("/agent-runs/{run_id}/review-draft")
async def save_healthcare_review_draft(
    run_id: str,
    body: HealthcareReviewDraftRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != HEALTHCARE_VERTICAL:
        raise HTTPException(404, "Healthcare agent run not found")
    if run.get("status") not in ("pending_approval", "approved"):
        raise HTTPException(400, f"Run is not ready for review edits: {run.get('status')}")

    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    workspace_role = await get_workspace_role(
        db,
        workspace_id,
        user_id,
        str(run["user_id"]) if run.get("user_id") else None,
    )
    assigned = await assigned_personas(db, workspace_id, user_id)
    requested_persona = resolve_persona(body.persona or (assigned[0] if assigned else None), workspace_role)
    if assigned and requested_persona not in assigned:
        raise HTTPException(403, f"Persona {requested_persona} is not assigned to this workspace member")
    if workspace_role not in ("owner", "editor"):
        raise HTTPException(403, "Requires owner or editor role to save healthcare review edits")

    result_data = _json(run.get("result_data")) or {}
    current_packet = result_data.get("review_packet") or result_data.get("approved_packet") or result_data
    changes = diff_packets(current_packet, body.review_packet)
    blocked = unauthorized_changes(requested_persona, changes)
    if blocked:
        raise HTTPException(
            403,
            f"Persona {requested_persona} cannot edit these fields: {', '.join(blocked[:8])}",
        )
    if changes:
        await record_field_changes(
            db,
            run=run,
            user_id=user_id,
            workspace_role=workspace_role,
            persona=requested_persona,
            action_type="healthcare_packet_field_update",
            changes=changes,
        )
    await db.execute(
        """
        UPDATE vertical_agent_runs
        SET result_data=jsonb_set(COALESCE(result_data, '{}'::jsonb), '{review_packet}', $2::jsonb, true),
            approval_notes=COALESCE($3, approval_notes),
            updated_at=NOW()
        WHERE id=$1
        """,
        run_id,
        json.dumps(body.review_packet),
        body.notes,
    )
    await audit(
        db,
        user_id=user_id,
        action="healthcare_review_draft_save",
        resource_type="agent_run",
        resource_id=run_id,
        metadata={
            "document_id": str(run["document_id"]),
            "workspace_role": workspace_role,
            "persona": requested_persona,
            "field_change_count": len(changes),
        },
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    fresh = await get_accessible_vertical_run(db, run_id, user_id)
    return await vertical_run_response(db, fresh)


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
    approved_packet = body.approved_packet or result_data.get("review_packet") or result_data.get("approved_packet")
    if not approved_packet:
        raise HTTPException(400, "No healthcare packet available to approve")

    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    workspace_role = await get_workspace_role(
        db,
        workspace_id,
        user_id,
        str(run["user_id"]) if run.get("user_id") else None,
    )
    assigned = await assigned_personas(db, workspace_id, user_id)
    requested_persona = resolve_persona(body.persona or (assigned[0] if assigned else None), workspace_role)
    if assigned and requested_persona not in assigned:
        raise HTTPException(403, f"Persona {requested_persona} is not assigned to this workspace member")
    owner_personal_doc = not workspace_id and str(run["user_id"]) == user_id
    if not can_persona_approve(requested_persona, workspace_role, owner_personal_doc=owner_personal_doc):
        raise HTTPException(403, f"Persona {requested_persona} cannot approve healthcare packets")

    draft_packet = result_data.get("review_packet") or result_data.get("approved_packet") or result_data
    changes = diff_packets(draft_packet, approved_packet)
    blocked = unauthorized_changes(requested_persona, changes)
    if blocked:
        raise HTTPException(
            403,
            f"Persona {requested_persona} cannot edit these fields: {', '.join(blocked[:8])}",
        )
    if changes:
        await record_field_changes(
            db,
            run=run,
            user_id=user_id,
            workspace_role=workspace_role,
            persona=requested_persona,
            action_type="healthcare_packet_approve",
            changes=changes,
        )
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
        metadata={
            "run_id": run_id,
            "workspace_role": workspace_role,
            "persona": requested_persona,
            "field_change_count": len(changes),
        },
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


async def _execute_prior_auth_workflow_background(
    run_id: str,
    doc_id: str,
    policy_doc_ids: list[str],
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        try:
            doc = await _get_accessible_doc(db, doc_id, user_id)
            policy_docs = [await _get_accessible_doc(db, policy_doc_id, user_id) for policy_doc_id in policy_doc_ids]
            patient_context = build_healthcare_context(
                doc["original_name"],
                await _load_doc_chunks(db, doc, user_id),
                max_chars=26000,
            )
            policy_context_parts = []
            for policy_doc in policy_docs:
                policy_context_parts.append(
                    build_healthcare_context(
                        policy_doc["original_name"],
                        await _load_doc_chunks(db, policy_doc, user_id),
                        max_chars=max(8000, 24000 // max(1, len(policy_docs))),
                    )
                )
            workflow_context = {
                "document_id": doc_id,
                "document_name": doc["original_name"],
                "patient_context": patient_context,
                "policy_context": "\n\n--- PAYER POLICY DOCUMENT ---\n\n".join(policy_context_parts),
                "policy_documents": [
                    {"document_id": str(p["id"]), "document_name": p["original_name"], "doc_type": p.get("doc_type")}
                    for p in policy_docs
                ],
                "doc_type": doc.get("doc_type"),
                "doc_domain": doc.get("doc_domain"),
            }
            workflow = await run_multi_agent_workflow(
                PRIOR_AUTH_WORKFLOW_ID,
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
            await log_event(db, user_id, "healthcare_agent_workflow", metadata={"doc_id": doc_id, "run_id": run_id, "workflow_id": PRIOR_AUTH_WORKFLOW_ID})
            await audit(
                db,
                user_id=user_id,
                action="healthcare_prior_auth_workflow",
                resource_type="document",
                resource_id=doc_id,
                metadata={"run_id": run_id, "workflow_id": PRIOR_AUTH_WORKFLOW_ID, "policy_document_ids": policy_doc_ids},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except (HealthcareIntelligenceError, WorkflowConfigError) as exc:
            log.warning("Prior auth workflow failed run_id=%s doc_id=%s: %s", run_id, doc_id, exc)
            await fail_vertical_run(db, run_id, str(exc))
        except Exception as exc:
            log.exception("Prior auth workflow crashed run_id=%s doc_id=%s", run_id, doc_id)
            await fail_vertical_run(db, run_id, str(exc))


async def _execute_transcription_workflow_background(
    run_id: str,
    doc_id: str,
    user_id: str,
    audio_bytes: bytes,
    audio_gcs_path: str,
    audio_filename: str,
    audio_mime_type: str,
    audio_size: int,
    language: str,
    ip_address: str | None,
    user_agent: str | None,
    new_visit: bool = False,
    transcript_gcs_path: str | None = None,
    transcript_filename: str | None = None,
    workspace_id: str | None = None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        try:
            doc = await _get_accessible_doc(db, doc_id, user_id)
            document_context = ""
            if doc["status"] in ("chunked", "embedding", "embedded"):
                try:
                    document_context = build_healthcare_context(
                        doc["original_name"],
                        await _load_doc_chunks(db, doc, user_id),
                        max_chars=12000,
                    )
                except Exception as exc:
                    log.warning("Could not load optional healthcare document context for transcription run_id=%s: %s", run_id, exc)
            workflow_context = {
                "document_id": doc_id,
                "document_name": doc["original_name"],
                "document_context": document_context,
                "doc_type": doc.get("doc_type"),
                "doc_domain": doc.get("doc_domain"),
                "audio_bytes": audio_bytes,
                "audio_gcs_path": audio_gcs_path,
                "audio_filename": audio_filename,
                "audio_mime_type": audio_mime_type,
                "audio_size": audio_size,
                "language": language,
                "consent_confirmed": True,
            }
            workflow = await run_multi_agent_workflow(
                TRANSCRIPTION_WORKFLOW_ID,
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
            if new_visit:
                await _persist_new_visit_transcript_document(
                    db,
                    doc_id=doc_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    result=workflow["result"],
                    transcript_gcs_path=transcript_gcs_path,
                    transcript_filename=transcript_filename or doc["original_name"],
                )
            await log_event(db, user_id, "healthcare_agent_workflow", metadata={"doc_id": doc_id, "run_id": run_id, "workflow_id": TRANSCRIPTION_WORKFLOW_ID})
            await audit(
                db,
                user_id=user_id,
                action="healthcare_transcription_workflow",
                resource_type="document",
                resource_id=doc_id,
                metadata={"run_id": run_id, "workflow_id": TRANSCRIPTION_WORKFLOW_ID, "audio_gcs_path": audio_gcs_path},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except (HealthcareIntelligenceError, WorkflowConfigError) as exc:
            log.warning("Healthcare transcription workflow failed run_id=%s doc_id=%s: %s", run_id, doc_id, exc)
            await fail_vertical_run(db, run_id, str(exc))
            if new_visit:
                await db.execute(
                    "UPDATE documents SET status='error', error_message=$2, updated_at=NOW() WHERE id=$1",
                    doc_id,
                    str(exc)[:500],
                )
        except Exception as exc:
            log.exception("Healthcare transcription workflow crashed run_id=%s doc_id=%s", run_id, doc_id)
            await fail_vertical_run(db, run_id, str(exc))
            if new_visit:
                await db.execute(
                    "UPDATE documents SET status='error', error_message=$2, updated_at=NOW() WHERE id=$1",
                    doc_id,
                    str(exc)[:500],
                )


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


async def _get_policy_docs(db, patient_doc: dict, user_id: str, policy_document_ids: list[str]) -> list[dict]:
    if policy_document_ids:
        docs = [await _get_accessible_doc(db, policy_id, user_id) for policy_id in policy_document_ids]
    else:
        rows = await db.fetch(
            """
            SELECT d.*
            FROM documents d
            WHERE d.status IN ('chunked','embedding','embedded')
              AND d.status != 'deleted'
              AND d.id != $1
              AND d.doc_type = ANY($2::text[])
              AND (
                (d.workspace_id IS NOT DISTINCT FROM $3)
                OR (d.user_id=$4 AND $3 IS NULL)
              )
              AND (
                d.user_id=$4
                OR EXISTS (
                  SELECT 1 FROM workspace_members wm
                  WHERE wm.workspace_id=d.workspace_id
                    AND wm.user_id=$4
                )
              )
            ORDER BY d.updated_at DESC
            LIMIT 5
            """,
            patient_doc["id"],
            ["payer_policy", "medical_policy", "prior_authorization"],
            patient_doc.get("workspace_id"),
            user_id,
        )
        docs = [dict(r) for r in rows]
    usable = [doc for doc in docs if doc["status"] in ("chunked", "embedding", "embedded")]
    return usable[:5]


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


async def _ensure_after_visit_summary_for_run(db, run_id: str, run: dict, result: dict, packet: dict) -> dict:
    avs = packet.get("after_visit_summary") if isinstance(packet, dict) else None
    if isinstance(avs, dict) and (avs.get("summary") or avs.get("visit_reason") or avs.get("follow_up_plan")):
        return packet

    transcript = packet.get("conversation_transcript") or {}
    transcript_text = transcript.get("transcript_text") or ""
    soap_note = packet.get("soap_note") or {}
    patient_summary = packet.get("patient_summary") or {}
    followup_checklist = packet.get("followup_checklist") or {}
    intake = packet.get("conversation_intake") or {}
    if not any((transcript_text, soap_note, patient_summary, followup_checklist)):
        return packet

    input_data = _json(run.get("input_data")) or {}
    language = transcript.get("language") or input_data.get("language") or ""
    try:
        generated_avs = await generate_after_visit_summary(
            transcript_text,
            intake,
            soap_note,
            patient_summary,
            followup_checklist,
            language,
        )
    except HealthcareIntelligenceError as exc:
        log.warning("Could not backfill after visit summary for run_id=%s: %s", run_id, exc)
        return packet
    if not generated_avs:
        return packet

    updated_result = dict(result or {})
    updated_packet = dict(packet or {})
    updated_packet["after_visit_summary"] = generated_avs

    if isinstance(updated_result.get("review_packet"), dict):
        updated_result["review_packet"] = {**updated_result["review_packet"], "after_visit_summary": generated_avs}
    if isinstance(updated_result.get("approved_packet"), dict):
        updated_result["approved_packet"] = {**updated_result["approved_packet"], "after_visit_summary": generated_avs}
    if not isinstance(updated_result.get("review_packet"), dict) and not isinstance(updated_result.get("approved_packet"), dict):
        updated_result = {**updated_result, "after_visit_summary": generated_avs}
        if isinstance(updated_result.get("approved_packet"), dict):
            updated_result["approved_packet"] = {**updated_result["approved_packet"], "after_visit_summary": generated_avs}

    await db.execute(
        """
        UPDATE vertical_agent_runs
        SET result_data=$2::jsonb,
            updated_at=NOW()
        WHERE id=$1
        """,
        run_id,
        json.dumps(updated_result),
    )
    return updated_packet


async def _persist_new_visit_transcript_document(
    db,
    *,
    doc_id: str,
    user_id: str,
    workspace_id: str | None,
    run_id: str,
    result: dict,
    transcript_gcs_path: str | None,
    transcript_filename: str,
) -> None:
    transcript = ((result or {}).get("conversation_transcript") or {}).get("transcript_text") or ""
    if not transcript.strip():
        raise HealthcareIntelligenceError("Clinical transcription did not produce transcript text to chunk/embed")
    packet = (result or {}).get("approved_packet") or result or {}
    soap = packet.get("soap_note") or {}
    patient_summary = packet.get("patient_summary") or {}
    followups = packet.get("followup_checklist") or {}
    text = sanitize_text_for_storage(
        "\n\n".join([
            f"CLINICAL VISIT TRANSCRIPT DOCUMENT\nRun ID: {run_id}",
            "TRANSCRIPT:\n" + transcript,
            "SOAP NOTE DRAFT:\n" + json.dumps(soap, ensure_ascii=False, indent=2),
            "PATIENT-FRIENDLY SUMMARY:\n" + json.dumps(patient_summary, ensure_ascii=False, indent=2),
            "FOLLOW-UP CHECKLIST:\n" + json.dumps(followups, ensure_ascii=False, indent=2),
        ])
    )
    transcript_gcs_path = transcript_gcs_path or gcs.source_path(user_id, doc_id, transcript_filename)
    await gcs.upload_text(transcript_gcs_path, text)
    doc_meta = {
        "document_id": doc_id,
        "user_id": user_id,
        "filename": transcript_filename,
        "file_type": "text",
        "source_kind": "healthcare_visit_transcription",
        "workflow_id": TRANSCRIPTION_WORKFLOW_ID,
        "run_id": run_id,
    }
    chunks = chunk_text(text, doc_meta=doc_meta)
    if not chunks:
        raise HealthcareIntelligenceError("Clinical transcript produced no chunks")
    for chunk in chunks:
        await gcs.upload_text(gcs.chunk_path(user_id, doc_id, chunk.index), chunk.text)
    now = datetime.now(timezone.utc).isoformat()
    meta_obj = {
        "document": {
            "id": doc_id,
            "user_id": user_id,
            "filename": transcript_filename,
            "file_type": "text",
            "total_chunks": len(chunks),
            "created_at": now,
            "source_kind": "healthcare_visit_transcription",
            "workflow_id": TRANSCRIPTION_WORKFLOW_ID,
            "run_id": run_id,
        },
        "chunks": [
            {
                "index": c.index,
                "word_count": c.word_count,
                "char_count": c.char_count,
                "gcs_path": gcs.chunk_path(user_id, doc_id, c.index),
                "source_kind": "healthcare_visit_transcription",
                "run_id": run_id,
            }
            for c in chunks
        ],
    }
    await gcs.upload_json(gcs.metadata_path(user_id, doc_id), meta_obj)
    await db.execute(
        """
        UPDATE documents
           SET status='embedding',
               chunk_count=$2,
               file_size=$3,
               gcs_source_path=$4,
               doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $5::jsonb,
               updated_at=NOW()
         WHERE id=$1
        """,
        doc_id,
        len(chunks),
        len(text.encode("utf-8")),
        transcript_gcs_path,
        json.dumps({"transcript_document": {"run_id": run_id, "chunk_count": len(chunks), "embedded_from_scribe": True}}),
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "embedding",
        "max_embeds_day",
        quantity=len(chunks),
        metadata={"doc_id": doc_id, "chunk_count": len(chunks), "source_kind": "healthcare_visit_transcription"},
    )
    await delete_document_vectors(doc_id)
    for chunk in chunks:
        content = sanitize_text_for_storage(await gcs.download_text(gcs.chunk_path(user_id, doc_id, chunk.index)))
        await store_chunk(
            document_id=doc_id,
            user_id=user_id,
            workspace_id=workspace_id,
            chunk_index=chunk.index,
            chunk_total=len(chunks),
            content=content,
            embedding=await embed(content),
            chunk_metadata=chunk.to_metadata(),
        )
    await db.execute("UPDATE documents SET status='embedded', updated_at=NOW() WHERE id=$1", doc_id)


async def _persist_after_visit_summary_document(
    db,
    *,
    doc_id: str,
    user_id: str,
    workspace_id: str | None,
    run_id: str,
    source_document_id: str,
    filename: str,
    title: str,
    source_path: str,
    text: str,
) -> None:
    clean_text = sanitize_text_for_storage(text)
    doc_meta = {
        "document_id": doc_id,
        "user_id": user_id,
        "filename": filename,
        "file_type": "pdf",
        "source_kind": "healthcare_after_visit_summary_pdf",
        "workflow_id": TRANSCRIPTION_WORKFLOW_ID,
        "run_id": run_id,
        "source_document_id": source_document_id,
        "title": title,
    }
    chunks = chunk_text(clean_text, doc_meta=doc_meta)
    if not chunks:
        raise HealthcareIntelligenceError("After visit summary produced no chunks")
    for chunk in chunks:
        await gcs.upload_text(gcs.chunk_path(user_id, doc_id, chunk.index), chunk.text)
    now = datetime.now(timezone.utc).isoformat()
    meta_obj = {
        "document": {
            "id": doc_id,
            "user_id": user_id,
            "filename": filename,
            "file_type": "pdf",
            "total_chunks": len(chunks),
            "created_at": now,
            "source_kind": "healthcare_after_visit_summary_pdf",
            "workflow_id": TRANSCRIPTION_WORKFLOW_ID,
            "run_id": run_id,
            "source_document_id": source_document_id,
        },
        "chunks": [
            {
                "index": c.index,
                "word_count": c.word_count,
                "char_count": c.char_count,
                "gcs_path": gcs.chunk_path(user_id, doc_id, c.index),
                "source_kind": "healthcare_after_visit_summary_pdf",
                "run_id": run_id,
                "source_document_id": source_document_id,
            }
            for c in chunks
        ],
    }
    await gcs.upload_json(gcs.metadata_path(user_id, doc_id), meta_obj)
    await db.execute(
        """
        UPDATE documents
           SET status='embedding',
               chunk_count=$2,
               doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $3::jsonb,
               updated_at=NOW()
         WHERE id=$1
        """,
        doc_id,
        len(chunks),
        json.dumps({"after_visit_summary_artifact": {"run_id": run_id, "chunk_count": len(chunks), "source_path": source_path}}),
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "embedding",
        "max_embeds_day",
        quantity=len(chunks),
        metadata={"doc_id": doc_id, "chunk_count": len(chunks), "source_kind": "healthcare_after_visit_summary_pdf"},
    )
    await delete_document_vectors(doc_id)
    for chunk in chunks:
        content = sanitize_text_for_storage(await gcs.download_text(gcs.chunk_path(user_id, doc_id, chunk.index)))
        await store_chunk(
            document_id=doc_id,
            user_id=user_id,
            workspace_id=workspace_id,
            chunk_index=chunk.index,
            chunk_total=len(chunks),
            content=content,
            embedding=await embed(content),
            chunk_metadata=chunk.to_metadata(),
        )
    await db.execute("UPDATE documents SET status='embedded', updated_at=NOW() WHERE id=$1", doc_id)


async def _persist_prior_auth_packet_document(
    db,
    *,
    doc_id: str,
    user_id: str,
    workspace_id: str | None,
    run_id: str,
    source_document_id: str,
    filename: str,
    source_path: str,
    text: str,
) -> None:
    await _persist_generated_prior_auth_document(
        db,
        doc_id=doc_id,
        user_id=user_id,
        workspace_id=workspace_id,
        run_id=run_id,
        source_document_id=source_document_id,
        filename=filename,
        source_path=source_path,
        text=text,
        source_kind="healthcare_prior_auth_packet_pdf",
        artifact_key="prior_auth_packet_artifact",
    )


async def _persist_generated_prior_auth_document(
    db,
    *,
    doc_id: str,
    user_id: str,
    workspace_id: str | None,
    run_id: str,
    source_document_id: str,
    filename: str,
    source_path: str,
    text: str,
    source_kind: str,
    artifact_key: str,
) -> None:
    clean_text = sanitize_text_for_storage(text)
    doc_meta = {
        "document_id": doc_id,
        "user_id": user_id,
        "filename": filename,
        "file_type": "pdf",
        "source_kind": source_kind,
        "workflow_id": PRIOR_AUTH_WORKFLOW_ID,
        "run_id": run_id,
        "source_document_id": source_document_id,
    }
    chunks = chunk_text(clean_text, doc_meta=doc_meta)
    if not chunks:
        raise HealthcareIntelligenceError("Generated prior authorization artifact produced no chunks")
    for chunk in chunks:
        await gcs.upload_text(gcs.chunk_path(user_id, doc_id, chunk.index), chunk.text)
    now = datetime.now(timezone.utc).isoformat()
    meta_obj = {
        "document": {
            "id": doc_id,
            "user_id": user_id,
            "filename": filename,
            "file_type": "pdf",
            "total_chunks": len(chunks),
            "created_at": now,
            "source_kind": source_kind,
            "workflow_id": PRIOR_AUTH_WORKFLOW_ID,
            "run_id": run_id,
            "source_document_id": source_document_id,
        },
        "chunks": [
            {
                "index": c.index,
                "word_count": c.word_count,
                "char_count": c.char_count,
                "gcs_path": gcs.chunk_path(user_id, doc_id, c.index),
                "source_kind": source_kind,
                "run_id": run_id,
                "source_document_id": source_document_id,
            }
            for c in chunks
        ],
    }
    await gcs.upload_json(gcs.metadata_path(user_id, doc_id), meta_obj)
    await db.execute(
        """
        UPDATE documents
           SET status='embedding',
               chunk_count=$2,
               doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $3::jsonb,
               updated_at=NOW()
         WHERE id=$1
        """,
        doc_id,
        len(chunks),
        json.dumps({artifact_key: {"run_id": run_id, "chunk_count": len(chunks), "source_path": source_path}}),
    )
    await check_and_log_daily_event(
        db,
        user_id,
        "embedding",
        "max_embeds_day",
        quantity=len(chunks),
        metadata={"doc_id": doc_id, "chunk_count": len(chunks), "source_kind": source_kind},
    )
    await delete_document_vectors(doc_id)
    for chunk in chunks:
        content = sanitize_text_for_storage(await gcs.download_text(gcs.chunk_path(user_id, doc_id, chunk.index)))
        await store_chunk(
            document_id=doc_id,
            user_id=user_id,
            workspace_id=workspace_id,
            chunk_index=chunk.index,
            chunk_total=len(chunks),
            content=content,
            embedding=await embed(content),
            chunk_metadata=chunk.to_metadata(),
        )
    await db.execute("UPDATE documents SET status='embedded', updated_at=NOW() WHERE id=$1", doc_id)


def _prior_auth_title(packet: dict, run_id: str) -> str:
    context = packet.get("patient_context") or {}
    request = packet.get("prior_auth_request") or {}
    patient = ((_field_value(context, "patient_name") or "Patient").strip() or "Patient")
    requested = ((request.get("requested_item") or {}).get("value") or "Prior Authorization Packet").strip()
    return f"Prior Authorization Packet - {patient} - {requested} - {run_id[:8]}"


def _missing_info_request_title(packet: dict, run_id: str) -> str:
    context = packet.get("patient_context") or {}
    request = packet.get("prior_auth_request") or {}
    patient = ((_field_value(context, "patient_name") or "Patient").strip() or "Patient")
    requested = ((request.get("requested_item") or {}).get("value") or "Prior Authorization").strip()
    return f"Missing Information Request - {patient} - {requested} - {run_id[:8]}"


def _format_missing_info_request_text(packet: dict, run_id: str) -> str:
    request = packet.get("prior_auth_request") or {}
    gaps = packet.get("gap_detection") or {}
    prior_packet = packet.get("prior_auth_packet") or {}
    context = packet.get("patient_context") or {}
    patient_name = _field_value(context, "patient_name")
    requested_item = _nested_field_value(request.get("requested_item"))
    effective_missing = _effective_missing_items(packet)
    missing_items = _missing_info_items_by_priority(effective_missing)
    high_priority = _natural_join(missing_items["high"]) if missing_items["high"] else ""
    additional = _natural_join(missing_items["other"]) if missing_items["other"] else ""
    next_actions = _summarize_next_actions(prior_packet.get("next_actions"))
    decision = _humanize_prior_auth_decision(prior_packet.get("recommended_decision"))
    code_readiness = _prior_auth_code_readiness_summary(packet)
    code_actions = _prior_auth_code_action_summary(packet)

    missing_paragraph = (
        f"To complete the prior authorization review for {requested_item}, please provide {high_priority}."
        if high_priority else
        f"To complete the prior authorization review for {requested_item}, please provide the missing clinical and administrative details identified during review."
    )
    if additional:
        missing_paragraph += f" Additional helpful information includes {additional}."

    lines = [
        "MISSING INFORMATION REQUEST",
        "",
        "Request Summary",
        (
            f"This request concerns {patient_name}, date of birth {_field_value(context, 'date_of_birth')}, "
            f"for {requested_item}. The prior authorization packet was reviewed and is not yet ready for payer submission."
        ),
        "",
        "Information Needed",
        missing_paragraph,
        "",
        "Code Readiness",
        code_readiness,
        "",
        "Code Review Actions",
        code_actions,
        "",
        "Why This Is Needed",
        (
            "The payer review may require complete service codes, diagnosis details, symptom duration, conservative treatment history, "
            "and a provider statement explaining why the requested service is needed now. Without these details, the request may be delayed, "
            "returned for more information, or denied for incomplete documentation."
        ),
        "",
        "Recommended Follow-Up",
        next_actions,
        "",
        "Submission Readiness",
        decision,
        "",
        "Review Notice",
        "This request is an administrative documentation aid. A qualified human reviewer should confirm the requested items before payer submission.",
    ]
    return sanitize_text_for_storage("\n".join(lines))


def _format_prior_auth_packet_text(packet: dict, run_id: str) -> str:
    request = packet.get("prior_auth_request") or {}
    evidence = packet.get("evidence_map") or {}
    gaps = packet.get("gap_detection") or {}
    prior_packet = packet.get("prior_auth_packet") or {}
    context = packet.get("patient_context") or {}
    patient_name = _field_value(context, "patient_name")
    requested_item = _nested_field_value(request.get("requested_item"))
    diagnoses = _summarize_diagnoses(request.get("diagnoses"))
    criteria_review = _summarize_prior_auth_readiness(prior_packet.get("criteria_checklist"), evidence.get("criteria_matches"))
    clinical_story = _summarize_clinical_story(request, evidence)
    effective_missing = _effective_missing_items(packet)
    missing_story = _summarize_missing_evidence(effective_missing)
    risk_story = _summarize_submission_risks(gaps.get("submission_risks"))
    next_actions = _summarize_next_actions(prior_packet.get("next_actions"))
    decision = _humanize_prior_auth_decision(prior_packet.get("recommended_decision"))
    code_readiness = _prior_auth_code_readiness_summary(packet)
    final_codes = _prior_auth_final_codes_summary(packet)
    readiness_lead = _prior_auth_packet_readiness_lead(packet)
    medical_necessity = _clean_prior_auth_pdf_text(
        prior_packet.get("medical_necessity_narrative")
        or prior_packet.get("packet_summary")
        or "A medical necessity narrative was not generated."
    )
    lines = [
        "PRIOR AUTHORIZATION PACKET",
        "",
        "Request Overview",
        (
            f"This prior authorization packet is for {patient_name}, date of birth {_field_value(context, 'date_of_birth')}. "
            f"The request is for {requested_item}, categorized as {request.get('service_category') or 'imaging'}, "
            f"with {_nested_field_value(request.get('urgency')).lower()} urgency. The encounter was documented on "
            f"{_field_value(context, 'encounter_date')} by {_field_value(context, 'provider')} at {_field_value(context, 'facility')}."
        ),
        "",
        "Clinical Story",
        (
            f"{diagnoses} {clinical_story}"
        ),
        "",
        "Key Points Summary",
        (
            f"{readiness_lead} "
            f"{criteria_review} {code_readiness} {missing_story} {decision}"
        ),
        "",
        "Final Coder-Reviewed Codes",
        final_codes,
        "",
        "Code Readiness",
        code_readiness,
        "",
        "What Is Missing",
        missing_story,
        "",
        "Medical Necessity Draft",
        medical_necessity,
        "",
        "Submission Readiness",
        f"{risk_story} {decision}",
        "",
        "Next Actions",
        next_actions,
        "",
        "Human Review Notice",
        packet.get("guardrail") or "Prior authorization assistance only. Not medical advice, not a coverage guarantee, and not a payer submission without human approval.",
    ]
    return sanitize_text_for_storage("\n".join(lines))


def _summarize_diagnoses(items) -> str:
    diagnoses = []
    for item in _dict_items(items):
        code = item.get("code")
        description = item.get("description")
        if code and description:
            diagnoses.append(f"{description} ({code})")
        elif description or code:
            diagnoses.append(str(description or code))
    if diagnoses:
        return f"The documented diagnosis or indication is {_natural_join(diagnoses)}."
    return "The diagnosis or indication was not clearly documented in the available packet."


def _prior_auth_code_readiness_summary(packet: dict) -> str:
    request = packet.get("prior_auth_request") or {}
    gaps = packet.get("gap_detection") or {}
    recommendations = packet.get("code_recommendations") or {}
    code_candidates = _dict_items(recommendations.get("candidates"))
    approved_candidates = [
        item for item in code_candidates
        if _is_coder_approved_code(item)
    ]
    approved_icd = _compact_unique([
        str(item.get("code") or "").strip()
        for item in approved_candidates
        if str(item.get("code_set") or "").upper() == "ICD-10-CM"
    ], 6)
    approved_procedure = _compact_unique([
        str(item.get("code") or "").strip()
        for item in approved_candidates
        if str(item.get("code_set") or "").upper() in {"CPT", "HCPCS", "CPT/HCPCS"}
    ], 6)
    approved_medication = _compact_unique([
        str(item.get("code") or "").strip()
        for item in approved_candidates
        if str(item.get("code_set") or "").upper() in {"RXNORM", "NDC", "RXNORM/NDC"}
    ], 6)
    icd_ai_candidates = _compact_unique([
        str(item.get("code") or "").strip()
        for item in code_candidates
        if str(item.get("code_set") or "").upper() == "ICD-10-CM"
        and item.get("code")
        and item.get("code") != "needs_lookup"
    ], 6)
    procedure_ai_candidates = _compact_unique([
        str(item.get("code") or "").strip()
        for item in code_candidates
        if str(item.get("code_set") or "").upper() in {"CPT", "HCPCS", "CPT/HCPCS"}
        and item.get("code")
        and item.get("code") != "needs_lookup"
    ], 6)
    reviewed_candidates = [
        item for item in code_candidates
        if any(term in str(item.get("review_status") or "").lower() for term in ("reviewed", "approved"))
    ]
    diagnoses = _dict_items(request.get("diagnoses"))
    diagnosis_codes = _compact_unique([
        str(item.get("code") or "").strip()
        for item in diagnoses
        if item.get("code")
    ], 5)
    requested_item = _nested_field_value(request.get("requested_item"))
    service_category = str(request.get("service_category") or "unknown").lower()
    gap_text = " ".join(
        _clean_prior_auth_pdf_text(item.get("item") or "")
        for item in _dict_items(gaps.get("missing_items"))
    ).lower()

    if approved_icd:
        icd_sentence = f"Coder-approved ICD-10-CM codes for packet use are {_natural_join(approved_icd)}."
    elif icd_ai_candidates:
        icd_sentence = f"AI-recommended ICD-10-CM candidates are {_natural_join(icd_ai_candidates)}."
    elif diagnosis_codes:
        icd_sentence = f"Diagnosis code candidates are present in the packet: {_natural_join(diagnosis_codes)}."
    else:
        icd_sentence = "Diagnosis coding is not ready because the packet does not contain a confirmed ICD-10-CM code."
    procedure_detail = _procedure_detail_summary(requested_item, service_category)
    if approved_procedure:
        cpt_sentence = f"Coder-approved CPT/HCPCS codes for packet use are {_natural_join(approved_procedure)}."
    elif procedure_ai_candidates:
        cpt_sentence = (
            f"AI-recommended CPT/HCPCS candidates are {_natural_join(procedure_ai_candidates)}. "
            "These procedure codes still require coder review against licensed CPT/HCPCS references, payer rules, and the signed order."
        )
    elif "cpt" in gap_text:
        cpt_sentence = "Procedure coding is not ready because the CPT code is missing from the available order or request."
    else:
        cpt_sentence = (
            "Procedure coding has enough service text for candidate review, but final CPT selection still requires coder review "
            "against licensed CPT content, payer rules, and the signed order."
        )
    medication_sentence = (
        f"Coder-approved medication or supply code candidates are {_natural_join(approved_medication)}."
        if approved_medication else
        ""
    )
    review_sentence = (
        f"{len(reviewed_candidates)} code candidate row{' has' if len(reviewed_candidates) == 1 else 's have'} been marked reviewed or approved."
        if reviewed_candidates else
        "No AI-recommended code candidate has been marked coder-reviewed yet."
    )
    return (
        f"{icd_sentence} {procedure_detail} {cpt_sentence} {medication_sentence} {review_sentence} "
        "DocIntel should treat any AI-recommended ICD, CPT, HCPCS, RxNorm, or NDC value as a candidate only. "
        "A certified coder, billing specialist, or other qualified reviewer must confirm final codes before payer submission."
    )


def _prior_auth_final_codes_summary(packet: dict) -> str:
    final_rows = _approved_code_rows_by_set(packet)
    if not any(final_rows.values()):
        return (
            "No final coder-approved code set has been added yet. The packet may contain AI-recommended candidate rows, "
            "but those rows should not be treated as final billing or payer-submission codes until a certified coder, "
            "billing specialist, or qualified reviewer edits and approves them."
        )
    parts = []
    if final_rows["diagnosis"]:
        parts.append(f"Diagnosis coding is finalized for packet use with {_describe_code_rows(final_rows['diagnosis'])}")
    if final_rows["procedure"]:
        parts.append(f"Procedure, service, or supply coding is finalized for packet use with {_describe_code_rows(final_rows['procedure'])}")
    if final_rows["medication"]:
        parts.append(f"Medication coding is finalized for packet use with {_describe_code_rows(final_rows['medication'])}")
    return (
        f"{_sentence_from_fragments(parts)} These codes were marked reviewed or approved in the code recommendation table. "
        "They should still be handled as human-reviewed administrative coding support, not as a coverage guarantee."
    )


def _prior_auth_code_action_summary(packet: dict) -> str:
    recommendations = packet.get("code_recommendations") or {}
    rows = _dict_items(recommendations.get("candidates"))
    lookup_rows = [
        row for row in rows
        if str(row.get("code") or "").strip() in {"", "needs_lookup"}
    ]
    unapproved_rows = [
        row for row in rows
        if str(row.get("code") or "").strip() not in {"", "needs_lookup"} and not _is_coder_approved_code(row)
    ]
    if not rows:
        return (
            "No AI code recommendation rows are available yet. Generate code recommendations, then have the certified coder "
            "enter or approve the diagnosis, procedure, medication, and supply codes needed for submission."
        )
    if not lookup_rows and not unapproved_rows:
        return (
            "All populated code rows have been marked reviewed or approved. The final prior authorization packet can use those rows "
            "as the human-reviewed code set while keeping the required administrative review notice."
        )
    parts = []
    if lookup_rows:
        descriptions = _compact_unique([
            _clean_prior_auth_pdf_text(row.get("description") or row.get("basis") or row.get("code_set") or "code lookup")
            for row in lookup_rows
        ], 5)
        parts.append(f"enter final codes for {_natural_join(descriptions)}")
    if unapproved_rows:
        descriptions = _compact_unique([
            f"{row.get('code_set') or 'Code'} {row.get('code') or ''}".strip()
            for row in unapproved_rows
        ], 5)
        parts.append(f"review and approve {_natural_join(descriptions)}")
    return (
        f"Before final packet generation, the coder should {_natural_join(parts)}. "
        "Rows left as needs_lookup or coder_review_required will remain candidate-only and should not be presented as final codes."
    )


def _prior_auth_packet_readiness_lead(packet: dict) -> str:
    final_rows = _approved_code_rows_by_set(packet)
    effective_missing = _effective_missing_items(packet)
    has_core_codes = bool(final_rows["diagnosis"] and final_rows["procedure"])
    if has_core_codes and not effective_missing:
        return "The request has clinical support and coder-approved diagnosis and procedure codes for human review before payer submission."
    if has_core_codes:
        return "The request has coder-approved diagnosis and procedure codes, but some non-code documentation items still need human review."
    return "The request has partial clinical support, but code review or documentation is still needed before payer submission."


def _approved_code_rows_by_set(packet: dict) -> dict[str, list[dict]]:
    recommendations = packet.get("code_recommendations") or {}
    rows = [row for row in _dict_items(recommendations.get("candidates")) if _is_coder_approved_code(row)]
    grouped = {"diagnosis": [], "procedure": [], "medication": []}
    for row in rows:
        code_set = str(row.get("code_set") or "").upper()
        if code_set == "ICD-10-CM":
            grouped["diagnosis"].append(row)
        elif code_set in {"CPT", "HCPCS", "CPT/HCPCS"}:
            grouped["procedure"].append(row)
        elif code_set in {"RXNORM", "NDC", "RXNORM/NDC"}:
            grouped["medication"].append(row)
    return grouped


def _describe_code_rows(rows: list[dict]) -> str:
    values = []
    for row in rows:
        code_set = str(row.get("code_set") or "Code").strip()
        code = str(row.get("code") or "").strip()
        description = _clean_prior_auth_pdf_text(row.get("description") or "")
        if code and description:
            values.append(f"{code_set} {code} for {description}")
        elif code:
            values.append(f"{code_set} {code}")
    return _natural_join(_compact_unique(values, 8))


def _is_coder_approved_code(item: dict) -> bool:
    status = str(item.get("review_status") or "").lower()
    code = str(item.get("code") or "").strip()
    return bool(code and code != "needs_lookup" and ("approved" in status or "reviewed" in status))


def _effective_missing_items(packet: dict) -> list[dict]:
    gaps = packet.get("gap_detection") or {}
    missing_items = _dict_items(gaps.get("missing_items"))
    recommendations = packet.get("code_recommendations") or {}
    candidates = _dict_items(recommendations.get("candidates"))
    approved_sets = {str(item.get("code_set") or "").upper() for item in candidates if _is_coder_approved_code(item)}
    has_icd = "ICD-10-CM" in approved_sets
    has_procedure = bool({"CPT", "HCPCS", "CPT/HCPCS"} & approved_sets)
    has_medication = bool({"RXNORM", "NDC", "RXNORM/NDC"} & approved_sets)
    filtered = []
    for item in missing_items:
        text = _clean_prior_auth_pdf_text(item.get("item") or item.get("reason") or "").lower()
        if has_icd and ("icd" in text or "diagnosis code" in text):
            continue
        if has_procedure and ("cpt" in text or "hcpcs" in text or "procedure code" in text or "service code" in text):
            continue
        if has_medication and ("rxnorm" in text or "ndc" in text or "medication code" in text or "drug code" in text):
            continue
        filtered.append(item)
    return filtered


def _procedure_detail_summary(requested_item: str, service_category: str) -> str:
    text = str(requested_item or "").lower()
    details = []
    if "mri" in text:
        details.append("MRI")
    if "lumbar" in text:
        details.append("lumbar spine")
    if "without contrast" in text:
        details.append("without contrast")
    elif "with and without contrast" in text:
        details.append("with and without contrast")
    elif "with contrast" in text:
        details.append("with contrast")
    elif "mri" in text:
        details.append("contrast status needs confirmation")
    if details:
        return f"The requested procedure text indicates {_natural_join(details)}."
    return f"The requested service is categorized as {service_category}, but the procedure details should be confirmed before code selection."


def _summarize_prior_auth_readiness(criteria_items, match_items) -> str:
    met = []
    needs = []
    missing = []
    not_met = []
    for item in _dict_items(criteria_items) + _dict_items(match_items):
        status = item.get("status")
        criterion = _clean_prior_auth_pdf_text(item.get("criterion") or item.get("item") or "")
        if not criterion:
            continue
        normalized = str(status or "").lower()
        if normalized == "met":
            met.append(criterion)
        elif normalized == "needs_clarification":
            needs.append(criterion)
        elif normalized == "missing_evidence":
            missing.append(criterion)
        elif normalized == "not_met":
            not_met.append(criterion)
    parts = []
    if met:
        parts.append(f"Documented support is present for {_natural_join(_compact_unique(met, 3))}")
    if needs:
        parts.append(f"additional clarification is needed for {_natural_join(_compact_unique(needs, 3))}")
    if missing:
        parts.append(f"evidence is missing for {_natural_join(_compact_unique(missing, 3))}")
    if not_met:
        parts.append(f"some red-flag pathways appear not to be met, including {_natural_join(_compact_unique(not_met, 2))}")
    if not parts:
        return "The payer criteria review did not produce a clear readiness summary."
    return _sentence_from_fragments(parts)


def _summarize_clinical_story(request: dict, evidence: dict) -> str:
    findings = []
    for item in _dict_items(request.get("clinical_rationale")):
        finding = _clean_prior_auth_pdf_text(item.get("finding") or "")
        if finding:
            findings.append(finding)
    for item in _dict_items(evidence.get("supporting_evidence")):
        value = _clean_prior_auth_pdf_text(item.get("evidence") or "")
        if value:
            findings.append(value)
    findings = _compact_unique(findings, 6)
    if findings:
        return f"The clinical record describes {_natural_join(findings)}."
    return "The clinical story was not detailed enough in the available packet."


def _summarize_missing_evidence(items) -> str:
    grouped = _missing_info_items_by_priority(items)
    high = grouped["high"]
    other = grouped["other"]
    if high or other:
        parts = []
        if high:
            parts.append(f"The most important missing items are {_natural_join(_compact_unique(high, 5))}")
        if other:
            parts.append(f"Additional items to clarify include {_natural_join(_compact_unique(other, 4))}")
        return _sentence_from_fragments(parts)
    return "No missing evidence was identified, but human review is still required before submission."


def _missing_info_items_by_priority(items) -> dict[str, list[str]]:
    grouped = {"high": [], "other": []}
    for item in _dict_items(items):
        missing = _clean_prior_auth_pdf_text(item.get("item") or "")
        priority = str(item.get("priority") or "").lower()
        if not missing:
            continue
        if priority == "high":
            grouped["high"].append(missing)
        else:
            grouped["other"].append(missing)
    grouped["high"] = _compact_unique(grouped["high"], 6)
    grouped["other"] = _compact_unique(grouped["other"], 5)
    return grouped


def _summarize_submission_risks(items) -> str:
    risks = []
    for item in _dict_items(items):
        risk = _clean_prior_auth_pdf_text(item.get("risk") or "")
        if risk:
            risks.append(risk)
    if risks:
        return f"The main submission risks are {_natural_join(_compact_unique(risks, 4))}."
    return "No specific submission risks were identified by the workflow."


def _summarize_next_actions(items) -> str:
    actions = []
    for item in _dict_items(items):
        action = _clean_prior_auth_pdf_text(item.get("action") or "")
        owner = _clean_prior_auth_pdf_text(item.get("owner") or "")
        if action and owner:
            actions.append(f"{owner} should {action[0].lower() + action[1:] if len(action) > 1 else action}")
        elif action:
            actions.append(action)
    if actions:
        return f"Before submission, {_natural_join(_compact_unique(actions, 6))}."
    return "No specific next actions were generated. The packet should be reviewed before payer submission."


def _humanize_prior_auth_decision(decision: str | None) -> str:
    normalized = str(decision or "").strip().lower()
    if normalized == "ready_for_human_review":
        return "The packet appears ready for human review before submission."
    if normalized == "not_supported_by_available_docs":
        return "The available documents do not fully support submission without additional clinical documentation."
    return "The recommended decision is to collect more information before submission."


def _dict_items(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _compact_unique(items: list[str], limit: int = 6) -> list[str]:
    seen = set()
    clean = []
    for item in items:
        value = _clean_prior_auth_pdf_text(item).strip().rstrip(".")
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        clean.append(value)
        if len(clean) >= limit:
            break
    return clean


def _sentence_from_fragments(parts: list[str]) -> str:
    clean = [part.strip().rstrip(".") for part in parts if part.strip()]
    if not clean:
        return "No details were available."
    sentence = "; ".join(clean)
    return sentence[0].upper() + sentence[1:] + "."


def _clean_prior_auth_pdf_text(value) -> str:
    text = str(value or "")
    text = re.sub(r"\[\s*Source\s+\d+\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:policy source|patient source|source)\s*:\s*Source\s+\d+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSource\s+\d+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:policy source|patient source|source)\s*:\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _natural_join(items: list[str]) -> str:
    clean = [item for item in items if item]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return f"{', '.join(clean[:-1])}, and {clean[-1]}"


def _nested_field_value(value) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "Not found")
    return str(value or "Not found")


def _render_prior_auth_packet_pdf(title: str, text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER, rightMargin=46, leftMargin=46, topMargin=42, bottomMargin=48)
        styles = _avs_pdf_styles(getSampleStyleSheet())
        story = _prior_auth_pdf_header(title, styles)

        section_titles = {
            "PRIOR AUTHORIZATION PACKET",
            "Request Overview",
            "Clinical Story",
            "Key Points Summary",
            "Final Coder-Reviewed Codes",
            "Code Readiness",
            "What Is Missing",
            "Medical Necessity Draft",
            "Submission Readiness",
            "Next Actions",
            "Human Review Notice",
        }
        pending_heading = None
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                story.append(Spacer(1, 8))
                continue
            if clean == "PRIOR AUTHORIZATION PACKET":
                continue
            if clean.startswith("Generated from prior authorization run:"):
                story.append(Paragraph(_xml_escape(clean), styles["Meta"]))
                story.append(Spacer(1, 10))
            elif clean in section_titles:
                pending_heading = Paragraph(_xml_escape(clean), styles["SectionHeading"])
            elif clean.startswith("- "):
                bullet = Paragraph(_xml_escape(clean[2:]), styles["BulletBody"])
                if pending_heading:
                    story.append(KeepTogether([pending_heading, Spacer(1, 5), bullet]))
                    pending_heading = None
                else:
                    story.append(bullet)
            else:
                paragraph_style = styles["NoticeBody"] if pending_heading and pending_heading.getPlainText() == "Human Review Notice" else styles["Body"]
                para = Paragraph(_xml_escape(clean), paragraph_style)
                if pending_heading:
                    story.append(KeepTogether([pending_heading, Spacer(1, 5), para]))
                    pending_heading = None
                else:
                    story.append(para)
        doc.build(story, onFirstPage=_prior_auth_pdf_footer, onLaterPages=_prior_auth_pdf_footer)
        return buffer.getvalue()
    except Exception:
        return _render_minimal_pdf(title, text)


def _render_missing_info_request_pdf(title: str, text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER, rightMargin=46, leftMargin=46, topMargin=42, bottomMargin=48)
        styles = _avs_pdf_styles(getSampleStyleSheet())
        story = _missing_info_pdf_header(title, styles)

        section_titles = {
            "MISSING INFORMATION REQUEST",
            "Request Summary",
            "Information Needed",
            "Code Readiness",
            "Code Review Actions",
            "Why This Is Needed",
            "Recommended Follow-Up",
            "Submission Readiness",
            "Review Notice",
        }
        pending_heading = None
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                story.append(Spacer(1, 8))
                continue
            if clean == "MISSING INFORMATION REQUEST":
                continue
            if clean in section_titles:
                pending_heading = Paragraph(_xml_escape(clean), styles["SectionHeading"])
            else:
                paragraph_style = styles["NoticeBody"] if pending_heading and pending_heading.getPlainText() == "Review Notice" else styles["Body"]
                para = Paragraph(_xml_escape(clean), paragraph_style)
                if pending_heading:
                    story.append(KeepTogether([pending_heading, Spacer(1, 5), para]))
                    pending_heading = None
                else:
                    story.append(para)
        doc.build(story, onFirstPage=_missing_info_pdf_footer, onLaterPages=_missing_info_pdf_footer)
        return buffer.getvalue()
    except Exception:
        return _render_minimal_pdf(title, text)


def _avs_title(packet: dict, run_id: str) -> str:
    context = packet.get("patient_context") or {}
    name = ((context.get("patient_name") or {}).get("value") or "Patient").strip()
    date = ((context.get("encounter_date") or {}).get("value") or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    return f"After Visit Summary - {name} - {date} - {run_id[:8]}"


def _format_after_visit_summary_text(packet: dict, run_id: str) -> str:
    avs = packet.get("after_visit_summary") or {}
    context = packet.get("patient_context") or {}
    lines = [
        "AFTER VISIT SUMMARY",
        f"Generated from clinical scribe run: {run_id}",
        "",
        "Patient / Encounter",
        f"Patient: {_field_value(context, 'patient_name')}",
        f"DOB: {_field_value(context, 'date_of_birth')}",
        f"Encounter date: {_field_value(context, 'encounter_date')}",
        f"Provider: {_field_value(context, 'provider')}",
        f"Facility: {_field_value(context, 'facility')}",
        f"Encounter type: {_field_value(context, 'encounter_type')}",
        "",
        "Summary",
        avs.get("summary") or "Not provided.",
        "",
        "Visit Reason",
        avs.get("visit_reason") or "Not provided.",
        "",
        "Clinician Impression",
        avs.get("clinician_impression") or "Review required.",
    ]
    sections = [
        ("Today We Discussed", avs.get("today_we_discussed"), "item"),
        ("Medication Instructions", avs.get("medication_instructions"), "item"),
        ("Tests and Orders", avs.get("tests_and_orders"), "item"),
        ("Referrals", avs.get("referrals"), "item"),
        ("Follow-Up Plan", avs.get("follow_up_plan"), "action"),
        ("Warning Signs", avs.get("warning_signs"), "sign"),
        ("Preventive Care Reminders", avs.get("preventive_care_reminders"), "item"),
        ("Facility Coordination", avs.get("facility_coordination"), "item"),
        ("Patient Questions", avs.get("patient_questions"), "question"),
    ]
    for title, items, key in sections:
        lines.extend(["", title])
        if not items:
            lines.append("- None listed.")
            continue
        for item in items:
            if not isinstance(item, dict):
                lines.append(f"- {item}")
                continue
            value = item.get(key) or item.get("item") or item.get("action") or item.get("question") or item.get("sign") or ""
            extra = []
            if item.get("owner"):
                extra.append(f"owner: {item['owner']}")
            if item.get("due_date"):
                extra.append(f"due: {item['due_date']}")
            if item.get("status"):
                extra.append(f"status: {item['status']}")
            if item.get("recommended_action"):
                extra.append(f"action: {item['recommended_action']}")
            if item.get("source"):
                extra.append(f"source: {item['source']}")
            suffix = f" ({'; '.join(extra)})" if extra else ""
            lines.append(f"- {value}{suffix}")
    lines.extend([
        "",
        "Review Notice",
        "This after visit summary is generated by DocIntel Clinical Scribe and requires clinician review. It is not diagnosis, treatment, or medical advice.",
    ])
    return sanitize_text_for_storage("\n".join(lines))


def _render_after_visit_summary_pdf(title: str, text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER, rightMargin=46, leftMargin=46, topMargin=42, bottomMargin=48)
        styles = _avs_pdf_styles(getSampleStyleSheet())
        story = _avs_pdf_header(title, styles)

        section_titles = {
            "AFTER VISIT SUMMARY", "Patient / Encounter", "Summary", "Visit Reason", "Clinician Impression",
            "Today We Discussed", "Medication Instructions", "Tests and Orders", "Referrals", "Follow-Up Plan",
            "Warning Signs", "Preventive Care Reminders", "Facility Coordination", "Patient Questions",
            "Review Notice",
        }
        pending_heading = None
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                story.append(Spacer(1, 8))
                continue
            if clean == "AFTER VISIT SUMMARY":
                continue
            if clean.startswith("Generated from clinical scribe run:"):
                story.append(Paragraph(_xml_escape(clean), styles["Meta"]))
                story.append(Spacer(1, 10))
            elif clean in section_titles:
                pending_heading = Paragraph(_xml_escape(clean), styles["SectionHeading"])
            elif clean.startswith("- "):
                bullet = Paragraph(_xml_escape(clean[2:]), styles["BulletBody"])
                if pending_heading:
                    story.append(KeepTogether([pending_heading, Spacer(1, 5), bullet]))
                    pending_heading = None
                else:
                    story.append(bullet)
            else:
                paragraph_style = styles["NoticeBody"] if pending_heading and pending_heading.getPlainText() == "Review Notice" else styles["Body"]
                para = Paragraph(_xml_escape(clean), paragraph_style)
                if pending_heading:
                    story.append(KeepTogether([pending_heading, Spacer(1, 5), para]))
                    pending_heading = None
                else:
                    story.append(para)
        doc.build(story, onFirstPage=_avs_pdf_footer, onLaterPages=_avs_pdf_footer)
        return buffer.getvalue()
    except Exception:
        return _render_minimal_pdf(title, text)


def _avs_pdf_styles(base_styles):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    bengali_font = _register_bengali_pdf_font()
    base_styles.add(ParagraphStyle(
        name="BrandBangla",
        parent=base_styles["Normal"],
        fontName=bengali_font,
        fontSize=18,
        leading=20,
        textColor=colors.HexColor("#4ade80"),
    ))
    base_styles.add(ParagraphStyle(
        name="BrandEnglish",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#6b7280"),
    ))
    base_styles.add(ParagraphStyle(
        name="BrandTag",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#15803d"),
    ))
    base_styles.add(ParagraphStyle(
        name="DocTitle",
        parent=base_styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#064e3b"),
        alignment=TA_CENTER,
        spaceAfter=10,
    ))
    base_styles.add(ParagraphStyle(
        name="HeaderTitle",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#064e3b"),
    ))
    base_styles.add(ParagraphStyle(
        name="HeaderSubtitle",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#15803d"),
    ))
    base_styles.add(ParagraphStyle(
        name="Meta",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#6b7280"),
        alignment=TA_CENTER,
    ))
    base_styles.add(ParagraphStyle(
        name="SectionHeading",
        parent=base_styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=14,
        textColor=colors.HexColor("#065f46"),
        borderColor=colors.HexColor("#a7f3d0"),
        borderWidth=0,
        borderPadding=0,
        spaceBefore=8,
        spaceAfter=5,
        keepWithNext=True,
    ))
    base_styles.add(ParagraphStyle(
        name="Body",
        parent=base_styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1f2937"),
        spaceAfter=4,
    ))
    base_styles.add(ParagraphStyle(
        name="BulletBody",
        parent=base_styles["Body"],
        leftIndent=14,
        firstLineIndent=-8,
        bulletIndent=0,
        bulletFontName="Helvetica-Bold",
        bulletFontSize=8,
        bulletText="-",
    ))
    base_styles.add(ParagraphStyle(
        name="NoticeBody",
        parent=base_styles["Body"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#7f1d1d"),
        backColor=colors.HexColor("#fef2f2"),
        borderColor=colors.HexColor("#fecaca"),
        borderWidth=0.6,
        borderPadding=7,
        spaceBefore=3,
    ))
    return base_styles


def _register_bengali_pdf_font() -> str:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        font_name = "AdarBengali"
        if font_name in pdfmetrics.getRegisteredFontNames():
            return font_name
        candidates = [
            os.getenv("ADAR_BENGALI_FONT_PATH", ""),
            "/System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources/Fonts/Subsets/NovemberBanglaTraditional.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansBengaliUI-Regular.ttf",
            "/usr/share/fonts/truetype/lohit-bengali/Lohit-Bengali.ttf",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
    except Exception:
        pass
    return "Helvetica-Bold"


def _avs_pdf_header(title: str, styles) -> list:
    return _healthcare_pdf_header(
        title,
        styles,
        document_title="After Visit Summary",
        document_subtitle="Patient-ready clinical scribe PDF artifact",
        brand_tag="Document Intelligence | Clinical Scribe",
    )


def _prior_auth_pdf_header(title: str, styles) -> list:
    return _healthcare_pdf_header(
        title,
        styles,
        document_title="Prior Authorization Packet",
        document_subtitle="Human-review payer evidence packet",
        brand_tag="Document Intelligence | Prior Authorization",
    )


def _missing_info_pdf_header(title: str, styles) -> list:
    return _healthcare_pdf_header(
        title,
        styles,
        document_title="Missing Information Request",
        document_subtitle="Care team follow-up request",
        brand_tag="Document Intelligence | Prior Authorization",
    )


def _healthcare_pdf_header(title: str, styles, *, document_title: str, document_subtitle: str, brand_tag: str) -> list:
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Circle, Drawing, Line
    from reportlab.platypus import HRFlowable, Image, Paragraph, Spacer, Table

    logo_path = _adar_docintel_logo_path()
    if logo_path:
        brand = Image(logo_path, width=210, height=46)
    else:
        leaf = Drawing(24, 24)
        leaf.add(Circle(9, 14, 6, fillColor=colors.HexColor("#4ade80"), strokeColor=colors.HexColor("#15803d"), strokeWidth=0.6))
        leaf.add(Circle(15, 10, 6, fillColor=colors.HexColor("#22c55e"), strokeColor=colors.HexColor("#15803d"), strokeWidth=0.6))
        leaf.add(Line(7, 6, 18, 18, strokeColor=colors.HexColor("#166534"), strokeWidth=1))
        brand_text = [
            Paragraph(
                f'<font name="{styles["BrandBangla"].fontName}" color="#4ade80" size="18"><b>আদর</b></font> '
                '<font name="Helvetica" color="#6b7280" size="12">DocIntel</font>',
                styles["BrandEnglish"],
            ),
            Paragraph(_xml_escape(brand_tag), styles["BrandTag"]),
        ]
        brand = Table(
            [[leaf, brand_text]],
            colWidths=[34, 410],
            style=[
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
                ("TOPPADDING", (0, 0), (0, 0), 2),
                ("BOTTOMPADDING", (0, 0), (0, 0), 2),
                ("LEFTPADDING", (1, 0), (1, 0), 2),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ],
        )
    header = Table(
        [[
            brand,
            [
                Paragraph(_xml_escape(document_title), styles["HeaderTitle"]),
                Paragraph(_xml_escape(document_subtitle), styles["HeaderSubtitle"]),
            ],
        ]],
        colWidths=[230, 260],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0fdf4")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#bbf7d0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 10),
            ("RIGHTPADDING", (0, 0), (0, 0), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (1, 0), (1, 0), 14),
            ("RIGHTPADDING", (1, 0), (1, 0), 12),
        ],
    )
    return [
        header,
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#d1fae5"), spaceAfter=14),
        Paragraph(_xml_escape(title), styles["DocTitle"]),
    ]


def _adar_docintel_logo_path() -> str | None:
    assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
    for filename in ("adar_docintel_logo_light.png", "adar_docintel_logo.png"):
        path = os.path.join(assets_dir, filename)
        if os.path.exists(path):
            return path
    return None


def _avs_pdf_footer(canvas, doc):
    _healthcare_pdf_footer(
        canvas,
        doc,
        "Generated by Adar DocIntel Clinical Scribe. Clinician review required before patient use.",
    )


def _prior_auth_pdf_footer(canvas, doc):
    _healthcare_pdf_footer(
        canvas,
        doc,
        "Generated by Adar DocIntel Prior Authorization Assistant. Human review required before payer submission.",
    )


def _missing_info_pdf_footer(canvas, doc):
    _healthcare_pdf_footer(
        canvas,
        doc,
        "Generated by Adar DocIntel Prior Authorization Assistant. Human review required before requesting or submitting information.",
    )


def _healthcare_pdf_footer(canvas, doc, notice: str):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER

    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#d1d5db"))
    canvas.setLineWidth(0.4)
    canvas.line(doc.leftMargin, 34, LETTER[0] - doc.rightMargin, 34)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.drawString(doc.leftMargin, 22, notice)
    canvas.drawRightString(LETTER[0] - doc.rightMargin, 22, f"Page {doc.page}")
    canvas.restoreState()


def _render_minimal_pdf(title: str, text: str) -> bytes:
    lines = [title, ""] + text.splitlines()
    pages = []
    current = []
    for line in lines:
        wrapped = textwrap.wrap(line, width=86) or [""]
        for part in wrapped:
            current.append(part)
            if len(current) >= 48:
                pages.append(current)
                current = []
    if current:
        pages.append(current)

    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = []
    next_id = 3
    font_id = None
    for page_lines in pages:
        page_id = next_id
        content_id = next_id + 1
        kids.append(f"{page_id} 0 R")
        content = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
        for idx, line in enumerate(page_lines):
            if idx:
                content.append("T*")
            content.append(f"({_pdf_escape(line)}) Tj")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1", errors="replace")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {content_id + 1} 0 R >> >> /Contents {content_id} 0 R >>")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream")
        font_id = content_id + 1
        objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        next_id += 3
    objects.insert(1, f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>")
    return _assemble_pdf(objects)


def _assemble_pdf(objects: list[str]) -> bytes:
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n{obj}\nendobj\n".encode("latin-1", errors="replace"))
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


def _field_value(context: dict, key: str) -> str:
    value = context.get(key) if isinstance(context, dict) else None
    if isinstance(value, dict):
        return str(value.get("value") or "Not found")
    return str(value or "Not found")


def _xml_escape(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _safe_filename(value: str, suffix: str = ".txt") -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", " ") else "_" for ch in value).strip()
    safe = "_".join(safe.split())[:80] or "clinical_visit_transcript"
    return safe if safe.endswith(suffix) else safe + suffix


def _language_code(locale: str) -> str:
    lang = (locale or "en").split("-")[0].lower()
    return lang if lang in {"en", "es", "bn", "hi", "ar"} else "en"
