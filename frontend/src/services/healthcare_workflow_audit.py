from __future__ import annotations

import json
from typing import Any


HEALTHCARE_PERSONAS: dict[str, dict[str, Any]] = {
    "provider_approver": {
        "label": "Provider / Doctor",
        "workspace_roles": ["owner", "editor"],
        "can_approve": True,
        "can_edit": [
            "patient_context",
            "clinical_summary",
            "soap_note",
            "patient_summary",
            "after_visit_summary",
            "medication_review",
            "risk_safety",
            "scribe_governance",
            "phi_governance",
        ],
        "scope": "Reviews and approves clinical documentation, SOAP note, assessment, plan, medication discussion, and clinical safety flags.",
    },
    "nurse_care_coordinator": {
        "label": "Nurse / Care Coordinator",
        "workspace_roles": ["owner", "editor"],
        "can_approve": False,
        "can_edit": ["patient_context", "care_gaps", "followup_checklist", "patient_summary", "after_visit_summary", "clinical_summary"],
        "scope": "Updates follow-ups, patient instructions, care gaps, referrals, pending labs, imaging, and care coordination tasks.",
    },
    "clinic_admin": {
        "label": "Clinic Admin",
        "workspace_roles": ["owner", "editor"],
        "can_approve": False,
        "can_edit": [
            "patient_context",
            "care_gaps",
            "followup_checklist",
            "after_visit_summary",
            "prior_auth_packet",
            "prior_auth_case",
            "prior_auth_readiness_overrides",
        ],
        "scope": "Coordinates scheduling, facility services, lab appointments, referrals, screening logistics, and administrative next actions.",
    },
    "prior_auth_billing": {
        "label": "Prior Auth / Billing",
        "workspace_roles": ["owner", "editor"],
        "can_approve": False,
        "can_edit": [
            "prior_auth_request",
            "policy_criteria",
            "evidence_map",
            "gap_detection",
            "prior_auth_packet",
            "code_recommendations",
            "prior_auth_readiness_overrides",
            "prior_auth_case",
        ],
        "scope": "Reviews payer criteria, evidence mapping, missing documentation, submission risks, and prior-authorization readiness.",
    },
    "compliance_reviewer": {
        "label": "Compliance / Governance",
        "workspace_roles": ["owner", "editor"],
        "can_approve": False,
        "can_edit": ["phi_governance", "scribe_governance"],
        "scope": "Reviews consent, PHI, redaction recommendations, audit traceability, retention, and minimum-necessary access.",
    },
    "patient": {
        "label": "Patient",
        "workspace_roles": ["owner", "editor", "viewer"],
        "can_approve": False,
        "can_edit": [],
        "scope": "Views approved patient-friendly summary, follow-up checklist, visit transcript when allowed, and asks chat questions.",
    },
    "caregiver": {
        "label": "Caregiver",
        "workspace_roles": ["owner", "editor", "viewer"],
        "can_approve": False,
        "can_edit": [],
        "scope": "Views approved summary, instructions, follow-ups, appointments, and medication discussion when shared by the workspace.",
    },
}


DEFAULT_PERSONA_BY_ROLE = {
    "owner": "provider_approver",
    "editor": "provider_approver",
    "viewer": "patient",
}


def persona_catalog() -> list[dict[str, Any]]:
    return [
        {"id": key, **value}
        for key, value in HEALTHCARE_PERSONAS.items()
    ]


def resolve_persona(persona: str | None, workspace_role: str | None) -> str:
    if persona in HEALTHCARE_PERSONAS:
        return str(persona)
    return DEFAULT_PERSONA_BY_ROLE.get(workspace_role or "", "provider_approver")


def persona_config(persona: str) -> dict[str, Any]:
    return HEALTHCARE_PERSONAS.get(persona) or HEALTHCARE_PERSONAS["provider_approver"]


def can_persona_approve(persona: str, workspace_role: str | None, owner_personal_doc: bool = False) -> bool:
    if owner_personal_doc and persona == "provider_approver":
        return True
    cfg = persona_config(persona)
    return bool(cfg.get("can_approve")) and (workspace_role in cfg.get("workspace_roles", []))


def unauthorized_changes(persona: str, changes: list[dict[str, Any]]) -> list[str]:
    allowed = set(persona_config(persona).get("can_edit") or [])
    if persona_config(persona).get("can_approve"):
        return []
    return sorted({
        change["field_path"]
        for change in changes
        if top_level_path(change["field_path"]) not in allowed
    })


def top_level_path(path: str) -> str:
    return (path or "").split(".", 1)[0].split("[", 1)[0]


def diff_packets(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    if _normalize(old) == _normalize(new):
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(old.keys()) | set(new.keys())):
            child = f"{path}.{key}" if path else str(key)
            changes.extend(diff_packets(old.get(key), new.get(key), child))
        return changes
    if isinstance(old, list) and isinstance(new, list):
        changes = []
        max_len = max(len(old), len(new))
        for idx in range(max_len):
            child = f"{path}[{idx}]"
            left = old[idx] if idx < len(old) else None
            right = new[idx] if idx < len(new) else None
            changes.extend(diff_packets(left, right, child))
        return changes
    return [{"field_path": path or "$", "old_value": old, "new_value": new}]


async def get_workspace_role(db, workspace_id: str | None, user_id: str, owner_id: str | None = None) -> str:
    if not workspace_id:
        return "owner" if owner_id and str(owner_id) == str(user_id) else "editor"
    row = await db.fetchrow(
        "SELECT role FROM workspace_members WHERE workspace_id=$1 AND user_id=$2",
        workspace_id,
        user_id,
    )
    return row["role"] if row else "none"


async def assigned_personas(db, workspace_id: str | None, user_id: str, vertical: str = "healthcare") -> list[str]:
    if not workspace_id:
        return []
    rows = await db.fetch(
        """
        SELECT persona
        FROM workspace_member_personas
        WHERE workspace_id=$1 AND user_id=$2 AND vertical=$3
        ORDER BY persona
        """,
        workspace_id,
        user_id,
        vertical,
    )
    return [str(row["persona"]) for row in rows]


async def record_field_changes(
    db,
    *,
    run: dict[str, Any],
    user_id: str,
    workspace_role: str,
    persona: str,
    action_type: str,
    changes: list[dict[str, Any]],
) -> None:
    for change in changes:
        await db.execute(
            """
            INSERT INTO vertical_agent_field_changes
              (run_id, document_id, workspace_id, vertical, workflow_id, user_id,
               workspace_role, persona, action_type, field_path, old_value, new_value)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb)
            """,
            str(run["id"]),
            str(run["document_id"]),
            str(run["workspace_id"]) if run.get("workspace_id") else None,
            run["vertical"],
            run["workflow_id"],
            user_id,
            workspace_role,
            persona,
            action_type,
            change["field_path"],
            json.dumps(change.get("old_value")),
            json.dumps(change.get("new_value")),
        )


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value
