from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-for-unit-tests")

from auth.dependencies import get_current_user
from database.connection import get_db
from routes import documents


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
        def __init__(self):
            self.executed = []

        async def execute(self, sql, *args):
            self.executed.append((sql, args))
            return "OK"

        async def fetchrow(self, sql, *args):
            return None

        async def fetch(self, sql, *args):
            return []

    return FakeDb()


@pytest.fixture
def app(current_user, fake_db):
    app = FastAPI()
    app.include_router(documents.router, prefix="/api/documents")

    async def override_user():
        return current_user

    async def override_db():
        return fake_db

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db] = override_db
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def patch_external_services(monkeypatch):
    async def noop(*args, **kwargs):
        return None

    async def fake_limits(*args, **kwargs):
        return {"max_file_mb": 10, "label": "Test"}

    monkeypatch.setattr(documents, "check_document_limit", noop)
    monkeypatch.setattr(documents, "log_event", noop)
    monkeypatch.setattr(documents, "audit", noop)
    monkeypatch.setattr(documents.gcs, "upload_bytes", noop)
    monkeypatch.setattr(documents, "_chunk_document", noop)

    import services.usage as usage

    monkeypatch.setattr(usage, "get_user_limits", fake_limits)


@pytest.mark.anyio
async def test_upload_document_stores_record_and_starts_chunking(client, fake_db):
    response = await client.post(
        "/api/documents/upload",
        files={"files": ("tax-note.txt", b"sample tax document", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["uploaded"]) == 1
    assert body["uploaded"][0]["filename"] == "tax-note.txt"
    assert body["uploaded"][0]["doc_id"]

    executed_sql = " ".join(sql for sql, _args in fake_db.executed)
    assert "INSERT INTO documents" in executed_sql
    assert "status='chunking'" in executed_sql


@pytest.mark.anyio
async def test_trigger_embedding_updates_status_and_schedules_embedding(
    client,
    fake_db,
    monkeypatch,
):
    embed_task = {"called": False}

    async def fake_get_owned(doc_id, user_id, db):
        return {
            "id": doc_id,
            "user_id": user_id,
            "status": "chunked",
            "chunk_count": 3,
            "workspace_id": "workspace-1",
        }

    async def fake_usage(*args, **kwargs):
        return None

    async def fake_embed_document(*args, **kwargs):
        embed_task["called"] = True

    monkeypatch.setattr(documents, "_get_owned", fake_get_owned)
    monkeypatch.setattr(documents, "check_and_log_daily_event", fake_usage)
    monkeypatch.setattr(documents, "_embed_document", fake_embed_document)

    response = await client.post("/api/documents/doc-1/embed")

    assert response.status_code == 200
    assert response.json() == {"message": "Embedding started", "doc_id": "doc-1"}

    executed_sql = " ".join(sql for sql, _args in fake_db.executed)
    assert "status='embedding'" in executed_sql


@pytest.mark.anyio
async def test_trigger_embedding_rejects_non_chunked_document(client, monkeypatch):
    async def fake_get_owned(doc_id, user_id, db):
        return {
            "id": doc_id,
            "user_id": user_id,
            "status": "uploading",
            "chunk_count": 0,
            "workspace_id": None,
        }

    monkeypatch.setattr(documents, "_get_owned", fake_get_owned)

    response = await client.post("/api/documents/doc-raw/embed")

    assert response.status_code == 400
    assert "Document must be in 'chunked' state" in response.json()["detail"]
