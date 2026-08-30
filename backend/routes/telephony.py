from __future__ import annotations

import hmac
import json
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser
from database.connection import get_db
from routes.workspaces import _require_role
from services.telephony_intelligence import path_from_gcs_uri, process_call
from services.conversation_assistant import (
    DEFAULT_TEMPLATES,
    MAX_TURN_AUDIO_BYTES,
    SUPPORTED_AUDIO_TYPES,
    generate_assistant_turn,
    initial_greeting,
    json_object_value,
    synthesize_speech,
    transcribe_turn,
)
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


class ConversationSessionRequest(BaseModel):
    workspace_id: str | None = None
    template_id: str = "customer-knowledge-capture"
    language_code: str = "en-US"
    title: str = "Customer Knowledge Conversation"
    redact_pii: bool = True


class ConsentRequest(BaseModel):
    confirmed: bool


class TemplateRequest(BaseModel):
    workspace_id: str | None = None
    name: str
    description: str = ""
    instructions: str = ""
    fields: list[dict] = Field(default_factory=list)


class SessionStateRequest(BaseModel):
    collected_fields: dict = Field(default_factory=dict)
    review_status: str | None = None


class TranscriptApprovalRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=500_000)


class ConversationSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    language_code: str = "en-US"


def _jsonable(row):
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif value is not None and key.endswith("_id"):
            data[key] = str(value)
    return data


async def _conversation_template(db, template_id: str, user_id: str, workspace_id: str | None) -> dict:
    built_in = next((item for item in DEFAULT_TEMPLATES if item["id"] == template_id), None)
    if built_in:
        return built_in
    row = await db.fetchrow(
        """SELECT * FROM conversation_templates WHERE id=$1 AND active=TRUE
           AND (owner_user_id=$2 OR (workspace_id IS NOT NULL AND EXISTS (
             SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=conversation_templates.workspace_id
               AND wm.user_id=$2)))""",
        template_id, user_id,
    )
    if not row:
        raise HTTPException(404, "Conversation template not found")
    result = _jsonable(row)
    result["fields"] = list(row["fields"] or [])
    return result


async def _create_in_app_session(db, body: ConversationSessionRequest, user_id: str, template: dict) -> tuple[str, str]:
    session_id, doc_id = str(uuid4()), str(uuid4())
    title = re.sub(r"[^A-Za-z0-9 ._()-]", "", body.title).strip()[:120] or "Guided Conversation"
    safe_name = f"{title} - {session_id[:8]}.transcript.txt"
    source_path = gcs.source_path(user_id, doc_id, safe_name)
    state = {
        "template": template,
        "collected_fields": {},
        "missing_required_fields": [field["key"] for field in template.get("fields", []) if field.get("required")],
        "redact_pii": body.redact_pii,
        "ready_to_finish": False,
    }
    async with db.transaction():
        await db.execute(
            """INSERT INTO documents(id,user_id,filename,original_name,file_type,file_size,gcs_source_path,
               gcs_chunks_dir,status,workspace_id,doc_type,doc_domain,doc_language,doc_metadata)
               VALUES($1,$2,$3,$3,'audio',0,$4,$5,'processing',$6,'conversation_transcript','conversation',$7,$8::jsonb)""",
            doc_id, user_id, safe_name, source_path, gcs.chunks_dir(user_id, doc_id), body.workspace_id,
            body.language_code.split("-")[0], json.dumps({"source_type": "conversation_assistant", "session_id": session_id}),
        )
        await db.execute(
            """INSERT INTO telephony_calls(
               id,provider,external_call_id,document_id,user_id,workspace_id,direction,language_code,
               consent_status,processing_status,processing_step,source_channel,session_state,review_status,started_at)
               VALUES($1,'docintel_app',$2,$3,$4,$5,'in_app',$6,'pending','active','awaiting_consent',
                      'in_app',$7::jsonb,'draft',NOW())""",
            session_id, session_id, doc_id, user_id, body.workspace_id, body.language_code, json.dumps(state),
        )
    return session_id, doc_id


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


@router.get("/conversation/templates")
async def list_conversation_templates(current_user: CurrentUser, workspace_id: str | None = None, db=Depends(get_db)):
    user_id = str(current_user["id"])
    if workspace_id:
        await _require_role(db, workspace_id, user_id, "viewer")
    rows = await db.fetch(
        """SELECT * FROM conversation_templates WHERE active=TRUE AND
           (owner_user_id=$1 OR workspace_id=$2) ORDER BY name""",
        user_id, workspace_id,
    )
    custom = []
    for row in rows:
        item = _jsonable(row)
        item["fields"] = list(row["fields"] or [])
        custom.append(item)
    return [*DEFAULT_TEMPLATES, *custom]


@router.post("/conversation/templates")
async def create_conversation_template(body: TemplateRequest, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    if body.workspace_id:
        await _require_role(db, body.workspace_id, user_id, "editor")
    if not body.name.strip() or not body.fields:
        raise HTTPException(400, "Template name and at least one field are required")
    keys = [str(field.get("key") or "").strip() for field in body.fields]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise HTTPException(400, "Every template field needs a unique key")
    row = await db.fetchrow(
        """INSERT INTO conversation_templates(workspace_id,owner_user_id,name,description,instructions,fields)
           VALUES($1,$2,$3,$4,$5,$6::jsonb) RETURNING *""",
        body.workspace_id, user_id, body.name.strip(), body.description.strip(), body.instructions.strip(),
        json.dumps(body.fields),
    )
    return _jsonable(row)


@router.post("/conversation/sessions")
async def start_conversation_session(body: ConversationSessionRequest, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    if body.language_code.lower().startswith("bn"):
        body = body.model_copy(update={"language_code": "bn-BD"})
    if body.workspace_id:
        await _require_role(db, body.workspace_id, user_id, "editor")
    template = await _conversation_template(db, body.template_id, user_id, body.workspace_id)
    session_id, document_id = await _create_in_app_session(db, body, user_id, template)
    return {
        "session_id": session_id,
        "document_id": document_id,
        "status": "awaiting_consent",
        "template": template,
    }


@router.post("/conversation/speech")
async def conversation_speech(body: ConversationSpeechRequest, current_user: CurrentUser):
    language = "bn-BD" if body.language_code.lower().startswith("bn") else body.language_code
    try:
        audio = await synthesize_speech(body.text, language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/conversation/sessions/{session_id}/consent")
async def set_conversation_consent(session_id: str, body: ConsentRequest, current_user: CurrentUser, db=Depends(get_db)):
    row = await _owned_call(db, session_id, str(current_user["id"]), "editor")
    if row["source_channel"] != "in_app":
        raise HTTPException(400, "Consent can only be changed here for in-app sessions")
    status = "confirmed" if body.confirmed else "declined"
    await db.execute(
        """UPDATE telephony_calls SET consent_status=$2,
           consent_confirmed_at=CASE WHEN $2='confirmed' THEN NOW() ELSE NULL END,
           processing_step=CASE WHEN $2='confirmed' THEN 'listening' ELSE 'consent_declined' END,
           updated_at=NOW() WHERE id=$1""",
        session_id, status,
    )
    greeting = ""
    if body.confirmed:
        state = json_object_value(row["session_state"])
        template = json_object_value(state.get("template")) or DEFAULT_TEMPLATES[0]
        greeting = initial_greeting(template, row["language_code"])
        existing = await db.fetchval(
            "SELECT 1 FROM conversation_turns WHERE call_id=$1 AND sequence=0", session_id,
        )
        if not existing:
            await db.execute(
                """INSERT INTO conversation_turns(call_id,sequence,role,speaker,transcript,metadata)
                   VALUES($1,0,'assistant','DocIntel Assistant',$2,$3::jsonb)""",
                session_id, greeting, json.dumps({"event": "session_greeting"}),
            )
            state["last_assistant_sequence"] = 0
            state["greeting_delivered"] = True
            state["last_question_field"] = str((template.get("fields") or [{}])[0].get("key") or "")
            await db.execute(
                """UPDATE telephony_calls SET session_state=$2::jsonb,progress_pct=5,
                   updated_at=NOW() WHERE id=$1""",
                session_id, json.dumps(state),
            )
    return {"session_id": session_id, "consent_status": status, "greeting": greeting}


@router.post("/conversation/sessions/{session_id}/turns")
async def add_conversation_turn(
    session_id: str,
    current_user: CurrentUser,
    transcript: str = Form(""),
    audio: UploadFile | None = File(default=None),
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    session = await _owned_call(db, session_id, user_id, "editor")
    if session["source_channel"] != "in_app":
        raise HTTPException(400, "This endpoint accepts only in-app conversation sessions")
    if session["consent_status"] != "confirmed":
        raise HTTPException(400, "Recording consent must be confirmed before adding turns")
    if session["processing_status"] not in {"active", "received"}:
        raise HTTPException(409, "This conversation is no longer accepting turns")

    audio_path = None
    text = transcript.strip()
    if audio:
        content_type = (audio.content_type or "").split(";")[0].lower()
        if content_type not in SUPPORTED_AUDIO_TYPES:
            raise HTTPException(400, f"Unsupported audio format: {content_type}")
        audio_bytes = await audio.read()
        if len(audio_bytes) > MAX_TURN_AUDIO_BYTES:
            raise HTTPException(413, f"Audio turn exceeds {MAX_TURN_AUDIO_BYTES // 1024 // 1024} MB")
        if not text:
            try:
                text = await transcribe_turn(audio_bytes, content_type, session["language_code"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc
        extension = os.path.splitext(audio.filename or "")[1] or ".webm"
        audio_path = f"users/{session['user_id']}/documents/{session['document_id']}/conversation-turns/{uuid4()}{extension}"
        await gcs.upload_bytes(audio_path, audio_bytes, content_type)
    if not text:
        raise HTTPException(400, "Provide a transcript or a non-empty audio turn")

    turns = [dict(row) for row in await db.fetch(
        "SELECT role,speaker,transcript,collected_fields,citations FROM conversation_turns WHERE call_id=$1 ORDER BY sequence",
        session_id,
    )]
    state = json_object_value(session["session_state"])
    template = json_object_value(state.get("template")) or DEFAULT_TEMPLATES[0]
    sequence = len(turns)
    await db.execute(
        """INSERT INTO conversation_turns(call_id,sequence,role,speaker,transcript,audio_gcs_path,metadata)
           VALUES($1,$2,'user','participant',$3,$4,$5::jsonb)""",
        session_id, sequence, text, audio_path, json.dumps({"source": "microphone" if audio else "typed"}),
    )
    turns.append({"role": "user", "speaker": "participant", "transcript": text})
    assistant = await generate_assistant_turn(
        db, user_id=user_id,
        workspace_id=str(session["workspace_id"]) if session["workspace_id"] else None,
        template=template, existing_state=state, turns=turns, user_text=text,
        language=session["language_code"],
    )
    next_sequence = sequence + 1
    await db.execute(
        """INSERT INTO conversation_turns(call_id,sequence,role,speaker,transcript,collected_fields,citations,metadata)
           VALUES($1,$2,'assistant','DocIntel Assistant',$3,$4::jsonb,$5::jsonb,$6::jsonb)""",
        session_id, next_sequence, assistant["response"], json.dumps(assistant["collected_fields"]),
        json.dumps(assistant["citations"]), json.dumps({"answered_from_knowledgebase": assistant["answered_from_knowledgebase"]}),
    )
    state.update({
        "collected_fields": assistant["collected_fields"],
        "missing_required_fields": assistant["missing_required_fields"],
        "ready_to_finish": assistant["ready_to_finish"],
        "last_question_field": assistant["next_question_field"],
        "awaiting_save_confirmation": assistant["awaiting_save_confirmation"],
        "last_assistant_sequence": next_sequence,
    })
    await db.execute(
        """UPDATE telephony_calls SET session_state=$2::jsonb,processing_step='listening',
           progress_pct=LEAST(60,10 + $3::integer * 5),updated_at=NOW() WHERE id=$1""",
        session_id, json.dumps(state), next_sequence + 1,
    )
    return {
        "session_id": session_id,
        "user_transcript": text,
        "assistant": assistant,
        "user_audio_gcs_path": audio_path,
    }


@router.patch("/conversation/sessions/{session_id}")
async def update_conversation_session(session_id: str, body: SessionStateRequest, current_user: CurrentUser, db=Depends(get_db)):
    session = await _owned_call(db, session_id, str(current_user["id"]), "editor")
    state = json_object_value(session["session_state"])
    state["collected_fields"] = body.collected_fields
    review_status = body.review_status or session["review_status"]
    if review_status not in {"draft", "in_review", "approved", "withdrawn"}:
        raise HTTPException(400, "Invalid review status")
    await db.execute(
        "UPDATE telephony_calls SET session_state=$2::jsonb,review_status=$3,updated_at=NOW() WHERE id=$1",
        session_id, json.dumps(state), review_status,
    )
    return {"session_id": session_id, "session_state": state, "review_status": review_status}


@router.post("/conversation/sessions/{session_id}/finalize")
async def finalize_conversation_session(session_id: str, current_user: CurrentUser, db=Depends(get_db)):
    session = await _owned_call(db, session_id, str(current_user["id"]), "editor")
    if session["consent_status"] != "confirmed":
        raise HTTPException(400, "Confirmed recording consent is required")
    turns = await db.fetch(
        "SELECT role,speaker,transcript FROM conversation_turns WHERE call_id=$1 ORDER BY sequence", session_id,
    )
    if not turns:
        raise HTTPException(400, "The conversation has no turns to finalize")
    await db.execute(
        """UPDATE telephony_calls SET processing_status='in_review',processing_step='transcript_review',
           review_status='in_review',progress_pct=65,ended_at=NOW(),updated_at=NOW() WHERE id=$1""",
        session_id,
    )
    return {"session_id": session_id, "document_id": str(session["document_id"]), "status": "in_review"}


@router.post("/conversation/sessions/{session_id}/approve-transcript")
async def approve_conversation_transcript(
    session_id: str, body: TranscriptApprovalRequest, background: BackgroundTasks,
    current_user: CurrentUser, db=Depends(get_db),
):
    session = await _owned_call(db, session_id, str(current_user["id"]), "editor")
    if session["source_channel"] != "in_app":
        raise HTTPException(400, "Transcript approval is available only for in-app conversations")
    if session["review_status"] == "approved":
        raise HTTPException(409, "This transcript has already been approved")
    transcript = body.transcript.strip()
    document = await db.fetchrow("SELECT gcs_source_path FROM documents WHERE id=$1", session["document_id"])
    await gcs.upload_text(document["gcs_source_path"], transcript)
    await db.execute(
        """UPDATE telephony_calls SET processing_status='received',processing_step='queued',
           review_status='approved',progress_pct=70,updated_at=NOW() WHERE id=$1""", session_id,
    )
    state = json_object_value(session["session_state"])
    background.add_task(process_call, session_id, transcript, bool(state.get("redact_pii", True)))
    return {"session_id": session_id, "document_id": str(session["document_id"]),
            "review_status": "approved", "status": "queued"}


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
    turns = await db.fetch("SELECT * FROM conversation_turns WHERE call_id=$1 ORDER BY sequence", call_id)
    data = _jsonable(row)
    data["segments"] = [_jsonable(segment) for segment in segments]
    data["turns"] = [_jsonable(turn) for turn in turns]
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
