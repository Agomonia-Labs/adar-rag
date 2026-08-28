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
    conn.fetchrow = AsyncMock(return_value={"id": "existing-job"})
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    with patch.object(video_dispatch, "get_pool", return_value=pool):
        job_id, reused = await video_dispatch.create_or_reuse_video_job(
            document_id="doc-1", user_id="user-1", workspace_id=None, payload={"filename": "v.mp4"}
        )

    assert (job_id, reused) == ("existing-job", True)
    assert conn.execute.await_count == 1  # advisory lock only; no duplicate insert


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
    assert json.loads(insert_args[5])["source_gcs_path"] == "users/u/v.mp4"
    assert insert_args[6] == "cloud_run_job"


def test_dispatch_mode_defaults_to_inline_locally(monkeypatch):
    monkeypatch.delenv("VIDEO_DISPATCH_MODE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert video_dispatch.video_dispatch_mode() == "inline"


def test_dispatch_mode_defaults_to_cloud_run_job_in_production(monkeypatch):
    monkeypatch.delenv("VIDEO_DISPATCH_MODE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert video_dispatch.video_dispatch_mode() == "cloud_run_job"
