from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-only-key")
os.environ.setdefault("GOOGLE_AI_KEY", "test-only-key")

from routes.guest import claim_guest_session


class FakeConnection:
    def __init__(self):
        self.execute = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


@pytest.mark.asyncio
async def test_claim_guest_session_casts_json_session_id_to_text():
    db = FakeConnection()
    session_id = "65cc3fe5-e7bc-4f6d-96b9-2af18418bd78"
    user_id = "8cd47531-f8b5-4f0e-9860-6138de337a96"

    with patch("routes.guest._require_guest", new=AsyncMock(return_value={"id": session_id})):
        result = await claim_guest_session(
            current_user={"id": user_id},
            x_guest_token="guest-token",
            db=db,
        )

    assert result == {"claimed": True, "guest_session_id": session_id}
    assert db.execute.await_count == 3
    chunk_update_sql = db.execute.await_args_list[1].args[0]
    assert "doc_metadata->>'claimed_from_guest_session' = $2::text" in chunk_update_sql
