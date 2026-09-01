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


class FakeEventDb:
    def __init__(self):
        self.subscription_query = ""
        self.delivery_args = None

    async def fetchrow(self, query, *args):
        return {"id": "event-1", "sequence_number": 8, "created_at": mcp_enterprise.datetime.now(mcp_enterprise.timezone.utc)}

    async def fetch(self, query, *args):
        self.subscription_query = query
        return [{"id": "subscription-1"}]

    async def fetchval(self, query, *args):
        self.delivery_args = args
        return "delivery-1"


@pytest.mark.anyio
async def test_workspace_event_matches_workspace_subscription_independent_of_uploader(monkeypatch):
    dispatched = []

    async def capture_dispatch(delivery_ids):
        dispatched.extend(delivery_ids)

    monkeypatch.setattr(mcp_enterprise, "dispatch_webhook_deliveries", capture_dispatch)
    db = FakeEventDb()
    await mcp_enterprise.emit_event(
        db,
        user_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        event_type="video.processing.completed",
        resource_type="document",
        resource_id="document-1",
        payload={"status": "completed"},
    )

    assert "s.workspace_id IS NOT NULL AND s.workspace_id=$2::uuid" in db.subscription_query
    assert "g.client_id=s.client_id AND g.workspace_id=$2::uuid" in db.subscription_query
    assert "s.client_id IS NULL AND s.user_id=$1::uuid" in db.subscription_query
    assert db.delivery_args == ("subscription-1", "event-1")
    assert dispatched == ["delivery-1"]


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
    assert captured["headers"]["X-DocIntel-Signature"] == f"v1={expected}"
    assert captured["headers"]["X-DocIntel-Event-ID"] == "event-1"
    assert captured["headers"]["Idempotency-Key"] == "event-1"
    assert captured["headers"]["X-DocIntel-Timestamp"] == "1700000000"
    assert any("status='delivered'" in query for query, _ in db.calls)
    assert any("mcp_webhook_delivery_attempts" in query for query, _ in db.calls)


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
