from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.adk_workflow import WorkflowConfigError, load_workflow_config, run_multi_agent_workflow
from services.audit import audit, ip_from, ua_from
from services.healthcare_agent_tools import HEALTHCARE_AGENT_TOOLS
from services.healthcare_intelligence import HealthcareIntelligenceError, build_healthcare_context
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


class HealthcareApprovalRequest(BaseModel):
    approved_packet: dict | None = None
    notes: str | None = None


class PriorAuthWorkflowRequest(BaseModel):
    policy_document_ids: list[str] = []


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


def _safe_filename(value: str, suffix: str = ".txt") -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", " ") else "_" for ch in value).strip()
    safe = "_".join(safe.split())[:80] or "clinical_visit_transcript"
    return safe if safe.endswith(suffix) else safe + suffix


def _language_code(locale: str) -> str:
    lang = (locale or "en").split("-")[0].lower()
    return lang if lang in {"en", "es", "bn", "hi", "ar"} else "en"
