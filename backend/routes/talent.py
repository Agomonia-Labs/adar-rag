from __future__ import annotations

import io
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser
from database.connection import get_db
from services.audit import audit, ip_from, ua_from
from services.talent_intelligence import create_talent_packet
import services.storage as gcs

router = APIRouter()


class TalentRunRequest(BaseModel):
    resume_document_ids: list[str] = Field(min_length=1)
    job_description_id: str
    candidate_name: str = ""
    notes: str = ""


class TalentReviewRequest(BaseModel):
    packet: dict
    notes: str = ""


@router.get("/documents")
async def list_talent_documents(workspace_id: str | None = None, current_user: CurrentUser = None, db=Depends(get_db)):
    rows = await db.fetch(
        """
        SELECT d.id, d.original_name, d.doc_type, d.doc_domain, d.status, d.chunk_count, d.workspace_id, d.created_at
        FROM documents d
        WHERE d.status != 'deleted'
          AND d.doc_type IN ('resume','cv','job_description')
          AND (($2::uuid IS NULL AND d.workspace_id IS NULL AND d.user_id=$1)
            OR ($2::uuid IS NOT NULL AND d.workspace_id=$2 AND EXISTS (
              SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=$2 AND wm.user_id=$1
            )))
        ORDER BY d.created_at DESC
        """,
        str(current_user["id"]), workspace_id,
    )
    return [_jsonable(dict(row)) for row in rows]


@router.post("/runs")
async def create_run(body: TalentRunRequest, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    ids = list(dict.fromkeys([*body.resume_document_ids, body.job_description_id]))
    docs = await _load_documents(db, ids, user_id)
    if len(docs) != len(ids):
        raise HTTPException(404, "One or more talent documents were not found or are not accessible")
    by_id = {str(d["id"]): d for d in docs}
    if by_id[body.job_description_id]["doc_type"] != "job_description":
        raise HTTPException(400, "The selected role document must be classified as Job Description")
    invalid_resumes = [doc_id for doc_id in body.resume_document_ids if by_id[doc_id]["doc_type"] not in ("resume", "cv")]
    if invalid_resumes:
        raise HTTPException(400, "Candidate documents must be classified as Resume or CV")
    if any(d["status"] not in ("chunked", "embedding", "embedded") for d in docs):
        raise HTTPException(400, "All selected documents must finish processing before role matching")

    source_docs = []
    for doc in docs:
        chunks = await _load_doc_chunks(db, doc, user_id)
        if not chunks or not any(str(chunk.get("content") or "").strip() for chunk in chunks):
            raise HTTPException(
                400,
                f"No readable content is available for {doc['original_name']}. Reprocess the document before running Talent Readiness.",
            )
        source_docs.append({"id": str(doc["id"]), "name": doc["original_name"], "doc_type": doc["doc_type"], "chunks": chunks})
    packet = await create_talent_packet(source_docs, body.candidate_name, body.notes)
    workspace_id = next((str(d["workspace_id"]) for d in docs if d["workspace_id"]), None)
    row = await db.fetchrow(
        """
        INSERT INTO talent_runs (user_id, workspace_id, resume_document_ids, job_description_id, candidate_name, status, packet, reviewer_notes, completed_at)
        VALUES ($1,$2,$3::jsonb,$4,$5,'needs_review',$6::jsonb,$7,NOW()) RETURNING *
        """,
        user_id, workspace_id, json.dumps(body.resume_document_ids), body.job_description_id,
        body.candidate_name or (packet.get("candidate_profile") or {}).get("name", ""), json.dumps(packet), body.notes,
    )
    await audit(db, user_id=user_id, action="talent_run_create", resource_type="talent_run", resource_id=str(row["id"]), metadata={"document_ids": ids}, ip_address=ip_from(request), user_agent=ua_from(request))
    return _run_response(row)


@router.get("/runs")
async def list_runs(workspace_id: str | None = None, current_user: CurrentUser = None, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT r.* FROM talent_runs r WHERE
        (($2::uuid IS NULL AND r.workspace_id IS NULL AND r.user_id=$1) OR
         ($2::uuid IS NOT NULL AND r.workspace_id=$2 AND EXISTS (SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=$2 AND wm.user_id=$1)))
        ORDER BY r.created_at DESC LIMIT 50""", str(current_user["id"]), workspace_id,
    )
    return [_run_response(row) for row in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    return _run_response(await _accessible_run(db, run_id, str(current_user["id"])))


@router.patch("/runs/{run_id}/save")
async def save_run(run_id: str, body: TalentReviewRequest, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _accessible_run(db, run_id, user_id)
    row = await db.fetchrow("UPDATE talent_runs SET packet=$2::jsonb, reviewer_notes=$3, status='saved', updated_at=NOW() WHERE id=$1 RETURNING *", run_id, json.dumps(body.packet), body.notes)
    await audit(db, user_id=user_id, action="talent_run_save", resource_type="talent_run", resource_id=run_id, metadata={}, ip_address=ip_from(request), user_agent=ua_from(request))
    return _run_response(row)


@router.post("/runs/{run_id}/rerun")
async def rerun(run_id: str, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    existing = await _accessible_run(db, run_id, user_id)
    resume_ids = _decode_json(existing["resume_document_ids"], [])
    ids = list(dict.fromkeys([*resume_ids, str(existing["job_description_id"])]))
    docs = await _load_documents(db, ids, user_id)
    if len(docs) != len(ids):
        raise HTTPException(404, "One or more source documents for this workflow are no longer available")
    source_docs = await _build_source_documents(db, docs, user_id)
    prior_packet = _decode_json(existing["packet"], {})
    packet = await create_talent_packet(source_docs, existing["candidate_name"] or "", existing["reviewer_notes"] or "", prior_packet=prior_packet)
    row = await db.fetchrow("UPDATE talent_runs SET packet=$2::jsonb, status='needs_review', completed_at=NOW(), updated_at=NOW() WHERE id=$1 RETURNING *", run_id, json.dumps(packet))
    await audit(db, user_id=user_id, action="talent_run_incremental", resource_type="talent_run", resource_id=run_id, metadata={"document_ids": ids}, ip_address=ip_from(request), user_agent=ua_from(request))
    return _run_response(row)


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _accessible_run(db, run_id, user_id)
    await audit(db, user_id=user_id, action="talent_run_delete", resource_type="talent_run", resource_id=run_id, metadata={}, ip_address=ip_from(request), user_agent=ua_from(request))
    await db.execute("DELETE FROM talent_runs WHERE id=$1", run_id)
    return {"deleted": run_id}


@router.patch("/runs/{run_id}/review")
async def save_review(run_id: str, body: TalentReviewRequest, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _accessible_run(db, run_id, user_id)
    review = body.packet.setdefault("recruiter_review", {})
    review.update({"status": "reviewed", "reviewed_by": current_user.get("full_name") or current_user.get("email") or user_id})
    row = await db.fetchrow("UPDATE talent_runs SET packet=$2::jsonb, reviewer_notes=$3, status='reviewed', reviewed_by=$4, reviewed_at=NOW(), updated_at=NOW() WHERE id=$1 RETURNING *", run_id, json.dumps(body.packet), body.notes, user_id)
    await audit(db, user_id=user_id, action="talent_run_review", resource_type="talent_run", resource_id=run_id, metadata={}, ip_address=ip_from(request), user_agent=ua_from(request))
    return _run_response(row)


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: str, body: TalentReviewRequest, request: Request, current_user: CurrentUser, db=Depends(get_db)):
    user_id = str(current_user["id"])
    await _accessible_run(db, run_id, user_id)
    review = body.packet.setdefault("recruiter_review", {})
    review.update({"status": "approved", "reviewed_by": current_user.get("full_name") or current_user.get("email") or user_id})
    row = await db.fetchrow("UPDATE talent_runs SET packet=$2::jsonb, reviewer_notes=$3, status='approved', reviewed_by=$4, reviewed_at=COALESCE(reviewed_at,NOW()), approved_by=$4, approved_at=NOW(), updated_at=NOW() WHERE id=$1 RETURNING *", run_id, json.dumps(body.packet), body.notes, user_id)
    await audit(db, user_id=user_id, action="talent_run_approve", resource_type="talent_run", resource_id=run_id, metadata={}, ip_address=ip_from(request), user_agent=ua_from(request))
    return _run_response(row)


@router.get("/runs/{run_id}/packet.pdf")
async def download_packet(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    run = await _accessible_run(db, run_id, str(current_user["id"]))
    if run["status"] != "approved":
        raise HTTPException(400, "Recruiter approval is required before downloading the candidate packet")
    content = _candidate_pdf(_jsonable(_decode_json(run["packet"], {})), run)
    filename = re_safe(run["candidate_name"] or "candidate") + "-talent-packet.pdf"
    return StreamingResponse(io.BytesIO(content), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def _load_documents(db, ids: list[str], user_id: str):
    return await db.fetch("""SELECT d.* FROM documents d WHERE d.id=ANY($1::uuid[]) AND (d.user_id=$2 OR EXISTS (SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=d.workspace_id AND wm.user_id=$2))""", ids, user_id)


async def _build_source_documents(db, docs, user_id: str) -> list[dict]:
    source_docs = []
    for doc in docs:
        chunks = await _load_doc_chunks(db, doc, user_id)
        if not chunks or not any(str(chunk.get("content") or "").strip() for chunk in chunks):
            raise HTTPException(400, f"No readable content is available for {doc['original_name']}. Reprocess the document before running Talent Readiness.")
        source_docs.append({"id": str(doc["id"]), "name": doc["original_name"], "doc_type": doc["doc_type"], "chunks": chunks})
    return source_docs


async def _load_doc_chunks(db, doc, user_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT chunk_index, content FROM document_chunks WHERE document_id=$1 ORDER BY chunk_index",
        str(doc["id"]),
    )
    if rows:
        return [dict(row) for row in rows]

    try:
        owner_id = str(doc.get("user_id") or user_id)
        metadata = await gcs.download_json(gcs.metadata_path(owner_id, str(doc["id"])))
        chunks = []
        for item in metadata.get("chunks") or []:
            content = await gcs.download_text(item["gcs_path"])
            chunks.append({"chunk_index": item.get("index", len(chunks)), "content": content})
        return chunks
    except Exception as exc:
        raise HTTPException(500, f"Could not load content for {doc['original_name']}: {exc}") from exc


async def _accessible_run(db, run_id: str, user_id: str):
    row = await db.fetchrow("""SELECT r.* FROM talent_runs r WHERE r.id=$1 AND (r.user_id=$2 OR EXISTS (SELECT 1 FROM workspace_members wm WHERE wm.workspace_id=r.workspace_id AND wm.user_id=$2))""", run_id, user_id)
    if not row:
        raise HTTPException(404, "Talent workflow not found")
    return row


def _run_response(row) -> dict:
    result = dict(row)
    result["packet"] = _decode_json(result.get("packet"), {})
    result["resume_document_ids"] = _decode_json(result.get("resume_document_ids"), [])
    return _jsonable(result)


def _decode_json(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


def _jsonable(value):
    if isinstance(value, dict): return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, UUID): return str(value)
    if isinstance(value, (datetime, date)): return value.isoformat()
    return value


def re_safe(value: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "candidate"


def _candidate_pdf(packet: dict, run) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER, leftMargin=.65*inch, rightMargin=.65*inch, topMargin=.6*inch, bottomMargin=.6*inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TalentTitle", parent=styles["Title"], textColor=colors.HexColor("#166534"), fontSize=19, leading=23))
    styles.add(ParagraphStyle(name="TalentHead", parent=styles["Heading2"], textColor=colors.HexColor("#166534"), fontSize=12, spaceBefore=12, spaceAfter=5))
    styles.add(ParagraphStyle(name="TalentBody", parent=styles["BodyText"], fontSize=9.5, leading=14))
    profile, role, match = packet.get("candidate_profile", {}), packet.get("role_profile", {}), packet.get("role_match", {})
    story = [Paragraph("ADAR DocIntel Candidate Review Packet", styles["TalentTitle"]), Paragraph("Human-reviewed talent intelligence for recruiter decision support", styles["TalentBody"]), Spacer(1, 12)]
    story.append(Table([["Candidate", profile.get("name") or run["candidate_name"] or "Not provided"], ["Role", role.get("title") or "Not provided"], ["Documented match", f"{match.get('score', 0)}%"], ["Review status", str(run["status"]).replace("_", " ").title()]], colWidths=[1.35*inch, 5.7*inch], style=TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#ecfdf5")),("TEXTCOLOR",(0,0),(0,-1),colors.HexColor("#166534")),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#d1d5db")),("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),6)])))
    sections = [("Candidate profile", profile.get("summary") or profile.get("headline") or "No summary available."), ("Role match", match.get("summary") or "No match narrative available."), ("Recruiter notes", run["reviewer_notes"] or "No reviewer notes provided.")]
    for title, text in sections:
        story += [Paragraph(title, styles["TalentHead"]), Paragraph(str(text), styles["TalentBody"])]
    for title, items, formatter in [
        ("Demonstrated skills", packet.get("skills", []), lambda x: f"<b>{x.get('name','')}</b>: {x.get('evidence','No evidence recorded')}"),
        ("Requirement gap analysis", packet.get("gap_analysis", []), lambda x: f"<b>{x.get('requirement','')}</b> ({x.get('status','unclear')}): {x.get('evidence','No evidence recorded')}"),
    ]:
        story.append(Paragraph(title, styles["TalentHead"]))
        for item in items or []:
            story.append(Paragraph(formatter(item), styles["TalentBody"])); story.append(Spacer(1, 4))
    story += [Spacer(1, 12), Paragraph("This packet supports human review and must not be used as an autonomous hiring, promotion, compensation, or termination decision.", styles["TalentBody"])]
    doc.build(story)
    return buffer.getvalue()
