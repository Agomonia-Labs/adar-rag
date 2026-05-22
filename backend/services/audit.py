# services/audit.py — audit logging for compliance + debugging
from __future__ import annotations
import json, logging
from typing import Any

log = logging.getLogger("docintel.audit")


async def audit(
    db,
    *,
    user_id:       str | None,
    action:        str,
    resource_type: str | None  = None,
    resource_id:   str | None  = None,
    metadata:      dict | None = None,
    ip_address:    str | None  = None,
    user_agent:    str | None  = None,
) -> None:
    """Fire-and-forget audit event — never raises, never blocks the caller."""
    try:
        await db.execute(
            """INSERT INTO audit_log
                   (user_id, action, resource_type, resource_id,
                    metadata, ip_address, user_agent)
               VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7)""",
            user_id, action, resource_type, resource_id,
            json.dumps(metadata or {}), ip_address, user_agent,
        )
    except Exception as e:
        log.warning(f"Audit log failed ({action}): {e}")


def ip_from(request) -> str | None:
    """Extract real client IP from request (handles proxy headers)."""
    if not request:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", None)


def ua_from(request) -> str | None:
    return (request.headers.get("user-agent") or "")[:400] if request else None