from __future__ import annotations

import json
import logging
import os
import re
import uuid
from io import BytesIO
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from database.connection import get_db, get_pool
from services.audit import audit, ip_from, ua_from
from services.chunker import chunk_text
from services import storage as gcs
from services.notifications import send_finance_tax_packet_notification
from services.text_safety import sanitize_text_for_storage
from services.usage import check_and_log_daily_event
from services.vertical_agent_runs import (
    approve_vertical_run,
    complete_vertical_run,
    create_vertical_run,
    emit_packet_generated,
    fail_vertical_run,
    get_accessible_vertical_run,
    run_vertical_step,
    vertical_run_response,
)


router = APIRouter()
log = logging.getLogger("docintel.finance_tax.route")

FINANCE_TAX_VERTICAL = "finance_tax"
TAX_MVP_WORKFLOW_ID = "finance_tax_tax_submission_mvp1"
TAX_FORM_ALIASES = {
    "tax": "prior_year_return",
    "tax_return": "prior_year_return",
    "prior_tax_return": "prior_year_return",
    "prior_year_tax_return": "prior_year_return",
    "prior_year_return": "prior_year_return",
    "previous_year_tax_return": "prior_year_return",
    "federal_tax_return": "prior_year_return",
    "state_tax_return": "prior_year_return",
    "form_1040": "prior_year_return",
    "1040": "prior_year_return",
    "1040_sr": "prior_year_return",
    "brokerage": "brokerage_statement",
    "brokerage_1099": "brokerage_statement",
    "investment_statement": "brokerage_statement",
    "investment_account_statement": "brokerage_statement",
    "consolidated_1099": "brokerage_statement",
    "1099_consolidated": "brokerage_statement",
    "bank": "bank_statement",
    "bank_statement": "bank_statement",
    "checking_statement": "bank_statement",
    "savings_statement": "bank_statement",
    "credit_card": "credit_card_statement",
    "credit_card_statement": "credit_card_statement",
    "card_statement": "credit_card_statement",
}
TAX_METADATA_FORM_TYPES = {
    "w2",
    "1099",
    "k1",
    "retirement_statement",
    "brokerage_statement",
    "bank_statement",
    "credit_card_statement",
    "mortgage_interest",
    "property_tax",
    "charitable_receipt",
    "business_expense",
}


class TaxSubmissionRunRequest(BaseModel):
    document_ids: list[str]
    client_name: str = ""
    tax_year: str = ""
    filing_status: str = ""
    notes: str = ""


class FinanceTaxApprovalRequest(BaseModel):
    approved_packet: dict | None = None
    notes: str | None = None


class FinanceTaxAdvisorPacketPdfRequest(BaseModel):
    packet: dict | None = None


def _finance_tax_run_summary(row: dict) -> dict:
    input_data = _json(row.get("input_data")) or {}
    result_data = _json(row.get("result_data")) or {}
    packet = result_data.get("approved_packet") or result_data.get("review_packet") or result_data
    client = packet.get("client") if isinstance(packet, dict) else {}
    document_ids = input_data.get("document_ids") or []
    return {
        "run_id": str(row["id"]),
        "status": row.get("status"),
        "client_name": (client or {}).get("name") or input_data.get("client_name") or "Client",
        "tax_year": (client or {}).get("tax_year") or input_data.get("tax_year") or "",
        "filing_status": (client or {}).get("filing_status") or input_data.get("filing_status") or "",
        "document_count": len(document_ids) if isinstance(document_ids, list) else 1,
        "approved_at": row["approved_at"].isoformat() if row.get("approved_at") else None,
        "approval_notes": row.get("approval_notes") or "",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.post("/tax-submission-runs")
async def start_tax_submission_run(
    body: TaxSubmissionRunRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    document_ids = _unique_ids(body.document_ids)
    if not document_ids:
        raise HTTPException(400, "Select at least one tax document")
    if len(document_ids) > 25:
        raise HTTPException(400, "MVP 1 supports up to 25 documents per run")

    docs = [await _get_accessible_doc(db, doc_id, user_id) for doc_id in document_ids]
    not_ready = [d["original_name"] for d in docs if d["status"] not in ("chunked", "embedding", "embedded")]
    if not_ready:
        raise HTTPException(400, f"Documents must be chunked before running tax submission: {', '.join(not_ready[:3])}")

    workspace_ids = {str(d["workspace_id"]) for d in docs if d.get("workspace_id")}
    workspace_id = str(docs[0]["workspace_id"]) if len(workspace_ids) == 1 else None
    await check_and_log_daily_event(
        db,
        user_id,
        "finance_tax_ai",
        "max_finance_tax_ai_day",
        metadata={"action": "tax_submission_mvp1", "document_ids": document_ids, "tax_year": body.tax_year},
    )
    run = await create_vertical_run(
        db,
        workflow_id=TAX_MVP_WORKFLOW_ID,
        workflow_version="mvp1-deterministic-v1",
        vertical=FINANCE_TAX_VERTICAL,
        document_id=document_ids[0],
        user_id=user_id,
        workspace_id=workspace_id,
        input_data={
            "document_ids": document_ids,
            "client_name": body.client_name.strip(),
            "tax_year": body.tax_year.strip(),
            "filing_status": body.filing_status.strip(),
            "notes": body.notes.strip(),
        },
    )
    run_id = str(run["id"])
    background_tasks.add_task(
        _execute_tax_submission_background,
        run_id,
        user_id,
        ip_from(request),
        ua_from(request),
    )
    return await vertical_run_response(db, run)


@router.get("/agent-runs")
async def list_finance_tax_runs(
    current_user: CurrentUser,
    db=Depends(get_db),
    status: str = "approved",
    limit: int = 25,
):
    user_id = str(current_user["id"])
    safe_limit = max(1, min(limit, 100))
    allowed_statuses = {"approved", "pending_approval", "withdrawn", "failed", "running", "all"}
    if status not in allowed_statuses:
        raise HTTPException(400, "Unsupported run status")
    rows = await db.fetch(
        """
        SELECT r.*
        FROM vertical_agent_runs r
        WHERE r.vertical=$1
          AND ($4::text = 'all' OR r.status=$4)
          AND (
            r.user_id=$2
            OR EXISTS (
              SELECT 1 FROM workspace_members wm
              WHERE wm.workspace_id=r.workspace_id
                AND wm.user_id=$2
            )
          )
        ORDER BY COALESCE(r.approved_at, r.completed_at, r.created_at) DESC
        LIMIT $3
        """,
        FINANCE_TAX_VERTICAL,
        user_id,
        safe_limit,
        status,
    )
    return {"runs": [_finance_tax_run_summary(dict(row)) for row in rows]}


@router.get("/agent-runs/{run_id}")
async def get_finance_tax_run(run_id: str, current_user: CurrentUser, db=Depends(get_db)):
    run = await get_accessible_vertical_run(db, run_id, str(current_user["id"]))
    if run.get("vertical") != FINANCE_TAX_VERTICAL:
        raise HTTPException(404, "Finance/tax run not found")
    return await vertical_run_response(db, run)


@router.delete("/agent-runs/{run_id}")
async def withdraw_finance_tax_run(
    run_id: str,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != FINANCE_TAX_VERTICAL:
        raise HTTPException(404, "Finance/tax run not found")
    await db.execute(
        """
        UPDATE vertical_agent_runs
        SET status='withdrawn',
            result_data='{}'::jsonb,
            error_message=NULL,
            approved_by=NULL,
            approved_at=NULL,
            approval_notes=NULL,
            updated_at=NOW()
        WHERE id=$1
        """,
        run_id,
    )
    await audit(
        db,
        user_id=user_id,
        action="finance_tax_packet_withdraw",
        resource_type="vertical_agent_run",
        resource_id=run_id,
        metadata={"workflow_id": run.get("workflow_id")},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    input_data = _json(run.get("input_data")) or {}
    await send_finance_tax_packet_notification(
        current_user["email"],
        action="withdrawn",
        run_id=run_id,
        client_name=input_data.get("client_name") or "",
        tax_year=input_data.get("tax_year") or "",
        reviewer_name=current_user.get("full_name") or current_user["email"],
        notes="The generated packet and approval state were cleared.",
    )
    updated = await get_accessible_vertical_run(db, run_id, user_id)
    return await vertical_run_response(db, updated)


@router.post("/agent-runs/{run_id}/approve")
async def approve_finance_tax_run(
    run_id: str,
    body: FinanceTaxApprovalRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != FINANCE_TAX_VERTICAL:
        raise HTTPException(404, "Finance/tax run not found")
    result = _json(run.get("result_data")) or {}
    packet = body.approved_packet or result.get("review_packet") or result
    await approve_vertical_run(db, run_id=run_id, user_id=user_id, approved_packet=packet, notes=body.notes or "")
    await audit(
        db,
        user_id=user_id,
        action="finance_tax_packet_approve",
        resource_type="vertical_agent_run",
        resource_id=run_id,
        metadata={"workflow_id": run.get("workflow_id")},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    input_data = _json(run.get("input_data")) or {}
    packet_client = packet.get("client") if isinstance(packet, dict) else {}
    await send_finance_tax_packet_notification(
        current_user["email"],
        action="approved",
        run_id=run_id,
        client_name=(packet_client or {}).get("name") or input_data.get("client_name") or "",
        tax_year=(packet_client or {}).get("tax_year") or input_data.get("tax_year") or "",
        reviewer_name=current_user.get("full_name") or current_user["email"],
        notes=body.notes or "CPA/EA review completed in DocIntel.",
    )
    updated = await get_accessible_vertical_run(db, run_id, user_id)
    return await vertical_run_response(db, updated)


@router.post("/agent-runs/{run_id}/advisor-packet/pdf")
async def generate_finance_tax_advisor_packet_pdf_artifact(
    run_id: str,
    body: FinanceTaxAdvisorPacketPdfRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    run = await get_accessible_vertical_run(db, run_id, user_id)
    if run.get("vertical") != FINANCE_TAX_VERTICAL:
        raise HTTPException(404, "Finance/tax run not found")
    if run.get("status") not in ("pending_approval", "approved"):
        raise HTTPException(400, f"Run is not ready for advisor packet PDF generation: {run.get('status')}")

    result = _json(run.get("result_data")) or {}
    packet = body.packet or result.get("approved_packet") or result.get("review_packet") or result
    if not isinstance(packet, dict) or not packet.get("tax_organizer"):
        raise HTTPException(400, "No finance/tax packet is available. Run the tax and financial planning readiness workflow first.")

    doc_id = str(uuid.uuid4())
    owner_id = user_id
    workspace_id = str(run["workspace_id"]) if run.get("workspace_id") else None
    title = _finance_tax_advisor_packet_title(packet, run_id)
    filename = _safe_filename(title, suffix=".pdf")
    source_path = gcs.source_path(owner_id, doc_id, filename)
    packet_text = _format_finance_tax_advisor_packet_text(packet, run_id)
    pdf_bytes = _render_finance_tax_advisor_packet_pdf(title, packet_text)

    await gcs.upload_bytes(source_path, pdf_bytes, "application/pdf")
    await db.execute(
        """
        INSERT INTO documents
          (id, user_id, workspace_id, filename, original_name, file_type, file_size,
           gcs_source_path, gcs_chunks_dir, status, doc_type, doc_domain, doc_language, classified_at, doc_metadata)
        VALUES ($1,$2,$3,$4,$5,'pdf',$6,$7,$8,'chunked','financial_advisor_packet','finance','en',NOW(),$9::jsonb)
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
            "source_kind": "finance_tax_advisor_packet_pdf",
            "source_run_id": run_id,
            "source_document_id": str(run["document_id"]),
            "generated_from": "tax_financial_planning_readiness",
        }),
    )
    await _persist_finance_tax_generated_document_chunks(
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
        action="finance_tax_advisor_packet_pdf_generate",
        resource_type="document",
        resource_id=doc_id,
        metadata={"run_id": run_id, "source_document_id": str(run["document_id"])},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    download_url = None
    try:
        download_url = await gcs.get_signed_url(source_path)
    except Exception:
        log.warning("Could not create signed URL for finance advisor packet doc_id=%s", doc_id, exc_info=True)
    await emit_packet_generated(db, run=run, run_id=run_id, document_id=doc_id, filename=filename)
    return {
        "ok": True,
        "run_id": run_id,
        "download_url": download_url,
        "document": {
            "doc_id": doc_id,
            "filename": filename,
            "original_name": title,
            "file_type": "pdf",
            "status": "chunked",
            "gcs_source_path": source_path,
        },
    }


async def _execute_tax_submission_background(run_id: str, user_id: str, ip_address: str | None, user_agent: str | None):
    pool = get_pool()
    async with pool.acquire() as db:
        try:
            run = await get_accessible_vertical_run(db, run_id, user_id)
            input_data = _json(run.get("input_data")) or {}
            document_ids = _unique_ids(input_data.get("document_ids") or [str(run["document_id"])])

            docs = await run_vertical_step(
                db,
                run_id,
                "Document Intake Agent",
                "Collect selected tax documents and readable chunks.",
                lambda: _collect_tax_documents(db, document_ids, user_id),
            )
            extracted = await run_vertical_step(
                db,
                run_id,
                "Tax Organizer Agent",
                "Classify forms and extract tax submission signals.",
                lambda: _build_tax_organizer(input_data, docs),
            )
            checklist = await run_vertical_step(
                db,
                run_id,
                "Missing Document Agent",
                "Compare available forms against MVP tax checklist.",
                lambda: _build_missing_items(input_data, extracted),
            )
            comparison = await run_vertical_step(
                db,
                run_id,
                "Prior-Year Comparison Agent",
                "Compare current tax package against prior-year return signals.",
                lambda: _build_prior_year_comparison(input_data, extracted),
            )
            packet = _build_review_packet(input_data, docs, extracted, checklist, comparison)
            await complete_vertical_run(db, run_id, packet, status="pending_approval")
            await audit(
                db,
                user_id=user_id,
                action="finance_tax_tax_submission_mvp1_run",
                resource_type="vertical_agent_run",
                resource_id=run_id,
                metadata={"document_count": len(document_ids), "tax_year": input_data.get("tax_year")},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as exc:
            log.exception("Finance/tax tax submission run failed: %s", exc)
            await fail_vertical_run(db, run_id, str(exc))


async def _collect_tax_documents(db, document_ids: list[str], user_id: str) -> dict:
    docs = [await _get_accessible_doc(db, doc_id, user_id) for doc_id in document_ids]
    collected = []
    for doc in docs:
        rows = await _load_tax_document_chunks(db, doc, user_id)
        text = _compose_tax_document_text(rows)
        collected.append({
            "document_id": str(doc["id"]),
            "name": doc["original_name"],
            "file_type": doc["file_type"],
            "status": doc["status"],
            "doc_type": doc.get("doc_type") or "",
            "doc_domain": doc.get("doc_domain") or "",
            "text": text[:60000],
            "chunk_count": len(rows),
        })
    return {"documents": collected}


async def _load_tax_document_chunks(db, doc: dict, user_id: str) -> list[dict]:
    doc_id = str(doc["id"])
    rows = await db.fetch(
        """
        SELECT chunk_index, content
        FROM document_chunks
        WHERE document_id=$1
        ORDER BY chunk_index
        LIMIT 120
        """,
        doc_id,
    )
    if rows:
        return [dict(row) for row in rows]

    owner_id = str(doc.get("user_id") or user_id)
    try:
        meta = await gcs.download_json(gcs.metadata_path(owner_id, doc_id))
        chunks = sorted(meta.get("chunks") or [], key=lambda item: item.get("index", 0))[:120]
        loaded = []
        for chunk_info in chunks:
            path = chunk_info.get("gcs_path") or gcs.chunk_path(owner_id, doc_id, int(chunk_info.get("index", 0)))
            content = await gcs.download_text(path)
            loaded.append({"chunk_index": int(chunk_info.get("index", len(loaded))), "content": content})
        if loaded:
            log.info("Finance/tax workflow loaded %d chunks from GCS fallback for %s", len(loaded), doc_id)
        return loaded
    except Exception as exc:
        log.warning("Finance/tax workflow could not load GCS chunks for %s: %s", doc_id, exc)
        return []


def _compose_tax_document_text(rows) -> str:
    chunks = [row["content"] or "" for row in rows]
    if not chunks:
        return ""

    priority_chunks = []
    seen_indexes = set()
    for index, content in enumerate(chunks):
        hay = content.lower()
        if _has_explicit_prior_year_return_marker(hay) or re.search(
            r"\b(?:adjusted\s+gross\s+income|taxable\s+income|amount\s+you\s+owe|standard\s+deduction|filing\s+status|total\s+tax)\b",
            hay,
            re.I,
        ):
            priority_chunks.append(content)
            seen_indexes.add(index)

    ordered_chunks = priority_chunks + [content for index, content in enumerate(chunks) if index not in seen_indexes]
    return "\n".join(ordered_chunks)[:60000]


def _detect_tax_form_from_document(doc: dict, text: str) -> str:
    name = doc.get("name") or ""
    source_doc_type = _normalize_tax_form(doc.get("doc_type"))
    source_doc_domain = (doc.get("doc_domain") or "").lower()
    metadata_hay = f"{name} {doc.get('doc_type') or ''} {doc.get('doc_domain') or ''}".lower()

    if source_doc_type == "prior_year_return":
        return "prior_year_return"
    if _has_explicit_prior_year_return_marker(metadata_hay):
        return "prior_year_return"
    if _looks_like_brokerage_statement(metadata_hay):
        return "brokerage_statement"
    if source_doc_type in TAX_METADATA_FORM_TYPES:
        return source_doc_type

    detected = _normalize_tax_form(_detect_tax_form(metadata_hay, text))
    if detected == "w2" and source_doc_domain == "finance" and _has_explicit_prior_year_return_marker(f"{metadata_hay}\n{text}"):
        return "prior_year_return"
    return detected


async def _build_tax_organizer(input_data: dict, docs_payload: dict) -> dict:
    documents = docs_payload.get("documents") or []
    forms = []
    totals = defaultdict(Decimal)
    years = Counter()
    for doc in documents:
        text = doc.get("text") or ""
        detected = _detect_tax_form_from_document(doc, text)
        amounts = _amounts_for_tax_form(detected, text)
        values = _values_for_tax_form(detected, text)
        year = _detect_year(text, input_data.get("tax_year"))
        if year:
            years[year] += 1
        for label, amount in amounts[:10]:
            totals[label] += amount
        forms.append({
            "document_id": doc["document_id"],
            "document_name": doc["name"],
            "source_doc_type": doc.get("doc_type") or "",
            "source_doc_domain": doc.get("doc_domain") or "",
            "detected_form": detected,
            "tax_year": year or input_data.get("tax_year") or "",
            "confidence": _classification_confidence(detected, text),
            "signals": _signals_for_form(detected, text),
            "sample_amounts": [{"label": label, "amount": float(amount)} for label, amount in amounts[:_amount_limit_for_form(detected)]],
            "sample_values": values[:_value_limit_for_form(detected)],
        })
    return {
        "client": {
            "name": input_data.get("client_name") or "Client",
            "tax_year": input_data.get("tax_year") or (years.most_common(1)[0][0] if years else ""),
            "filing_status": input_data.get("filing_status") or "Needs review",
        },
        "forms": forms,
        "income_summary": _summarize_income(forms),
        "deduction_credit_summary": _summarize_deductions(forms),
        "review_flags": _review_flags(forms, input_data),
    }


async def _build_missing_items(input_data: dict, extracted: dict) -> dict:
    found = {_normalize_tax_form(f.get("detected_form")) for f in extracted.get("forms", [])}
    expected = [
        ("prior_year_return", "Prior-year tax return", "Needed for comparison and continuity checks"),
        ("w2", "W-2 wage statement", "Needed when client has employment income"),
        ("1099", "1099 income forms", "Needed for interest, dividends, nonemployee compensation, retirement, or brokerage income"),
        ("mortgage_interest", "Mortgage interest statement", "Needed when itemizing home-related deductions"),
        ("property_tax", "Property tax record", "Needed when itemizing state/local taxes"),
        ("charitable_receipt", "Charitable contribution receipts", "Needed when charitable deductions are claimed"),
    ]
    missing = []
    for code, label, reason in expected:
        if code not in found:
            missing.append({"item": label, "reason": reason, "priority": "high" if code in {"prior_year_return", "w2", "1099"} else "medium"})
    return {
        "missing_items": missing,
        "ready_for_cpa_review": len([m for m in missing if m["priority"] == "high"]) == 0,
        "client_questions": _client_questions(found, extracted),
    }


async def _build_prior_year_comparison(input_data: dict, extracted: dict) -> dict:
    forms = extracted.get("forms", [])
    normalized_forms = [_normalize_tax_form(f.get("detected_form")) for f in forms if f.get("detected_form")]
    prior_forms = [f for f in forms if _normalize_tax_form(f.get("detected_form")) == "prior_year_return"]
    has_prior = any(form == "prior_year_return" for form in normalized_forms)
    current = Counter(normalized_forms)
    changes = []
    if not has_prior:
        changes.append({
            "area": "Prior-year comparison",
            "finding": "No prior-year return was detected in the selected documents.",
            "recommended_action": "Upload last year's federal and state return for year-over-year income, deduction, and carryforward checks.",
        })
    else:
        prior_names = ", ".join(f.get("document_name") or "prior-year return" for f in prior_forms[:3])
        extracted_values = _prior_year_comparison_value_summary(prior_forms)
        changes.append({
            "area": "Prior-year return",
            "finding": f"Prior-year return detected from {prior_names}. {extracted_values}",
            "recommended_action": "Use these prior-year values as the baseline for CPA/EA review, carryforward checks, and year-over-year income and deduction comparison.",
        })
        current_year_forms = [
            form for form, count in current.items()
            if form not in {"prior_year_return", "tax_document"} and count > 0
        ]
        if current_year_forms:
            changes.append({
                "area": "Available comparison inputs",
                "finding": f"Current-year supporting documents detected: {', '.join(formLabel.replace('_', ' ') for formLabel in current_year_forms)}.",
                "recommended_action": "Compare current-year income, withholding, mortgage interest, property tax, charitable contributions, and investment activity against the prior-year baseline.",
            })
        else:
            changes.append({
                "area": "Available comparison inputs",
                "finding": "Prior-year return is available, but no separate current-year W-2, 1099, mortgage, property tax, brokerage, retirement, or charitable documents were detected in this run.",
                "recommended_action": "Select the current-year supporting documents and rerun the workflow for a fuller year-over-year comparison.",
            })
    if current.get("1099", 0) > 0 and current.get("w2", 0) == 0:
        changes.append({
            "area": "Income mix",
            "finding": "1099 income is present, but no W-2 was detected.",
            "recommended_action": "Confirm whether the taxpayer had employment income or only contractor/investment income.",
        })
    return {"prior_year_return_detected": has_prior, "comparison_notes": changes}


def _prior_year_comparison_value_summary(prior_forms: list[dict]) -> str:
    amount_rows = []
    value_rows = []
    for form in prior_forms:
        amount_rows.extend(form.get("sample_amounts") or [])
        value_rows.extend(form.get("sample_values") or [])

    labels = []
    priority_labels = [
        "Adjusted gross income",
        "Taxable income",
        "Total tax",
        "Federal income tax withheld",
        "Refund",
        "Amount owed",
        "Standard deduction",
        "Itemized deductions",
    ]
    for wanted in priority_labels:
        match = next((row for row in amount_rows if wanted.lower() in str(row.get("label") or "").lower()), None)
        if match:
            labels.append(f"{wanted}: {_money(match.get('amount'))}")
    filing = next((row for row in value_rows if "filing status" in str(row.get("label") or "").lower()), None)
    if filing:
        labels.insert(0, f"Filing status: {filing.get('value')}")
    if labels:
        return "Key extracted baseline values include " + "; ".join(labels[:6]) + "."
    return "No key Form 1040 baseline amounts were extracted yet, so reviewer should confirm AGI, taxable income, total tax, refund or balance due from the source return."


def _money(value) -> str:
    try:
        amount = Decimal(str(value or 0))
    except Exception:
        return "$0"
    return f"${amount:,.2f}"


def _build_review_packet(input_data: dict, docs: dict, extracted: dict, checklist: dict, comparison: dict) -> dict:
    client = extracted.get("client") or {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow": "Finance/Tax - Tax & Financial Planning Readiness",
        "client": client,
        "document_summary": [
            {
                "document_id": d["document_id"],
                "name": d["name"],
                "file_type": d["file_type"],
                "chunk_count": d["chunk_count"],
            }
            for d in docs.get("documents", [])
        ],
        "tax_organizer": extracted,
        "missing_document_checklist": checklist,
        "prior_year_comparison": comparison,
        "cpa_review_packet": {
            "summary": (
                f"DocIntel prepared a tax submission readiness packet for {client.get('name') or 'the client'}"
                f" for tax year {client.get('tax_year') or 'not specified'}. The packet organizes selected documents,"
                " identifies likely tax forms, highlights missing information, and separates AI-assisted findings"
                " from CPA/EA review decisions."
            ),
            "review_status": "CPA/EA review required",
            "reviewer_notes": input_data.get("notes") or "",
            "next_actions": _next_actions(checklist, comparison),
        },
        "guardrails": [
            "AI-assisted extraction is a draft and must be reviewed by a qualified CPA, EA, or tax professional.",
            "Do not file based only on this packet. Confirm values against source tax documents and taxpayer interview notes.",
        ],
    }


def _finance_tax_advisor_packet_title(packet: dict, run_id: str) -> str:
    client = packet.get("client") or {}
    name = str(client.get("name") or "Client").strip() or "Client"
    year = str(client.get("tax_year") or datetime.now(timezone.utc).year).strip()
    return f"Advisor Packet - {name} - {year} - {run_id[:8]}"


def _format_finance_tax_advisor_packet_text(packet: dict, run_id: str) -> str:
    client = packet.get("client") or {}
    review = packet.get("cpa_review_packet") or {}
    organizer = packet.get("tax_organizer") or {}
    saves = packet.get("tab_review_saves") or {}
    profile = _saved_snapshot(saves, "client_profile") or _finance_tax_default_profile(packet)
    networth = _saved_snapshot(saves, "networth") or {}
    cashflow = _saved_snapshot(saves, "cashflow") or {}
    retirement = _saved_snapshot(saves, "retirement") or {}
    questions = (_saved_snapshot(saves, "advisor_questions") or {}).get("questions") or []
    score = _saved_snapshot(saves, "readiness_score") or _finance_tax_readiness_score_from_saves(saves, packet)

    lines = [
        "ADVISOR PACKET",
        f"Generated from finance/tax run: {run_id}",
        "",
        "Client Overview",
        (
            f"This packet was prepared for {client.get('name') or profile.get('client_name') or 'the client'}"
            f" for tax year {client.get('tax_year') or profile.get('tax_year') or 'not specified'}."
            f" Filing status is {client.get('filing_status') or profile.get('filing_status') or 'needs review'}."
            " It organizes tax submission readiness and financial planning readiness in one human-reviewed packet."
        ),
        _profile_sentence(profile),
        "",
        "Tax Readiness Summary",
        review.get("summary") or "DocIntel prepared a tax and financial planning readiness packet from the selected documents.",
        _organizer_sentence(organizer),
        "",
        "Net Worth Snapshot",
        _networth_sentence(networth),
        *_section_rows(networth.get("assets"), "Asset"),
        *_section_rows(networth.get("liabilities"), "Liability"),
        "",
        "Cash Flow Snapshot",
        _cashflow_sentence(cashflow),
        *_section_rows(cashflow.get("inflows"), "Inflow"),
        *_section_rows(cashflow.get("outflows"), "Outflow"),
        "",
        "Retirement Readiness",
        _retirement_sentence(retirement),
        *_section_rows(retirement.get("signals"), "Retirement signal"),
        "",
        "Planning Readiness Score",
        _score_sentence(score),
        *_section_rows(score.get("categories"), "Score category"),
        "",
        "Advisor Questions",
        *(_advisor_question_lines(questions) or ["No advisor questions were generated. Advisor should confirm planning objectives directly with the client."]),
        "",
        "Missing Planning Items",
        *(_missing_item_lines(packet, networth, cashflow, retirement, score) or ["No missing planning items were identified from the reviewed packet."]),
        "",
        "Human Review Notice",
        "This advisor packet is planning readiness support. It is not tax, investment, insurance, legal, or estate advice. A qualified advisor, CPA, EA, or appropriate reviewer must validate extracted values and recommendations before client action.",
    ]
    return sanitize_text_for_storage("\n".join(str(line) for line in lines if line is not None))


def _saved_snapshot(saves: dict, key: str) -> dict | None:
    value = saves.get(key)
    if not isinstance(value, dict):
        return None
    snapshot = value.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def _finance_tax_default_profile(packet: dict) -> dict:
    client = packet.get("client") or {}
    return {
        "client_name": client.get("name") or "Client",
        "tax_year": client.get("tax_year") or "",
        "filing_status": client.get("filing_status") or "",
        "planning_stage": "Needs advisor review",
        "risk_tolerance": "Needs advisor review",
        "advisor_notes": (packet.get("cpa_review_packet") or {}).get("summary") or "",
    }


def _profile_sentence(profile: dict) -> str:
    return (
        f"Planning profile: household stage is {profile.get('planning_stage') or 'needs review'},"
        f" risk tolerance is {profile.get('risk_tolerance') or 'needs review'},"
        f" retirement target is {profile.get('retirement_target_age') or 'needs review'},"
        f" and advisor notes are {_plain(profile.get('advisor_notes') or 'not provided')}."
    )


def _organizer_sentence(organizer: dict) -> str:
    forms = organizer.get("forms") if isinstance(organizer, dict) else []
    counts = Counter(str((form or {}).get("detected_from") or (form or {}).get("detected_form") or "tax document") for form in forms or [])
    if not counts:
        return "No tax organizer forms were available in the packet."
    summary = ", ".join(f"{label}: {count}" for label, count in counts.most_common(8))
    return f"Detected tax organizer records include {summary}."


def _networth_sentence(networth: dict) -> str:
    assets = _number(networth.get("totalAssets"))
    liabilities = _number(networth.get("totalLiabilities"))
    value = _number(networth.get("netWorth"))
    return f"Reviewed assets total {_money(assets)}, reviewed liabilities total {_money(liabilities)}, and estimated net worth is {_money(value)}."


def _cashflow_sentence(cashflow: dict) -> str:
    inflows = _number(cashflow.get("totalInflows"))
    outflows = _number(cashflow.get("totalOutflows"))
    estimated = _number(cashflow.get("estimatedCashFlow"))
    return f"Reviewed inflows total {_money(inflows)}, reviewed outflows total {_money(outflows)}, and estimated reviewed cash flow is {_money(estimated)}."


def _retirement_sentence(retirement: dict) -> str:
    status = retirement.get("status") or "Needs advisor review"
    score = retirement.get("score")
    summary = retirement.get("summary") or "Retirement readiness requires advisor review."
    score_text = f" with a readiness score of {score}%" if score not in (None, "") else ""
    return f"Retirement status is {status}{score_text}. {_plain(summary)}"


def _score_sentence(score: dict) -> str:
    overall = score.get("overallScore") or score.get("overall_score") or score.get("score")
    status = score.get("status") or "Needs review"
    if overall not in (None, ""):
        return f"Overall planning readiness score is {overall}% and status is {status}."
    return f"Overall planning readiness status is {status}."


def _section_rows(rows, prefix: str) -> list[str]:
    output = []
    for row in rows or []:
        if not isinstance(row, dict):
            output.append(f"- {prefix}: {_plain(row)}")
            continue
        label = row.get("label") or row.get("category") or row.get("item") or row.get("question") or prefix
        amount = row.get("amount")
        detail = row.get("detail") or row.get("reason") or row.get("source_document") or row.get("status") or ""
        if amount not in (None, ""):
            suffix = f" ({_plain(detail)})" if detail else ""
            output.append(f"- {prefix}: {_plain(label)} - {_money(amount)}{suffix}")
        else:
            suffix = f" - {_plain(detail)}" if detail else ""
            output.append(f"- {prefix}: {_plain(label)}{suffix}")
    return output[:30]


def _advisor_question_lines(questions) -> list[str]:
    lines = []
    for row in questions or []:
        if not isinstance(row, dict):
            lines.append(f"- {_plain(row)}")
            continue
        question = row.get("question") or "Confirm planning detail with client."
        reason = row.get("reason") or row.get("category") or ""
        priority = row.get("priority") or "medium"
        reason_text = f" Reason: {_plain(reason)}" if reason else ""
        lines.append(f"- {_plain(question)} Priority: {_plain(priority)}.{reason_text}")
    return lines[:20]


def _missing_item_lines(packet: dict, networth: dict, cashflow: dict, retirement: dict, score: dict) -> list[str]:
    rows = []
    sources = (
        (packet.get("missing_document_checklist") or {}).get("missing_items"),
        networth.get("missingItems") or networth.get("missing_items"),
        cashflow.get("missingItems") or cashflow.get("missing_items"),
        retirement.get("missingItems") or retirement.get("missing_items"),
        score.get("gaps"),
    )
    for source in sources:
        for item in source or []:
            if isinstance(item, dict):
                label = item.get("item") or item.get("category") or item.get("label") or "Missing item"
                reason = item.get("reason") or item.get("detail") or item.get("priority") or ""
                rows.append(f"- {_plain(label)}{f': {_plain(reason)}' if reason else ''}")
            else:
                rows.append(f"- {_plain(item)}")
    return list(dict.fromkeys(rows))[:25]


def _finance_tax_readiness_score_from_saves(saves: dict, packet: dict) -> dict:
    saved_count = len([key for key in ("client_profile", "networth", "cashflow", "retirement", "advisor_questions") if key in saves])
    score = min(95, 35 + saved_count * 12)
    return {
        "overallScore": score,
        "status": "Ready for advisor review" if score >= 70 else "Needs additional review",
        "categories": [
            {"category": "Tax organizer", "score": 75 if (packet.get("tax_organizer") or {}).get("forms") else 35, "detail": "Tax organizer values are available for reviewer validation."},
            {"category": "Human review", "score": score, "detail": f"{saved_count} planning sections have saved review state."},
        ],
    }


async def _persist_finance_tax_generated_document_chunks(
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
    clean_text = sanitize_text_for_storage(text)
    doc_meta = {
        "document_id": doc_id,
        "user_id": user_id,
        "filename": filename,
        "file_type": "pdf",
        "source_kind": "finance_tax_advisor_packet_pdf",
        "workflow_id": TAX_MVP_WORKFLOW_ID,
        "run_id": run_id,
        "source_document_id": source_document_id,
    }
    chunks = chunk_text(clean_text, doc_meta=doc_meta)
    if not chunks:
        chunks = chunk_text("Generated finance advisor packet.", doc_meta=doc_meta)
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
            "source_kind": "finance_tax_advisor_packet_pdf",
            "workflow_id": TAX_MVP_WORKFLOW_ID,
            "run_id": run_id,
            "source_document_id": source_document_id,
        },
        "chunks": [
            {
                "index": c.index,
                "word_count": c.word_count,
                "char_count": c.char_count,
                "gcs_path": gcs.chunk_path(user_id, doc_id, c.index),
                "source_kind": "finance_tax_advisor_packet_pdf",
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
           SET status='chunked',
               chunk_count=$2,
               doc_metadata = COALESCE(doc_metadata, '{}'::jsonb) || $3::jsonb,
               updated_at=NOW()
         WHERE id=$1
        """,
        doc_id,
        len(chunks),
        json.dumps({"advisor_packet_artifact": {"run_id": run_id, "chunk_count": len(chunks), "source_path": source_path}}),
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


def _detect_tax_form(name: str, text: str) -> str:
    hay = f"{name}\n{text}".lower()
    if _has_explicit_prior_year_return_marker(hay):
        return "prior_year_return"
    if _looks_like_w2(hay):
        return "w2"
    if _looks_like_retirement_statement(hay):
        return "retirement_statement"
    if _looks_like_brokerage_statement(hay):
        return "brokerage_statement"
    if _looks_like_credit_card_statement(hay):
        return "credit_card_statement"
    if _looks_like_bank_statement(hay):
        return "bank_statement"
    if _looks_like_mortgage_interest(hay):
        return "mortgage_interest"
    if _looks_like_property_tax(hay):
        return "property_tax"
    if _looks_like_charitable_receipt(hay):
        return "charitable_receipt"
    if _looks_like_prior_year_return(hay):
        return "prior_year_return"
    patterns = [
        ("prior_year_return", [r"form\s+1040(?:-sr|-nr)?", r"u\.?s\.?\s+individual\s+income\s+tax\s+return", r"prior[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return", r"previous[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return", r"federal\s+tax\s+return", r"state\s+tax\s+return"]),
        ("brokerage_statement", [r"brokerage", r"capital gain", r"dividend", r"1099[- ]?int", r"1099[- ]?div", r"1099[- ]?b", r"gross proceeds", r"cost basis"]),
        ("bank_statement", [r"bank statement", r"deposit accounts", r"account summary", r"beginning balance", r"ending balance", r"deposits and other additions", r"withdrawals and other subtractions"]),
        ("credit_card_statement", [r"credit card statement", r"new balance total", r"minimum payment due", r"payment due date", r"purchases and adjustments", r"payments and other credits"]),
        ("1099", [r"\b1099\b", r"nonemployee compensation", r"interest income", r"dividends and distributions"]),
        ("k1", [r"\bk-?1\b", r"schedule k"]),
        ("mortgage_interest", [r"\b1098\b", r"mortgage interest"]),
        ("property_tax", [r"property tax", r"real estate tax"]),
        ("charitable_receipt", [r"charitable", r"donation", r"contribution receipt"]),
        ("business_expense", [r"receipt", r"invoice", r"business expense", r"mileage"]),
        ("retirement_statement", [r"\bira\b", r"401\s*\(?\s*k\s*\)?", r"403\s*\(?\s*b\s*\)?", r"457\s*\(?\s*b\s*\)?", r"retirement"]),
    ]
    for label, regs in patterns:
        if any(re.search(reg, hay) for reg in regs):
            return label
    return "tax_document"


def _normalize_tax_form(form: str | None) -> str:
    normalized = re.sub(r"[\s\-]+", "_", str(form or "").strip().lower())
    return TAX_FORM_ALIASES.get(normalized, normalized or "tax_document")


def _has_explicit_prior_year_return_marker(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\b(?:tax_return|prior_year_return|prior_tax_return|prior_year_tax_return|previous_year_tax_return|federal_tax_return|state_tax_return)\b",
        r"\bform\s+1040(?:-sr|-nr)?\b",
        r"\bu\.?s\.?\s+individual\s+income\s+tax\s+return\b",
        r"\bprior[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bprevious[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\blast\s+year'?s?\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bfederal\s+tax\s+return\b",
        r"\bstate\s+tax\s+return\b",
    ]
    return any(re.search(pattern, normalized, re.I) for pattern in explicit_markers)


def _looks_like_w2(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bform\s+w\s*[- ]?\s*2\b",
        r"\bw\s*[- ]?\s*2\b",
        r"\bw2\b",
        r"\bwage\s+(?:and\s+)?tax\s+statement\b",
        r"\bwages?\s+and\s+tax\s+statement\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    w2_box_signals = [
        r"\bwages?,?\s+tips?,?\s+(?:and\s+)?other\s+compensation\b",
        r"\bfederal\s+income\s+tax\s+withheld\b",
        r"\bsocial\s+security\s+wages\b",
        r"\bsocial\s+security\s+tax\s+withheld\b",
        r"\bmedicare\s+wages?\s+(?:and\s+)?tips\b",
        r"\bmedicare\s+tax\s+withheld\b",
        r"\bemployer\s+identification\s+number\b",
        r"\bemployer\s+ein\b",
    ]
    hits = sum(1 for pattern in w2_box_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_retirement_statement(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\b401\s*\(?\s*k\s*\)?\b",
        r"\b403\s*\(?\s*b\s*\)?\b",
        r"\b457\s*\(?\s*b\s*\)?\b",
        r"\bira\b",
        r"\broth\s+ira\b",
        r"\bpension\b",
        r"\bretirement\s+(?:plan\s+)?statement\b",
        r"\bretirement\s+account\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    retirement_signals = [
        r"\bemployee\s+(?:pre-tax\s+|roth\s+)?contributions?\b",
        r"\bemployer\s+(?:matching\s+)?contributions?\b",
        r"\bemployer\s+match\b",
        r"\bvested\s+balance\b",
        r"\baccount\s+balance\b",
        r"\bparticipant\b",
        r"\brollover\b",
        r"\bplan\s+year\b",
        r"\bplan\s+administrator\b",
    ]
    hits = sum(1 for pattern in retirement_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_brokerage_statement(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bbrokerage\s+statement\b",
        r"\binvestment\s+account\s+statement\b",
        r"\bconsolidated\s+1099\b",
        r"\b1099\s+consolidated\b",
        r"\b1099[- ]?int\b",
        r"\b1099[- ]?div\b",
        r"\b1099[- ]?b\b",
        r"\bcapital\s+gain\s+distributions?\b",
        r"\bqualified\s+dividends?\b",
        r"\bgross\s+proceeds\b",
        r"\bcost\s+basis\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    brokerage_signals = [
        r"\binterest\s+income\b",
        r"\bordinary\s+dividends?\b",
        r"\bqualified\s+dividends?\b",
        r"\bcapital\s+gain\s+distributions?\b",
        r"\bgross\s+proceeds\b",
        r"\bcost\s+basis\b",
        r"\bshort[- ]?term\b",
        r"\blong[- ]?term\b",
        r"\bfederal\s+income\s+tax\s+withheld\b",
        r"\baccount\s+value\b",
    ]
    hits = sum(1 for pattern in brokerage_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_bank_statement(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bbank\s+statement\b",
        r"\bdeposit\s+accounts?\b",
        r"\bchecking\s+account\s+statement\b",
        r"\bsavings\s+account\s+statement\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    bank_signals = [
        r"\baccount\s+summary\b",
        r"\bbeginning\s+balance\b",
        r"\bending\s+balance\b",
        r"\bdeposits?\s+and\s+other\s+additions\b",
        r"\bwithdrawals?\s+and\s+other\s+subtractions\b",
        r"\bservice\s+fees?\b",
        r"\bchecks\b",
    ]
    hits = sum(1 for pattern in bank_signals if re.search(pattern, normalized, re.I))
    has_bank_context = bool(re.search(r"\bbank\s+of\s+america\b|\bchecking\b|\bsavings\b|\bdeposit\b", normalized, re.I))
    return has_bank_context and hits >= 3


def _looks_like_credit_card_statement(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bcredit\s+card\s+statement\b",
        r"\bvisa\s+signature\b",
        r"\bmastercard\s+statement\b",
        r"\bnew\s+balance\s+total\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    card_signals = [
        r"\bprevious\s+balance\b",
        r"\bpayments?\s+and\s+other\s+credits\b",
        r"\bpurchases?\s+and\s+adjustments\b",
        r"\bminimum\s+payment\s+due\b",
        r"\bpayment\s+due\s+date\b",
        r"\btotal\s+credit\s+line\b",
        r"\btotal\s+credit\s+available\b",
    ]
    hits = sum(1 for pattern in card_signals if re.search(pattern, normalized, re.I))
    has_card_context = bool(re.search(r"\bcredit\s+card\b|\bvisa\b|\bmastercard\b|\bcard\s+services\b", normalized, re.I))
    return has_card_context and hits >= 3


def _looks_like_mortgage_interest(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bform\s+1098\b",
        r"\b1098\s+mortgage\b",
        r"\bmortgage\s+interest\s+statement\b",
        r"\bmortgage\s+interest\s+(?:statement|paid)\b",
        r"\b(?:year[- ]?to[- ]?date|ytd|total)\s+(?:mortgage\s+)?interest\s+paid\b",
        r"\binterest\s+paid\s+(?:this\s+year|year[- ]?to[- ]?date|ytd)\b",
        r"\binterest\s+received\s+from\s+(?:payer|borrower)\b",
        r"\boutstanding\s+mortgage\s+principal\b",
        r"\bprincipal\s+balance\b",
        r"\brefund\s+of\s+overpaid\s+interest\b",
        r"\bpoints\s+paid\s+on\s+purchase\b",
        r"\bprivate\s+mortgage\s+insurance\b",
        r"\bmortgage\s+lender\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    mortgage_signals = [
        r"\bmortgage\s+interest\b",
        r"\bbox\s*1\b",
        r"\bbox\s*2\b",
        r"\bbox\s*4\b",
        r"\bbox\s*5\b",
        r"\bbox\s*6\b",
        r"\bpoints\s+paid\b",
        r"\binterest\s+paid\b",
        r"\bytd\s+interest\b",
        r"\byear[- ]?to[- ]?date\s+interest\b",
        r"\boutstanding\s+(?:mortgage\s+)?principal\b",
        r"\bprincipal\s+balance\b",
        r"\bending\s+principal\b",
        r"\brefund\s+of\s+overpaid\s+interest\b",
        r"\bmortgage\s+insurance\s+premiums?\b",
        r"\bprivate\s+mortgage\s+insurance\b",
        r"\bpmi\b",
        r"\bloan\s+origination\s+date\b",
        r"\blender\b",
        r"\bproperty\s+address\b",
    ]
    hits = sum(1 for pattern in mortgage_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_property_tax(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bproperty\s+tax\s+(?:statement|bill|notice|record)\b",
        r"\breal\s+estate\s+tax(?:es)?\b",
        r"\bcounty\s+tax\s+(?:statement|bill)\b",
        r"\bparcel\s+(?:number|id)\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    property_tax_signals = [
        r"\bproperty\s+tax(?:es)?\b",
        r"\breal\s+estate\s+tax(?:es)?\b",
        r"\bassessed\s+value\b",
        r"\btaxable\s+value\b",
        r"\bparcel\b",
        r"\btax\s+year\b",
        r"\btax\s+due\b",
    ]
    hits = sum(1 for pattern in property_tax_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_charitable_receipt(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    explicit_markers = [
        r"\bdonation\s+receipt\b",
        r"\bcharitable\s+(?:contribution|donation)\s+receipt\b",
        r"\bthank\s+you\s+for\s+your\s+donation\b",
        r"\bdonation\s+contribution\b",
        r"\bmonetary\s+donation\b",
        r"\bcash\s+donation\b",
        r"\bno\s+goods\s+or\s+services\b",
        r"\bdonor\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    charity_signals = [
        r"\bdonation\b",
        r"\bcontribution\b",
        r"\bcharitable\b",
        r"\bnon[- ]?profit\b",
        r"\b501\s*\(?c\)?\(?3\)?\b",
        r"\bein\b",
        r"\breceipt\b",
    ]
    hits = sum(1 for pattern in charity_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _looks_like_prior_year_return(hay: str) -> bool:
    normalized = re.sub(r"[\u2010-\u2015]", "-", hay or "")
    if re.search(r"\b(?:tax_return|prior_year_return|tax)\b", normalized, re.I):
        return True

    explicit_markers = [
        r"\bform\s+1040(?:-sr|-nr)?\b",
        r"\bu\.?s\.?\s+individual\s+income\s+tax\s+return\b",
        r"\bprior[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bprevious[- ]?year\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\blast\s+year'?s?\s+(?:federal\s+|state\s+)?tax\s+return\b",
        r"\bfederal\s+tax\s+return\b",
        r"\bstate\s+tax\s+return\b",
    ]
    if any(re.search(pattern, normalized, re.I) for pattern in explicit_markers):
        return True

    return_signals = [
        r"\badjusted\s+gross\s+income\b",
        r"\btaxable\s+income\b",
        r"\bstandard\s+deduction\b",
        r"\bitemized\s+deductions?\b",
        r"\bschedule\s+[a-f]\b",
        r"\bfiling\s+status\b",
        r"\btotal\s+tax\b",
        r"\brefund\b",
        r"\bamount\s+you\s+owe\b",
    ]
    hits = sum(1 for pattern in return_signals if re.search(pattern, normalized, re.I))
    return hits >= 3


def _classification_confidence(form: str, text: str) -> float:
    if form in {"w2", "1099", "prior_year_return", "mortgage_interest", "property_tax", "retirement_statement", "brokerage_statement", "charitable_receipt"}:
        return 0.82
    return 0.62 if len(text) > 300 else 0.45


def _detect_year(text: str, fallback: str | None = None) -> str:
    if fallback:
        return str(fallback)
    years = re.findall(r"\b20[1-3][0-9]\b", text or "")
    return Counter(years).most_common(1)[0][0] if years else ""


def _money_amounts(text: str) -> list[tuple[str, Decimal]]:
    amounts = []
    for match in re.finditer(r"(?P<label>[A-Za-z][A-Za-z /_-]{0,45})?\s*\$?\s*(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})", text or ""):
        raw = match.group("amount").replace(",", "")
        try:
            amount = Decimal(raw)
        except Exception:
            continue
        label = (match.group("label") or "amount").strip()[-42:] or "amount"
        if amount >= 1:
            amounts.append((label, amount))
    return amounts


def _amounts_for_tax_form(form: str, text: str) -> list[tuple[str, Decimal]]:
    if form == "prior_year_return":
        return_amounts = _prior_year_return_amounts(text)
        if return_amounts:
            return return_amounts
    if form == "w2":
        w2_amounts = _w2_amounts(text)
        if w2_amounts:
            return w2_amounts
    if form == "retirement_statement":
        retirement_amounts = _retirement_amounts(text)
        if retirement_amounts:
            return retirement_amounts
    if form == "brokerage_statement":
        brokerage_amounts = _brokerage_amounts(text)
        if brokerage_amounts:
            return brokerage_amounts
    if form == "bank_statement":
        bank_amounts = _bank_statement_amounts(text)
        if bank_amounts:
            return bank_amounts
    if form == "credit_card_statement":
        card_amounts = _credit_card_statement_amounts(text)
        if card_amounts:
            return card_amounts
    if form == "mortgage_interest":
        mortgage_amounts = _mortgage_interest_amounts(text)
        if mortgage_amounts:
            return mortgage_amounts
    if form == "property_tax":
        property_tax_amounts = _property_tax_amounts(text)
        if property_tax_amounts:
            return property_tax_amounts
    if form == "charitable_receipt":
        charitable_amounts = _charitable_receipt_amounts(text)
        if charitable_amounts:
            return charitable_amounts
    return _money_amounts(text)


def _values_for_tax_form(form: str, text: str) -> list[dict]:
    if form == "prior_year_return":
        return _prior_year_return_values(text)
    if form == "w2":
        return _w2_values(text)
    if form == "mortgage_interest":
        return _mortgage_interest_values(text)
    if form == "charitable_receipt":
        return _charitable_receipt_values(text)
    if form == "bank_statement":
        return _bank_statement_values(text)
    if form == "credit_card_statement":
        return _credit_card_statement_values(text)
    return []


def _prior_year_return_amounts(text: str) -> list[tuple[str, Decimal]]:
    field_patterns = [
        ("Form 1040 wages, salaries, tips", [r"\b(?:line\s*)?1[a-z]?\b[^$0-9]{0,80}wages?,?\s+salaries?,?\s+tips?", r"\bwages?,?\s+salaries?,?\s+tips?\b"]),
        ("Taxable interest", [r"\b(?:line\s*)?2b\b[^$0-9]{0,80}taxable\s+interest\b", r"\btaxable\s+interest\b"]),
        ("Ordinary dividends", [r"\b(?:line\s*)?3b\b[^$0-9]{0,80}ordinary\s+dividends?\b", r"\bordinary\s+dividends?\b"]),
        ("IRA distributions", [r"\b(?:line\s*)?4b\b[^$0-9]{0,80}ira\s+distributions?\b", r"\btaxable\s+amount\s+ira\b", r"\bira\s+distributions?\b"]),
        ("Pensions and annuities", [r"\b(?:line\s*)?5b\b[^$0-9]{0,80}pensions?\s+and\s+annuities\b", r"\bpensions?\s+and\s+annuities\b"]),
        ("Social Security taxable benefits", [r"\b(?:line\s*)?6b\b[^$0-9]{0,80}social\s+security\s+benefits\b", r"\btaxable\s+social\s+security\s+benefits\b"]),
        ("Capital gain or loss", [r"\b(?:line\s*)?7\b[^$0-9]{0,80}capital\s+gain(?:\s+or\s+loss)?\b", r"\bcapital\s+gain(?:\s+or\s+loss)?\b"]),
        ("Business income or loss", [r"\b(?:schedule\s+c|business\s+income|business\s+loss)\b"]),
        ("Total income", [r"\b(?:line\s*)?9\b[^$0-9]{0,80}total\s+income\b", r"\btotal\s+income\b"]),
        ("Adjusted gross income", [r"\b(?:line\s*)?11\b[^$0-9]{0,80}adjusted\s+gross\s+income\b", r"\badjusted\s+gross\s+income\b", r"\bagi\b"]),
        ("Standard deduction", [r"\b(?:line\s*)?12\b[^$0-9]{0,80}standard\s+deduction\b", r"\bstandard\s+deduction\b"]),
        ("Itemized deductions", [r"\bitemized\s+deductions?\b", r"\bschedule\s+a\b"]),
        ("Qualified business income deduction", [r"\bqualified\s+business\s+income\s+deduction\b", r"\bqbi\s+deduction\b"]),
        ("Taxable income", [r"\b(?:line\s*)?15\b[^$0-9]{0,80}taxable\s+income\b", r"\btaxable\s+income\b"]),
        ("Total tax", [r"\b(?:line\s*)?24\b[^$0-9]{0,80}total\s+tax\b", r"\btotal\s+tax\b"]),
        ("Federal income tax withheld", [r"\bfederal\s+income\s+tax\s+withheld\b", r"\bfederal\s+tax\s+withheld\b"]),
        ("Estimated tax payments", [r"\bestimated\s+tax\s+payments?\b", r"\bestimated\s+tax\b"]),
        ("Earned income credit", [r"\bearned\s+income\s+credit\b", r"\beic\b"]),
        ("Child tax credit", [r"\bchild\s+tax\s+credit\b"]),
        ("Refund", [r"\brefund\b", r"\boverpaid\b"]),
        ("Amount owed", [r"\bamount\s+you\s+owe\b", r"\bamount\s+owed\b", r"\bbalance\s+due\b"]),
    ]
    return _extract_labeled_amounts(text, field_patterns)


def _prior_year_return_values(text: str) -> list[dict]:
    field_patterns = [
        ("Filing status", [r"\bfiling\s+status\b", r"\bsingle\b|\bmarried\s+filing\s+jointly\b|\bmarried\s+filing\s+separately\b|\bhead\s+of\s+household\b|\bqualifying\s+surviving\s+spouse\b"]),
        ("Taxpayer name", [r"\byour\s+first\s+name\b", r"\btaxpayer\s+name\b", r"\bprimary\s+taxpayer\b"]),
        ("Spouse name", [r"\bspouse'?s?\s+first\s+name\b", r"\bspouse\s+name\b"]),
        ("Tax year", [r"\btax\s+year\b", r"\b20[1-3][0-9]\s+form\s+1040\b"]),
        ("Dependents", [r"\bdependents?\b", r"\bqualifying\s+child\b"]),
        ("Schedule A present", [r"\bschedule\s+a\b", r"\bitemized\s+deductions?\b"]),
        ("Schedule C present", [r"\bschedule\s+c\b", r"\bprofit\s+or\s+loss\s+from\s+business\b"]),
        ("Schedule D present", [r"\bschedule\s+d\b", r"\bcapital\s+gains?\s+and\s+losses\b"]),
    ]
    return _extract_labeled_values(text, field_patterns)


def _w2_amounts(text: str) -> list[tuple[str, Decimal]]:
    box_patterns = [
        ("Box 1 - Wages, tips, other compensation", [r"\bbox\s*1\b", r"\bwages?,?\s+tips?,?\s+(?:and\s+)?other\s+compensation\b"]),
        ("Box 2 - Federal income tax withheld", [r"\bbox\s*2\b", r"\bfederal\s+income\s+tax\s+withheld\b"]),
        ("Box 3 - Social Security wages", [r"\bbox\s*3\b", r"\bsocial\s+security\s+wages\b"]),
        ("Box 4 - Social Security tax withheld", [r"\bbox\s*4\b", r"\bsocial\s+security\s+tax\s+withheld\b"]),
        ("Box 5 - Medicare wages and tips", [r"\bbox\s*5\b", r"\bmedicare\s+wages?\s+(?:and\s+)?tips\b"]),
        ("Box 6 - Medicare tax withheld", [r"\bbox\s*6\b", r"\bmedicare\s+tax\s+withheld\b"]),
        ("Box 10 - Dependent care benefits", [r"\bbox\s*10\b", r"\bdependent\s+care\s+benefits\b"]),
        ("Box 11 - Nonqualified plans", [r"\bbox\s*11\b", r"\bnonqualified\s+plans\b"]),
        ("Box 12 - Code amounts", [r"\bbox\s*12[a-d]?\b", r"\bcode\s+[a-z]{1,2}\b"]),
        ("Box 14 - Other", [r"\bbox\s*14\b", r"\b14\s+other\b"]),
        ("Box 16 - State wages, tips, etc.", [r"\bbox\s*16\b", r"\bstate\s+wages\b"]),
        ("Box 17 - State income tax", [r"\bbox\s*17\b", r"\bstate\s+income\s+tax\b"]),
        ("Box 18 - Local wages, tips, etc.", [r"\bbox\s*18\b", r"\blocal\s+wages\b"]),
        ("Box 19 - Local income tax", [r"\bbox\s*19\b", r"\blocal\s+income\s+tax\b"]),
    ]
    found: list[tuple[str, Decimal]] = _w2_normalized_box_amounts(text)
    found.extend((label, amount) for label, amount in _w2_layout_amounts(text) if label not in {seen_label for seen_label, _ in found})
    seen = {label for label, _ in found}
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    for label, patterns in box_patterns:
        if label in seen:
            continue
        if label in {
            "Box 10 - Dependent care benefits",
            "Box 11 - Nonqualified plans",
            "Box 12 - Code amounts",
            "Box 14 - Other",
        }:
            continue
        amount = _first_amount_after_any_pattern(normalized, patterns)
        if amount is not None:
            found.append((label, amount))
            seen.add(label)

    for line in (text or "").splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        amount = _last_money_amount(clean)
        if amount is None:
            continue
        lowered = clean.lower()
        for label, patterns in box_patterns:
            if any(re.search(pattern, lowered, re.I) for pattern in patterns):
                if label not in seen:
                    found.append((label, amount))
                    seen.add(label)
                break
    return _sort_w2_amounts(_dedupe_amounts(found))


def _amount_limit_for_form(form: str) -> int:
    return 24 if form == "w2" else 8


def _value_limit_for_form(form: str) -> int:
    return 16 if form == "w2" else 8


def _sort_w2_amounts(items: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
    def order(item: tuple[str, Decimal]) -> tuple[int, str]:
        label = item[0]
        box_match = re.search(r"\bbox\s+(\d+)", label, re.I)
        if not box_match:
            return (999, label)
        box = int(box_match.group(1))
        code_match = re.search(r"\bcode\s+([A-Z]{1,2})\b", label, re.I)
        code_rank = code_match.group(1) if code_match else ""
        if box == 12:
            return (120, code_rank)
        return (box, label)
    return sorted(items, key=order)


def _w2_values(text: str) -> list[dict]:
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    values = []
    if re.search(r"\b13\s+stat(?:utory)?\s+emp\.?.*?ret\.?\s+plan.*?\bX\b", normalized, re.I):
        values.append({"label": "Box 13 - Retirement plan", "value": "Checked"})
    if re.search(r"\b13\s+stat(?:utory)?\s+emp\.?\s+X\b", normalized, re.I):
        values.append({"label": "Box 13 - Statutory employee", "value": "Checked"})
    if re.search(r"\b13\s+.*?third[- ]?party\s+sick\s+pay\s+X\b", normalized, re.I):
        values.append({"label": "Box 13 - Third-party sick pay", "value": "Checked"})
    ein = re.search(r"\bemployer'?s?\s+(?:fed\s+id\s+number|identification\s+number|ein)\s+(?P<value>\d{2}-?\d{7})", normalized, re.I)
    if ein:
        values.append({"label": "Employer EIN", "value": ein.group("value")})
    return values


def _w2_normalized_box_amounts(text: str) -> list[tuple[str, Decimal]]:
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    specs = [
        ("Box 1 - Wages, tips, other compensation", [r"\bbox\s*1\s*[-:]?\s*wages?,?\s+tips?,?\s+(?:other\s+)?comp(?:ensation|\.)?\s*:"]),
        ("Box 2 - Federal income tax withheld", [r"\bbox\s*2\s*[-:]?\s*federal\s+income\s+tax\s+withheld\s*:"]),
        ("Box 3 - Social Security wages", [r"\bbox\s*3\s*[-:]?\s*social\s+security\s+wages\s*:"]),
        ("Box 4 - Social Security tax withheld", [r"\bbox\s*4\s*[-:]?\s*social\s+security\s+tax\s+withheld\s*:"]),
        ("Box 5 - Medicare wages and tips", [r"\bbox\s*5\s*[-:]?\s*medicare\s+wages?\s+and\s+tips\s*:"]),
        ("Box 6 - Medicare tax withheld", [r"\bbox\s*6\s*[-:]?\s*medicare\s+tax\s+withheld\s*:"]),
        ("Box 7 - Social security tips", [r"\bbox\s*7\s*[-:]?\s*social\s+security\s+tips\s*:"]),
        ("Box 8 - Allocated tips", [r"\bbox\s*8\s*[-:]?\s*allocated\s+tips\s*:"]),
        ("Box 10 - Dependent care benefits", [r"\bbox\s*10\s*[-:]?\s*dependent\s+care\s+benefits\s*:"]),
        ("Box 11 - Nonqualified plans", [r"\bbox\s*11\s*[-:]?\s*nonqualified\s+plans\s*:"]),
        ("Box 14 - Other", [r"\bbox\s*14\s*[-:]?\s*other\s*:"]),
    ]
    found: list[tuple[str, Decimal]] = []
    seen = set()
    for label, patterns in specs:
        for pattern in patterns:
            amount = _first_w2_amount_after_label(normalized, pattern)
            if amount is not None and label not in seen:
                found.append((label, amount))
                seen.add(label)
                break
    for code, amount in _w2_box12_amounts_anywhere(normalized):
        label = f"Box 12 - Code {code}"
        if label not in seen:
            found.append((label, amount))
            seen.add(label)
    return found


def _first_w2_amount_after_label(text: str, label_pattern: str) -> Decimal | None:
    matches = list(re.finditer(label_pattern, text or "", re.I))
    best: Decimal | None = None
    for match in matches:
        window = text[match.end():match.end() + 120]
        amount = _first_financial_amount(window)
        if amount is None:
            continue
        if best is None:
            best = amount
            continue
        # Repeated W-2 copies on one image should agree; keep the first clear copy.
    return best


def _w2_layout_amounts(text: str) -> list[tuple[str, Decimal]]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    found: list[tuple[str, Decimal]] = _w2_flattened_amounts(text)
    seen = {label for label, _ in found}
    paired_rows = [
        (
            r"\b1\s+wages?,?\s+tips?,?\s+(?:other\s+)?comp(?:ensation|\.)?\b.*\b2\s+federal\s+income\s+tax\s+withheld\b",
            [
                ("Box 1 - Wages, tips, other compensation", 0),
                ("Box 2 - Federal income tax withheld", 1),
            ],
        ),
        (
            r"\b3\s+social\s+security\s+wages\b.*\b4\s+social\s+security\s+tax\s+withheld\b",
            [
                ("Box 3 - Social Security wages", 0),
                ("Box 4 - Social Security tax withheld", 1),
            ],
        ),
        (
            r"\b5\s+medicare\s+wages?\s+and\s+tips\b.*\b6\s+medicare\s+tax\s+withheld\b",
            [
                ("Box 5 - Medicare wages and tips", 0),
                ("Box 6 - Medicare tax withheld", 1),
            ],
        ),
        (
            r"\b7\s+social\s+security\s+tips\b.*\b8\s+allocated\s+tips\b",
            [
                ("Box 7 - Social security tips", 0),
                ("Box 8 - Allocated tips", 1),
            ],
        ),
        (
            r"\b10\s+dependent\s+care\s+benefits\b",
            [("Box 10 - Dependent care benefits", 0)],
        ),
        (
            r"\b11\s+nonqualified\s+plans\b",
            [("Box 11 - Nonqualified plans", 0)],
        ),
    ]

    for i, line in enumerate(lines):
        if not line:
            continue
        lowered = line.lower()
        for row_pattern, assignments in paired_rows:
            if not re.search(row_pattern, lowered, re.I):
                continue
            value_amounts = _next_w2_value_amounts(lines, i + 1, max_index=max(index for _, index in assignments))
            if not value_amounts:
                continue
            for label, amount_index in assignments:
                if label in seen or amount_index >= len(value_amounts):
                    continue
                found.append((label, value_amounts[amount_index]))
                seen.add(label)

    for code, amount in _w2_box12_code_amounts(lines):
        label = f"Box 12 - Code {code}"
        if label not in seen:
            found.append((label, amount))
            seen.add(label)
    return found


def _next_w2_value_amounts(lines: list[str], start: int, max_index: int) -> list[Decimal]:
    for candidate in lines[start:start + 6]:
        amounts = _financial_amounts_in_line(candidate)
        if len(amounts) > max_index:
            return amounts
    return []


def _w2_flattened_amounts(text: str) -> list[tuple[str, Decimal]]:
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    found: list[tuple[str, Decimal]] = []
    seen = set()
    spans = [
        (
            r"\b1\s+wages?,?\s+tips?,?\s+(?:other\s+)?comp(?:ensation|\.)?\b(?P<body>.*?)\b3\s+social\s+security\s+wages\b",
            [
                ("Box 1 - Wages, tips, other compensation", 0),
                ("Box 2 - Federal income tax withheld", 1),
            ],
        ),
        (
            r"\b3\s+social\s+security\s+wages\b(?P<body>.*?)\b5\s+medicare\s+wages?\s+and\s+tips\b",
            [
                ("Box 3 - Social Security wages", 0),
                ("Box 4 - Social Security tax withheld", 1),
            ],
        ),
        (
            r"\b5\s+medicare\s+wages?\s+and\s+tips\b(?P<body>.*?)\b7\s+social\s+security\s+tips\b",
            [
                ("Box 5 - Medicare wages and tips", 0),
                ("Box 6 - Medicare tax withheld", 1),
            ],
        ),
        (
            r"\b7\s+social\s+security\s+tips\b(?P<body>.*?)\b(?:b\s+employer\s+identification\s+number|9\b|10\s+dependent\s+care\s+benefits)\b",
            [
                ("Box 7 - Social security tips", 0),
                ("Box 8 - Allocated tips", 1),
            ],
        ),
        (
            r"\b10\s+dependent\s+care\s+benefits\b(?P<body>.*?)\b(?:e\s+employee|11\s+nonqualified\s+plans|13\s+statutory)\b",
            [("Box 10 - Dependent care benefits", 0)],
        ),
        (
            r"\b11\s+nonqualified\s+plans\b(?P<body>.*?)\b(?:12\s+see\s+instructions|13\s+statutory)\b",
            [("Box 11 - Nonqualified plans", 0)],
        ),
    ]
    for pattern, assignments in spans:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        body = match.group("body")
        amounts = _financial_amounts_in_w2_value_span(body)
        for label, amount_index in assignments:
            if label in seen or amount_index >= len(amounts):
                continue
            found.append((label, amounts[amount_index]))
            seen.add(label)

    for code, amount in _w2_flattened_box12_code_amounts(normalized):
        label = f"Box 12 - Code {code}"
        if label not in seen:
            found.append((label, amount))
            seen.add(label)
    box14 = _w2_box14_other_amount(normalized)
    if box14 is not None and "Box 14 - Other" not in seen:
        found.append(("Box 14 - Other", box14))
        seen.add("Box 14 - Other")
    return found


def _financial_amounts_in_w2_value_span(span: str) -> list[Decimal]:
    cleaned = re.sub(r"\b(?:XXX[- ]?XX[- ]?\d{4}|\d{2}-\d{7})\b", " ", span or "", flags=re.I)
    cleaned = re.sub(
        r"\b(?:federal\s+income\s+tax\s+withheld|social\s+security\s+tax\s+withheld|medicare\s+tax\s+withheld|allocated\s+tips|dependent\s+care\s+benefits|nonqualified\s+plans)\b",
        " ",
        cleaned,
        flags=re.I,
    )
    return _financial_amounts_in_line(cleaned)


def _w2_flattened_box12_code_amounts(normalized: str) -> list[tuple[str, Decimal]]:
    match = re.search(r"\b12a?\s+(?:see\s+instructions\s+for\s+box\s+12)?\b(?P<body>.*?)\b15\s+state\b", normalized or "", re.I)
    if not match:
        return []
    amounts = []
    for item in re.finditer(r"\b(?P<code>[A-Z]{1,2})\s+(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})\b", match.group("body")):
        try:
            amounts.append((item.group("code"), Decimal(item.group("amount").replace(",", ""))))
        except Exception:
            continue
    return amounts


def _w2_box12_amounts_anywhere(normalized: str) -> list[tuple[str, Decimal]]:
    out: list[tuple[str, Decimal]] = []
    seen = set()
    for match in re.finditer(
        r"\b12(?P<suffix>[a-d])?\s*(?:see\s+instructions\s+for\s+box\s+12)?\s*(?P<code>[A-Z]{1,2})\s+(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})",
        normalized or "",
        re.I,
    ):
        code = match.group("code").upper()
        if code in seen:
            continue
        try:
            out.append((code, Decimal(match.group("amount").replace(",", ""))))
            seen.add(code)
        except Exception:
            continue
    return out


def _w2_box14_other_amount(normalized: str) -> Decimal | None:
    for match in re.finditer(r"\b14\s+other\b(?P<body>.*?)(?:\b12a\b|\b12\s+see|\b13\s+stat|\b15\s+state\b)", normalized or "", re.I):
        prefix = normalized[max(0, match.start() - 50):match.start()]
        if re.search(r"\b12\s+(?:see|[a-d])\b", prefix, re.I):
            continue
        amount = _first_financial_amount(match.group("body"))
        if amount is not None:
            return amount
    return None


def _financial_amounts_in_line(line: str) -> list[Decimal]:
    amounts = []
    for match in re.finditer(r"(?:\$\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})", line or ""):
        try:
            amounts.append(Decimal(match.group(1).replace(",", "")))
        except Exception:
            continue
    return amounts


def _w2_box12_code_amounts(lines: list[str]) -> list[tuple[str, Decimal]]:
    amounts = []
    in_box12 = False
    for line in lines:
        if re.search(r"\b12\s+see\s+instructions\s+for\s+box\s+12\b", line, re.I):
            in_box12 = True
            continue
        if in_box12 and re.search(r"\b15\s+state\b", line, re.I):
            break
        if not in_box12:
            continue
        match = re.match(r"^(?P<code>[A-Z]{1,2})\s+(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})\b", line)
        if not match:
            continue
        try:
            amounts.append((match.group("code"), Decimal(match.group("amount").replace(",", ""))))
        except Exception:
            continue
    return amounts


def _retirement_amounts(text: str) -> list[tuple[str, Decimal]]:
    field_patterns = [
        ("Beginning balance", [r"\bbeginning\s+(?:account\s+)?balance\b", r"\bopening\s+(?:account\s+)?balance\b"]),
        ("Ending balance", [r"\bending\s+(?:account\s+)?balance\b", r"\bcurrent\s+(?:account\s+)?balance\b", r"\btotal\s+balance\b"]),
        ("Vested balance", [r"\bvested\s+balance\b", r"\bvested\s+account\s+balance\b"]),
        ("Employee pre-tax contribution", [r"\bemployee\s+pre[- ]?tax\s+contributions?\b", r"\bpre[- ]?tax\s+contributions?\b"]),
        ("Employee Roth contribution", [r"\bemployee\s+roth\s+contributions?\b", r"\broth\s+contributions?\b"]),
        ("Employee after-tax contribution", [r"\bemployee\s+after[- ]?tax\s+contributions?\b", r"\bafter[- ]?tax\s+contributions?\b"]),
        ("Employer match", [r"\bemployer\s+match(?:ing)?\b", r"\bcompany\s+match(?:ing)?\b"]),
        ("Employer contribution", [r"\bemployer\s+contributions?\b", r"\bcompany\s+contributions?\b"]),
        ("Total contribution", [r"\btotal\s+contributions?\b", r"\byear[- ]?to[- ]?date\s+contributions?\b", r"\bytd\s+contributions?\b"]),
        ("Rollover contribution", [r"\brollover\s+contributions?\b", r"\brollovers?\b"]),
        ("Distribution / withdrawal", [r"\bdistributions?\b", r"\bwithdrawals?\b", r"\bhardship\s+withdrawals?\b"]),
        ("Loan balance", [r"\bloan\s+balance\b", r"\boutstanding\s+loan\b"]),
        ("Loan repayment", [r"\bloan\s+repayments?\b"]),
        ("Investment gain / loss", [r"\binvestment\s+(?:gain|loss|gains|losses)\b", r"\bchange\s+in\s+value\b", r"\bmarket\s+gain\b", r"\bearnings\b"]),
        ("Fees", [r"\bfees?\b", r"\badministrative\s+fees?\b", r"\bexpense\s+charges?\b"]),
    ]
    found: list[tuple[str, Decimal]] = []
    seen = set()
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    for label, patterns in field_patterns:
        amount = _first_financial_amount_after_any_pattern(normalized, patterns)
        if amount is not None:
            found.append((label, amount))
            seen.add(label)

    for line in (text or "").splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        amount = _last_financial_amount(clean)
        if amount is None:
            continue
        lowered = clean.lower()
        for label, patterns in field_patterns:
            if any(re.search(pattern, lowered, re.I) for pattern in patterns):
                if label not in seen:
                    found.append((label, amount))
                    seen.add(label)
                break
    return found


def _brokerage_amounts(text: str) -> list[tuple[str, Decimal]]:
    field_patterns = [
        ("1099-INT interest income", [r"\b1099[- ]?int\s+interest\s+income\b", r"\binterest\s+income\b", r"\btaxable\s+interest\b"]),
        ("1099-DIV ordinary dividends", [r"\b1099[- ]?div\s+ordinary\s+dividends?\b", r"\bordinary\s+dividends?\b", r"\btotal\s+ordinary\s+dividends?\b"]),
        ("Qualified dividends", [r"\bqualified\s+dividends?\b"]),
        ("Capital gain distributions", [r"\bcapital\s+gain\s+distributions?\b", r"\bcapital\s+gains?\s+distributions?\b"]),
        ("Gross proceeds", [r"\bgross\s+proceeds\b", r"\bproceeds\s+from\s+(?:broker|barter)\b", r"\bsales?\s+proceeds\b"]),
        ("Cost basis", [r"\bcost\s+basis\b", r"\badjusted\s+cost\s+basis\b"]),
        ("Short-term gain / loss", [r"\bshort[- ]?term\s+(?:capital\s+)?(?:gain|loss|gains|losses)\b"]),
        ("Long-term gain / loss", [r"\blong[- ]?term\s+(?:capital\s+)?(?:gain|loss|gains|losses)\b"]),
        ("Federal income tax withheld", [r"\bfederal\s+income\s+tax\s+withheld\b", r"\bfederal\s+tax\s+withheld\b", r"\bbackup\s+withholding\b"]),
        ("Foreign tax paid", [r"\bforeign\s+tax\s+paid\b"]),
        ("Margin interest", [r"\bmargin\s+interest\b"]),
        ("Investment advisory fees", [r"\binvestment\s+advisory\s+fees?\b", r"\badvisory\s+fees?\b", r"\baccount\s+fees?\b"]),
        ("Beginning account value", [r"\bbeginning\s+account\s+value\b", r"\bopening\s+account\s+value\b"]),
        ("Ending account value", [r"\bending\s+account\s+value\b", r"\bclosing\s+account\s+value\b", r"\baccount\s+value\b"]),
        ("Deposits / contributions", [r"\bdeposits?\b", r"\bcontributions?\b"]),
        ("Withdrawals", [r"\bwithdrawals?\b", r"\bcash\s+distributions?\b"]),
    ]
    found: list[tuple[str, Decimal]] = []
    seen = set()
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    for label, patterns in field_patterns:
        amount = _first_financial_amount_after_any_pattern(normalized, patterns)
        if amount is not None:
            found.append((label, amount))
            seen.add(label)

    for line in (text or "").splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        amount = _last_financial_amount(clean)
        if amount is None:
            continue
        lowered = clean.lower()
        for label, patterns in field_patterns:
            if any(re.search(pattern, lowered, re.I) for pattern in patterns):
                if label not in seen:
                    found.append((label, amount))
                    seen.add(label)
                break
    return found


def _bank_statement_amounts(text: str) -> list[tuple[str, Decimal]]:
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    found: list[tuple[str, Decimal]] = []
    account_summary = _bank_account_summary_amounts(text)

    total_balance = _amount_after_line_label(text, r"\btotal\s+balance\b")
    ending_balance = account_summary.get("Ending balance") or _amount_after_line_label(text, r"\bending\s+balance\b")
    if total_balance is not None:
        found.append(("Total deposit balance", total_balance))
    elif ending_balance is not None:
        found.append(("Ending balance", ending_balance))

    for label in [
        "Beginning balance",
        "Deposits and other additions",
        "Withdrawals and other subtractions",
        "Checks",
        "Service fees",
        "Interest paid YTD",
    ]:
        if label in account_summary:
            found.append((label, account_summary[label]))

    field_patterns = [
        ("Beginning balance", [r"\bbeginning\s+balance\b", r"\bopening\s+balance\b"]),
        ("Deposits and other additions", [r"\bdeposits?\s+and\s+other\s+additions\b", r"\btotal\s+deposits?\b", r"\bcredits?\b"]),
        ("Withdrawals and other subtractions", [r"\bwithdrawals?\s+and\s+other\s+subtractions\b", r"\btotal\s+withdrawals?\b", r"\bdebits?\b"]),
        ("Checks", [r"\bchecks\b", r"\bchecks?\s+paid\b"]),
        ("Service fees", [r"\bservice\s+fees?\b", r"\bmonthly\s+maintenance\s+fees?\b"]),
        ("Interest paid YTD", [r"\binterest\s+paid\s+ytd\b", r"\byear[- ]?to[- ]?date\s+interest\b"]),
    ]
    seen = {label for label, _ in found}
    for label, patterns in field_patterns:
        amount = _first_signed_financial_amount_after_any_pattern(normalized, patterns)
        if amount is not None and label not in seen:
            found.append((label, amount))
            seen.add(label)
    return _dedupe_amounts(found)


def _credit_card_statement_amounts(text: str) -> list[tuple[str, Decimal]]:
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    summary = _credit_card_summary_amounts(text)
    field_patterns = [
        ("Previous balance", [r"\bprevious\s+balance\b"]),
        ("Payments and other credits", [r"\bpayments?\s+and\s+other\s+credits\b", r"\bpayments?\s*/\s*credits\b"]),
        ("Purchases and adjustments", [r"\bpurchases?\s+and\s+adjustments\b", r"\bpurchases?\b"]),
        ("Fees charged", [r"\bfees?\s+charged\b", r"\bfees\b"]),
        ("Interest charged", [r"\binterest\s+charged\b", r"\binterest\b"]),
        ("New balance total", [r"\bnew\s+balance\s+total\b", r"\bnew\s+balance\b", r"\bstatement\s+balance\b"]),
    ]
    found: list[tuple[str, Decimal]] = []
    seen = set()
    for label in [
        "Previous balance",
        "Payments and other credits",
        "Purchases and adjustments",
        "Fees charged",
        "Interest charged",
        "New balance total",
    ]:
        if label in summary:
            found.append((label, summary[label]))
            seen.add(label)
    for label, patterns in field_patterns:
        amount = _first_signed_financial_amount_after_any_pattern(normalized, patterns)
        if amount is not None and label not in seen:
            found.append((label, amount))
            seen.add(label)
    return _dedupe_amounts(found)


def _bank_statement_values(text: str) -> list[dict]:
    values: list[dict] = []
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    period = re.search(
        r"\b(?:for\s+the\s+period\s+)?([A-Za-z]+\s+\d{1,2},?\s+20[1-3][0-9])\s+(?:to|through|-)\s+([A-Za-z]+\s+\d{1,2},?\s+20[1-3][0-9])",
        normalized,
        re.I,
    )
    if period:
        values.append({"label": "Statement period", "value": f"{period.group(1)} to {period.group(2)}"})
    account = re.search(r"\baccount\s+(?:number\s*)?(?:#\s*)?(\d{4}\s+\d{4}\s+\d{4}|\d{8,16})\b", normalized, re.I)
    if account:
        values.append({"label": "Account number", "value": account.group(1).strip()})
    if re.search(r"\badvantage\s+plus\s+banking\b", normalized, re.I):
        values.append({"label": "Account type", "value": "Advantage Plus Banking"})
    elif re.search(r"\badvantage\s+savings\b", normalized, re.I):
        values.append({"label": "Account type", "value": "Advantage Savings"})
    return values[:8]


def _credit_card_statement_values(text: str) -> list[dict]:
    values: list[dict] = []
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    account = re.search(r"\baccount\s+(?:number\s*)?(?:#\s*)?(\d{4}\s+\d{4}\s+\d{4}\s+\d{4}|\d{12,19})\b", normalized, re.I)
    if account:
        values.append({"label": "Account number", "value": account.group(1).strip()})
    period = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\s*-\s*(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+20[1-3][0-9]",
        normalized,
        re.I,
    )
    if period:
        values.append({"label": "Statement period", "value": period.group(0).strip()})
    due_date = re.search(r"\b(?:payment\s+)?due\s+date\b[^0-9]{0,40}(\d{1,2}/\d{1,2}/\d{2,4})", normalized, re.I)
    if due_date:
        values.append({"label": "Payment due date", "value": due_date.group(1)})
    closing_date = re.search(r"\bstatement\s+closing\s+date\b[^0-9]{0,40}(\d{1,2}/\d{1,2}/\d{2,4})", normalized, re.I)
    if closing_date:
        values.append({"label": "Statement closing date", "value": closing_date.group(1)})
    return values[:8]


def _bank_account_summary_amounts(text: str) -> dict[str, Decimal]:
    lines = _nonempty_statement_lines(text)
    summary: dict[str, Decimal] = {}
    for index, line in enumerate(lines):
        if not re.fullmatch(r"account\s+summary", line, re.I):
            continue
        label_patterns = [
            ("Beginning balance", r"\bbeginning\s+balance\b"),
            ("Deposits and other additions", r"\bdeposits?\s+and\s+other\s+additions\b"),
            ("Withdrawals and other subtractions", r"\bwithdrawals?\s+and\s+other\s+subtractions\b"),
        ]
        labels: list[str] = []
        scan = index + 1
        while scan < len(lines) and scan <= index + 12:
            if _first_signed_financial_amount(lines[scan]) is not None:
                break
            for label, pattern in label_patterns:
                if label not in labels and re.search(pattern, lines[scan], re.I):
                    labels.append(label)
            scan += 1
        amounts: list[Decimal] = []
        while scan < len(lines) and len(amounts) < len(labels) and scan <= index + 24:
            amount = _first_signed_financial_amount(lines[scan])
            if amount is not None:
                amounts.append(amount)
            scan += 1
        for label, amount in zip(labels, amounts):
            summary[label] = amount
        break

    for label, pattern in [
        ("Checks", r"\bchecks\b"),
        ("Service fees", r"\bservice\s+fees?\b"),
        ("Ending balance", r"\bending\s+balance\b"),
        ("Interest paid YTD", r"\binterest\s+paid\s+ytd\b"),
    ]:
        amount = _amount_after_line_label(text, pattern)
        if amount is not None:
            summary[label] = amount
    return summary


def _credit_card_summary_amounts(text: str) -> dict[str, Decimal]:
    lines = _nonempty_statement_lines(text)
    summary: dict[str, Decimal] = {}
    start = next((index for index, line in enumerate(lines) if re.search(r"\baccount\s+summary/payment\s+information\b", line, re.I)), None)
    if start is None:
        return summary

    amounts: list[Decimal] = []
    for line in lines[start + 1:start + 45]:
        amount = _first_signed_financial_amount(line)
        if amount is not None:
            amounts.append(amount)
    if len(amounts) >= 3:
        summary["Previous balance"] = amounts[0]
        summary["Payments and other credits"] = amounts[1]
        summary["Purchases and adjustments"] = amounts[2]
    zero_amounts = [amount for amount in amounts[3:] if amount == 0]
    if zero_amounts:
        summary["Fees charged"] = zero_amounts[0]
    if len(zero_amounts) > 1:
        summary["Interest charged"] = zero_amounts[1]

    for label, pattern in [
        ("New balance total", r"\bnew\s+balance\s+total\b"),
    ]:
        amount = _amount_after_line_label(text, pattern)
        if amount is not None:
            summary[label] = amount
    return summary


def _amount_after_line_label(text: str, pattern: str, max_lines: int = 8) -> Decimal | None:
    lines = _nonempty_statement_lines(text)
    for index, line in enumerate(lines):
        if not re.search(pattern, line, re.I):
            continue
        for candidate in lines[index + 1:index + 1 + max_lines]:
            if re.search(r"\b(?:page|date|days\s+in\s+billing\s+cycle|account\s+summary)\b", candidate, re.I):
                continue
            amount = _first_signed_financial_amount(candidate)
            if amount is not None:
                return amount
    return None


def _nonempty_statement_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines() if re.sub(r"\s+", " ", line).strip()]


def _mortgage_interest_amounts(text: str) -> list[tuple[str, Decimal]]:
    field_patterns = [
        ("Box 1 - Mortgage interest received from payer/borrower", [r"\bbox\s*1\b[^$0-9]{0,80}(?:mortgage\s+)?interest\s+received\s+from\s+(?:payer|borrower)", r"\bform\s+1098\s+mortgage\s+interest\s+paid\b", r"\bmortgage\s+interest\s+paid\b", r"\binterest\s+received\s+from\s+(?:payer|borrower)\b", r"\b(?:year[- ]?to[- ]?date|ytd|total)\s+(?:mortgage\s+)?interest\s+paid\b", r"\b(?:year[- ]?to[- ]?date|ytd|total)\s+interest\b", r"\binterest\s+paid\s+(?:this\s+year|year[- ]?to[- ]?date|ytd)\b"]),
        ("Box 2 - Outstanding mortgage principal", [r"\bbox\s*2\b[^$0-9]{0,80}outstanding\s+mortgage\s+principal", r"\boutstanding\s+mortgage\s+principal\b", r"\boutstanding\s+principal\b", r"\bprincipal\s+balance\b", r"\bending\s+principal\s+balance\b", r"\bending\s+principal\b", r"\bcurrent\s+principal\s+balance\b", r"\bunpaid\s+principal\s+balance\b"]),
        ("Box 4 - Refund of overpaid interest", [r"\bbox\s*4\b[^$0-9]{0,80}refund\s+of\s+overpaid\s+interest", r"\brefund\s+of\s+overpaid\s+interest\b", r"\boverpaid\s+interest\s+refund\b", r"\binterest\s+refund\b"]),
        ("Box 5 - Mortgage insurance premiums", [r"\bbox\s*5\b[^$0-9]{0,80}mortgage\s+insurance\s+premiums?", r"\bmortgage\s+insurance\s+premiums?\b", r"\bprivate\s+mortgage\s+insurance\b", r"\bpmi\b", r"\bmip\b"]),
        ("Box 6 - Points paid on purchase of principal residence", [r"\bbox\s*6\b[^$0-9]{0,100}points\s+paid\s+on\s+purchase", r"\bpoints\s+paid\s+on\s+purchase(?:\s+of\s+principal\s+residence)?\b", r"\bpoints\s+paid\b", r"\borigination\s+points\b", r"\bdiscount\s+points\b"]),
        ("Box 10 - Other", [r"\bbox\s*10\b[^$0-9]{0,80}other\b", r"\bother\s+1098\s+amount\b"]),
        ("Real Estate Taxes Paid", [r"\breal\s+estate\s+tax(?:es)?\s+paid(?:\s+through\s+escrow)?\b", r"\bproperty\s+tax(?:es)?\s+paid\s+through\s+escrow\b", r"\bescrow\s+(?:property\s+)?tax(?:es)?\b", r"\btaxes?\s+disbursed\s+from\s+escrow\b", r"\btaxes?\s+paid\s+from\s+escrow\b"]),
        ("Escrow balance", [r"\bescrow\s+balance\b"]),
        ("Loan origination amount", [r"\boriginal\s+loan\s+amount\b", r"\bloan\s+amount\b"]),
    ]
    return _extract_labeled_amounts(text, field_patterns)


def _mortgage_interest_values(text: str) -> list[dict]:
    field_patterns = [
        ("Mortgage origination date", [r"\bbox\s*3\b[^a-z0-9]{0,40}mortgage\s+origination\s+date\b", r"\bmortgage\s+origination\s+date\b", r"\borigination\s+date\b", r"\bloan\s+origination\s+date\b"]),
        ("Mortgage lender", [r"\bmortgage\s+lender\b", r"\blender\b", r"\brecipient/lender\b"]),
        ("Borrower", [r"\bpayer/borrower\b", r"\bborrower\b"]),
        ("Property address", [r"\bproperty\s+address\b", r"\bsecured\s+property\s+address\b"]),
        ("Account number", [r"\baccount\s+number\b", r"\bloan\s+number\b"]),
    ]
    return _extract_labeled_values(text, field_patterns)


def _property_tax_amounts(text: str) -> list[tuple[str, Decimal]]:
    field_patterns = [
        ("Property tax paid", [r"\bproperty\s+tax(?:es)?\s+paid\b", r"\breal\s+estate\s+tax(?:es)?\s+paid\b", r"\btaxes?\s+paid\b"]),
        ("Total property tax due", [r"\btotal\s+property\s+tax(?:es)?\s+due\b", r"\btotal\s+tax(?:es)?\s+due\b", r"\btax\s+due\b", r"\bamount\s+due\b"]),
        ("First installment", [r"\bfirst\s+installment\b", r"\b1st\s+installment\b"]),
        ("Second installment", [r"\bsecond\s+installment\b", r"\b2nd\s+installment\b"]),
        ("County tax", [r"\bcounty\s+tax(?:es)?\b"]),
        ("City tax", [r"\bcity\s+tax(?:es)?\b"]),
        ("School district tax", [r"\bschool\s+(?:district\s+)?tax(?:es)?\b"]),
        ("Special assessment", [r"\bspecial\s+assessment\b", r"\bassessments?\b"]),
        ("Assessed value", [r"\bassessed\s+value\b", r"\btotal\s+assessed\s+value\b"]),
        ("Taxable value", [r"\btaxable\s+value\b"]),
        ("Land value", [r"\bland\s+value\b"]),
        ("Improvement value", [r"\bimprovement\s+value\b", r"\bbuilding\s+value\b"]),
        ("Prior year balance", [r"\bprior\s+year\s+balance\b", r"\bdelinquent\s+tax(?:es)?\b"]),
    ]
    return _extract_labeled_amounts(text, field_patterns)


def _charitable_receipt_amounts(text: str) -> list[tuple[str, Decimal]]:
    field_patterns = [
        ("Total charitable contribution", [r"\bdonation\s+contribution\s+valued\s+at\b", r"\btotal\s+(?:charitable\s+)?(?:donation|contribution)\b", r"\btotal\s+amount\s+(?:donated|contributed)\b", r"\bcontribution\s+amount\b", r"\bdonation\s+amount\b"]),
        ("Monetary donation", [r"\bmonetary\s+donation\b", r"\bcash\s+donation\b", r"\bcash\s+contribution\b"]),
        ("Non-cash donation value", [r"\bnon[- ]?cash\s+donation\b", r"\bfair\s+market\s+value\b", r"\bgoods\s+donation\b", r"\bin[- ]?kind\s+donation\b"]),
    ]
    found = _extract_labeled_amounts(text, field_patterns)
    found.extend(_charitable_purpose_amounts(text, {label for label, _ in found}))
    return _dedupe_amounts(found)


def _charitable_receipt_values(text: str) -> list[dict]:
    values = _charitable_sentence_values(text, set())
    seen = {item["label"] for item in values}
    field_patterns = [
        ("Goods or services received", [r"\bgoods\s+or\s+services\s+received\b", r"\bno\s+goods\s+or\s+services\b"]),
    ]
    for item in _extract_labeled_values(text, field_patterns):
        if item["label"] not in seen:
            values.append(item)
            seen.add(item["label"])
    return values[:8]


def _charitable_purpose_amounts(text: str, seen_labels: set[str]) -> list[tuple[str, Decimal]]:
    amounts: list[tuple[str, Decimal]] = []
    patterns = [
        (r"\$(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+\.\d{2})\s+as\s+(?P<label>[^.,;\n]{2,80})", False),
        (r"\$(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+\.\d{2})\s+for\s+(?P<label>[^.,;\n]{2,80})", False),
    ]
    for pattern, label_first in patterns:
        for match in re.finditer(pattern, text or "", re.I):
            raw_label = match.group("label")
            label = _clean_donation_label(raw_label)
            if not label or label in seen_labels:
                continue
            try:
                amount = Decimal(match.group("amount").replace(",", ""))
            except Exception:
                continue
            amounts.append((label, amount))
            seen_labels.add(label)
    return amounts


def _clean_donation_label(label: str) -> str:
    clean = re.sub(r"\s+", " ", label or "").strip(" :;-.,")
    clean = re.sub(r"^donation\s+for\s+", "", clean, flags=re.I)
    clean = re.sub(r"^(?:a|an|the)\s+", "", clean, flags=re.I)
    clean = re.sub(r"\s+(?:and|with)\s+.*$", "", clean, flags=re.I)
    clean = clean[:80].strip()
    if not clean or re.search(r"\b(?:summarized|follows|valued|receipt|record)\b", clean, re.I):
        return ""
    return clean[:1].upper() + clean[1:]


def _charitable_sentence_values(text: str, seen_labels: set[str]) -> list[dict]:
    values: list[dict] = []
    if "Donor" not in seen_labels:
        match = re.search(r"\bDonor\s*[:#-]\s*([^\n]+)", text or "", re.I) or re.search(r"\bDear\s+([^,\n]+)", text or "", re.I)
        if match:
            values.append({"label": "Donor", "value": match.group(1).strip()})
            seen_labels.add("Donor")
    if "Organization" not in seen_labels:
        match = re.search(r"\bOrganization\s*[:#-]\s*([^\n]+)", text or "", re.I) or re.search(r"\bSincerely\s+([^\n]+)", text or "", re.I)
        if match:
            value = re.sub(r"\s+Date:.*$", "", match.group(1).strip(), flags=re.I)
            values.append({"label": "Organization", "value": value})
            seen_labels.add("Organization")
    if "Receipt date" not in seen_labels:
        match = re.search(r"\bDate:\s*([A-Za-z]+\s+\d{1,2},?\s+20[1-3][0-9]|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|20[1-3][0-9][/-]\d{1,2}[/-]\d{1,2})", text or "", re.I)
        if match:
            values.append({"label": "Receipt date", "value": match.group(1).strip().rstrip(".")})
            seen_labels.add("Receipt date")
    if "EIN" not in seen_labels:
        match = re.search(r"\bEIN\s*[:#-]?\s*(\d{2}-\d{7})", text or "", re.I)
        if match:
            values.append({"label": "EIN", "value": match.group(1)})
            seen_labels.add("EIN")
    if "Goods or services received" not in seen_labels:
        match = re.search(r"\bgoods\s+or\s+services\s+received\s*[:#-]?\s*([^\n.]+|none)", text or "", re.I)
        if match:
            values.append({"label": "Goods or services received", "value": match.group(1).strip()})
    return values


def _dedupe_amounts(items: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
    out: list[tuple[str, Decimal]] = []
    seen = set()
    for label, amount in items:
        key = (label.lower(), amount)
        if key in seen:
            continue
        out.append((label, amount))
        seen.add(key)
    return out


def _extract_labeled_amounts(text: str, field_patterns: list[tuple[str, list[str]]]) -> list[tuple[str, Decimal]]:
    found: list[tuple[str, Decimal]] = []
    seen = set()
    normalized = re.sub(r"\s+", " ", text or " ").strip()
    for label, patterns in field_patterns:
        amount = _first_financial_amount_after_any_pattern(normalized, patterns)
        if amount is not None:
            found.append((label, amount))
            seen.add(label)

    for line in (text or "").splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        amount = _last_financial_amount(clean)
        if amount is None:
            continue
        lowered = clean.lower()
        for label, patterns in field_patterns:
            if any(re.search(pattern, lowered, re.I) for pattern in patterns):
                if label not in seen:
                    found.append((label, amount))
                    seen.add(label)
                break
    return found


def _extract_labeled_values(text: str, field_patterns: list[tuple[str, list[str]]]) -> list[dict]:
    found: list[dict] = []
    seen = set()
    for line in (text or "").splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        lowered = clean.lower()
        for label, patterns in field_patterns:
            if label in seen:
                continue
            for pattern in patterns:
                match = re.search(pattern, lowered, re.I)
                if match:
                    value = _value_after_label(clean, match.end())
                    if value:
                        found.append({"label": label, "value": value})
                        seen.add(label)
                    break
            if label in seen:
                break

    normalized = re.sub(r"\s+", " ", text or " ").strip()
    lowered_normalized = normalized.lower()
    for label, patterns in field_patterns:
        if label in seen:
            continue
        for pattern in patterns:
            match = re.search(pattern, lowered_normalized, re.I)
            if match:
                value = _value_after_label(normalized, match.end())
                if value:
                    found.append({"label": label, "value": value})
                    seen.add(label)
                break
    return found


def _value_after_label(text: str, start: int) -> str:
    fragment = text[start:start + 140]
    if re.search(r"^\s*:?\s*(?:not\s+shown|not\s+included|none|n/?a|not\s+available)\b", fragment, re.I):
        return ""
    date_match = re.search(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|20[1-3][0-9][/-]\d{1,2}[/-]\d{1,2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20[1-3][0-9])\b",
        fragment,
        re.I,
    )
    if date_match:
        return date_match.group(0).strip()
    value = re.sub(r"^\s*[:#-]?\s*", "", fragment).strip()
    value = re.split(r"\s{2,}|\s(?:box\s+\d+|mortgage\s+interest|outstanding\s+mortgage|real\s+estate\s+tax|property\s+tax|escrow\s+balance)\b", value, maxsplit=1, flags=re.I)[0]
    value = value.strip(" ;,.")
    if not value or len(value) > 80 or re.fullmatch(r"[$\d,.\s/-]+", value):
        return ""
    return value


def _first_amount_after_any_pattern(text: str, patterns: list[str]) -> Decimal | None:
    matches = []
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            matches.append(match)
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start())
    window = text[match.end():match.end() + 180]
    amount_match = re.search(r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})", window)
    if not amount_match:
        return None
    try:
        amount = Decimal(amount_match.group(1).replace(",", ""))
    except Exception:
        return None
    return amount if amount >= 0 else None


def _first_financial_amount_after_any_pattern(text: str, patterns: list[str]) -> Decimal | None:
    matches = []
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            matches.append(match)
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start())
    window = text[match.end():match.end() + 180]
    if re.search(r"^\s*:?\s*(?:not\s+shown|not\s+included|none|n/?a|not\s+available)\b", window, re.I):
        return None
    return _first_financial_amount(window)


def _first_signed_financial_amount_after_any_pattern(text: str, patterns: list[str]) -> Decimal | None:
    matches = []
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            matches.append(match)
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start())
    window = text[match.end():match.end() + 180]
    if re.search(r"^\s*:?\s*(?:not\s+shown|not\s+included|none|n/?a|not\s+available)\b", window, re.I):
        return None
    return _first_signed_financial_amount(window)


def _first_signed_financial_amount(text: str) -> Decimal | None:
    match = re.search(
        r"(?P<paren>\(\s*\$?\s*(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})\s*\))|(?P<signed>-?\s*\$?\s*(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2}))",
        text or "",
    )
    if not match:
        return None
    raw = match.group("paren") or match.group("signed") or ""
    is_negative = raw.strip().startswith("-") or bool(match.group("paren"))
    cleaned = re.sub(r"[\s$(),-]", "", raw)
    try:
        amount = Decimal(cleaned)
    except Exception:
        return None
    return -amount if is_negative else amount


def _first_financial_amount(text: str) -> Decimal | None:
    match = re.search(r"(?:\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+\.\d{2})|\b(\d{1,3}(?:,\d{3})+(?:\.\d{2})?)\b|\b(\d+\.\d{2})\b)", text or "")
    if not match:
        return None
    raw = next(group for group in match.groups() if group)
    try:
        amount = Decimal(raw.replace(",", ""))
    except Exception:
        return None
    return amount if amount >= 0 else None


def _last_financial_amount(text: str) -> Decimal | None:
    matches = list(re.finditer(r"(?:\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+\.\d{2})|\b(\d{1,3}(?:,\d{3})+(?:\.\d{2})?)\b|\b(\d+\.\d{2})\b)", text or ""))
    if not matches:
        return None
    match = matches[-1]
    raw = next(group for group in match.groups() if group)
    try:
        amount = Decimal(raw.replace(",", ""))
    except Exception:
        return None
    return amount if amount >= 1 else None


def _last_money_amount(text: str) -> Decimal | None:
    matches = list(re.finditer(r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})", text or ""))
    if not matches:
        return None
    raw = matches[-1].group(1).replace(",", "")
    try:
        amount = Decimal(raw)
    except Exception:
        return None
    return amount if amount >= 1 else None


def _signals_for_form(form: str, text: str) -> list[str]:
    signals = []
    if re.search(r"\b\d{2}-\d{7}\b", text or ""):
        signals.append("EIN-like identifier present")
    if re.search(r"\bSSN\b|social security", text or "", re.I):
        signals.append("Taxpayer identifier language present")
    if _amounts_for_tax_form(form, text):
        signals.append("Dollar amounts detected")
    if form == "w2" and _w2_amounts(text):
        signals.append("W-2 standard boxes extracted")
    if form == "prior_year_return":
        signals.append("Can support year-over-year comparison")
    return signals[:5]


def _summarize_income(forms: list[dict]) -> list[dict]:
    out = []
    for f in forms:
        if f.get("detected_form") in {"w2", "1099", "k1", "brokerage_statement", "retirement_statement"}:
            out.append({
                "source_document": f.get("document_name"),
                "income_form": f.get("detected_form"),
                "sample_amounts": f.get("sample_amounts", [])[:4],
                "review_status": "needs CPA review",
            })
    return out


def _summarize_deductions(forms: list[dict]) -> list[dict]:
    out = []
    for f in forms:
        if f.get("detected_form") in {"mortgage_interest", "property_tax", "charitable_receipt", "business_expense"}:
            out.append({
                "source_document": f.get("document_name"),
                "category": f.get("detected_form"),
                "sample_amounts": f.get("sample_amounts", [])[:4],
                "sample_values": f.get("sample_values", [])[:4],
                "review_status": "needs CPA review",
            })
    return out


def _review_flags(forms: list[dict], input_data: dict) -> list[dict]:
    flags = []
    if not input_data.get("tax_year"):
        flags.append({"priority": "medium", "finding": "Tax year was not provided.", "recommended_action": "Confirm filing year before final review."})
    for f in forms:
        if f.get("confidence", 0) < 0.6:
            flags.append({"priority": "medium", "finding": f"Low confidence classification for {f.get('document_name')}.", "recommended_action": "Reviewer should confirm document type."})
    return flags


def _client_questions(found: set[str], extracted: dict) -> list[dict]:
    questions = []
    normalized_found = {_normalize_tax_form(item) for item in found}
    if "prior_year_return" not in normalized_found:
        questions.append({"question": "Can you provide last year's federal and state tax return?", "reason": "Needed for comparison and carryforward review."})
    if "1099" in found:
        questions.append({"question": "Were there any additional 1099 forms not uploaded yet?", "reason": "1099 income often arrives from multiple payers or institutions."})
    if not extracted.get("deduction_credit_summary"):
        questions.append({"question": "Do you expect to claim mortgage interest, property tax, charitable, education, childcare, or business deductions?", "reason": "No deduction-supporting documents were detected."})
    return questions


def _next_actions(checklist: dict, comparison: dict) -> list[dict]:
    actions = []
    for item in checklist.get("missing_items", [])[:6]:
        actions.append({"owner": "Client", "action": f"Provide {item['item']}", "priority": item["priority"]})
    for note in comparison.get("comparison_notes", [])[:4]:
        actions.append({"owner": "CPA/EA", "action": note["recommended_action"], "priority": "medium"})
    if not actions:
        actions.append({"owner": "CPA/EA", "action": "Review extracted values against source documents and approve final organizer.", "priority": "high"})
    return actions


def _render_finance_tax_advisor_packet_pdf(title: str, text: str) -> bytes:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=LETTER, rightMargin=46, leftMargin=46, topMargin=42, bottomMargin=48)
        styles = _finance_tax_pdf_styles(getSampleStyleSheet())
        story = _finance_tax_pdf_header(title, styles)
        section_titles = {
            "ADVISOR PACKET",
            "Client Overview",
            "Tax Readiness Summary",
            "Net Worth Snapshot",
            "Cash Flow Snapshot",
            "Retirement Readiness",
            "Planning Readiness Score",
            "Advisor Questions",
            "Missing Planning Items",
            "Human Review Notice",
        }
        pending_heading = None
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                story.append(Spacer(1, 8))
                continue
            if clean == "ADVISOR PACKET":
                continue
            if clean.startswith("Generated from finance/tax run:"):
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
        doc.build(story, onFirstPage=_finance_tax_pdf_footer, onLaterPages=_finance_tax_pdf_footer)
        return buffer.getvalue()
    except Exception:
        log.exception("Finance/tax advisor packet PDF render failed; using minimal PDF fallback")
        return _render_minimal_pdf(title, text)


def _finance_tax_pdf_styles(base_styles):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    base_styles.add(ParagraphStyle(name="BrandEnglish", parent=base_styles["Normal"], fontName="Helvetica", fontSize=12, leading=14, textColor=colors.HexColor("#6b7280")))
    base_styles.add(ParagraphStyle(name="BrandTag", parent=base_styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#15803d")))
    base_styles.add(ParagraphStyle(name="DocTitle", parent=base_styles["Title"], fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=colors.HexColor("#064e3b"), alignment=TA_CENTER, spaceAfter=10))
    base_styles.add(ParagraphStyle(name="HeaderTitle", parent=base_styles["Normal"], fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=colors.HexColor("#064e3b")))
    base_styles.add(ParagraphStyle(name="HeaderSubtitle", parent=base_styles["Normal"], fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#15803d")))
    base_styles.add(ParagraphStyle(name="Meta", parent=base_styles["Normal"], fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#6b7280"), alignment=TA_CENTER))
    base_styles.add(ParagraphStyle(name="SectionHeading", parent=base_styles["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=colors.HexColor("#065f46"), spaceBefore=8, spaceAfter=5, keepWithNext=True))
    base_styles.add(ParagraphStyle(name="Body", parent=base_styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=14, textColor=colors.HexColor("#1f2937"), spaceAfter=4))
    base_styles.add(ParagraphStyle(name="BulletBody", parent=base_styles["Body"], leftIndent=14, firstLineIndent=-8, bulletIndent=0, bulletFontName="Helvetica-Bold", bulletFontSize=8, bulletText="-"))
    base_styles.add(ParagraphStyle(name="NoticeBody", parent=base_styles["Body"], fontSize=9, leading=13, textColor=colors.HexColor("#7f1d1d"), backColor=colors.HexColor("#fef2f2"), borderColor=colors.HexColor("#fecaca"), borderWidth=0.6, borderPadding=7, spaceBefore=3))
    return base_styles


def _finance_tax_pdf_header(title: str, styles) -> list:
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
        brand = Table(
            [[leaf, [
                Paragraph('<font name="Helvetica-Bold" color="#4ade80" size="18"><b>ADAR</b></font> <font name="Helvetica" color="#6b7280" size="12">DocIntel</font>', styles["BrandEnglish"]),
                Paragraph("Document Intelligence | Finance Planning", styles["BrandTag"]),
            ]]],
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
        [[brand, [
            Paragraph("Advisor Packet", styles["HeaderTitle"]),
            Paragraph("Tax and financial planning readiness", styles["HeaderSubtitle"]),
        ]]],
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


def _finance_tax_pdf_footer(canvas, doc):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER

    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#d1d5db"))
    canvas.setLineWidth(0.4)
    canvas.line(doc.leftMargin, 34, LETTER[0] - doc.rightMargin, 34)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.drawString(doc.leftMargin, 22, "Generated by ADAR DocIntel Finance. Human advisor review required before client action.")
    canvas.drawRightString(LETTER[0] - doc.rightMargin, 22, f"Page {doc.page}")
    canvas.restoreState()


def _render_minimal_pdf(title: str, text: str) -> bytes:
    lines = [title, ""] + text.splitlines()
    content = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
    for line in lines[:48]:
        content.append(f"({_pdf_escape(line[:95])}) Tj")
        content.append("T*")
    content.append("ET")
    stream = "\n".join(content)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\nstream\n{stream}\nendstream",
    ]
    return _assemble_pdf(objects)


def _assemble_pdf(objects: list[str]) -> bytes:
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{idx} 0 obj\n{obj}\nendobj\n".encode("latin-1", errors="replace"))
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


def _xml_escape(value: str) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _safe_filename(value: str, suffix: str = ".txt") -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "document")).strip("-._")[:90]
    return f"{base or 'document'}{suffix}"


def _plain(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _unique_ids(values) -> list[str]:
    seen = set()
    out = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value
