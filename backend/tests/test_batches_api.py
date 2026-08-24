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
from routes import batches


class FakeDb:
    def __init__(self):
        self.executed = []
        self.document_ids = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql, *args):
        if "FROM documents d WHERE d.id=ANY" in sql:
            return [{"id": value, "chunk_count": 2} for value in self.document_ids if value in set(args[0])]
        if "batch_job_items" in sql:
            return []
        return []

    async def fetchrow(self, sql, *args):
        if "FROM batch_jobs" in sql:
            return {
                "id": args[0], "user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "workspace_id": None, "operation": "embedding", "status": "running",
                "configuration": {}, "result": {}, "progress_pct": 50,
            }
        return None


@pytest.fixture
def fake_db(): return FakeDb()


@pytest.fixture
def app(fake_db, monkeypatch):
    app = FastAPI()
    app.include_router(batches.router, prefix="/api/batches")

    async def user(): return {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "role": "editor"}
    async def db(): return fake_db
    async def noop(*_args, **_kwargs): return None
    async def limits(*_args, **_kwargs): return {"max_file_mb": 100}
    async def signed_url(*_args, **_kwargs): return "https://storage.example/upload"

    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(batches, "execute_job", noop)
    monkeypatch.setattr(batches, "check_and_log_daily_event", noop)
    monkeypatch.setattr(batches, "check_document_limit", noop)
    monkeypatch.setattr(batches, "get_user_limits", limits)
    monkeypatch.setattr(batches.gcs, "get_signed_upload_url", signed_url)
    return app


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.mark.anyio
async def test_starts_durable_embedding_batch(client, fake_db):
    response = await client.post("/api/batches/embedding", json={
        "document_ids": fake_db.document_ids, "concurrency": 2, "force": False,
    })
    assert response.status_code == 200
    assert response.json()["total_items"] == 2
    sql = " ".join(statement for statement, _ in fake_db.executed)
    assert "INSERT INTO batch_jobs" in sql
    assert sql.count("INSERT INTO batch_job_items") == 2


@pytest.mark.anyio
async def test_batch_upload_stages_document_id_without_violating_document_fk(client, fake_db):
    response = await client.post("/api/batches/uploads", json={
        "files": [{"filename": "return.pdf", "content_type": "application/pdf", "file_size": 1024}],
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "awaiting_upload"
    assert payload["files"][0]["upload_url"] == "https://storage.example/upload"
    item_statement, item_args = next(
        entry for entry in fake_db.executed if "INSERT INTO batch_job_items" in entry[0]
    )
    assert "job_id,item_key" in item_statement
    assert "document_id" not in item_statement.split("VALUES", 1)[0]
    staged_data = __import__("json").loads(item_args[2])
    assert staged_data["document_id"] == payload["files"][0]["document_id"]


@pytest.mark.anyio
async def test_rejects_inaccessible_document_in_batch(client, fake_db):
    response = await client.post("/api/batches/classification", json={
        "document_ids": [*fake_db.document_ids, "33333333-3333-3333-3333-333333333333"],
    })
    assert response.status_code == 403
    assert "inaccessible" in response.json()["detail"]


@pytest.mark.anyio
async def test_returns_batch_status_with_items(client):
    response = await client.get("/api/batches/44444444-4444-4444-4444-444444444444")
    assert response.status_code == 200
    assert response.json()["operation"] == "embedding"
    assert response.json()["progress_pct"] == 50


@pytest.mark.anyio
async def test_cancel_marks_job_and_queued_items(client, fake_db):
    response = await client.post("/api/batches/44444444-4444-4444-4444-444444444444/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelling"
    sql = " ".join(statement for statement, _ in fake_db.executed)
    assert "cancel_requested=TRUE" in sql
    assert "status='skipped'" in sql


def test_batch_item_limit_is_enforced():
    with pytest.raises(Exception) as exc:
        batches._limit(range(batches.MAX_ITEMS + 1))
    assert exc.value.status_code == 400
