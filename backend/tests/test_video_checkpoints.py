from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import video_checkpoints


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


def _pool(conn):
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)
    return pool


@pytest.mark.asyncio
async def test_completed_output_returns_saved_json():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"output_data": {"text": "saved transcript"}})

    with patch.object(video_checkpoints, "get_pool", return_value=_pool(conn)):
        output = await video_checkpoints.completed_output("job-1", "transcript")

    assert output == {"text": "saved transcript"}


@pytest.mark.asyncio
async def test_begin_returns_false_when_checkpoint_has_live_foreign_lease():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    with patch.object(video_checkpoints, "get_pool", return_value=_pool(conn)):
        acquired = await video_checkpoints.begin(
            job_id="job-1", document_id="doc-1", stage="embedding",
            item_key="000001", owner="worker-2",
        )

    assert acquired is False


@pytest.mark.asyncio
async def test_complete_clears_lease_and_persists_output():
    conn = MagicMock()
    conn.execute = AsyncMock()

    with patch.object(video_checkpoints, "get_pool", return_value=_pool(conn)):
        await video_checkpoints.complete(
            job_id="job-1", stage="frame", item_key="000003",
            output_data={"frame_path": "gs://frame"}, owner="worker-1",
        )

    sql = conn.execute.await_args.args[0]
    assert "status='completed'" in sql
    assert "lease_owner=NULL" in sql
