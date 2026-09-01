from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException


DEFAULT_RESERVATION_SECONDS = max(60, int(os.getenv("USAGE_RESERVATION_SECONDS", "3600")))

QUOTA_COUNTER_RESERVE_SQL = """INSERT INTO usage_quota_counters(policy_id,window_start,reserved_units)
   SELECT $1::uuid,$2::timestamptz,$3::bigint WHERE $3::bigint <= $4::bigint
   ON CONFLICT(policy_id,window_start) DO UPDATE SET
     reserved_units=usage_quota_counters.reserved_units + EXCLUDED.reserved_units,
     updated_at=NOW()
   WHERE usage_quota_counters.used_units + usage_quota_counters.reserved_units + EXCLUDED.reserved_units <= $4::bigint
   RETURNING used_units,reserved_units"""


@dataclass(frozen=True)
class UsageContext:
    user_id: str
    client_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    principal_type: str = "user"
    scope: str | None = None
    operation: str = "api.request"
    request_id: str | None = None
    trace_id: str | None = None
    job_id: str | None = None


def operation_for_request(method: str, path: str) -> str:
    normalized = path.removeprefix("/api/v1/").strip("/") or "catalog"
    pieces = ["{id}" if part.replace("-", "").isalnum() and len(part) >= 24 else part for part in normalized.split("/")]
    return f"{method.lower()}.{'/'.join(pieces)}"


def _window_start(now: datetime, seconds: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


async def applicable_policies(db, context: UsageContext) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """SELECT * FROM usage_quota_policies p
           WHERE p.status='active' AND p.effective_from<=NOW()
             AND (p.expires_at IS NULL OR p.expires_at>NOW())
             AND (p.organization_id IS NULL OR p.organization_id=$1::uuid)
             AND (p.client_id IS NULL OR p.client_id=$2)
             AND (p.workspace_id IS NULL OR p.workspace_id=$3::uuid)
             AND (p.scope IS NULL OR p.scope=$4)
             AND (p.operation IS NULL OR p.operation=$5)
           ORDER BY
             ((p.organization_id IS NOT NULL)::int + (p.client_id IS NOT NULL)::int +
              (p.workspace_id IS NOT NULL)::int + (p.scope IS NOT NULL)::int +
              (p.operation IS NOT NULL)::int) DESC,
             p.window_seconds ASC""",
        context.organization_id, context.client_id, context.workspace_id,
        context.scope, context.operation,
    )
    return [dict(row) for row in rows]


async def reserve_usage(
    db, context: UsageContext, units: int = 1, *, idempotency_key: str | None = None,
    reservation_seconds: int = DEFAULT_RESERVATION_SECONDS,
) -> dict[str, Any]:
    units = max(1, int(units or 1))
    policies = await applicable_policies(db, context)
    if not policies:
        return {"allowed": True, "limit": None, "remaining": None, "reset_at": None, "reservation_ids": []}

    now = datetime.now(timezone.utc)
    reservations: list[str] = []
    remaining_values: list[int] = []
    reset_values: list[datetime] = []
    async with db.transaction():
        for policy in policies:
            seconds = int(policy["window_seconds"])
            window_start = _window_start(now, seconds)
            reservation_key = f"{idempotency_key}:{policy['id']}" if idempotency_key else str(uuid4())
            if idempotency_key:
                existing = await db.fetchrow(
                    """SELECT id FROM usage_reservations
                       WHERE policy_id=$1 AND idempotency_key=$2 AND status IN ('reserved','reconciled')""",
                    policy["id"], reservation_key,
                )
                if existing:
                    counter = await db.fetchrow(
                        "SELECT used_units,reserved_units FROM usage_quota_counters WHERE policy_id=$1 AND window_start=$2",
                        policy["id"], window_start,
                    )
                    consumed = int(counter["used_units"] or 0) + int(counter["reserved_units"] or 0) if counter else 0
                    reservations.append(str(existing["id"]))
                    remaining_values.append(max(0, int(policy["limit_value"]) - consumed))
                    reset_values.append(window_start + timedelta(seconds=seconds))
                    continue
            row = await db.fetchrow(
                QUOTA_COUNTER_RESERVE_SQL,
                policy["id"], window_start, units, int(policy["limit_value"]),
            )
            if not row:
                current = await db.fetchrow(
                    "SELECT used_units,reserved_units FROM usage_quota_counters WHERE policy_id=$1 AND window_start=$2",
                    policy["id"], window_start,
                )
                used = int((current or {}).get("used_units", 0)) + int((current or {}).get("reserved_units", 0))
                reset_at = window_start + timedelta(seconds=seconds)
                raise HTTPException(
                    status_code=429,
                    detail={"code": "quota_exceeded", "message": f"Quota exceeded for {policy['policy_name']}",
                            "policy_id": str(policy["id"]), "limit": int(policy["limit_value"]),
                            "used": used, "reset_at": reset_at.isoformat(), "trace_id": context.trace_id},
                    headers={"Retry-After": str(max(1, int((reset_at - now).total_seconds())))},
                )
            remaining_values.append(max(0, int(policy["limit_value"]) - int(row["used_units"]) - int(row["reserved_units"])))
            reset_values.append(window_start + timedelta(seconds=seconds))
            reservation_id = await db.fetchval(
                """INSERT INTO usage_reservations
                   (policy_id,organization_id,client_id,workspace_id,operation,units_reserved,
                    window_start,idempotency_key,expires_at)
                   VALUES($1,$2::uuid,$3,$4::uuid,$5,$6,$7,$8,$9) RETURNING id""",
                policy["id"], context.organization_id, context.client_id, context.workspace_id,
                context.operation, units, window_start,
                reservation_key,
                now + timedelta(seconds=reservation_seconds),
            )
            reservations.append(str(reservation_id))
    return {"allowed": True, "limit": min(int(p["limit_value"]) for p in policies),
            "remaining": min(remaining_values), "reset_at": min(reset_values).isoformat(),
            "reservation_ids": reservations, "policy_ids": [str(p["id"]) for p in policies]}


async def reconcile_usage(
    db, context: UsageContext, reservation_ids: list[str], *, units_used: int = 1,
    status_code: int = 200, input_bytes: int = 0, output_bytes: int = 0,
    token_count: int = 0, latency_ms: int | None = None, metadata: dict | None = None,
) -> None:
    units_used = max(0, int(units_used or 0))
    async with db.transaction():
        for reservation_id in reservation_ids:
            row = await db.fetchrow(
                """UPDATE usage_reservations SET status='reconciled',units_used=$2,
                     reconciled_at=NOW() WHERE id=$1::uuid AND status='reserved'
                   RETURNING policy_id,window_start,units_reserved""", reservation_id, units_used,
            )
            if row:
                await db.execute(
                    """UPDATE usage_quota_counters SET
                         reserved_units=GREATEST(0,reserved_units-$3),used_units=used_units+$4,updated_at=NOW()
                       WHERE policy_id=$1 AND window_start=$2""",
                    row["policy_id"], row["window_start"], int(row["units_reserved"]), units_used,
                )
        await db.execute(
            """INSERT INTO usage_events
               (user_id,event_type,quantity,metadata,organization_id,client_id,workspace_id,
                principal_type,scope,operation,request_id,trace_id,job_id,input_bytes,
                output_bytes,token_count,latency_ms,status_code)
               VALUES($1::uuid,$2,$3,$4::jsonb,$5::uuid,$6,$7::uuid,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""",
            context.user_id, context.operation, units_used, json.dumps(metadata or {}),
            context.organization_id, context.client_id, context.workspace_id, context.principal_type,
            context.scope, context.operation, context.request_id, context.trace_id, context.job_id,
            max(0, input_bytes), max(0, output_bytes), max(0, token_count), latency_ms, status_code,
        )


async def release_expired_reservations(db, limit: int = 500) -> int:
    rows = await db.fetch(
        """WITH expired AS (
             SELECT id FROM usage_reservations WHERE status='reserved' AND expires_at<NOW()
             ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT $1
           )
           UPDATE usage_reservations r SET status='expired',reconciled_at=NOW()
           FROM expired e WHERE r.id=e.id
           RETURNING r.policy_id,r.window_start,r.units_reserved""", limit,
    )
    for row in rows:
        await db.execute(
            """UPDATE usage_quota_counters SET reserved_units=GREATEST(0,reserved_units-$3),updated_at=NOW()
               WHERE policy_id=$1 AND window_start=$2""",
            row["policy_id"], row["window_start"], int(row["units_reserved"]),
        )
    return len(rows)


async def application_usage_summary(db, client_id: str, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    rows = await db.fetch(
        """SELECT COALESCE(operation,event_type) AS operation,COUNT(*) AS requests,
                  COALESCE(SUM(quantity),0) AS units,
                  COUNT(*) FILTER (WHERE status_code>=400) AS failures,
                  COALESCE(SUM(input_bytes),0) AS input_bytes,
                  COALESCE(SUM(output_bytes),0) AS output_bytes,
                  COALESCE(SUM(token_count),0) AS token_count,
                  COALESCE(AVG(latency_ms),0)::bigint AS average_latency_ms
             FROM usage_events WHERE client_id=$1 AND created_at>=NOW()-($2 * INTERVAL '1 day')
            GROUP BY COALESCE(operation,event_type) ORDER BY requests DESC""", client_id, days,
    )
    totals = {"requests": 0, "units": 0, "failures": 0, "input_bytes": 0, "output_bytes": 0, "token_count": 0}
    data = []
    for row in rows:
        item = dict(row)
        data.append(item)
        for key in totals:
            totals[key] += int(item.get(key) or 0)
    return {"days": days, "totals": totals, "operations": data}
