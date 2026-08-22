from __future__ import annotations

from typing import Any

from .errors import DocIntelMcpError


WORKFLOW_CATALOG: dict[str, dict[str, Any]] = {
    "healthcare_clinical": {
        "vertical": "healthcare",
        "description": "Clinical document intelligence and human-reviewed healthcare packet.",
        "required_inputs": ["document_ids[0]"],
        "review": True,
        "approval": True,
        "packet_types": ["after_visit_summary"],
    },
    "healthcare_prior_auth": {
        "vertical": "healthcare",
        "description": "Prior authorization evidence, readiness, missing information, and packet workflow.",
        "required_inputs": ["document_ids[0]", "inputs.policy_document_ids"],
        "review": True,
        "approval": True,
        "packet_types": ["prior_auth", "missing_information"],
    },
    "finance_tax_readiness": {
        "vertical": "finance_tax",
        "description": "Tax submission and financial planning readiness workflow.",
        "required_inputs": ["document_ids"],
        "review": False,
        "approval": True,
        "packet_types": ["advisor"],
    },
    "talent_readiness": {
        "vertical": "talent",
        "description": "Candidate evidence, role matching, gap analysis, and recruiter review.",
        "required_inputs": ["document_ids", "inputs.job_description_id"],
        "review": True,
        "approval": True,
        "packet_types": ["candidate"],
    },
    "employee_mobility": {
        "vertical": "talent",
        "description": "Employee growth and internal-mobility evidence workflow.",
        "required_inputs": ["document_ids", "inputs.job_description_id"],
        "review": True,
        "approval": True,
        "packet_types": ["mobility"],
    },
    "lease_intelligence": {
        "vertical": "lease",
        "description": "Lease abstraction, obligations, clause review, and optional amendment comparison.",
        "required_inputs": ["document_ids[0]"],
        "review": False,
        "approval": True,
        "packet_types": [],
    },
}


def workflow_definition(workflow: str) -> dict[str, Any]:
    definition = WORKFLOW_CATALOG.get(workflow)
    if definition is None:
        raise DocIntelMcpError(
            "unsupported_workflow",
            f"Unsupported workflow '{workflow}'. Use list_vertical_workflows to discover supported workflows.",
            status_code=400,
        )
    return definition


def vertical_name(value: str) -> str:
    aliases = {
        "finance": "finance_tax",
        "finance_tax": "finance_tax",
        "healthcare": "healthcare",
        "talent": "talent",
        "hcm": "talent",
        "lease": "lease",
    }
    vertical = aliases.get(value)
    if vertical is None:
        raise DocIntelMcpError("unsupported_vertical", f"Unsupported vertical '{value}'", status_code=400)
    return vertical
