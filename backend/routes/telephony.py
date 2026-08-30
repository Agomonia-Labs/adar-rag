from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser
from database.connection import get_db
from routes.workspaces import _require_role
from services.telephony_intelligence import path_from_gcs_uri, process_call
from services.vectordb import delete_document_vectors
import services.storage as gcs

router = APIRouter()


class CompletedCall(BaseModel):
    provider: str = "google"
    external_call_id: str
    external_account_id: str | None = None
    workspace_id: str | None = None
    recording_gcs_uri: str | None = None
    recording_url: str | None = None
    recording_mime_type: str = "audio/wav"
    language_code: str = "en-US"
    direction: str = "inbound"
    caller_number: str | None = None
    destination_number: str | None = None
    consent_status: str = "confirmed"
    duration_seconds: float | None = None
    transcript: str | None = None
    redact_pii: bool = True
    provider_payload: dict = Field(default_factory=dict)


class IntegrationRequest(BaseModel):
    provider: str = "google"
    external_account_id: str
    workspace_id: str | None = None
    configuration: dict = Field(default_factory=dict)


def _jsonable(row):
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif value is not None and key.endswith("_id"):
            data[key] = str(value)
    return data


async def _create_call(db, body: CompletedCall, user_id: str, workspace_id: str | None) -> tuple[str, str]:
    existing = await db.fetchrow("SELECT id,document_id FROM telephony_calls WHERE provider=$1 AND external_call_id=$2", body.provider, body.external_call_id)
    if existing:
        return str(existing["id"]), str(existing["document_id"])
    doc_id, call_id = str(uuid4()), str(uuid4())
    safe_name = f"Call {body.external_call_id}.transcript.txt"
    source_path = gcs.source_path(user_id, doc_id, safe_name)
    recording_gcs_uri = body.recording_gcs_uri
    if recording_gcs_uri:
        source_recording_path = path_from_gcs_uri(recording_gcs_uri)
        extension = os.path.splitext(source_recording_path)[1] or ".wav"
        source_path = gcs.source_path(user_id, doc_id, f"recording{extension}")
        if source_recording_path != source_path:
            await gcs.copy_blob(source_recording_path, source_path)
        recording_gcs_uri = f"gs://{gcs.GCS_BUCKET}/{source_path}"
    await db.execute(
        """INSERT INTO documents(id,user_id,filename,original_name,file_type,file_size,gcs_source_path,
           gcs_chunks_dir,status,workspace_id,doc_type,doc_domain,doc_language,doc_metadata)
           VALUES($1,$2,$3,$3,'audio',0,$4,$5,'processing',$6,'call_transcript','conversation',$7,$8::jsonb)""",
        doc_id, user_id, safe_name, source_path, gcs.chunks_dir(user_id, doc_id), workspace_id,
        body.language_code.split("-")[0], json.dumps({"source_type": "telephony", "external_call_id": body.external_call_id}),
    )
    await db.execute(
        """INSERT INTO telephony_calls(id,provider,external_call_id,external_account_id,document_id,user_id,
           workspace_id,direction,caller_number,destination_number,language_code,consent_status,
           recording_gcs_uri,recording_url,recording_mime_type,duration_seconds,provider_payload)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb)""",
        call_id, body.provider, body.external_call_id, body.external_account_id, doc_id, user_id,
        workspace_id, body.direction, body.caller_number, body.destination_number, body.language_code,
        body.consent_status, recording_gcs_uri, body.recording_url, body.recording_mime_type,
        body.duration_seconds, json.dumps(body.provider_payload),
    )
    return call_id, doc_id


@router.post("/calls")
async def create_test_call(body: CompletedCall, background: BackgroundTasks, current_user: CurrentUser, db=Depends(get_db)):
    workspace_id = body.workspace_id
    if workspace_id:
        await _require_role(db, workspace_id, str(current_user["id"]), "editor")
    if body.consent_status != "confirmed":
        raise HTTPException(400, "Recorded-call ingestion requires confirmed consent")
    if not body.recording_gcs_uri and not body.transcript:
        raise HTTPException(400, "Provide recording_gcs_uri or transcript")
    call_id, doc_id = await _create_call(db, body, str(current_user["id"]), workspace_id)
    background.add_task(process_call, call_id, body.transcript, body.redact_pii)
    return {"call_id": call_id, "document_id": doc_id, "status": "received"}


@router.post("/integrations")
async def save_integration(body: IntegrationRequest, current_user: CurrentUser, db=Depends(get_db)):
    if body.workspace_id:
        await _require_role(db, body.workspace_id, str(current_user["id"]), "owner")
    row = await db.fetchrow(
        """INSERT INTO telephony_integrations(provider,external_account_id,workspace_id,owner_user_id,configuration)
           VALUES($1,$2,$3,$4,$5::jsonb)
           ON CONFLICT(provider,external_account_id) DO UPDATE SET
             workspace_id=EXCLUDED.workspace_id, owner_user_id=EXCLUDED.owner_user_id,
             configuration=EXCLUDED.configuration, enabled=TRUE, updated_at=NOW()
           RETURNING *""",
        body.provider, body.external_account_id, body.workspace_id, str(current_user["id"]),
        json.dumps(body.configuration),
    )
    return _jsonable(row)


@router.get("/integrations")
async def list_integrations(current_user: CurrentUser, db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM telephony_integrations WHERE owner_user_id=$1 ORDER BY created_at DESC", str(current_user["id"]))
    return [_jsonable(row) for row in rows]


@router.post("/webhooks/completed-call")
async def completed_call_webhook(body: CompletedCall, background: BackgroundTasks,
                                  x_docintel_webhook_secret: str | None = Header(default=None), db=Depends(get_db)):
    expected = os.getenv("TELEPHONY_WEBHOOK_SECRET", "")
    if not expected or not x_docintel_webhook_secret or not hmac.compare_digest(expected, x_docintel_webhook_secret):
        raise HTTPException(401, "Invalid webhook secret")
    integration = await db.fetchrow(
        """SELECT owner_user_id,workspace_id FROM telephony_integrations
           WHERE provider=$1 AND external_account_id=$2 AND enabled=TRUE""",
        body.provider, body.external_account_id,
    )
    if not integration:
        raise HTTPException(404, "No enabled telephony integration matches this provider account")
    if body.consent_status != "confirmed":
        raise HTTPException(400, "Recording consent was not confirmed")
    if not body.recording_gcs_uri and not body.transcript:
        raise HTTPException(400, "Completed-call webhook requires recording_gcs_uri or transcript")
    call_id, doc_id = await _create_call(db, body, str(integration["owner_user_id"]),
                                         str(integration["workspace_id"]) if integration["workspace_id"] else None)
    background.add_task(process_call, call_id, body.transcript, body.redact_pii)
    return {"accepted": True, "call_id": call_id, "document_id": doc_id}


@router.get("/calls")
async def list_calls(current_user: CurrentUser, workspace_id: str | None = None, db=Depends(get_db)):
    user_id = str(current_user["id"])
    if workspace_id:
        await _require_role(db, workspace_id, user_id, "viewer")
        rows = await db.fetch("SELECT * FROM telephony_calls WHERE workspace_id=$1 ORDER BY created_at DESC", workspace_id)
    else:
        rows = await db.fetch("SELECT * FROM telephony_calls WHERE user_id=$1 AND workspace_id IS NULL ORDER BY created_at DESC", user_id)
    return [_jsonable(row) for row in rows]


async def _owned_call(db, call_id: str, user_id: str, min_role: str = "viewer"):
    row = await db.fetchrow("SELECT * FROM telephony_calls WHERE id=$1", call_id)
    if not row:
        raise HTTPException(404, "Call not found")
    if row["workspace_id"]:
        await _require_role(db, str(row["workspace_id"]), user_id, min_role)
    elif str(row["user_id"]) != user_id:
        raise HTTPException(404, "Call not found")
    return row


@router.get("/calls/{call_id}")
async def get_call(call_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await _owned_call(db, call_id, str(current_user["id"]))
    segments = await db.fetch("SELECT * FROM telephony_segments WHERE call_id=$1 ORDER BY segment_index", call_id)
    data = _jsonable(row)
    data["segments"] = [_jsonable(segment) for segment in segments]
    return data


@router.post("/calls/{call_id}/retry")
async def retry_call(call_id: str, background: BackgroundTasks, current_user: CurrentUser, db=Depends(get_db)):
    row = await _owned_call(db, call_id, str(current_user["id"]), "editor")
    await db.execute("UPDATE telephony_calls SET processing_status='received',processing_step='received',progress_pct=0,error_message=NULL,updated_at=NOW() WHERE id=$1", call_id)
    transcript = None
    if not row["recording_gcs_uri"]:
        segments = await db.fetch("SELECT speaker,start_seconds,end_seconds,transcript FROM telephony_segments WHERE call_id=$1 ORDER BY segment_index", call_id)
        transcript = "\n".join(
            f"[{segment['start_seconds']}-{segment['end_seconds']}] {segment['speaker']}: {segment['transcript']}"
            for segment in segments
        ) or None
    background.add_task(process_call, call_id, transcript)
    return {"ok": True, "call_id": call_id}


@router.delete("/calls/{call_id}")
async def delete_call(call_id: str, current_user: CurrentUser, db=Depends(get_db)):
    row = await _owned_call(db, call_id, str(current_user["id"]), "editor")
    doc_id, user_id = str(row["document_id"]), str(row["user_id"])
    warnings = []
    try:
        await gcs.delete_prefix(f"users/{user_id}/documents/{doc_id}/")
    except Exception as exc:
        warnings.append(f"GCS cleanup skipped: {exc}")
    try:
        await delete_document_vectors(doc_id)
    except Exception as exc:
        warnings.append(f"Vector cleanup skipped: {exc}")
    await db.execute("DELETE FROM documents WHERE id=$1", doc_id)
    return {"deleted": call_id, "document_id": doc_id, "warnings": warnings}
