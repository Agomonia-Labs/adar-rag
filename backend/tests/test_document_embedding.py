import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-for-unit-tests")

from services import document_embedding


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_embedding_job_resumes_only_incomplete_chunks():
    conn = MagicMock()
    conn.execute = AsyncMock()

    async def fetchrow(sql, *_args):
        if "RETURNING attempt_count" in sql:
            return {"attempt_count": 2}
        if "gcs_chunks_dir" in sql:
            return {
                "gcs_chunks_dir": "users/u/documents/d/chunks/",
                "guest_session_id": None,
                "original_name": "large.pdf",
            }
        if "notify_on_embed" in sql:
            return {"email": "user@example.com", "notify_on_embed": False}
        return None

    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    async def completed_output(_job_id, stage, item_key="stage"):
        if stage == "document_embedding_prepare":
            return {"vectors_cleared": True}
        if stage == "document_embedding_chunk" and item_key == "000000":
            return {"chunk_index": 0, "stored": True}
        return None

    chunks = [
        {"index": 0, "gcs_path": "chunks/0.txt"},
        {"index": 1, "gcs_path": "chunks/1.txt"},
    ]

    with (
        patch.object(document_embedding, "get_pool", return_value=pool),
        patch.object(document_embedding.checkpoints, "worker_id", return_value="worker-1"),
        patch.object(document_embedding.checkpoints, "completed_output", side_effect=completed_output),
        patch.object(document_embedding.checkpoints, "begin", new=AsyncMock(return_value=True)) as begin,
        patch.object(document_embedding.checkpoints, "complete", new=AsyncMock()) as complete,
        patch.object(document_embedding.checkpoints, "heartbeat", new=AsyncMock()),
        patch.object(document_embedding.gcs, "download_json", new=AsyncMock(return_value={"chunks": chunks})),
        patch.object(document_embedding.gcs, "download_text", new=AsyncMock(return_value="second chunk")) as download,
        patch.object(document_embedding, "embed", new=AsyncMock(return_value=[0.1, 0.2])) as embed,
        patch.object(document_embedding, "replace_chunk", new=AsyncMock()) as replace,
        patch.object(document_embedding, "delete_document_vectors", new=AsyncMock()) as delete_vectors,
        patch.object(document_embedding, "emit_event", new=AsyncMock()),
        patch.object(document_embedding, "_set_progress", new=AsyncMock()),
    ):
        result = await document_embedding.process_document_embedding_job(
            job_id="job-1",
            document_id="doc-1",
            user_id="user-1",
            workspace_id="workspace-1",
        )

    assert result["status"] == "embedded"
    delete_vectors.assert_not_awaited()
    download.assert_awaited_once_with("chunks/1.txt")
    embed.assert_awaited_once_with("second chunk")
    replace.assert_awaited_once()
    assert replace.await_args.kwargs["chunk_index"] == 1
    begin.assert_awaited_once()
    assert begin.await_args.kwargs["item_key"] == "000001"
    assert any(call.kwargs.get("item_key") == "000001" for call in complete.await_args_list)
