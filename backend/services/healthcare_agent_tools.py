from __future__ import annotations

from typing import Any

from services.healthcare_intelligence import (
    HealthcareIntelligenceError,
    classify_intake,
    extract_followups,
    extract_labs,
    flag_safety,
    detect_prior_auth_gaps,
    extract_prior_auth_policy_criteria,
    extract_prior_auth_request,
    extract_transcript_intake,
    extract_visit_followup_checklist,
    generate_patient_friendly_summary,
    generate_soap_note,
    generate_prior_auth_packet,
    map_prior_auth_evidence,
    merge_healthcare_outputs,
    merge_prior_auth_outputs,
    merge_transcription_outputs,
    review_medications,
    review_phi_governance,
    review_scribe_governance,
    summarize_clinical,
    transcribe_clinical_audio,
)


async def intake_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("document_intake"))
    document_context = _document_context(context)
    try:
        result = await classify_intake(context["document_name"], document_context)
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
    return _with_quality(result, _patient_summary_quality(result))


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


async def prior_auth_request_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("prior_auth_request"))
    try:
        intake = outputs.get("document_intake") or {}
        result = await extract_prior_auth_request(
            context["document_name"],
            context["patient_context"],
            intake,
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Prior authorization request extraction failed.", "confidence": 0}
    return _with_quality(result, _prior_auth_request_quality(result))


async def policy_criteria_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("policy_criteria"))
    try:
        result = await extract_prior_auth_policy_criteria(context.get("policy_context") or "", outputs.get("prior_auth_request") or {})
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Policy criteria extraction failed.", "criteria": [], "required_documentation": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("policy_documents_used", "criteria", "required_documentation"))
    return _with_quality(result, _multi_list_quality(result, ("criteria", "required_documentation"), allow_empty=False))


async def evidence_mapping_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("evidence_map"))
    try:
        result = await map_prior_auth_evidence(
            context["patient_context"],
            outputs.get("prior_auth_request") or {},
            outputs.get("policy_criteria") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Evidence mapping failed.", "criteria_matches": [], "supporting_evidence": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("criteria_matches", "supporting_evidence"))
    return _with_quality(result, _multi_list_quality(result, ("criteria_matches", "supporting_evidence"), allow_empty=False))


async def gap_detection_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("gap_detection"))
    try:
        result = await detect_prior_auth_gaps(
            outputs.get("prior_auth_request") or {},
            outputs.get("policy_criteria") or {},
            outputs.get("evidence_map") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Gap detection failed.", "missing_items": [], "submission_risks": [], "ready_for_submission": False, "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("missing_items", "submission_risks"))
    return _with_quality(result, _multi_list_quality(result, ("missing_items", "submission_risks"), allow_empty=True))


async def prior_auth_packet_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("prior_auth_packet"))
    try:
        result = await generate_prior_auth_packet(
            outputs.get("prior_auth_request") or {},
            outputs.get("policy_criteria") or {},
            outputs.get("evidence_map") or {},
            outputs.get("gap_detection") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {"packet_summary": "Prior authorization packet generation failed.", "criteria_checklist": [], "next_actions": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("criteria_checklist", "next_actions"))
    return _with_quality(result, _prior_auth_packet_quality(result))


async def merge_prior_auth_outputs_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    result = merge_prior_auth_outputs(outputs)
    result["policy_documents"] = context.get("policy_documents") or []
    result["patient_document"] = {
        "document_id": context.get("document_id"),
        "document_name": context.get("document_name"),
    }
    return result


async def transcription_audio_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("conversation_transcript"))
    if previous and previous.get("transcript_text"):
        return _with_quality(previous, {"complete": True, "missing": [], "confidence": previous.get("confidence", 0)})
    audio_bytes = context.get("audio_bytes")
    if not audio_bytes:
        raise HealthcareIntelligenceError("No audio bytes were provided for clinical transcription")
    result = await transcribe_clinical_audio(
        audio_bytes,
        context.get("audio_mime_type") or "application/octet-stream",
        context.get("language") or "",
    )
    if not result.get("transcript_text"):
        raise HealthcareIntelligenceError("Clinical transcription returned no text")
    return _with_quality(result, {"complete": True, "missing": [], "confidence": result.get("confidence", 0)})


async def transcription_intake_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("conversation_intake"))
    transcript = _transcript_text(context, outputs)
    try:
        result = await extract_transcript_intake(
            transcript,
            context.get("document_name") or "Clinical conversation",
            context.get("document_context") or "",
        )
    except HealthcareIntelligenceError:
        if previous:
            result = previous
        else:
            raise
    return _with_quality(result, _intake_quality(result))


async def soap_note_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("soap_note"))
    try:
        result = await generate_soap_note(
            _transcript_text(context, outputs),
            outputs.get("conversation_intake") or {},
            context.get("document_context") or "",
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "SOAP note generation failed and requires clinician review.", "subjective": [], "objective": [], "assessment": [], "plan": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("subjective", "objective", "assessment", "plan", "medications_discussed", "orders_or_tests_discussed", "human_review_notes"))
    return _with_quality(result, _multi_list_quality(result, ("subjective", "assessment", "plan"), allow_empty=False))


async def patient_summary_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("patient_summary"))
    try:
        result = await generate_patient_friendly_summary(
            _transcript_text(context, outputs),
            outputs.get("soap_note") or {},
            context.get("language") or "",
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Patient summary generation failed and requires review.", "what_we_discussed": [], "care_team_recommendations": [], "patient_questions": [], "questions_to_ask_next": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("what_we_discussed", "care_team_recommendations", "patient_questions", "questions_to_ask_next"))
    return _with_quality(result, _summary_quality(result))


async def visit_followup_checklist_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("followup_checklist"))
    try:
        result = await extract_visit_followup_checklist(
            _transcript_text(context, outputs),
            outputs.get("soap_note") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {"summary": "Follow-up checklist generation failed and requires review.", "follow_up_actions": [], "open_questions": [], "confidence": 0}
    if previous:
        result = _merge_lists(previous, result, ("follow_up_actions", "open_questions"))
    return _with_quality(result, _multi_list_quality(result, ("follow_up_actions", "open_questions"), allow_empty=True))


async def scribe_governance_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    previous = _first_dict(agent.get("previous_output"), outputs.get("scribe_governance"))
    try:
        result = await review_scribe_governance(
            _transcript_text(context, outputs),
            outputs.get("conversation_intake") or {},
        )
    except HealthcareIntelligenceError:
        result = previous or {
            "summary": "Scribe governance review failed and requires human review.",
            "consent_status": "unknown",
            "phi_categories": [],
            "redaction_recommendations": [],
            "governance_notes": [{"control": "clinical_review", "note": "Clinician review is required before use."}],
            "requires_clinician_review": True,
            "confidence": 0,
        }
    if previous:
        result = _merge_lists(previous, result, ("phi_categories", "redaction_recommendations", "governance_notes"))
    return _with_quality(result, _multi_list_quality(result, ("governance_notes",), allow_empty=False))


async def merge_transcription_outputs_tool(context: dict[str, Any], outputs: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    return merge_transcription_outputs(outputs, context)


HEALTHCARE_AGENT_TOOLS = {
    "healthcare.classify_intake": intake_tool,
    "healthcare.summarize_clinical": clinical_summary_tool,
    "healthcare.extract_labs": lab_results_tool,
    "healthcare.review_medications": medication_review_tool,
    "healthcare.extract_followups": followups_tool,
    "healthcare.flag_safety": risk_safety_tool,
    "healthcare.review_phi_governance": phi_governance_tool,
    "healthcare.merge_outputs": merge_outputs_tool,
    "healthcare.prior_auth.extract_request": prior_auth_request_tool,
    "healthcare.prior_auth.extract_policy_criteria": policy_criteria_tool,
    "healthcare.prior_auth.map_evidence": evidence_mapping_tool,
    "healthcare.prior_auth.detect_gaps": gap_detection_tool,
    "healthcare.prior_auth.generate_packet": prior_auth_packet_tool,
    "healthcare.prior_auth.merge_outputs": merge_prior_auth_outputs_tool,
    "healthcare.transcription.transcribe_audio": transcription_audio_tool,
    "healthcare.transcription.extract_intake": transcription_intake_tool,
    "healthcare.transcription.generate_soap": soap_note_tool,
    "healthcare.transcription.generate_patient_summary": patient_summary_tool,
    "healthcare.transcription.extract_followup_checklist": visit_followup_checklist_tool,
    "healthcare.transcription.review_governance": scribe_governance_tool,
    "healthcare.transcription.merge_outputs": merge_transcription_outputs_tool,
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


def _prior_auth_request_quality(data: dict[str, Any]) -> dict[str, Any]:
    missing = []
    requested = data.get("requested_item") if isinstance(data.get("requested_item"), dict) else {}
    if _empty_field(requested.get("value")):
        missing.append("requested_item")
    if not data.get("service_category") or data.get("service_category") == "unknown":
        missing.append("service_category")
    return {"complete": not missing, "missing": missing, "confidence": data.get("confidence", 0)}


def _prior_auth_packet_quality(data: dict[str, Any]) -> dict[str, Any]:
    missing = []
    if not data.get("packet_summary"):
        missing.append("packet_summary")
    if not data.get("criteria_checklist"):
        missing.append("criteria_checklist")
    if not data.get("next_actions"):
        missing.append("next_actions")
    return {"complete": not missing, "missing": missing, "confidence": data.get("confidence", 0)}


def _patient_summary_quality(data: dict[str, Any]) -> dict[str, Any]:
    missing = []
    if not data.get("summary"):
        missing.append("summary")
    if not data.get("what_we_discussed") and not data.get("care_team_recommendations"):
        missing.append("patient_summary_items")
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
        for key in ("test_name", "name", "task", "item", "action", "question", "gap", "finding", "field", "control"):
            if value.get(key):
                return f"{key}:{str(value[key]).strip().lower()}"
    return str(value).strip().lower()


def _document_context(context: dict[str, Any]) -> str:
    return context.get("document_context") or context.get("patient_context") or ""


def _transcript_text(context: dict[str, Any], outputs: dict[str, Any]) -> str:
    transcript = outputs.get("conversation_transcript") or {}
    return transcript.get("transcript_text") or context.get("transcript_text") or ""
