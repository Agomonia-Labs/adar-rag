from __future__ import annotations

from typing import Any

from services.healthcare_intelligence import (
    HealthcareIntelligenceError,
    classify_intake,
    extract_followups,
    extract_labs,
    flag_safety,
    merge_healthcare_outputs,
    review_medications,
    review_phi_governance,
    summarize_clinical,
)


async def intake_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("document_intake"))
    try:
        result = await classify_intake(context["document_name"], context["document_context"])
    except HealthcareIntelligenceError:
        if previous:
            result = previous
        else:
            raise
    return _with_quality(result, _intake_quality(result))


async def clinical_summary_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("clinical_summary"))
    try:
        result = await summarize_clinical(
            context["document_name"],
            context["document_context"],
            outputs.get("document_intake") or {},
        )
    except HealthcareIntelligenceError:
        if previous:
            result = previous
        else:
            raise
    if previous:
        result = _merge_lists(previous, result, ("diagnoses_or_assessments_mentioned", "plan", "patient_instructions", "human_review_notes"))
    return _with_quality(result, _summary_quality(result))


async def lab_results_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("lab_results"))
    try:
        result = await extract_labs(context["document_name"], context["document_context"])
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Lab extraction failed and needs human review.", "lab_results": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("lab_results",))
    return _with_quality(result, _list_quality(result, "lab_results", allow_empty=True))


async def medication_review_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("medication_review"))
    try:
        result = await review_medications(context["document_name"], context["document_context"])
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Medication review failed and needs human review.", "medications": [], "review_flags": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("medications", "review_flags"))
    return _with_quality(result, _list_quality(result, "medications", allow_empty=True))


async def followups_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("care_gaps"))
    try:
        result = await extract_followups(
            context["document_name"],
            context["document_context"],
            outputs.get("clinical_summary") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Follow-up extraction failed and needs human review.", "follow_ups": [], "pending_items": [], "care_gaps": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("follow_ups", "pending_items", "care_gaps"))
    return _with_quality(result, _multi_list_quality(result, ("follow_ups", "pending_items", "care_gaps"), allow_empty=True))


async def risk_safety_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("risk_safety"))
    try:
        result = await flag_safety(
            context["document_name"],
            context["document_context"],
            outputs.get("lab_results") or {},
            outputs.get("medication_review") or {},
            outputs.get("care_gaps") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Safety flagging failed and needs human review.", "risk_flags": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("risk_flags",))
    return _with_quality(result, _list_quality(result, "risk_flags", allow_empty=True))


async def phi_governance_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("phi_governance"))
    try:
        result = await review_phi_governance(context["document_name"], context["document_context"])
    except HealthcareIntelligenceError:
        result = previous or {
            "summary": "PHI/governance review failed and requires human review.",
            "phi_categories": [],
            "redaction_recommendations": [],
            "governance_notes": [{"control": "human_approval", "note": "Review document manually before sharing."}],
            "requires_human_approval": True,
            "confidence": 0,
        }
    if previous:
        result = _merge_lists(previous, result, ("phi_categories", "redaction_recommendations", "governance_notes"))
    return _with_quality(result, _multi_list_quality(result, ("governance_notes", "redaction_recommendations"), allow_empty=False))


async def merge_outputs_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    return merge_healthcare_outputs(outputs)


HEALTHCARE_AGENT_TOOLS = {
    "healthcare.classify_intake": intake_tool,
    "healthcare.summarize_clinical": clinical_summary_tool,
    "healthcare.extract_labs": lab_results_tool,
    "healthcare.review_medications": medication_review_tool,
    "healthcare.extract_followups": followups_tool,
    "healthcare.flag_safety": risk_safety_tool,
    "healthcare.review_phi_governance": phi_governance_tool,
    "healthcare.merge_outputs": merge_outputs_tool,
}


def _with_quality(output: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    output = dict(output)
    output["agent_quality"] = quality
    return output


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return None


def _intake_quality(data: dict[str, Any]) -> dict[str, Any]:
    patient_context = data.get("patient_context") if isinstance(data.get("patient_context"), dict) else {}
    missing = []
    if not data.get("document_type") or data.get("document_type") == "unknown":
        missing.append("document_type")
    if _empty_field(patient_context.get("encounter_date")):
        missing.append("encounter_date")
    return {"complete": not missing, "missing": missing, "confidence": data.get("confidence", 0)}


def _summary_quality(data: dict[str, Any]) -> dict[str, Any]:
    missing = []
    if not data.get("summary"):
        missing.append("summary")
    if not data.get("plan"):
        missing.append("plan")
    return {"complete": not missing, "missing": missing, "confidence": data.get("confidence", 0)}


def _list_quality(data: dict[str, Any], key: str, allow_empty: bool = False) -> dict[str, Any]:
    items = data.get(key) if isinstance(data.get(key), list) else []
    missing = [] if items or allow_empty else [key]
    return {"complete": not missing, "missing": missing, "item_count": len(items)}


def _multi_list_quality(data: dict[str, Any], keys: tuple[str, ...], allow_empty: bool = False) -> dict[str, Any]:
    count = 0
    for key in keys:
        items = data.get(key) if isinstance(data.get(key), list) else []
        count += len(items)
    missing = [] if count or allow_empty else [keys[0]]
    return {"complete": not missing, "missing": missing, "item_count": count}


def _empty_field(value: Any) -> bool:
    if not isinstance(value, dict):
        return value in (None, "")
    return value.get("value") in (None, "")


def _merge_lists(previous: dict[str, Any], current: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    merged = dict(current)
    for key in keys:
        current_items = current.get(key) if isinstance(current.get(key), list) else []
        previous_items = previous.get(key) if isinstance(previous.get(key), list) else []
        seen = {_stable_key(item) for item in current_items}
        combined = list(current_items)
        for item in previous_items:
            key_value = _stable_key(item)
            if key_value in seen:
                continue
            combined.append(item)
            seen.add(key_value)
        merged[key] = combined
    return merged


def _stable_key(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("test_name", "name", "task", "item", "gap", "finding", "field", "control"):
            if value.get(key):
                return f"{key}:{str(value[key]).strip().lower()}"
    return str(value).strip().lower()
