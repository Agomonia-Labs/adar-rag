from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from services import mcp_enterprise


class FakeResponse:
    status_code = 200
    text = "accepted"

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, captured, *args, **kwargs):
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, *, content, headers):
        self.captured.update(url=url, content=content, headers=headers)
        return FakeResponse()


class FakeDb:
    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))


@pytest.mark.anyio
async def test_webhook_delivery_is_timestamp_signed(monkeypatch):
    captured = {}
    monkeypatch.setattr(mcp_enterprise.time, "time", lambda: 1700000000)
    monkeypatch.setattr(
        mcp_enterprise.httpx, "AsyncClient",
        lambda *args, **kwargs: FakeClient(captured, *args, **kwargs),
    )
    db = FakeDb()
    event = {
        "id": "event-1", "sequence_number": 7, "event_type": "batch.completed",
        "resource_type": "batch", "resource_id": "batch-1", "payload": {},
        "created_at": "2026-08-31T00:00:00+00:00",
    }
    await mcp_enterprise._deliver_webhook(
        db,
        {"id": "delivery-1", "attempt_count": 0, "max_attempts": 6},
        {"id": "subscription-1", "webhook_url": "https://example.com/hook", "webhook_secret": "secret"},
        event,
    )

    body = json.dumps(event, separators=(",", ":"), default=str).encode()
    expected = hmac.new(b"secret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert captured["headers"]["X-DocIntel-Signature-SHA256"] == expected
    assert captured["headers"]["X-DocIntel-Event-ID"] == "event-1"
    assert captured["headers"]["X-DocIntel-Timestamp"] == "1700000000"
    assert any("status='delivered'" in query for query, _ in db.calls)


@pytest.mark.anyio
async def test_webhook_delivery_moves_to_dead_letter_after_last_attempt(monkeypatch):
    class FailingClient(FakeClient):
        async def post(self, url, *, content, headers):
            raise RuntimeError("receiver unavailable")

    monkeypatch.setattr(
        mcp_enterprise.httpx, "AsyncClient",
        lambda *args, **kwargs: FailingClient({}, *args, **kwargs),
    )
    db = FakeDb()
    await mcp_enterprise._deliver_webhook(
        db,
        {"id": "delivery-1", "attempt_count": 5, "max_attempts": 6},
        {"id": "subscription-1", "webhook_url": "https://example.com/hook", "webhook_secret": "secret"},
        {"id": "event-1", "sequence_number": 7, "event_type": "batch.failed",
         "resource_type": "batch", "resource_id": "batch-1", "payload": {},
         "created_at": "2026-08-31T00:00:00+00:00"},
    )
    assert any(args[1] == "dead_letter" for _, args in db.calls if len(args) > 1)
