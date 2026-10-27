from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import video_dispatch


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_create_video_job_reuses_active_job():
    conn = MagicMock()
    conn.transaction.return_value = _Transaction()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "existing-job", "status": "running", "stale": False})
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with patch.object(video_dispatch, "get_pool", return_value=pool):
        job_id, reused = await video_dispatch.create_or_reuse_video_job(
            document_id="doc-1", user_id="user-1", workspace_id=None, payload={"filename": "v.mp4"}
        )

    assert (job_id, reused) == ("existing-job", True)
    assert conn.execute.await_count == 1  # advisory lock only; no duplicate insert


@pytest.mark.asyncio
async def test_create_video_job_resumes_failed_job_with_checkpoints():
    conn = MagicMock()
    conn.transaction.return_value = _Transaction()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "failed-job", "status": "error", "stale": True})
    conn.fetchval = AsyncMock(return_value=True)
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with patch.object(video_dispatch, "get_pool", return_value=pool):
        job_id, reused = await video_dispatch.create_or_reuse_video_job(
            document_id="doc-1", user_id="user-1", workspace_id=None,
            payload={"filename": "v.mp4"},
        )

    assert (job_id, reused) == ("failed-job", False)
    assert conn.fetchval.await_count == 1
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert any("SET status='queued'" in statement for statement in statements)


@pytest.mark.asyncio
async def test_create_video_job_does_not_resume_checkpoints_from_another_profile():
    conn = MagicMock()
    conn.transaction.return_value = _Transaction()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "id": "failed-job",
        "status": "error",
        "stale": True,
        "input_data": {"processing_profile": "standard"},
    })
    conn.fetchval = AsyncMock(return_value=True)
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with (
        patch.object(video_dispatch, "get_pool", return_value=pool),
        patch.object(video_dispatch, "uuid4", return_value="cultural-job"),
    ):
        job_id, reused = await video_dispatch.create_or_reuse_video_job(
            document_id="doc-1",
            user_id="user-1",
            workspace_id=None,
            payload={"filename": "v.mp4", "processing_profile": "cultural_performance"},
        )

    assert (job_id, reused) == ("cultural-job", False)
    assert conn.fetchval.await_count == 0


@pytest.mark.asyncio
async def test_create_video_job_persists_payload_before_dispatch():
    conn = MagicMock()
    conn.transaction.return_value = _Transaction()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with (
        patch.object(video_dispatch, "get_pool", return_value=pool),
        patch.object(video_dispatch, "video_dispatch_mode", return_value="cloud_run_job"),
        patch.object(video_dispatch, "uuid4", return_value="new-job"),
    ):
        job_id, reused = await video_dispatch.create_or_reuse_video_job(
            document_id="doc-1",
            user_id="user-1",
            workspace_id="workspace-1",
            payload={"filename": "v.mp4", "source_gcs_path": "users/u/v.mp4"},
        )

    assert (job_id, reused) == ("new-job", False)
    insert_args = conn.execute.await_args_list[1].args
    assert insert_args[5] == "process_video"
    assert json.loads(insert_args[6])["source_gcs_path"] == "users/u/v.mp4"
    assert insert_args[7] == "cloud_run_job"


@pytest.mark.asyncio
async def test_create_document_job_uses_large_document_profile_and_job_type():
    conn = MagicMock()
    conn.transaction.return_value = _Transaction()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with (
        patch.object(video_dispatch, "get_pool", return_value=pool),
        patch.object(video_dispatch, "document_dispatch_mode", return_value="cloud_run_job"),
        patch.object(video_dispatch, "uuid4", return_value="document-job"),
    ):
        job_id, reused = await video_dispatch.create_or_reuse_document_job(
            document_id="doc-1",
            user_id="user-1",
            workspace_id="workspace-1",
            payload={"filename": "large.pdf", "processing_profile": "large_document"},
        )

    assert (job_id, reused) == ("document-job", False)
    insert_args = conn.execute.await_args_list[1].args
    assert insert_args[5] == "process_document"
    assert json.loads(insert_args[6])["filename"] == "large.pdf"
    assert insert_args[7] == "cloud_run_job"


@pytest.mark.asyncio
async def test_create_document_embedding_job_uses_durable_job_type():
    conn = MagicMock()
    conn.transaction.return_value = _Transaction()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with (
        patch.object(video_dispatch, "get_pool", return_value=pool),
        patch.object(video_dispatch, "document_dispatch_mode", return_value="cloud_run_job"),
        patch.object(video_dispatch, "uuid4", return_value="embedding-job"),
    ):
        job_id, reused = await video_dispatch.create_or_reuse_document_embedding_job(
            document_id="doc-1",
            user_id="user-1",
            workspace_id="workspace-1",
            payload={"processing_profile": "durable_embedding", "chunk_count": 42},
        )

    assert (job_id, reused) == ("embedding-job", False)
    insert_args = conn.execute.await_args_list[1].args
    assert insert_args[5] == "process_document_embedding"
    assert json.loads(insert_args[6])["chunk_count"] == 42
    assert insert_args[7] == "cloud_run_job"
    status_update = conn.execute.await_args_list[2].args
    assert status_update[3] == "embedding"


def test_dispatch_mode_defaults_to_inline_locally(monkeypatch):
    monkeypatch.delenv("VIDEO_DISPATCH_MODE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert video_dispatch.video_dispatch_mode() == "inline"


def test_dispatch_mode_defaults_to_cloud_run_job_in_production(monkeypatch):
    monkeypatch.delenv("VIDEO_DISPATCH_MODE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert video_dispatch.video_dispatch_mode() == "cloud_run_job"
