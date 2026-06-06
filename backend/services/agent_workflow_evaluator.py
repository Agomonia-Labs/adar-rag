from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EVALUATOR_VERSION = "agent-workflow-eval-v1"
DEFAULT_PASS_THRESHOLD = 0.70


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    score: float
    detail: str
    weight: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": _clamp(self.score),
            "detail": self.detail,
            "weight": self.weight,
            "status": _status(self.score),
        }


def evaluate_agent_workflow(vertical: str, run: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a completed agentic workflow from persisted run payloads.

    This is deterministic and auditable. It measures run completeness, source
    coverage, confidence, domain-specific governance, and approval readiness.
    """
    vertical = (vertical or "").strip().lower()
    if vertical == "lease":
        return _evaluate_lease(run)
    if vertical == "healthcare":
        return _evaluate_healthcare(run)
    raise ValueError(f"Unsupported agent workflow vertical: {vertical}")


def _evaluate_lease(run: dict[str, Any]) -> dict[str, Any]:
    result = run.get("result") or {}
    packet = result.get("approved_abstract") or result.get("abstract") or {}
    obligations = run.get("obligations") or (result.get("obligation_checklist") or {}).get("obligations") or []
    steps = run.get("steps") or []
    fields = packet.get("fields") if isinstance(packet.get("fields"), dict) else {}
    required = ["landlord", "tenant", "property_address", "lease_start_date", "lease_end_date", "base_rent"]
    present = sum(1 for key in required if _field_value(fields.get(key)))
    leaf_items = _collect_leaf_objects(packet)
    cite_eligible = [item for item in leaf_items if "source" in item]
    cited = [item for item in cite_eligible if _has_citation(item.get("source"))]
    confidences = _confidences(packet)
    risks = packet.get("risk_flags") if isinstance(packet.get("risk_flags"), list) else []

    metrics = [
        Metric("agent_completion", "Agent completion", _ratio(_completed_steps(steps), len(steps)), f"{_completed_steps(steps)}/{len(steps)} steps completed", 1.2),
        Metric("required_fields", "Required fields", _ratio(present, len(required)), f"{present}/{len(required)} core lease fields found", 1.4),
        Metric("citation_coverage", "Citation coverage", _ratio(len(cited), len(cite_eligible)), f"{len(cited)}/{len(cite_eligible)} sourced items cite document chunks", 1.4),
        Metric("confidence", "Confidence", _avg(confidences), f"{round(_avg(confidences) * 100)}% average extracted confidence", 1.0),
        Metric("obligation_coverage", "Obligation coverage", 1.0 if obligations else 0.0, f"{len(obligations)} obligations generated", 1.0),
        Metric("risk_review", "Risk review", 1.0 if risks else 0.5, f"{len(risks)} risk flags returned" if risks else "No risk flags returned", 0.8),
        Metric("approval_readiness", "Approval readiness", 1.0 if run.get("status") in ("pending_approval", "approved") else 0.0, f"Status: {run.get('status')}", 1.2),
    ]
    return _result("lease", run, metrics, _lease_recommendations(metrics, packet, obligations))


def _evaluate_healthcare(run: dict[str, Any]) -> dict[str, Any]:
    packet = ((run.get("result") or {}).get("approved_packet") or run.get("result") or {})
    steps = run.get("steps") or []
    patient_context = packet.get("patient_context") if isinstance(packet.get("patient_context"), dict) else {}
    required = ["patient_name", "encounter_date", "provider", "encounter_type"]
    present = sum(1 for key in required if _field_value(patient_context.get(key)))
    leaf_items = _collect_leaf_objects(packet)
    cite_eligible = [item for item in leaf_items if "source" in item]
    cited = [item for item in cite_eligible if _has_citation(item.get("source"))]
    confidences = _confidences(packet)
    governance = packet.get("phi_governance") if isinstance(packet.get("phi_governance"), dict) else {}
    governance_count = len(governance.get("governance_notes") or []) + len(governance.get("redaction_recommendations") or [])
    safety_flags = ((packet.get("risk_safety") or {}).get("risk_flags") or []) if isinstance(packet.get("risk_safety"), dict) else []
    care_gaps = packet.get("care_gaps") if isinstance(packet.get("care_gaps"), dict) else {}
    followups = care_gaps.get("follow_ups") or []
    pending = care_gaps.get("pending_items") or []

    metrics = [
        Metric("agent_completion", "Agent completion", _ratio(_completed_steps(steps), len(steps)), f"{_completed_steps(steps)}/{len(steps)} steps completed", 1.2),
        Metric("patient_context", "Patient context", _ratio(present, len(required)), f"{present}/{len(required)} core context fields found", 1.2),
        Metric("citation_coverage", "Citation coverage", _ratio(len(cited), len(cite_eligible)), f"{len(cited)}/{len(cite_eligible)} sourced items cite document chunks", 1.4),
        Metric("confidence", "Confidence", _avg(confidences), f"{round(_avg(confidences) * 100)}% average extracted confidence", 1.0),
        Metric("safety_review", "Safety review", 1.0 if safety_flags else 0.5, f"{len(safety_flags)} safety flags returned" if safety_flags else "No safety flags returned", 1.1),
        Metric("followup_coverage", "Follow-up coverage", 1.0 if followups or pending else 0.5, f"{len(followups)} follow-ups, {len(pending)} pending items", 0.9),
        Metric("phi_governance", "PHI governance", 1.0 if governance_count else 0.0, f"{governance_count} governance/redaction items returned", 1.5),
        Metric("approval_readiness", "Approval readiness", 1.0 if run.get("status") in ("pending_approval", "approved") else 0.0, f"Status: {run.get('status')}", 1.2),
    ]
    return _result("healthcare", run, metrics, _healthcare_recommendations(metrics, packet))


def _result(vertical: str, run: dict[str, Any], metrics: list[Metric], recommendations: list[dict[str, str]]) -> dict[str, Any]:
    score = _weighted_average(metrics)
    passed = score >= DEFAULT_PASS_THRESHOLD and all(
        metric.score >= 0.50 for metric in metrics if metric.key in _critical_metric_keys(vertical)
    )
    return {
        "vertical": vertical,
        "run_id": run.get("run_id"),
        "document_id": run.get("document_id"),
        "evaluator_version": EVALUATOR_VERSION,
        "overall_score": score,
        "passed": passed,
        "gate_status": "pass" if passed else "needs_review",
        "metrics": [metric.as_dict() for metric in metrics],
        "recommendations": recommendations,
        "policy": {
            "pass_threshold": DEFAULT_PASS_THRESHOLD,
            "critical_metrics": sorted(_critical_metric_keys(vertical)),
            "human_approval_required": True,
        },
        "metadata": {
            "workflow_version": run.get("workflow_version"),
            "run_status": run.get("status"),
            "approved": run.get("status") == "approved",
        },
    }


def _critical_metric_keys(vertical: str) -> set[str]:
    common = {"agent_completion", "citation_coverage", "approval_readiness"}
    if vertical == "lease":
        return common | {"required_fields"}
    if vertical == "healthcare":
        return common | {"patient_context", "phi_governance"}
    return common


def _lease_recommendations(metrics: list[Metric], packet: dict[str, Any], obligations: list[dict[str, Any]]) -> list[dict[str, str]]:
    recs = _metric_recommendations(metrics)
    fields = packet.get("fields") if isinstance(packet.get("fields"), dict) else {}
    missing = [key for key in ("landlord", "tenant", "property_address", "lease_start_date", "lease_end_date", "base_rent") if not _field_value(fields.get(key))]
    if missing:
        recs.append({"severity": "high", "action": "rerun_or_manual_review", "message": f"Missing lease fields: {', '.join(missing)}."})
    if not obligations:
        recs.append({"severity": "medium", "action": "rerun_obligation_agent", "message": "No obligation checklist was produced."})
    return recs


def _healthcare_recommendations(metrics: list[Metric], packet: dict[str, Any]) -> list[dict[str, str]]:
    recs = _metric_recommendations(metrics)
    governance = packet.get("phi_governance") if isinstance(packet.get("phi_governance"), dict) else {}
    if not governance.get("governance_notes") and not governance.get("redaction_recommendations"):
        recs.append({"severity": "high", "action": "manual_phi_review", "message": "PHI governance output is missing; do not approve without manual review."})
    if not (packet.get("patient_context") or {}):
        recs.append({"severity": "medium", "action": "rerun_intake_agent", "message": "Patient/encounter context is empty."})
    return recs


def _metric_recommendations(metrics: list[Metric]) -> list[dict[str, str]]:
    recs = []
    for metric in metrics:
        if metric.score >= 0.70:
            continue
        severity = "high" if metric.score < 0.50 else "medium"
        recs.append({
            "severity": severity,
            "action": f"review_{metric.key}",
            "message": f"{metric.label} is below threshold: {round(metric.score * 100)}%. {metric.detail}",
        })
    return recs


def _collect_leaf_objects(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        if "source" in item or "confidence" in item:
            found.append(item)
        for child in item.values():
            walk(child)

    walk(value)
    return found


def _confidences(value: Any) -> list[float]:
    scores = []
    for item in _collect_leaf_objects(value):
        try:
            score = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        if score > 0:
            scores.append(_clamp(score))
    return scores


def _field_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value")
    return value


def _completed_steps(steps: list[dict[str, Any]]) -> int:
    return sum(1 for step in steps if step.get("status") == "completed")


def _has_citation(value: Any) -> bool:
    return isinstance(value, str) and "source" in value.lower() and any(ch.isdigit() for ch in value)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(numerator / denominator)


def _avg(values: list[float]) -> float:
    return _clamp(sum(values) / len(values)) if values else 0.0


def _weighted_average(metrics: list[Metric]) -> float:
    total_weight = sum(max(0.0, metric.weight) for metric in metrics)
    if total_weight <= 0:
        return 0.0
    return _clamp(sum(_clamp(metric.score) * max(0.0, metric.weight) for metric in metrics) / total_weight)


def _clamp(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _status(score: float) -> str:
    score = _clamp(score)
    if score >= 0.80:
        return "strong"
    if score >= 0.70:
        return "pass"
    if score >= 0.50:
        return "needs_review"
    return "fail"
