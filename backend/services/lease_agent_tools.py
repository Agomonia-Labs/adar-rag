from __future__ import annotations

from typing import Any

from services.lease_intelligence import (
    LEASE_ABSTRACT_FIELDS,
    LeaseIntelligenceError,
    compare_lease_documents,
    extract_critical_dates_agent,
    extract_lease_abstract,
    generate_obligation_checklist,
    review_lease_clauses,
)


async def lease_abstraction_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    base = _first_dict(agent.get("previous_output"), outputs.get("abstract"), context.get("existing_abstract"))
    if base and _lease_abstract_complete(base):
        abstract = dict(base)
        abstract["agent_source"] = abstract.get("agent_source") or "saved_lease_abstract"
        return _with_quality(abstract, _lease_abstract_quality(abstract))

    try:
        fresh = await extract_lease_abstract(context["document_name"], context["document_context"])
        fresh["agent_source"] = "fresh_agent_extraction"
        abstract = _merge_lease_abstract(base, fresh) if base else fresh
    except LeaseIntelligenceError as exc:
        if not base:
            raise
        abstract = dict(base)
        abstract["agent_source"] = abstract.get("agent_source") or "saved_abstract_after_model_json_error"
        abstract["error"] = str(exc)
    return _with_quality(abstract, _lease_abstract_quality(abstract))


async def clause_review_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("clause_review"))
    try:
        review = await review_lease_clauses(context["document_name"], context["document_context"])
    except LeaseIntelligenceError as exc:
        review = {
            "summary": "Clause review could not be completed from model JSON output.",
            "clauses": (outputs.get("abstract") or {}).get("clause_flags") or [],
            "review_recommendations": [{
                "priority": "medium",
                "recommendation": "Review lease clauses manually because the clause review agent returned malformed JSON.",
                "source": "workflow fallback",
            }],
            "confidence": 0,
            "agent_source": "fallback_after_model_json_error",
            "error": str(exc),
        }
    if previous:
        review = _merge_list_output(previous, review, "clauses")
        review = _merge_list_output(previous, review, "review_recommendations")
    return _with_quality(review, _list_quality(review, "clauses", min_count=3))


async def critical_dates_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("critical_dates"))
    try:
        dates = await extract_critical_dates_agent(context["document_name"], context["document_context"])
    except LeaseIntelligenceError as exc:
        dates = {
            "summary": "Critical dates reused from saved lease abstract because the critical dates agent returned malformed JSON.",
            "critical_dates": (outputs.get("abstract") or {}).get("critical_dates") or [],
            "confidence": 0,
            "agent_source": "saved_abstract_fallback_after_model_json_error",
            "error": str(exc),
        }
    if previous:
        dates = _merge_list_output(previous, dates, "critical_dates")
    return _with_quality(dates, _critical_dates_quality(dates, outputs.get("abstract") or {}))


async def amendment_comparison_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("amendment_comparison"))
    try:
        comparison = await compare_lease_documents(
            context["document_name"],
            context["base_compare_context"],
            context["amendment_document_name"],
            context["amendment_context"],
        )
    except LeaseIntelligenceError as exc:
        comparison = {
            "summary": "Amendment comparison could not be completed from model JSON output.",
            "changed_terms": [],
            "added_obligations": [],
            "removed_or_superseded_terms": [],
            "critical_date_changes": [],
            "risk_flags": [{
                "risk_level": "medium",
                "finding": "Amendment comparison requires manual review because the comparison agent returned malformed JSON.",
                "source": "workflow fallback",
                "recommended_review": "Review amendment against original lease manually.",
            }],
            "confidence": 0,
            "agent_source": "fallback_after_model_json_error",
            "error": str(exc),
        }
    if previous:
        for key in ("changed_terms", "added_obligations", "removed_or_superseded_terms", "critical_date_changes", "risk_flags"):
            comparison = _merge_list_output(previous, comparison, key)
            previous = comparison
    return _with_quality(comparison, _comparison_quality(comparison))


async def obligation_checklist_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("obligation_checklist"))
    try:
        checklist = await generate_obligation_checklist(
            context["document_name"],
            outputs.get("abstract") or {},
            outputs.get("clause_review") or {},
            outputs.get("critical_dates") or {},
            outputs.get("amendment_comparison"),
        )
    except LeaseIntelligenceError as exc:
        checklist = {
            "summary": "Generated fallback obligations because the obligation checklist agent returned malformed JSON.",
            "obligations": [],
            "confidence": 0,
            "error": str(exc),
        }
    if previous:
        checklist = _merge_list_output(previous, checklist, "obligations")
    if checklist.get("obligations"):
        return _with_quality(checklist, _list_quality(checklist, "obligations", min_count=2))

    fallback = _fallback_obligations(outputs)
    checklist["summary"] = checklist.get("summary") or "Generated operational obligations from critical dates and clause review."
    checklist["obligations"] = fallback
    checklist["agent_source"] = "deterministic_fallback"
    return _with_quality(checklist, _list_quality(checklist, "obligations", min_count=1))


async def merge_lease_agent_outputs(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    abstract = outputs.get("abstract") or {}
    clause_review = outputs.get("clause_review") or {}
    critical_dates = outputs.get("critical_dates") or {}
    comparison = outputs.get("amendment_comparison")
    obligations = outputs.get("obligation_checklist") or {}

    approved_abstract = dict(abstract)
    if critical_dates.get("critical_dates"):
        approved_abstract["critical_dates"] = critical_dates["critical_dates"]
    if clause_review.get("clauses"):
        approved_abstract["clause_flags"] = clause_review["clauses"]
    approved_abstract["agent_review"] = {
        "clause_review": clause_review,
        "critical_dates": critical_dates,
        "amendment_comparison": comparison,
        "obligation_checklist": obligations,
    }

    return {
        "abstract": abstract,
        "clause_review": clause_review,
        "critical_dates": critical_dates,
        "amendment_comparison": comparison,
        "obligation_checklist": obligations,
        "approved_abstract": approved_abstract,
    }


LEASE_AGENT_TOOLS = {
    "lease.extract_abstract": lease_abstraction_tool,
    "lease.review_clauses": clause_review_tool,
    "lease.extract_critical_dates": critical_dates_tool,
    "lease.compare_amendment": amendment_comparison_tool,
    "lease.generate_obligation_checklist": obligation_checklist_tool,
    "lease.merge_outputs": merge_lease_agent_outputs,
}


ESSENTIAL_ABSTRACT_FIELDS = [
    "landlord",
    "tenant",
    "property_address",
    "lease_start_date",
    "lease_end_date",
    "base_rent",
]


def _with_quality(output: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    output = dict(output)
    output["agent_quality"] = quality
    return output


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return None


def _lease_abstract_quality(abstract: dict[str, Any]) -> dict[str, Any]:
    fields = abstract.get("fields") if isinstance(abstract.get("fields"), dict) else {}
    missing = [
        field
        for field in ESSENTIAL_ABSTRACT_FIELDS
        if _empty_field(fields.get(field))
    ]
    critical_dates = abstract.get("critical_dates") if isinstance(abstract.get("critical_dates"), list) else []
    clause_flags = abstract.get("clause_flags") if isinstance(abstract.get("clause_flags"), list) else []
    if not critical_dates:
        missing.append("critical_dates")
    if len(clause_flags) < 3:
        missing.append("clause_flags")
    present_count = sum(1 for field in LEASE_ABSTRACT_FIELDS if not _empty_field(fields.get(field)))
    return {
        "complete": not missing,
        "missing": missing,
        "present_field_count": present_count,
        "required_field_count": len(ESSENTIAL_ABSTRACT_FIELDS),
        "critical_date_count": len(critical_dates),
        "clause_flag_count": len(clause_flags),
    }


def _lease_abstract_complete(abstract: dict[str, Any]) -> bool:
    return bool(_lease_abstract_quality(abstract)["complete"])


def _critical_dates_quality(dates: dict[str, Any], abstract: dict[str, Any]) -> dict[str, Any]:
    items = dates.get("critical_dates") if isinstance(dates.get("critical_dates"), list) else []
    date_types = {item.get("date_type") for item in items if isinstance(item, dict)}
    abstract_fields = abstract.get("fields") if isinstance(abstract.get("fields"), dict) else {}
    expected = []
    if not _empty_field(abstract_fields.get("lease_start_date")):
        expected.append("lease_start")
    if not _empty_field(abstract_fields.get("lease_end_date")):
        expected.append("lease_end")
    if not _empty_field(abstract_fields.get("rent_escalation")):
        expected.append("rent_escalation")
    missing = [date_type for date_type in expected if date_type not in date_types]
    return {
        "complete": not missing and len(items) > 0,
        "missing": missing or ([] if items else ["critical_dates"]),
        "item_count": len(items),
    }


def _comparison_quality(comparison: dict[str, Any]) -> dict[str, Any]:
    count = sum(
        len(comparison.get(key) or [])
        for key in ("changed_terms", "added_obligations", "removed_or_superseded_terms", "critical_date_changes", "risk_flags")
    )
    return {
        "complete": count > 0 or bool(comparison.get("summary")),
        "missing": [] if count > 0 or comparison.get("summary") else ["comparison_findings"],
        "item_count": count,
    }


def _list_quality(output: dict[str, Any], key: str, min_count: int) -> dict[str, Any]:
    items = output.get(key) if isinstance(output.get(key), list) else []
    return {
        "complete": len(items) >= min_count,
        "missing": [] if len(items) >= min_count else [key],
        "item_count": len(items),
        "min_count": min_count,
    }


def _merge_lease_abstract(base: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged["summary"] = fresh.get("summary") or merged.get("summary") or ""
    merged["document_kind"] = fresh.get("document_kind") or merged.get("document_kind") or "unknown"
    base_fields = base.get("fields") if isinstance(base.get("fields"), dict) else {}
    fresh_fields = fresh.get("fields") if isinstance(fresh.get("fields"), dict) else {}
    merged["fields"] = {
        field: _best_field(base_fields.get(field), fresh_fields.get(field))
        for field in LEASE_ABSTRACT_FIELDS
    }
    for key in ("critical_dates", "clause_flags", "risk_flags"):
        merged[key] = _merge_unique_lists(base.get(key), fresh.get(key))
    merged["confidence"] = max(_confidence(base), _confidence(fresh))
    merged["agent_source"] = "saved_abstract_plus_fresh_agent_extraction"
    merged["generated_at"] = fresh.get("generated_at") or merged.get("generated_at")
    return merged


def _merge_list_output(base: dict[str, Any], fresh: dict[str, Any], key: str) -> dict[str, Any]:
    merged = dict(fresh)
    merged[key] = _merge_unique_lists(base.get(key), fresh.get(key))
    if not merged.get("summary"):
        merged["summary"] = base.get("summary") or ""
    merged["confidence"] = max(_confidence(base), _confidence(fresh))
    return merged


def _merge_unique_lists(first: Any, second: Any) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in (first if isinstance(first, list) else []) + (second if isinstance(second, list) else []):
        key = _item_key(item)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _best_field(base: Any, fresh: Any) -> dict[str, Any]:
    base = base if isinstance(base, dict) else {"value": base, "source": "not found", "confidence": 0}
    fresh = fresh if isinstance(fresh, dict) else {"value": fresh, "source": "not found", "confidence": 0}
    if _empty_field(base):
        return fresh
    if _empty_field(fresh):
        return base
    return fresh if _confidence(fresh) > _confidence(base) else base


def _empty_field(field: Any) -> bool:
    if not isinstance(field, dict):
        return field in (None, "")
    value = field.get("value")
    return value is None or str(value).strip() == ""


def _confidence(value: dict[str, Any]) -> float:
    try:
        return float(value.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def _item_key(item: Any) -> str:
    if isinstance(item, dict):
        for field in ("date_type", "clause_type", "title", "term", "finding", "obligation"):
            if item.get(field):
                return f"{field}:{str(item[field]).strip().lower()}"
    return str(item)


def _fallback_obligations(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    for item in (outputs.get("critical_dates") or {}).get("critical_dates", []):
        if not isinstance(item, dict):
            continue
        date_type = item.get("date_type") or "critical_date"
        obligations.append({
            "title": f"Track {date_type.replace('_', ' ')}",
            "party": item.get("responsible_party") or "unknown",
            "category": _category_for_date(date_type),
            "priority": "high" if date_type in ("lease_end", "renewal_notice", "termination_notice") else "medium",
            "due_date": item.get("date_value"),
            "trigger": item.get("description") or item.get("raw_value"),
            "source": item.get("source") or "critical dates agent",
            "status": "open",
            "notes": item.get("raw_value"),
        })
    for item in (outputs.get("clause_review") or {}).get("clauses", []):
        if not isinstance(item, dict) or item.get("risk_level") not in ("high", "medium"):
            continue
        obligations.append({
            "title": f"Review {str(item.get('clause_type') or 'lease clause').replace('_', ' ')}",
            "party": "unknown",
            "category": "review",
            "priority": item.get("risk_level") or "medium",
            "due_date": None,
            "trigger": item.get("finding"),
            "source": item.get("source") or "clause review agent",
            "status": "open",
            "notes": item.get("finding"),
        })
    return obligations


def _category_for_date(date_type: str) -> str:
    if "renewal" in date_type:
        return "renewal"
    if "rent" in date_type:
        return "rent"
    if "insurance" in date_type:
        return "insurance"
    if "cam" in date_type:
        return "cam"
    if "notice" in date_type or "termination" in date_type:
        return "notice"
    return "compliance"
