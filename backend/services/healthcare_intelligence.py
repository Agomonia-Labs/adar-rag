from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from services.llm import chat_stream

log = logging.getLogger("docintel.healthcare")


class HealthcareIntelligenceError(RuntimeError):
    """Raised when a healthcare agent response cannot be converted into usable JSON."""


def build_healthcare_context(document_name: str, chunks: list[dict[str, Any]], max_chars: int = 36000) -> str:
    parts = [f"DOCUMENT: {document_name}"]
    used = 0
    for chunk in chunks:
        idx = chunk.get("chunk_index", chunk.get("index", 0))
        content = chunk.get("content") or chunk.get("text") or ""
        if not content:
            continue
        label = f"\n\n[Source {idx + 1} | chunk {idx}]\n"
        remaining = max_chars - used - len(label)
        if remaining <= 0:
            break
        clipped = content[:remaining]
        parts.append(label + clipped)
        used += len(label) + len(clipped)
    return "".join(parts)


async def classify_intake(document_name: str, context: str) -> dict[str, Any]:
    system = _clinical_system("Healthcare Document Intake Agent")
    user = f"""Classify this healthcare document and extract patient/encounter context.

Return JSON in this exact shape:
{{
  "document_type": "lab_report|after_visit_summary|medication_list|discharge_summary|referral|imaging_report|prior_authorization|payer_policy|medical_policy|administrative|unknown",
  "summary": "1-2 sentence intake summary",
  "patient_context": {{
    "patient_name": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}},
    "date_of_birth": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}},
    "encounter_date": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}},
    "provider": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}},
    "facility": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}},
    "encounter_type": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}}
  }},
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}
{context}"""
    return normalize_intake(await _complete_json(system, user))


async def summarize_clinical(document_name: str, context: str, intake: dict[str, Any]) -> dict[str, Any]:
    system = _clinical_system("Clinical Summary Agent")
    user = f"""Create an assistive clinical document summary from the source only.

Return JSON in this exact shape:
{{
  "summary": "3-5 sentence summary for human review",
  "reason_for_visit": string|null,
  "diagnoses_or_assessments_mentioned": [{{"text": "diagnosis/assessment mentioned in document", "source": "Source citation", "confidence": 0.0-1.0}}],
  "plan": [{{"item": "plan/instruction", "source": "Source citation", "confidence": 0.0-1.0}}],
  "patient_instructions": [{{"instruction": "patient instruction", "source": "Source citation", "confidence": 0.0-1.0}}],
  "human_review_notes": [{{"priority": "low|medium|high", "note": "review note", "source": "Source citation or derived from missing/ambiguous content"}}],
  "confidence": 0.0-1.0
}}

INTAKE:
{json.dumps(intake)[:6000]}

DOCUMENT: {document_name}
{context}"""
    return normalize_clinical_summary(await _complete_json(system, user))


async def extract_labs(document_name: str, context: str) -> dict[str, Any]:
    system = _clinical_system("Lab Result Agent")
    user = f"""Extract lab results from this document if present. If no labs are present, return an empty lab_results array.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence lab summary",
  "lab_results": [
    {{"test_name": "text", "result_value": string|null, "unit": string|null, "reference_range": string|null, "abnormal_flag": "high|low|critical|abnormal|normal|unknown", "collection_date": "YYYY-MM-DD or null", "source": "Source citation", "confidence": 0.0-1.0}}
  ],
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}
{context}"""
    return normalize_labs(await _complete_json(system, user))


async def review_medications(document_name: str, context: str) -> dict[str, Any]:
    system = _clinical_system("Medication Review Agent")
    user = f"""Extract medications and medication-review flags from this document.

Do not recommend medication changes. Only flag items for clinician/pharmacist review.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence medication summary",
  "medications": [
    {{"name": "text", "dose": string|null, "route": string|null, "frequency": string|null, "start_date": "YYYY-MM-DD or null", "stop_date": "YYYY-MM-DD or null", "prescriber": string|null, "source": "Source citation", "confidence": 0.0-1.0}}
  ],
  "review_flags": [
    {{"priority": "low|medium|high", "finding": "duplicate/missing dose/allergy conflict/changed medication/other review need", "source": "Source citation", "recommended_review": "human review step"}}
  ],
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}
{context}"""
    return normalize_medications(await _complete_json(system, user))


async def extract_followups(document_name: str, context: str, clinical_summary: dict[str, Any]) -> dict[str, Any]:
    system = _clinical_system("Care Gap and Follow-Up Agent")
    user = f"""Extract follow-ups, pending tests, referrals, screenings, and care gaps mentioned in this document.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence follow-up summary",
  "follow_ups": [
    {{"task": "follow-up action", "due_date": "YYYY-MM-DD or null", "responsible_party": "patient|provider|care_team|unknown", "source": "Source citation", "priority": "low|medium|high"}}
  ],
  "pending_items": [
    {{"item": "pending lab/test/referral/documentation", "source": "Source citation", "priority": "low|medium|high"}}
  ],
  "care_gaps": [
    {{"gap": "possible care gap explicitly supported by document", "source": "Source citation", "recommended_review": "human review step"}}
  ],
  "confidence": 0.0-1.0
}}

CLINICAL_SUMMARY:
{json.dumps(clinical_summary)[:6000]}

DOCUMENT: {document_name}
{context}"""
    return normalize_followups(await _complete_json(system, user))


async def flag_safety(
    document_name: str,
    context: str,
    lab_results: dict[str, Any],
    medication_review: dict[str, Any],
    care_gaps: dict[str, Any],
) -> dict[str, Any]:
    system = _clinical_system("Risk and Safety Flag Agent")
    user = f"""Flag safety and operational review items. Do not diagnose. Do not provide medical advice.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence risk/safety summary",
  "risk_flags": [
    {{"risk_level": "low|medium|high", "category": "lab|medication|follow_up|urgent_language|missing_information|other", "finding": "brief finding", "source": "Source citation or derived from agent outputs", "recommended_review": "human review step"}}
  ],
  "confidence": 0.0-1.0
}}

LAB_RESULTS:
{json.dumps(lab_results)[:7000]}

MEDICATION_REVIEW:
{json.dumps(medication_review)[:7000]}

CARE_GAPS:
{json.dumps(care_gaps)[:7000]}

DOCUMENT: {document_name}
{context}"""
    return normalize_risk_flags(await _complete_json(system, user))


async def review_phi_governance(document_name: str, context: str) -> dict[str, Any]:
    system = _clinical_system("PHI and Governance Agent")
    user = f"""Review PHI/PII, redaction needs, audit needs, access sensitivity, and approval gates.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence governance summary",
  "phi_categories": ["name|dob|mrn|phone|email|address|insurance|provider|facility|other"],
  "redaction_recommendations": [
    {{"field": "text", "recommendation": "redact|mask|restrict|keep_for_clinical_context", "reason": "short reason", "source": "Source citation or policy-derived"}}
  ],
  "governance_notes": [
    {{"control": "audit|access_control|human_approval|retention|minimum_necessary|other", "note": "governance note"}}
  ],
  "requires_human_approval": true,
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}
{context}"""
    return normalize_phi_governance(await _complete_json(system, user))


def merge_healthcare_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    packet = {
        "document_intake": outputs.get("document_intake") or {},
        "patient_context": (outputs.get("document_intake") or {}).get("patient_context") or {},
        "clinical_summary": outputs.get("clinical_summary") or {},
        "lab_results": outputs.get("lab_results") or {},
        "medication_review": outputs.get("medication_review") or {},
        "care_gaps": outputs.get("care_gaps") or {},
        "risk_safety": outputs.get("risk_safety") or {},
        "phi_governance": outputs.get("phi_governance") or {},
        "approved_for": "human_review_required",
        "guardrail": "Assistive clinical/admin document intelligence only. Not diagnosis, treatment, or medical advice.",
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    return {
        **packet,
        "approved_packet": packet,
    }


async def extract_prior_auth_request(
    document_name: str,
    patient_context: str,
    intake: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system = _clinical_system("Prior Authorization Request Agent")
    user = f"""Extract the requested prior authorization service/medication and clinical request context.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence request summary",
  "requested_item": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}},
  "service_category": "imaging|pharmacy|therapy|procedure|referral|device|unknown",
  "diagnoses": [{{"code": string|null, "description": string|null, "source": "Source citation", "confidence": 0.0-1.0}}],
  "clinical_rationale": [{{"finding": "supporting rationale", "source": "Source citation", "confidence": 0.0-1.0}}],
  "urgency": {{"value": "routine|urgent|unknown", "source": "Source citation or not found", "confidence": 0.0-1.0}},
  "confidence": 0.0-1.0
}}

PATIENT DOCUMENT CONTEXT:
INTAKE:
{json.dumps(intake or {})[:6000]}

DOCUMENT SOURCES:
{patient_context}"""
    data = await _complete_json(system, user)
    return {
        "summary": data.get("summary") or "",
        "requested_item": _field(data.get("requested_item")),
        "service_category": data.get("service_category") or "unknown",
        "diagnoses": _list(data.get("diagnoses")),
        "clinical_rationale": _list(data.get("clinical_rationale")),
        "urgency": _field(data.get("urgency")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def extract_prior_auth_policy_criteria(policy_context: str, request: dict[str, Any]) -> dict[str, Any]:
    system = _clinical_system("Payer Policy Criteria Agent")
    user = f"""Extract prior authorization policy criteria from payer policy/reference documents.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence policy summary",
  "policy_documents_used": [{{"title": "document or policy title", "source": "Source citation"}}],
  "criteria": [
    {{"criterion_id": "short stable id", "criterion": "approval criterion", "required": true, "source": "Source citation", "category": "diagnosis|lab|treatment_history|imaging|documentation|benefit|other"}}
  ],
  "required_documentation": [
    {{"item": "required document or field", "source": "Source citation", "priority": "low|medium|high"}}
  ],
  "confidence": 0.0-1.0
}}

REQUEST:
{json.dumps(request)[:7000]}

PAYER POLICY CONTEXT:
{policy_context or "No payer policy context provided."}"""
    data = await _complete_json(system, user)
    return {
        "summary": data.get("summary") or "",
        "policy_documents_used": _list(data.get("policy_documents_used")),
        "criteria": _list(data.get("criteria")),
        "required_documentation": _list(data.get("required_documentation")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def map_prior_auth_evidence(
    patient_context: str,
    request: dict[str, Any],
    policy_criteria: dict[str, Any],
) -> dict[str, Any]:
    system = _clinical_system("Prior Authorization Evidence Mapping Agent")
    user = f"""Map payer criteria to patient evidence. Use only cited patient document context.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence evidence summary",
  "criteria_matches": [
    {{"criterion_id": "id from policy criteria", "criterion": "criterion text", "status": "met|not_met|missing_evidence|needs_clarification", "patient_evidence": "evidence or null", "policy_source": "Source citation", "patient_source": "Source citation or not found", "confidence": 0.0-1.0}}
  ],
  "supporting_evidence": [
    {{"evidence": "important patient fact", "source": "Source citation", "supports": "criterion id or request field", "confidence": 0.0-1.0}}
  ],
  "confidence": 0.0-1.0
}}

REQUEST:
{json.dumps(request)[:7000]}

POLICY CRITERIA:
{json.dumps(policy_criteria)[:10000]}

PATIENT DOCUMENT CONTEXT:
{patient_context}"""
    data = await _complete_json(system, user)
    return {
        "summary": data.get("summary") or "",
        "criteria_matches": _list(data.get("criteria_matches")),
        "supporting_evidence": _list(data.get("supporting_evidence")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def detect_prior_auth_gaps(
    request: dict[str, Any],
    policy_criteria: dict[str, Any],
    evidence_map: dict[str, Any],
) -> dict[str, Any]:
    system = _clinical_system("Prior Authorization Gap Detection Agent")
    user = f"""Detect missing documentation and submission risks before prior authorization submission.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence gap summary",
  "missing_items": [
    {{"item": "missing document/field/evidence", "reason": "why needed", "priority": "low|medium|high", "source": "policy source or derived from missing evidence"}}
  ],
  "submission_risks": [
    {{"risk": "submission or denial risk", "priority": "low|medium|high", "recommended_action": "human action"}}
  ],
  "ready_for_submission": false,
  "confidence": 0.0-1.0
}}

REQUEST:
{json.dumps(request)[:7000]}

POLICY CRITERIA:
{json.dumps(policy_criteria)[:10000]}

EVIDENCE MAP:
{json.dumps(evidence_map)[:12000]}"""
    data = await _complete_json(system, user)
    return {
        "summary": data.get("summary") or "",
        "missing_items": _list(data.get("missing_items")),
        "submission_risks": _list(data.get("submission_risks")),
        "ready_for_submission": bool(data.get("ready_for_submission", False)),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def generate_prior_auth_packet(
    request: dict[str, Any],
    policy_criteria: dict[str, Any],
    evidence_map: dict[str, Any],
    gaps: dict[str, Any],
) -> dict[str, Any]:
    system = _clinical_system("Prior Authorization Packet Agent")
    user = f"""Create a human-review prior authorization packet. Do not claim submission is approved.

Return JSON in this exact shape:
{{
  "packet_summary": "concise packet summary",
  "medical_necessity_narrative": "payer-facing narrative using only supported evidence",
  "criteria_checklist": [
    {{"criterion": "criterion", "status": "met|not_met|missing_evidence|needs_clarification", "evidence": "evidence or missing item", "source": "policy/patient source"}}
  ],
  "recommended_decision": "ready_for_human_review|needs_more_information|not_supported_by_available_docs",
  "next_actions": [
    {{"action": "human action", "owner": "provider|patient|care_team|billing|unknown", "priority": "low|medium|high"}}
  ],
  "confidence": 0.0-1.0
}}

REQUEST:
{json.dumps(request)[:7000]}

POLICY CRITERIA:
{json.dumps(policy_criteria)[:10000]}

EVIDENCE MAP:
{json.dumps(evidence_map)[:12000]}

GAPS:
{json.dumps(gaps)[:9000]}"""
    data = await _complete_json(system, user)
    return {
        "packet_summary": data.get("packet_summary") or "",
        "medical_necessity_narrative": data.get("medical_necessity_narrative") or "",
        "criteria_checklist": _list(data.get("criteria_checklist")),
        "recommended_decision": data.get("recommended_decision") or "needs_more_information",
        "next_actions": _list(data.get("next_actions")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def merge_prior_auth_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    packet = {
        "document_intake": outputs.get("document_intake") or {},
        "patient_context": (outputs.get("document_intake") or {}).get("patient_context") or {},
        "prior_auth_request": outputs.get("prior_auth_request") or {},
        "policy_criteria": outputs.get("policy_criteria") or {},
        "evidence_map": outputs.get("evidence_map") or {},
        "gap_detection": outputs.get("gap_detection") or {},
        "prior_auth_packet": outputs.get("prior_auth_packet") or {},
        "approved_for": "human_review_required",
        "guardrail": "Prior authorization assistance only. Not medical advice, not a coverage guarantee, and not a payer submission without human approval.",
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    return {**packet, "approved_packet": packet}


def normalize_intake(data: dict[str, Any]) -> dict[str, Any]:
    context = data.get("patient_context") if isinstance(data.get("patient_context"), dict) else {}
    return {
        "document_type": data.get("document_type") or "unknown",
        "summary": data.get("summary") or "",
        "patient_context": {key: _field(context.get(key)) for key in (
            "patient_name", "date_of_birth", "encounter_date", "provider", "facility", "encounter_type"
        )},
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_clinical_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "reason_for_visit": data.get("reason_for_visit"),
        "diagnoses_or_assessments_mentioned": _list(data.get("diagnoses_or_assessments_mentioned")),
        "plan": _list(data.get("plan")),
        "patient_instructions": _list(data.get("patient_instructions")),
        "human_review_notes": _list(data.get("human_review_notes")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_labs(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "lab_results": _list(data.get("lab_results")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_medications(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "medications": _list(data.get("medications")),
        "review_flags": _list(data.get("review_flags")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_followups(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "follow_ups": _list(data.get("follow_ups")),
        "pending_items": _list(data.get("pending_items")),
        "care_gaps": _list(data.get("care_gaps")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_risk_flags(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "risk_flags": _list(data.get("risk_flags")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_phi_governance(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "phi_categories": _list(data.get("phi_categories")),
        "redaction_recommendations": _list(data.get("redaction_recommendations")),
        "governance_notes": _list(data.get("governance_notes")),
        "requires_human_approval": bool(data.get("requires_human_approval", True)),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def _clinical_system(agent_name: str) -> str:
    return f"""You are DocIntel {agent_name}.
Use only the provided source context and cite findings using [Source N] labels.
This is clinical/admin workflow assistance only.
Do not diagnose, prescribe, recommend treatment, or replace clinician judgment.
Flag uncertainty and route sensitive or clinical decisions to human review.
Return only valid JSON."""


async def _complete_text(system_prompt: str, user_prompt: str) -> str:
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    await chat_stream(
        messages=[{"role": "user", "content": user_prompt}],
        system_prompt=system_prompt,
        on_token=on_token,
    )
    return "".join(chunks)


async def _complete_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    raw = await _complete_text(system_prompt, user_prompt)
    try:
        return _json_from_text(raw)
    except json.JSONDecodeError as exc:
        log.warning("Healthcare model returned malformed JSON; attempting repair: %s", exc)
        repaired = await _complete_text(
            "You repair malformed JSON. Return only valid JSON. Do not explain.",
            "Repair this malformed JSON into one valid JSON object. Preserve recoverable keys and values. "
            "If a value is incomplete, close it as a short string. Return only JSON:\n\n"
            + raw[:12000],
        )
        try:
            return _json_from_text(repaired)
        except json.JSONDecodeError as repair_exc:
            preview = re.sub(r"\s+", " ", raw[:500])
            raise HealthcareIntelligenceError(
                f"Healthcare model returned invalid JSON after repair attempt: {repair_exc}. Preview: {preview}"
            ) from repair_exc


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        obj = _extract_json_object(cleaned)
        if not obj:
            raise first_error
        return json.loads(obj)


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    return None


def _field(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {"value": value, "source": "not found", "confidence": 0}
    return {
        "value": value.get("value"),
        "source": value.get("source") or "not found",
        "confidence": _float(value.get("confidence")),
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
