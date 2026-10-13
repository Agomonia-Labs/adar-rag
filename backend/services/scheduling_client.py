"""services/scheduling_client.py — thin server-to-server client for
adar-core's scheduling admin API (the same Firestore-backed booking engine
that powers ADAR Front Desk), used to back ADAR Eats' dine-in table
reservations.

Modeling: a restaurant maps 1:1 to a scheduling "practice"; each physical
table maps to one "provider" (the engine has no native seats/capacity
concept -- one provider == exactly one concurrent booking -- so each table
is its own provider rather than a party-size "tier" with a count; tiers are
just a display grouping on our side, by table name/role).

Auth: we hold ONE platform-admin credential for adar-core (role="admin" has
unrestricted access to every practice there -- see adar-core's
api/routes/auth.py) rather than minting a practice_staff account per
restaurant. Admin tokens never expire there, so we cache one in memory for
this process and only re-mint on a 401.
"""
from __future__ import annotations

import logging
import os
from datetime import date as date_cls
from datetime import datetime, timedelta
from datetime import time as time_cls
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("docintel.scheduling_client")

SCHEDULING_BASE_URL = os.getenv("SCHEDULING_ADMIN_BASE_URL", "").rstrip("/")
SCHEDULING_ADMIN_EMAIL = os.getenv("SCHEDULING_ADMIN_EMAIL", "")
SCHEDULING_ADMIN_PASSWORD = os.getenv("SCHEDULING_ADMIN_PASSWORD", "")

_cached_token: Optional[str] = None


class SchedulingClientError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


def configured() -> bool:
    """False until SCHEDULING_ADMIN_* env vars are set (e.g. in a dev
    environment that hasn't been given adar-core credentials yet) -- callers
    should surface a clean 503 rather than a confusing connection error."""
    return bool(SCHEDULING_BASE_URL and SCHEDULING_ADMIN_EMAIL and SCHEDULING_ADMIN_PASSWORD)


async def _mint_token() -> str:
    global _cached_token
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            f"{SCHEDULING_BASE_URL}/api/auth/login",
            json={"email": SCHEDULING_ADMIN_EMAIL, "password": SCHEDULING_ADMIN_PASSWORD},
        )
    if resp.status_code >= 400:
        raise SchedulingClientError(resp.status_code, f"admin login failed: {resp.text[:300]}")
    token = (resp.json() or {}).get("access_token")
    if not token:
        raise SchedulingClientError(500, "admin login returned no access_token")
    _cached_token = token
    return token


async def _request(
    method: str, path: str, *, json: Any = None, params: Optional[dict] = None, _retry: bool = True,
) -> Any:
    if not configured():
        raise SchedulingClientError(503, "Dine-in reservations are not configured on this server yet")
    global _cached_token
    token = _cached_token or await _mint_token()
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.request(
            method, f"{SCHEDULING_BASE_URL}{path}",
            json=json, params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code == 401 and _retry:
        # Cached token was rejected (e.g. ADMIN_PASSWORD rotated) -- re-mint
        # once and retry, rather than failing every request until restart.
        _cached_token = None
        return await _request(method, path, json=json, params=params, _retry=False)
    if resp.status_code >= 400:
        log.warning("scheduling admin API %s %s -> %s: %s", method, path, resp.status_code, resp.text[:300])
        raise SchedulingClientError(resp.status_code, resp.text[:500])
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


# ── High-level helpers -- one per adar-core admin route we actually use ────

async def create_practice(
    *, name: str, timezone: str = "UTC", lead_time_minutes: int = 120,
    max_advance_days: int = 7, notification_email: str = "",
    slot_step_minutes: int = 15,
) -> dict:
    return await _request("POST", "/admin/scheduling/practices", json={
        "name": name, "timezone": timezone, "lead_time_minutes": lead_time_minutes,
        "max_advance_days": max_advance_days, "notification_email": notification_email,
        "slot_step_minutes": slot_step_minutes,
    })


async def update_practice(practice_id: str, **fields: Any) -> dict:
    return await _request("PATCH", f"/admin/scheduling/practices/{practice_id}", json=fields)


async def get_practice(practice_id: str) -> dict:
    return await _request("GET", f"/admin/scheduling/practices/{practice_id}")


async def list_providers(practice_id: str) -> list[dict]:
    data = await _request("GET", f"/admin/scheduling/practices/{practice_id}/providers")
    return data.get("providers", [])


async def create_provider(
    practice_id: str, *, name: str, role: str = "",
    appointment_type_ids: Optional[list[str]] = None, working_hours: Optional[list[dict]] = None,
) -> dict:
    return await _request("POST", f"/admin/scheduling/practices/{practice_id}/providers", json={
        "name": name, "role": role,
        "appointment_type_ids": appointment_type_ids or [],
        "working_hours": working_hours or [],
    })


async def update_provider(provider_id: str, **fields: Any) -> dict:
    return await _request("PATCH", f"/admin/scheduling/providers/{provider_id}", json=fields)


async def list_appointment_types(practice_id: str) -> list[dict]:
    data = await _request("GET", f"/admin/scheduling/practices/{practice_id}/appointment-types")
    return data.get("appointment_types", [])


async def create_appointment_type(
    practice_id: str, *, name: str, duration_minutes: int = 90,
    buffer_minutes: int = 15, description: str = "",
) -> dict:
    return await _request("POST", f"/admin/scheduling/practices/{practice_id}/appointment-types", json={
        "name": name, "duration_minutes": duration_minutes,
        "buffer_minutes": buffer_minutes, "description": description,
    })


async def update_appointment_type(type_id: str, **fields: Any) -> dict:
    return await _request("PATCH", f"/admin/scheduling/appointment-types/{type_id}", json=fields)


async def list_bookings(
    practice_id: str, *, start: str, end: str, provider_id: str = "", status: str = "",
) -> list[dict]:
    params: dict[str, str] = {"start": start, "end": end}
    if provider_id:
        params["provider_id"] = provider_id
    if status:
        params["status"] = status
    data = await _request("GET", f"/admin/scheduling/practices/{practice_id}/bookings", params=params)
    return data.get("bookings", [])


async def create_booking(
    practice_id: str, *, provider_id: str, appointment_type_id: str, start_time: str,
    caller_name: str, caller_phone: str = "", caller_email: str = "", reason: str = "",
) -> dict:
    """Immediate confirm, no hold phase -- adar-core's REST surface doesn't
    expose the chat/voice tools' two-step hold_slot/confirm_booking (those
    are ADK agent tools, not HTTP endpoints); this admin "manual booking"
    route is the only REST primitive that actually creates a confirmed
    appointment, and it has its own transactional overlap re-check server
    side, so a race with another booking still fails safely."""
    return await _request("POST", f"/admin/scheduling/practices/{practice_id}/bookings", json={
        "provider_id": provider_id, "appointment_type_id": appointment_type_id, "start_time": start_time,
        "caller_name": caller_name, "caller_phone": caller_phone, "caller_email": caller_email, "reason": reason,
    })


async def cancel_booking(appointment_id: str, *, reason: str = "") -> dict:
    """DELETE /admin/scheduling/appointments/{id} -- adar-core's own
    cancel_booking already sets the Firestore appointment's status to
    "cancelled" AND sends the caller-facing cancellation email
    (src.adar.notify.send_appointment_cancelled_email) whenever the
    original booking had a caller_email, mirroring how create_booking
    already sends its own confirmation email. Callers in adar-rag should
    rely on that for the customer leg of a customer-initiated cancel, and
    only add their own notification for whichever party did NOT initiate
    the action (see _notify_dine_in_cancellation in routes/restaurant.py)."""
    params = {"reason": reason} if reason else None
    return await _request("DELETE", f"/admin/scheduling/appointments/{appointment_id}", params=params)


def compute_table_slots(
    *, working_hours: list[dict], busy_intervals: list[tuple[datetime, datetime]],
    target_date: date_cls, duration_minutes: int, buffer_minutes: int,
    now: datetime, lead_time_minutes: int, tz: ZoneInfo, slot_step_minutes: int = 30,
) -> list[datetime]:
    """A simplified, single-day version of adar-core's own
    compute_open_slots (domains/scheduling/tools/availability_tools.py) --
    duplicated here rather than imported, since adar-rag and adar-core are
    separate services/repos with no shared Python package. This is only
    used to render an availability PREVIEW for the Eats booking UI; the
    authoritative, race-safe check is adar-core's own transactional overlap
    check inside create_booking (via create_booking() above) -- a slot this
    function offers can still lose a race to another customer, same as any
    other booking UI, and create_booking will reject it safely if so."""
    weekday = target_date.weekday()
    slots: list[datetime] = []
    for block in working_hours:
        if int(block.get("weekday", -1)) != weekday:
            continue
        try:
            start_h, start_m = (int(x) for x in str(block["start"]).split(":"))
            end_h, end_m = (int(x) for x in str(block["end"]).split(":"))
        except Exception:
            continue
        cursor = datetime.combine(target_date, time_cls(start_h, start_m), tzinfo=tz)
        block_end = datetime.combine(target_date, time_cls(end_h, end_m), tzinfo=tz)
        earliest = now + timedelta(minutes=lead_time_minutes)
        while cursor + timedelta(minutes=duration_minutes) <= block_end:
            if cursor < earliest:
                cursor += timedelta(minutes=slot_step_minutes)
                continue
            padded_start = cursor - timedelta(minutes=buffer_minutes)
            padded_end = cursor + timedelta(minutes=duration_minutes) + timedelta(minutes=buffer_minutes)
            if not any(padded_start < b_end and b_start < padded_end for b_start, b_end in busy_intervals):
                slots.append(cursor)
            cursor += timedelta(minutes=slot_step_minutes)
    return slots
