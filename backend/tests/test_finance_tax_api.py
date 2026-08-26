import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient

from routes import finance_tax


@pytest.fixture
def current_user():
    return {
        "id": "user-1",
        "email": "reviewer@example.com",
        "full_name": "Review User",
        "role": "editor",
    }


@pytest.fixture
def fake_db():
    class FakeDb:
        async def fetch(self, *args, **kwargs):
            return []

        async def fetchrow(self, *args, **kwargs):
            return None

        async def execute(self, *args, **kwargs):
            return "OK"

    return FakeDb()


@pytest.fixture
def app(current_user, fake_db):
    app = FastAPI()
    app.include_router(finance_tax.router, prefix="/api/finance-tax")

    async def override_user():
        return current_user

    async def override_db():
        return fake_db

    app.dependency_overrides[finance_tax.CurrentUser.__metadata__[0].dependency] = override_user
    app.dependency_overrides[finance_tax.get_db] = override_db
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def ready_docs():
    return {
        "doc-w2": {
            "id": "doc-w2",
            "original_name": "sample_w2.pdf",
            "status": "embedded",
            "workspace_id": "workspace-1",
        },
        "doc-1098": {
            "id": "doc-1098",
            "original_name": "mortgage_1098.pdf",
            "status": "chunked",
            "workspace_id": "workspace-1",
        },
    }


@pytest.fixture
def sample_run():
    now = datetime.now(timezone.utc)
    return {
        "id": "run-1",
        "workflow_id": finance_tax.TAX_MVP_WORKFLOW_ID,
        "workflow_version": "mvp1-deterministic-v1",
        "vertical": finance_tax.FINANCE_TAX_VERTICAL,
        "document_id": "doc-w2",
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "status": "running",
        "input_data": json.dumps({
            "document_ids": ["doc-w2", "doc-1098"],
            "client_name": "Avery Morgan",
            "tax_year": "2025",
            "filing_status": "Married filing jointly",
            "notes": "test run",
        }),
        "result_data": None,
        "error_message": None,
        "approval_notes": None,
        "approved_by": None,
        "approved_at": None,
        "created_at": now,
        "completed_at": None,
    }


@pytest.mark.anyio
async def test_start_tax_submission_run_creates_run_and_schedules_background(
    client,
    monkeypatch,
    ready_docs,
    sample_run,
):
    created_payload = {}
    usage_called = {"value": False}
    background_called = {"value": False}

    async def fake_get_doc(db, doc_id, user_id):
        return ready_docs[doc_id]

    async def fake_usage(*args, **kwargs):
        usage_called["value"] = True

    async def fake_create_vertical_run(db, **kwargs):
        created_payload.update(kwargs)
        return sample_run

    async def fake_vertical_run_response(db, run):
        return {
            "run_id": run["id"],
            "status": run["status"],
            "input": json.loads(run["input_data"]),
            "steps": [],
            "result": None,
        }

    async def fake_background(*args, **kwargs):
        background_called["value"] = True

    monkeypatch.setattr(finance_tax, "_get_accessible_doc", fake_get_doc)
    monkeypatch.setattr(finance_tax, "check_and_log_daily_event", fake_usage)
    monkeypatch.setattr(finance_tax, "create_vertical_run", fake_create_vertical_run)
    monkeypatch.setattr(finance_tax, "vertical_run_response", fake_vertical_run_response)
    monkeypatch.setattr(finance_tax, "_execute_tax_submission_background", fake_background)

    response = await client.post(
        "/api/finance-tax/tax-submission-runs",
        json={
            "document_ids": ["doc-w2", "doc-1098"],
            "client_name": "Avery Morgan",
            "tax_year": "2025",
            "filing_status": "Married filing jointly",
            "notes": "test run",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["status"] == "running"
    assert body["input"]["document_ids"] == ["doc-w2", "doc-1098"]

    assert usage_called["value"] is True
    assert created_payload["workflow_id"] == finance_tax.TAX_MVP_WORKFLOW_ID
    assert created_payload["vertical"] == finance_tax.FINANCE_TAX_VERTICAL
    assert created_payload["document_id"] == "doc-w2"
    assert created_payload["workspace_id"] == "workspace-1"


@pytest.mark.anyio
async def test_start_tax_submission_run_rejects_empty_document_list(client):
    response = await client.post(
        "/api/finance-tax/tax-submission-runs",
        json={"document_ids": []},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Select at least one tax document"


@pytest.mark.anyio
async def test_start_tax_submission_run_rejects_not_chunked_document(
    client,
    monkeypatch,
):
    async def fake_get_doc(db, doc_id, user_id):
        return {
            "id": doc_id,
            "original_name": "raw_upload.pdf",
            "status": "uploaded",
            "workspace_id": "workspace-1",
        }

    monkeypatch.setattr(finance_tax, "_get_accessible_doc", fake_get_doc)

    response = await client.post(
        "/api/finance-tax/tax-submission-runs",
        json={"document_ids": ["doc-raw"], "tax_year": "2025"},
    )

    assert response.status_code == 400
    assert "Documents must be chunked before running tax submission" in response.json()["detail"]


@pytest.mark.anyio
async def test_get_finance_tax_run_returns_run(client, monkeypatch, sample_run):
    sample_run = {
        **sample_run,
        "status": "pending_approval",
        "result_data": json.dumps({
            "review_packet": {
                "client": {"name": "Avery Morgan", "tax_year": "2025"},
                "tax_organizer": {"forms": []},
            }
        }),
    }

    async def fake_get_run(db, run_id, user_id):
        assert run_id == "run-1"
        assert user_id == "user-1"
        return sample_run

    async def fake_vertical_run_response(db, run):
        return {
            "run_id": run["id"],
            "status": run["status"],
            "result": json.loads(run["result_data"]),
            "steps": [],
        }

    monkeypatch.setattr(finance_tax, "get_accessible_vertical_run", fake_get_run)
    monkeypatch.setattr(finance_tax, "vertical_run_response", fake_vertical_run_response)

    response = await client.get("/api/finance-tax/agent-runs/run-1")

    assert response.status_code == 200
    assert response.json()["status"] == "pending_approval"
    assert response.json()["result"]["review_packet"]["client"]["name"] == "Avery Morgan"


@pytest.mark.anyio
async def test_approve_finance_tax_run_saves_updated_packet(
    client,
    monkeypatch,
    sample_run,
):
    approved_packet = {
        "client": {"name": "Avery Morgan", "tax_year": "2025"},
        "tab_review_saves": {
            "cashflow": {
                "label": "Cash Flow",
                "snapshot": {
                    "total_inflows": 100000,
                    "total_outflows": 40000,
                    "estimated_cash_flow": 60000,
                },
            }
        },
    }

    approve_payload = {}

    async def fake_get_run(db, run_id, user_id):
        return {
            **sample_run,
            "status": "approved",
            "result_data": json.dumps({"approved_packet": {"client": {"name": "Old"}}}),
        }

    async def fake_approve_run(db, **kwargs):
        approve_payload.update(kwargs)

    async def fake_audit(*args, **kwargs):
        return None

    async def fake_notify(*args, **kwargs):
        return None

    async def fake_vertical_run_response(db, run):
        return {
            "run_id": run["id"],
            "status": "approved",
            "result": {"approved_packet": approved_packet},
            "steps": [],
        }

    monkeypatch.setattr(finance_tax, "get_accessible_vertical_run", fake_get_run)
    monkeypatch.setattr(finance_tax, "approve_vertical_run", fake_approve_run)
    monkeypatch.setattr(finance_tax, "audit", fake_audit)
    monkeypatch.setattr(finance_tax, "send_finance_tax_packet_notification", fake_notify)
    monkeypatch.setattr(finance_tax, "vertical_run_response", fake_vertical_run_response)

    response = await client.post(
        "/api/finance-tax/agent-runs/run-1/approve",
        json={
            "approved_packet": approved_packet,
            "notes": "Cash Flow updated in DocIntel.",
        },
    )

    assert response.status_code == 200
    assert approve_payload["run_id"] == "run-1"
    assert approve_payload["user_id"] == "user-1"
    assert approve_payload["approved_packet"]["tab_review_saves"]["cashflow"]["snapshot"]["estimated_cash_flow"] == 60000
    assert approve_payload["notes"] == "Cash Flow updated in DocIntel."
