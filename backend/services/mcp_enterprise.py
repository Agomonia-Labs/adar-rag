from __future__ import annotations

import hashlib
import hmac
import json
import asyncio
import random
import time
from typing import Any

from services.tracing import current_trace_id
from database.connection import get_pool
import httpx


def request_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def idempotent_result(db, user_id: str, operation: str, key: str | None, payload: Any) -> dict | None:
    if not key:
        return None
    row = await db.fetchrow(
        """SELECT request_hash,response_data,resource_type,resource_id,status
           FROM mcp_idempotency_records
           WHERE user_id=$1::uuid AND operation=$2 AND idempotency_key=$3 AND expires_at>NOW()""",
        user_id, operation, key,
    )
    if not row:
        return None
    if row["request_hash"] != request_hash(payload):
        return {"ok": False, "error": {"code": "idempotency_conflict", "message": "This idempotency key was used with a different request"}}
    result = _json(row["response_data"])
    result["idempotent_replay"] = True
    return result


async def save_idempotent_result(
    db, user_id: str, operation: str, key: str | None, payload: Any, response: dict,
    *, resource_type: str | None = None, resource_id: str | None = None,
) -> None:
    if not key:
        return
    await db.execute(
        """INSERT INTO mcp_idempotency_records
           (user_id,operation,idempotency_key,request_hash,resource_type,resource_id,response_data)
           VALUES($1::uuid,$2,$3,$4,$5,$6,$7::jsonb)
           ON CONFLICT(user_id,operation,idempotency_key) DO NOTHING""",
        user_id, operation, key, request_hash(payload), resource_type, resource_id, json.dumps(response, default=str),
    )


async def emit_event(
    db, *, user_id: str, event_type: str, resource_type: str, resource_id: str,
    workspace_id: str | None = None, payload: dict[str, Any] | None = None,
) -> None:
    row = await db.fetchrow(
        """INSERT INTO mcp_events(user_id,workspace_id,event_type,resource_type,resource_id,payload,trace_id)
           VALUES($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb,$7) RETURNING id,sequence_number,created_at""",
        user_id, workspace_id, event_type, resource_type, resource_id,
        json.dumps(bounded_payload(payload or {}), default=str), current_trace_id.get(),
    )
    subscriptions = await db.fetch(
        """SELECT * FROM mcp_event_subscriptions WHERE user_id=$1::uuid AND status='active' AND webhook_url IS NOT NULL
           AND (workspace_id IS NULL OR workspace_id=$2::uuid)
           AND (resource_type IS NULL OR resource_type=$3)
           AND (resource_id IS NULL OR resource_id=$4)
           AND (event_types='[]'::jsonb OR event_types ? $5)""",
        user_id, workspace_id, resource_type, resource_id, event_type,
    )
    event = {"id": str(row["id"]), "sequence_number": row["sequence_number"], "event_type": event_type,
             "resource_type": resource_type, "resource_id": resource_id, "payload": bounded_payload(payload or {}),
             "created_at": row["created_at"].isoformat()}
    delivery_ids = []
    for subscription in subscriptions:
        delivery_id = await db.fetchval(
            """INSERT INTO mcp_webhook_deliveries(subscription_id,event_id)
               VALUES($1,$2) ON CONFLICT(subscription_id,event_id) DO UPDATE
               SET updated_at=NOW() RETURNING id""",
            subscription["id"], row["id"],
        )
        delivery_ids.append(str(delivery_id))
    if delivery_ids:
        asyncio.create_task(process_webhook_deliveries(delivery_ids=delivery_ids))


async def _deliver_webhook(db, delivery: dict[str, Any], subscription: dict[str, Any], event: dict[str, Any]) -> None:
    body = json.dumps(event, separators=(",", ":"), default=str).encode()
    timestamp = str(int(time.time()))
    signed = timestamp.encode() + b"." + body
    signature = hmac.new(str(subscription.get("webhook_secret") or "").encode(), signed, hashlib.sha256).hexdigest()
    attempt = int(delivery.get("attempt_count") or 0) + 1
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.post(subscription["webhook_url"], content=body, headers={
                "Content-Type": "application/json", "X-DocIntel-Event": event["event_type"],
                "X-DocIntel-Event-ID": event["id"], "X-DocIntel-Timestamp": timestamp,
                "X-DocIntel-Signature-SHA256": signature,
            })
            response.raise_for_status()
        await db.execute(
            """UPDATE mcp_webhook_deliveries SET status='delivered',attempt_count=$2,
               last_http_status=$3,response_preview=$4,last_error=NULL,delivered_at=NOW(),updated_at=NOW()
               WHERE id=$1""", delivery["id"], attempt, response.status_code, response.text[:1000],
        )
        await db.execute("UPDATE mcp_event_subscriptions SET last_sequence=GREATEST(last_sequence,$2),updated_at=NOW() WHERE id=$1", subscription["id"], event["sequence_number"])
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        terminal = attempt >= int(delivery.get("max_attempts") or 6)
        delay_seconds = min(3600, (2 ** min(attempt, 10)) * 5 + random.randint(0, 5))
        await db.execute(
            """UPDATE mcp_webhook_deliveries SET status=$2,attempt_count=$3,last_http_status=$4,
               last_error=$5,next_attempt_at=NOW()+($6 * INTERVAL '1 second'),updated_at=NOW()
               WHERE id=$1""", delivery["id"], "dead_letter" if terminal else "retrying", attempt,
            status_code, str(exc)[:2000], delay_seconds,
        )


async def process_webhook_deliveries(*, delivery_ids: list[str] | None = None, limit: int = 50) -> dict[str, int]:
    """Claim and deliver due webhooks using a connection independent of the caller."""
    counts = {"processed": 0, "delivered": 0, "failed": 0}
    async with get_pool().acquire() as db:
        rows = await db.fetch(
            """SELECT d.*,s.webhook_url,s.webhook_secret,s.last_sequence,
                      e.sequence_number,e.event_type,e.resource_type,e.resource_id,e.payload,e.created_at
               FROM mcp_webhook_deliveries d
               JOIN mcp_event_subscriptions s ON s.id=d.subscription_id AND s.status='active'
               JOIN mcp_events e ON e.id=d.event_id
               WHERE ((d.status IN ('pending','retrying') AND d.next_attempt_at<=NOW())
                      OR (d.status='delivering' AND d.updated_at<NOW()-INTERVAL '2 minutes'))
                 AND ($1::uuid[] IS NULL OR d.id=ANY($1::uuid[]))
               ORDER BY d.next_attempt_at FOR UPDATE OF d SKIP LOCKED LIMIT $2""",
            delivery_ids or None, limit,
        )
        for row in rows:
            item = dict(row)
            await db.execute("UPDATE mcp_webhook_deliveries SET status='delivering',updated_at=NOW() WHERE id=$1", item["id"])
            event = {
                "id": str(item["event_id"]), "sequence_number": item["sequence_number"],
                "event_type": item["event_type"], "resource_type": item["resource_type"],
                "resource_id": item["resource_id"], "payload": _json(item["payload"]),
                "created_at": item["created_at"].isoformat(),
            }
            await _deliver_webhook(db, item, item, event)
            final = await db.fetchval("SELECT status FROM mcp_webhook_deliveries WHERE id=$1", item["id"])
            counts["processed"] += 1
            counts["delivered" if final == "delivered" else "failed"] += 1
    return counts


def normalize_citations(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    citations = []
    for index, source in enumerate(sources or []):
        citations.append({
            "citation_id": str(source.get("citation_id") or f"citation-{index + 1}"),
            "document_id": _text(source.get("document_id") or source.get("doc_id")),
            "document_name": _text(source.get("document_name") or source.get("filename") or source.get("source")),
            "chunk_id": _text(source.get("chunk_id") or source.get("id")),
            "chunk_index": source.get("chunk_index") if source.get("chunk_index") is not None else source.get("index"),
            "page_number": source.get("page_number") or source.get("page"),
            "start_seconds": source.get("start_seconds"),
            "end_seconds": source.get("end_seconds"),
            "retrieval_score": _number(source.get("retrieval_score") or source.get("score")),
            "rerank_score": _number(source.get("rerank_score")),
            "confidence": _number(source.get("confidence")),
            "source_url": _text(source.get("source_url") or source.get("url")),
            "excerpt": _text(source.get("excerpt") or source.get("text") or source.get("content"))[:600],
        })
    return citations


def bounded_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"status", "stage", "progress_pct", "operation", "document_id", "batch_job_id", "run_id", "error_code", "message"}
    return {key: payload[key] for key in allowed if key in payload}


def _json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return json.loads(value or "{}")
    return dict(value or {})


def _text(value) -> str:
    return str(value or "")


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
