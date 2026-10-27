from __future__ import annotations

import os
import sys
import tempfile

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-for-unit-tests")

from auth.dependencies import get_current_user
from database.connection import get_db
from routes import documents
from routes import workspaces
from services.document_processing import _split_pdf_range
from services.extractor import detect_type


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
    monkeypatch.setattr(documents, "emit_event", noop)
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

    monkeypatch.setattr(documents, "_get_owned", fake_get_owned)
    monkeypatch.setattr(documents, "check_and_log_daily_event", fake_usage)

    async def fake_create_job(**kwargs):
        return "embedding-job-1", False

    async def fake_dispatch(job_id):
        return "operations/embedding-job-1"

    monkeypatch.setattr(documents, "create_or_reuse_document_embedding_job", fake_create_job)
    monkeypatch.setattr(documents, "dispatch_cloud_run_document_embedding_job", fake_dispatch)
    monkeypatch.setattr(documents, "document_dispatch_mode", lambda: "cloud_run_job")

    async def fake_require_role(*args, **kwargs):
        return "editor"

    monkeypatch.setattr(workspaces, "_require_role", fake_require_role)

    response = await client.post("/api/documents/doc-1/embed")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Embedding queued",
        "doc_id": "doc-1",
        "job_id": "embedding-job-1",
        "job_reused": False,
        "processing_mode": "durable_job",
    }


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
    assert "Document must be chunked before embedding" in response.json()["detail"]


@pytest.mark.anyio
async def test_split_pdf_creates_one_durable_job_per_range(client, fake_db, monkeypatch):
    async def fake_get_owned(doc_id, user_id, db):
        return {
            "id": doc_id,
            "user_id": user_id,
            "workspace_id": None,
            "original_name": "large-book.pdf",
            "file_type": "pdf",
            "gcs_source_path": "users/user-1/documents/source/source/large-book.pdf",
        }

    jobs = []

    async def fake_create_job(**kwargs):
        jobs.append(kwargs)
        return f"job-{len(jobs)}", False

    async def fake_dispatch(_job_id):
        return "operation-1"

    monkeypatch.setattr(documents, "_get_owned", fake_get_owned)
    async def fake_pdf_info(*args, **kwargs):
        return {"doc_id": "source", "page_count": 100, "cached": True}

    monkeypatch.setattr(documents, "get_pdf_info", fake_pdf_info)
    monkeypatch.setattr(documents, "create_or_reuse_document_job", fake_create_job)
    monkeypatch.setattr(documents, "dispatch_cloud_run_document_job", fake_dispatch)
    monkeypatch.setattr(documents, "document_dispatch_mode", lambda: "cloud_run_job")

    response = await client.post(
        "/api/documents/source/split",
        json={
            "parts": [
                {"start_page": 1, "end_page": 10, "filename": "Volume One"},
                {"start_page": 11, "end_page": 20, "filename": "Volume Two.pdf"},
            ],
            "embed_after_processing": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["filename"] for item in body["created"]] == ["Volume One.pdf", "Volume Two.pdf"]
    assert len(jobs) == 2
    assert jobs[0]["payload"]["split_start_page"] == 1
    assert jobs[1]["payload"]["split_end_page"] == 20
    assert all(job["payload"]["embed_after_processing"] for job in jobs)
    assert sum("INSERT INTO documents" in sql for sql, _args in fake_db.executed) == 2


@pytest.mark.anyio
async def test_split_pdf_rejects_overlapping_ranges(client, monkeypatch):
    async def fake_get_owned(doc_id, user_id, db):
        return {
            "id": doc_id,
            "user_id": user_id,
            "workspace_id": None,
            "original_name": "large-book.pdf",
            "file_type": "pdf",
            "gcs_source_path": "source.pdf",
        }

    monkeypatch.setattr(documents, "_get_owned", fake_get_owned)
    async def fake_pdf_info(*args, **kwargs):
        return {"doc_id": "source", "page_count": 100, "cached": True}

    monkeypatch.setattr(documents, "get_pdf_info", fake_pdf_info)
    response = await client.post(
        "/api/documents/source/split",
        json={"parts": [
            {"start_page": 1, "end_page": 10, "filename": "one.pdf"},
            {"start_page": 10, "end_page": 15, "filename": "two.pdf"},
        ]},
    )

    assert response.status_code == 422
    assert "overlap" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_split_pdf_rejects_page_beyond_source(client, monkeypatch):
    async def fake_get_owned(doc_id, user_id, db):
        return {
            "id": doc_id, "user_id": user_id, "workspace_id": None,
            "original_name": "book.pdf", "file_type": "pdf", "gcs_source_path": "source.pdf",
        }

    async def fake_pdf_info(*args, **kwargs):
        return {"doc_id": "source", "page_count": 12, "cached": True}

    monkeypatch.setattr(documents, "_get_owned", fake_get_owned)
    monkeypatch.setattr(documents, "get_pdf_info", fake_pdf_info)
    response = await client.post(
        "/api/documents/source/split",
        json={"parts": [{"start_page": 1, "end_page": 13, "filename": "too-long.pdf"}]},
    )

    assert response.status_code == 422
    assert "12 pages" in response.json()["detail"]


def test_powerpoint_files_are_detected_as_presentations():
    assert detect_type("briefing.pptx") == "presentation"
    assert detect_type("legacy.ppt", "application/vnd.ms-powerpoint") == "presentation"


def test_split_pdf_range_writes_inclusive_pages():
    import fitz

    with tempfile.TemporaryDirectory() as directory:
        source_path = os.path.join(directory, "source.pdf")
        output_path = os.path.join(directory, "part.pdf")
        source = fitz.open()
        for index in range(5):
            page = source.new_page()
            page.insert_text((72, 72), f"Page {index + 1}")
        source.save(source_path)
        source.close()

        source_pages, output_size = _split_pdf_range(source_path, output_path, 2, 4)

        output = fitz.open(output_path)
        try:
            assert source_pages == 5
            assert output.page_count == 3
            assert "Page 2" in output[0].get_text()
            assert output_size > 0
        finally:
            output.close()
