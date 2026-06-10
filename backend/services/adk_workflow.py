from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable


ToolFn = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]
StepRunner = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]]], Awaitable[dict[str, Any]]]


class WorkflowConfigError(RuntimeError):
    pass


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "agent_workflows"

DEFAULT_WORKFLOW_CONFIGS: dict[str, dict[str, Any]] = {
    "healthcare_phase1": {
        "id": "healthcare_phase1",
        "version": "healthcare-adk-v1",
        "domain": "healthcare_clinical_document_intelligence",
        "description": "Config-driven ADK-style workflow for lab reports, after-visit summaries, medication documents, care gaps, PHI review, and human approval.",
        "orchestrator": {
            "id": "healthcare_intelligence_orchestrator",
            "name": "Healthcare Intelligence Orchestrator Agent",
            "policy": {
                "execution": "sequential",
                "human_approval_required": True,
                "persist_agent_steps": True,
                "source_citations_required": True,
                "clinical_guardrails": "assistive_summary_only_no_diagnosis_no_medical_advice",
                "max_agent_attempts": 2,
            },
            "subagents": [
                {
                    "id": "document_intake",
                    "name": "Healthcare Document Intake Agent",
                    "tool": "healthcare.classify_intake",
                    "output_key": "document_intake",
                    "input_summary": "Classify clinical/admin document type and extract patient encounter context.",
                    "max_attempts": 2,
                },
                {
                    "id": "clinical_summary",
                    "name": "Clinical Summary Agent",
                    "tool": "healthcare.summarize_clinical",
                    "output_key": "clinical_summary",
                    "input_summary": "Summarize reason for visit, findings, assessment, plan, follow-ups, and citations.",
                    "max_attempts": 2,
                },
                {
                    "id": "lab_results",
                    "name": "Lab Result Agent",
                    "tool": "healthcare.extract_labs",
                    "output_key": "lab_results",
                    "input_summary": "Extract lab tests, values, units, reference ranges, abnormal flags, dates, and citations.",
                    "max_attempts": 2,
                },
                {
                    "id": "medication_review",
                    "name": "Medication Review Agent",
                    "tool": "healthcare.review_medications",
                    "output_key": "medication_review",
                    "input_summary": "Extract medications, dosage, route, frequency, start/stop dates, and review flags.",
                    "max_attempts": 2,
                },
                {
                    "id": "care_gaps",
                    "name": "Care Gap and Follow-Up Agent",
                    "tool": "healthcare.extract_followups",
                    "output_key": "care_gaps",
                    "input_summary": "Extract follow-ups, pending tests, referrals, screenings, care gaps, and patient instructions.",
                    "max_attempts": 2,
                },
                {
                    "id": "risk_safety",
                    "name": "Risk and Safety Flag Agent",
                    "tool": "healthcare.flag_safety",
                    "output_key": "risk_safety",
                    "input_summary": "Flag abnormal labs, urgent language, medication conflicts, missing follow-up, and human review needs.",
                    "max_attempts": 2,
                },
                {
                    "id": "phi_governance",
                    "name": "PHI and Governance Agent",
                    "tool": "healthcare.review_phi_governance",
                    "output_key": "phi_governance",
                    "input_summary": "Identify PHI/PII, redaction needs, audit requirements, approval gates, and governance notes.",
                    "max_attempts": 2,
                },
            ],
            "result_tool": "healthcare.merge_outputs",
        },
    },
    "healthcare_prior_auth_phase1": {
        "id": "healthcare_prior_auth_phase1",
        "version": "healthcare-prior-auth-v1",
        "domain": "healthcare_prior_authorization",
        "description": "Config-driven prior authorization workflow that maps payer criteria to patient evidence and creates a human-review packet.",
        "orchestrator": {
            "id": "prior_auth_orchestrator",
            "name": "Prior Authorization Orchestrator Agent",
            "policy": {
                "execution": "sequential",
                "human_approval_required": True,
                "persist_agent_steps": True,
                "source_citations_required": True,
                "clinical_guardrails": "administrative_assistance_only_no_medical_advice_no_coverage_guarantee",
                "max_agent_attempts": 2,
            },
            "subagents": [
                {
                    "id": "document_intake",
                    "name": "Healthcare Document Intake Agent",
                    "tool": "healthcare.classify_intake",
                    "output_key": "document_intake",
                    "input_summary": "Classify patient document and extract patient/encounter context before prior authorization review.",
                    "max_attempts": 2,
                },
                {
                    "id": "prior_auth_request",
                    "name": "Prior Authorization Request Agent",
                    "tool": "healthcare.prior_auth.extract_request",
                    "output_key": "prior_auth_request",
                    "input_summary": "Extract requested service, diagnosis, clinical rationale, and urgency from patient documents.",
                    "max_attempts": 2,
                },
                {
                    "id": "policy_criteria",
                    "name": "Payer Policy Criteria Agent",
                    "tool": "healthcare.prior_auth.extract_policy_criteria",
                    "output_key": "policy_criteria",
                    "input_summary": "Extract payer medical necessity criteria and required documentation from policy guide documents.",
                    "max_attempts": 2,
                },
                {
                    "id": "evidence_map",
                    "name": "Evidence Mapping Agent",
                    "tool": "healthcare.prior_auth.map_evidence",
                    "output_key": "evidence_map",
                    "input_summary": "Map each payer criterion to patient evidence with citations and status.",
                    "max_attempts": 2,
                },
                {
                    "id": "gap_detection",
                    "name": "Gap Detection Agent",
                    "tool": "healthcare.prior_auth.detect_gaps",
                    "output_key": "gap_detection",
                    "input_summary": "Identify missing documents, incomplete fields, and denial/submission risks.",
                    "max_attempts": 2,
                },
                {
                    "id": "prior_auth_packet",
                    "name": "Prior Authorization Packet Agent",
                    "tool": "healthcare.prior_auth.generate_packet",
                    "output_key": "prior_auth_packet",
                    "input_summary": "Create a human-review prior authorization packet with checklist, narrative, and next actions.",
                    "max_attempts": 2,
                },
            ],
            "result_tool": "healthcare.prior_auth.merge_outputs",
        },
    },
    "healthcare_transcription_phase1": {
        "id": "healthcare_transcription_phase1",
        "version": "healthcare-transcription-v1",
        "domain": "healthcare_clinical_scribe",
        "description": "Config-driven healthcare transcription workflow for doctor-patient conversations, clinical note drafting, patient summaries, follow-up checklist, and human approval.",
        "orchestrator": {
            "id": "healthcare_transcription_orchestrator",
            "name": "Healthcare Transcription Orchestrator Agent",
            "policy": {
                "execution": "sequential",
                "human_approval_required": True,
                "persist_agent_steps": True,
                "source_citations_required": False,
                "clinical_guardrails": "scribe_draft_only_requires_clinician_review_no_diagnosis_no_treatment_recommendation",
                "max_agent_attempts": 2,
            },
            "subagents": [
                {
                    "id": "conversation_transcript",
                    "name": "Clinical Conversation Transcription Agent",
                    "tool": "healthcare.transcription.transcribe_audio",
                    "output_key": "conversation_transcript",
                    "input_summary": "Transcribe doctor-patient audio and preserve the conversation as source text for review.",
                    "max_attempts": 1,
                },
                {
                    "id": "conversation_intake",
                    "name": "Clinical Conversation Intake Agent",
                    "tool": "healthcare.transcription.extract_intake",
                    "output_key": "conversation_intake",
                    "input_summary": "Extract patient, provider, encounter, language, and conversation context from the transcript.",
                    "max_attempts": 2,
                },
                {
                    "id": "soap_note",
                    "name": "SOAP Note Draft Agent",
                    "tool": "healthcare.transcription.generate_soap",
                    "output_key": "soap_note",
                    "input_summary": "Generate a clinician-review SOAP note draft from the transcript only.",
                    "max_attempts": 2,
                },
                {
                    "id": "patient_summary",
                    "name": "Patient-Friendly Summary Agent",
                    "tool": "healthcare.transcription.generate_patient_summary",
                    "output_key": "patient_summary",
                    "input_summary": "Generate a patient-friendly visit summary and questions to bring back to the care team.",
                    "max_attempts": 2,
                },
                {
                    "id": "followup_checklist",
                    "name": "Visit Follow-Up Checklist Agent",
                    "tool": "healthcare.transcription.extract_followup_checklist",
                    "output_key": "followup_checklist",
                    "input_summary": "Extract follow-up actions, orders discussed, patient tasks, and open questions.",
                    "max_attempts": 2,
                },
                {
                    "id": "scribe_governance",
                    "name": "Clinical Scribe Governance Agent",
                    "tool": "healthcare.transcription.review_governance",
                    "output_key": "scribe_governance",
                    "input_summary": "Identify PHI, consent, uncertainty, clinician review needs, and safe-use guardrails.",
                    "max_attempts": 2,
                },
            ],
            "result_tool": "healthcare.transcription.merge_outputs",
        },
    },
}


def load_workflow_config(workflow_id: str) -> dict[str, Any]:
    safe_id = workflow_id.replace("/", "").replace("\\", "")
    path = CONFIG_DIR / f"{safe_id}.json"
    if not path.exists():
        if safe_id in DEFAULT_WORKFLOW_CONFIGS:
            return DEFAULT_WORKFLOW_CONFIGS[safe_id]
        raise WorkflowConfigError(f"Workflow config not found: {workflow_id}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise WorkflowConfigError(f"Workflow config is invalid JSON: {path}") from exc


async def run_multi_agent_workflow(
    workflow_id: str,
    context: dict[str, Any],
    tools: dict[str, ToolFn],
    run_step: StepRunner,
) -> dict[str, Any]:
    config = load_workflow_config(workflow_id)
    orchestrator = config.get("orchestrator") or {}
    policy = orchestrator.get("policy") or {}
    outputs: dict[str, Any] = {}

    for agent in orchestrator.get("subagents", []):
        if not _is_enabled(agent, context, outputs):
            continue
        tool_name = agent.get("tool")
        output_key = agent.get("output_key") or agent.get("id")
        if not tool_name or not output_key:
            raise WorkflowConfigError(f"Agent is missing tool or output_key: {agent}")
        tool = tools.get(tool_name)
        if not tool:
            raise WorkflowConfigError(f"Tool is not registered: {tool_name}")
        max_attempts = _max_attempts(agent, policy)
        best_output: dict[str, Any] | None = None
        for attempt in range(1, max_attempts + 1):
            if best_output is not None:
                outputs[output_key] = best_output
            attempt_agent = {
                **agent,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "previous_output": best_output,
                "name": _attempt_name(agent, attempt, max_attempts),
                "input_summary": _attempt_summary(agent, best_output, attempt, max_attempts),
            }
            output = await run_step(
                attempt_agent,
                lambda tool=tool, attempt_agent=attempt_agent: tool(context, outputs, attempt_agent),
            )
            best_output = _merge_agent_outputs(best_output, output)
            if _is_output_complete(best_output) or attempt >= max_attempts:
                break
        outputs[output_key] = best_output or {}

    result_tool_name = orchestrator.get("result_tool")
    if result_tool_name:
        result_tool = tools.get(result_tool_name)
        if not result_tool:
            raise WorkflowConfigError(f"Result tool is not registered: {result_tool_name}")
        result = await result_tool(context, outputs, {"id": "orchestrator", "name": orchestrator.get("name")})
    else:
        result = dict(outputs)

    return {
        "workflow_id": config.get("id") or workflow_id,
        "workflow_version": config.get("version") or "v1",
        "orchestrator": orchestrator,
        "outputs": outputs,
        "result": result,
    }


def _is_enabled(agent: dict[str, Any], context: dict[str, Any], outputs: dict[str, Any]) -> bool:
    condition = agent.get("enabled_if")
    if not condition:
        return True
    if "context_key" in condition:
        return bool(context.get(condition["context_key"]))
    if "output_key" in condition:
        return bool(outputs.get(condition["output_key"]))
    return True


def _max_attempts(agent: dict[str, Any], policy: dict[str, Any]) -> int:
    value = agent.get("max_attempts", policy.get("max_agent_attempts", 1))
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def _attempt_name(agent: dict[str, Any], attempt: int, max_attempts: int) -> str:
    name = agent.get("name") or agent.get("id") or "Agent"
    if max_attempts <= 1:
        return name
    return f"{name} (attempt {attempt}/{max_attempts})"


def _attempt_summary(
    agent: dict[str, Any],
    previous_output: dict[str, Any] | None,
    attempt: int,
    max_attempts: int,
) -> str:
    summary = agent.get("input_summary") or ""
    if attempt <= 1 or max_attempts <= 1:
        return summary
    missing = ((previous_output or {}).get("agent_quality") or {}).get("missing") or []
    if missing:
        return f"{summary} Retry and fill missing fields: {', '.join(str(item) for item in missing[:8])}."
    return f"{summary} Retry and fill missing or low-confidence information from the prior attempt."


def _is_output_complete(output: dict[str, Any] | None) -> bool:
    if not isinstance(output, dict):
        return False
    quality = output.get("agent_quality")
    if isinstance(quality, dict) and "complete" in quality:
        return bool(quality.get("complete"))
    return True


def _merge_agent_outputs(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(previous, dict):
        return current if isinstance(current, dict) else {}
    if not isinstance(current, dict):
        return previous
    return _merge_values(previous, current)


def _merge_values(previous: Any, current: Any) -> Any:
    if _is_field_object(previous) and _is_field_object(current):
        if _is_empty(previous) and not _is_empty(current):
            return current
        if _is_empty(current):
            return previous
        return current if _field_confidence(current) > _field_confidence(previous) else previous
    if isinstance(previous, dict) and isinstance(current, dict):
        merged = dict(previous)
        for key, value in current.items():
            if key == "agent_quality":
                merged[key] = value
            elif key in merged:
                merged[key] = _merge_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    if isinstance(previous, list) and isinstance(current, list):
        merged = list(previous)
        seen = {_stable_key(item) for item in merged}
        for item in current:
            key = _stable_key(item)
            if key in seen:
                continue
            merged.append(item)
            seen.add(key)
        return merged
    if _is_empty(previous) and not _is_empty(current):
        return current
    if isinstance(previous, dict) and _field_confidence(current) > _field_confidence(previous):
        return current
    return previous


def _field_confidence(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    try:
        return float(value.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if value == "":
        return True
    if isinstance(value, dict):
        if set(value.keys()) <= {"value", "source", "confidence"}:
            return _is_empty(value.get("value"))
        return not value
    if isinstance(value, list):
        return not value
    return False


def _is_field_object(value: Any) -> bool:
    return isinstance(value, dict) and set(value.keys()) <= {"value", "source", "confidence"}


def _stable_key(value: Any) -> str:
    if isinstance(value, dict):
        for key_name in ("date_type", "clause_type", "title", "term", "finding", "obligation"):
            if value.get(key_name):
                return f"{key_name}:{str(value[key_name]).strip().lower()}"
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)
