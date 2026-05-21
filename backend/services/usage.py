# services/usage.py — usage metering + tiered limit enforcement
from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from fastapi import HTTPException

log = logging.getLogger("docintel.usage")

# ── Tier definitions ───────────────────────────────────────────────────────────
TIER_LIMITS: dict[str, dict] = {
    "free": {
        "max_documents":    20,
        "max_file_mb":      10,
        "max_queries_day":  50,
        "max_embeds_day":   10,
        "max_summaries_day":5,
        "label":            "Free",
    },
    "pro": {
        "max_documents":    500,
        "max_file_mb":      50,
        "max_queries_day":  500,
        "max_embeds_day":   100,
        "max_summaries_day":50,
        "label":            "Pro",
    },
    "enterprise": {
        "max_documents":    -1,    # -1 = unlimited
        "max_file_mb":      200,
        "max_queries_day":  -1,
        "max_embeds_day":   -1,
        "max_summaries_day":-1,
        "label":            "Enterprise",
    },
}

def _today():
    return datetime.now(timezone.utc).date()  # date object — asyncpg requires this, not a string


# ── Limit helpers ──────────────────────────────────────────────────────────────

async def get_user_limits(db, user_id: str) -> dict:
    """Return effective limits: tier defaults merged with any custom_limits overrides."""
    row = await db.fetchrow(
        "SELECT tier, custom_limits FROM users WHERE id=$1", user_id
    )
    tier   = (row["tier"] if row else None) or "free"
    limits = dict(TIER_LIMITS.get(tier, TIER_LIMITS["free"]))
    limits["tier"] = tier

    overrides = row["custom_limits"] if row else None
    if overrides:
        parsed = overrides if isinstance(overrides, dict) else json.loads(overrides)
        limits.update(parsed)

    return limits


async def check_document_limit(db, user_id: str) -> None:
    """Raise 403 if user is at their document limit."""
    limits  = await get_user_limits(db, user_id)
    max_doc = limits["max_documents"]
    if max_doc == -1:
        return   # unlimited

    count = await db.fetchval(
        "SELECT COUNT(*) FROM documents WHERE user_id=$1 AND status!='deleted'",
        user_id,
    )
    if count >= max_doc:
        tier = limits["tier"]
        raise HTTPException(
            403,
            f"Document limit reached ({max_doc} docs on {limits['label']} tier). "
            f"Upgrade your plan to upload more.",
        )


async def check_daily_limit(db, user_id: str, event_type: str, limit_key: str) -> None:
    """Raise 429 if user has hit their daily quota for an event type."""
    limits = await get_user_limits(db, user_id)
    max_   = limits.get(limit_key, -1)
    if max_ == -1:
        return   # unlimited

    used = await db.fetchval(
        """SELECT COALESCE(SUM(quantity), 0) FROM usage_events
           WHERE user_id=$1 AND event_type=$2 AND DATE(created_at AT TIME ZONE 'UTC')=$3""",
        user_id, event_type, _today(),
    )
    if used >= max_:
        raise HTTPException(
            429,
            f"Daily {event_type} limit reached ({max_} on {limits['label']} tier). "
            f"Upgrade your plan or try again tomorrow.",
        )


# ── Logging ───────────────────────────────────────────────────────────────────

async def log_event(
    db,
    user_id:    str,
    event_type: str,
    quantity:   int  = 1,
    metadata:   dict | None = None,
) -> None:
    """Fire-and-forget usage event. Swallows errors so nothing breaks if DB is slow."""
    try:
        await db.execute(
            """INSERT INTO usage_events (user_id, event_type, quantity, metadata)
               VALUES ($1, $2, $3, $4::jsonb)""",
            user_id, event_type, quantity, json.dumps(metadata or {}),
        )
    except Exception as e:
        log.warning(f"Usage log failed ({event_type}): {e}")


# ── Summary queries ────────────────────────────────────────────────────────────

async def get_my_usage(db, user_id: str) -> dict:
    """Full usage summary for a user — called by /api/usage/me."""
    today = _today()

    # Event totals + today's counts
    rows = await db.fetch(
        """SELECT
               event_type,
               SUM(quantity)                                                              AS total,
               SUM(CASE WHEN DATE(created_at AT TIME ZONE 'UTC') = $2
                        THEN quantity ELSE 0 END)                                        AS today
           FROM usage_events
           WHERE user_id = $1
           GROUP BY event_type""",
        user_id, today,
    )
    events = {
        r["event_type"]: {"total": int(r["total"]), "today": int(r["today"])}
        for r in rows
    }

    # Current doc count
    doc_count = await db.fetchval(
        "SELECT COUNT(*) FROM documents WHERE user_id=$1 AND status!='deleted'",
        user_id,
    )

    # Storage estimate (sum of file sizes from metadata)
    storage = await db.fetchval(
        """SELECT COALESCE(SUM((metadata->>'file_size')::bigint), 0)
           FROM usage_events
           WHERE user_id=$1
             AND event_type='upload'
             AND metadata->>'file_size' IS NOT NULL""",
        user_id,
    )

    limits = await get_user_limits(db, user_id)

    return {
        "tier":           limits["tier"],
        "tier_label":     limits.get("label", limits["tier"].title()),
        "limits":         limits,
        "document_count": int(doc_count),
        "storage_bytes":  int(storage or 0),
        "events":         events,
    }


async def get_all_usage(db) -> list[dict]:
    """Admin view: per-user usage rollup."""
    rows = await db.fetch(
        """SELECT
               u.id, u.email, u.tier,
               COUNT(DISTINCT d.id) FILTER (WHERE d.status != 'deleted') AS doc_count,
               COUNT(ue.id)                                               AS total_events,
               MAX(ue.created_at)                                         AS last_active
           FROM users u
           LEFT JOIN documents     d  ON d.user_id  = u.id
           LEFT JOIN usage_events  ue ON ue.user_id = u.id
           GROUP BY u.id, u.email, u.tier
           ORDER BY last_active DESC NULLS LAST"""
    )
    return [
        {
            "user_id":      str(r["id"]),
            "email":        r["email"],
            "tier":         r["tier"] or "free",
            "doc_count":    int(r["doc_count"] or 0),
            "total_events": int(r["total_events"] or 0),
            "last_active":  r["last_active"].isoformat() if r["last_active"] else None,
        }
        for r in rows
    ]