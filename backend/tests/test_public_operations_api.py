from __future__ import annotations

import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

import pytest
from fastapi import BackgroundTasks

from auth.api_oauth import ApiPrincipal
from routes import batches, public_operations_api


def principal(*scopes: str) -> ApiPrincipal:
    return ApiPrincipal(
        user={"id": "user-1", "role": "user"},
        client_id="client-1",
        scopes=frozenset(scopes),
    )


def route_scope(path: str, method: str) -> str:
    route = next(
        item
        for item in public_operations_api.router.routes
        if item.path == path and method in item.methods
    )
    dependencies = [
        dependency.call
        for dependency in route.dependant.dependencies
        if hasattr(dependency.call, "required_scope")
    ]
    assert len(dependencies) == 1
    return dependencies[0].required_scope


@pytest.mark.parametrize(
    ("path", "method", "scope"),
    [
        ("/batches", "GET", "batches:read"),
        ("/batches/embedding", "POST", "batches:write"),
        ("/batches/{job_id}/cancel", "POST", "batches:write"),
        ("/operations/catalog", "GET", "workflows:read"),
        ("/operations", "GET", "batches:read"),
        ("/operations/{operation_id}", "GET", "batches:read"),
        ("/workflows/{workflow}/validate", "POST", "workflows:read"),
        ("/events", "GET", "events:read"),
        ("/event-subscriptions", "POST", "events:write"),
        ("/webhook-deliveries", "GET", "events:read"),
        ("/webhook-deliveries/{delivery_id}/retry", "POST", "events:write"),
        ("/webhook-deliveries/process-due", "POST", "events:write"),
        ("/events/{event_id}/replay", "POST", "events:write"),
        ("/reviews", "POST", "reviews:write"),
        ("/reviews/{task_id}/decision", "POST", "reviews:approve"),
        ("/artifacts", "GET", "artifacts:read"),
        ("/artifacts", "POST", "artifacts:write"),
        ("/documents/{document_id}/versions", "GET", "versions:read"),
        ("/documents/{document_id}/versions", "POST", "versions:write"),
        ("/evaluations", "POST", "evaluations:run"),
    ],
)
def test_public_operation_routes_have_narrow_oauth_scopes(path, method, scope):
    assert route_scope(path, method) == scope


@pytest.mark.anyio
async def test_batch_embedding_delegates_to_durable_batch_handler(monkeypatch):
    captured = {}

    async def fake_start(body, background_tasks, current_user, db):
        captured.update(body=body, tasks=background_tasks, user=current_user, db=db)
        return {"batch_job_id": "batch-1", "status": "queued"}

    monkeypatch.setattr(batches, "start_batch_embedding", fake_start)
    body = batches.DocumentBatchRequest(document_ids=["document-1"])
    result = await public_operations_api.api_start_batch_embedding(
        body,
        BackgroundTasks(),
        principal("batches:write"),
        db="db",
    )

    assert result == {"batch_job_id": "batch-1", "status": "queued"}
    assert captured["body"] is body
    assert captured["user"]["id"] == "user-1"


@pytest.mark.anyio
async def test_workflow_validation_delegates_to_versioned_contract(monkeypatch):
    captured = {}

    async def fake_validate(workflow, payload, current_user):
        captured.update(workflow=workflow, payload=payload, user=current_user)
        return {"workflow": workflow, "valid": True}

    monkeypatch.setattr(public_operations_api.mcp_enterprise, "validate_workflow", fake_validate)
    result = await public_operations_api.api_validate_workflow(
        "healthcare_prior_auth",
        {"document_ids": ["doc-1"], "policy_document_ids": ["doc-2"]},
        principal("workflows:read"),
    )

    assert result["valid"] is True
    assert captured["workflow"] == "healthcare_prior_auth"
    assert captured["user"]["id"] == "user-1"
