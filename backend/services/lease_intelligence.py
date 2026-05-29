from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from services.llm import chat_stream

log = logging.getLogger("docintel.lease")


LEASE_ABSTRACT_FIELDS = [
    "landlord",
    "tenant",
    "property_address",
    "premises",
    "lease_start_date",
    "lease_end_date",
    "base_rent",
    "rent_escalation",
    "renewal_options",
    "termination_rights",
    "notice_periods",
    "security_deposit",
    "cam_obligations",
    "maintenance_obligations",
    "insurance_requirements",
    "assignment_subletting",
    "use_restrictions",
    "governing_law",
]

LEASE_CLAUSE_TYPES = [
    "renewal",
    "termination",
    "default",
    "indemnity",
    "assignment_subletting",
    "rent_escalation",
    "exclusivity",
    "co_tenancy",
    "use_restriction",
    "maintenance_repair",
    "insurance",
    "force_majeure",
]


class LeaseIntelligenceError(RuntimeError):
    """Raised when the lease model response cannot be converted into usable JSON."""


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
        log.warning("Lease model returned malformed JSON; attempting repair: %s", exc)
        repaired = await _complete_text(
            "You repair malformed JSON. Return only valid JSON. Do not explain.",
            "Repair this malformed JSON into one valid JSON object. "
            "Preserve all keys and values that are recoverable. "
            "If a value is incomplete, close it as a short string. "
            "Return only JSON:\n\n"
            + raw[:12000],
        )
        try:
            return _json_from_text(repaired)
        except json.JSONDecodeError as repair_exc:
            preview = re.sub(r"\s+", " ", raw[:500])
            raise LeaseIntelligenceError(
                f"Lease model returned invalid JSON after repair attempt: {repair_exc}. Preview: {preview}"
            ) from repair_exc


def build_lease_context(document_name: str, chunks: list[dict[str, Any]], max_chars: int = 36000) -> str:
    """Build citation-ready lease context from chunk rows or GCS metadata."""
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


async def extract_lease_abstract(document_name: str, context: str) -> dict[str, Any]:
    system = """You are DocIntel Lease Intelligence, a careful lease abstraction analyst.
Extract only facts supported by the provided lease context.
Never provide legal advice.
Every extracted field, date, clause flag, and risk must include source citations using the provided [Source N] labels.
If a field is not found, set value to null, confidence to 0, and source to "not found".
Respond only with valid JSON."""

    user = f"""Extract a structured lease abstract from this lease context.

Return JSON in this exact shape:
{{
  "document_kind": "lease|lease_amendment|lease_extension|unknown",
  "summary": "2-4 sentence business summary",
  "fields": {{
    "<field_name>": {{"value": string|null, "source": "Source citation or not found", "confidence": 0.0-1.0}}
  }},
  "critical_dates": [
    {{"date_type": "lease_start|lease_end|renewal_notice|rent_escalation|termination_notice|insurance_due|cam_reconciliation|other", "date_value": "YYYY-MM-DD or null", "raw_value": "exact text", "description": "what this date means", "responsible_party": "landlord|tenant|both|unknown", "source": "Source citation", "confidence": 0.0-1.0}}
  ],
  "clause_flags": [
    {{"clause_type": "<one of: {', '.join(LEASE_CLAUSE_TYPES)}>", "status": "present|missing|ambiguous", "risk_level": "low|medium|high|unknown", "finding": "brief finding", "source": "Source citation or not found", "confidence": 0.0-1.0}}
  ],
  "risk_flags": [
    {{"risk_level": "low|medium|high", "finding": "brief risk", "source": "Source citation", "recommended_review": "human review step"}}
  ],
  "confidence": 0.0-1.0
}}

Use these fields exactly for fields:
{', '.join(LEASE_ABSTRACT_FIELDS)}

LEASE CONTEXT:
{context}"""
    result = await _complete_json(system, user)
    return normalize_lease_abstract(result)


async def compare_lease_documents(
    base_name: str,
    base_context: str,
    amendment_name: str,
    amendment_context: str,
) -> dict[str, Any]:
    system = """You are DocIntel Lease Intelligence, comparing lease documents.
Compare only from the provided contexts.
Every change must cite the base and/or amendment source labels.
Never provide legal advice.
Respond only with valid JSON."""
    user = f"""Compare a base lease against an amendment or second lease document.

Keep the response compact:
- changed_terms: maximum 8 items
- added_obligations: maximum 6 items
- removed_or_superseded_terms: maximum 6 items
- critical_date_changes: maximum 6 items
- risk_flags: maximum 6 items
- Keep each text value under 220 characters.

Return JSON in this exact shape:
{{
  "summary": "2-4 sentence comparison summary",
  "changed_terms": [
    {{"term": "rent|term|renewal|termination|maintenance|insurance|assignment|cam|other", "before": string|null, "after": string|null, "impact": "business impact", "base_source": "citation or not found", "amendment_source": "citation or not found", "risk_level": "low|medium|high|unknown"}}
  ],
  "added_obligations": [
    {{"party": "landlord|tenant|both|unknown", "obligation": "text", "source": "citation", "risk_level": "low|medium|high|unknown"}}
  ],
  "removed_or_superseded_terms": [
    {{"term": "text", "base_source": "citation", "amendment_source": "citation or not found", "impact": "text"}}
  ],
  "critical_date_changes": [
    {{"date_type": "text", "before": string|null, "after": string|null, "source": "citation", "impact": "text"}}
  ],
  "risk_flags": [
    {{"risk_level": "low|medium|high", "finding": "brief risk", "source": "citation", "recommended_review": "human review step"}}
  ],
  "confidence": 0.0-1.0
}}

BASE DOCUMENT: {base_name}
{base_context}

AMENDMENT / COMPARISON DOCUMENT: {amendment_name}
{amendment_context}"""
    return normalize_lease_comparison(await _complete_json(system, user))


async def review_lease_clauses(document_name: str, context: str) -> dict[str, Any]:
    system = """You are DocIntel Clause Review Agent.
Review lease clauses from the provided context only.
Focus on business and operational review points, not legal advice.
Every item must include a source citation using [Source N].
Respond only with valid JSON."""
    user = f"""Review important lease clauses for this document.

Keep the response compact: maximum 12 clauses, each finding under 220 characters.

Return JSON in this exact shape:
{{
  "summary": "2-3 sentence clause review summary",
  "clauses": [
    {{"clause_type": "renewal|termination|default|indemnity|assignment_subletting|rent_escalation|exclusivity|co_tenancy|use_restriction|maintenance_repair|insurance|force_majeure|other", "status": "present|missing|ambiguous", "risk_level": "low|medium|high|unknown", "finding": "brief finding", "source": "Source citation or not found", "confidence": 0.0-1.0}}
  ],
  "review_recommendations": [
    {{"priority": "low|medium|high", "recommendation": "human review action", "source": "Source citation or not found"}}
  ],
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}
{context}"""
    return normalize_clause_review(await _complete_json(system, user))


async def extract_critical_dates_agent(document_name: str, context: str) -> dict[str, Any]:
    system = """You are DocIntel Critical Dates Agent.
Extract lease dates that drive business action.
Every date must include a source citation using [Source N].
Return at most 8 critical dates. Keep each text field under 160 characters.
Respond only with valid JSON."""
    user = f"""Extract critical dates and reminders from this lease.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence date summary",
  "critical_dates": [
    {{"date_type": "lease_start|lease_end|renewal_notice|rent_escalation|termination_notice|insurance_due|cam_reconciliation|other", "date_value": "YYYY-MM-DD or null", "raw_value": "exact text", "description": "what this date means", "responsible_party": "landlord|tenant|both|unknown", "source": "Source citation", "confidence": 0.0-1.0}}
  ],
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}
{context}"""
    data = await _complete_json(system, user)
    return {
        "summary": data.get("summary") or "",
        "critical_dates": _list(data.get("critical_dates")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def generate_obligation_checklist(
    document_name: str,
    abstract_data: dict[str, Any],
    clause_review: dict[str, Any],
    critical_dates: dict[str, Any],
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system = """You are DocIntel Obligation Checklist Agent.
Generate actionable lease obligations for property, finance, and legal operations.
Use only the provided agent outputs.
Respond only with valid JSON."""
    user = f"""Create an obligation checklist from these lease intelligence outputs.

Return JSON in this exact shape:
{{
  "summary": "1-2 sentence checklist summary",
  "obligations": [
    {{"title": "short task name", "party": "landlord|tenant|both|unknown", "category": "rent|notice|renewal|maintenance|insurance|cam|compliance|review|other", "priority": "low|medium|high", "due_date": "YYYY-MM-DD or null", "trigger": "what causes this task", "source": "Source citation or derived from agent output", "status": "open", "notes": "brief operational note"}}
  ],
  "confidence": 0.0-1.0
}}

DOCUMENT: {document_name}

LEASE_ABSTRACT:
{json.dumps(abstract_data)[:10000]}

CLAUSE_REVIEW:
{json.dumps(clause_review)[:7000]}

CRITICAL_DATES:
{json.dumps(critical_dates)[:7000]}

AMENDMENT_COMPARISON:
{json.dumps(comparison or {})[:7000]}"""
    return normalize_obligations(await _complete_json(system, user))


def normalize_lease_abstract(data: dict[str, Any]) -> dict[str, Any]:
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    normalized_fields = {}
    for field in LEASE_ABSTRACT_FIELDS:
        value = fields.get(field, {})
        if not isinstance(value, dict):
            value = {"value": value, "source": "not found", "confidence": 0}
        normalized_fields[field] = {
            "value": value.get("value"),
            "source": value.get("source") or "not found",
            "confidence": _float(value.get("confidence")),
        }
    return {
        "document_kind": data.get("document_kind") or "unknown",
        "summary": data.get("summary") or "",
        "fields": normalized_fields,
        "critical_dates": _list(data.get("critical_dates")),
        "clause_flags": _list(data.get("clause_flags")),
        "risk_flags": _list(data.get("risk_flags")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_lease_comparison(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "changed_terms": _list(data.get("changed_terms")),
        "added_obligations": _list(data.get("added_obligations")),
        "removed_or_superseded_terms": _list(data.get("removed_or_superseded_terms")),
        "critical_date_changes": _list(data.get("critical_date_changes")),
        "risk_flags": _list(data.get("risk_flags")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_clause_review(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": data.get("summary") or "",
        "clauses": _list(data.get("clauses")),
        "review_recommendations": _list(data.get("review_recommendations")),
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def normalize_obligations(data: dict[str, Any]) -> dict[str, Any]:
    obligations = []
    for item in _list(data.get("obligations")):
        if not isinstance(item, dict):
            continue
        obligations.append({
            "title": item.get("title") or "Review lease obligation",
            "party": item.get("party") or "unknown",
            "category": item.get("category") or "other",
            "priority": item.get("priority") or "medium",
            "due_date": item.get("due_date"),
            "trigger": item.get("trigger"),
            "source": item.get("source") or "derived from agent output",
            "status": item.get("status") or "open",
            "notes": item.get("notes"),
        })
    return {
        "summary": data.get("summary") or "",
        "obligations": obligations,
        "confidence": _float(data.get("confidence")),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
