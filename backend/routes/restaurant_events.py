from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from auth.dependencies import AdminUser, CurrentUser, OptionalUser
from database.connection import get_db
from services.audit import audit, ip_from, ua_from
import services.storage as gcs
# Reused rather than re-implemented: these already encode the exact
# order/items/events response shape (Decimal/datetime cleanup via _clean)
# that the existing submit/checkout/owner-dashboard endpoints and the
# mobile app's cart UI all expect. An event order is still just a row in
# restaurant_orders/restaurant_order_items (tagged with event_id/stall_id),
# so submit_restaurant_order and create_restaurant_order_checkout in
# routes/restaurant.py work on it completely unmodified -- this draft
# endpoint is the only new code the event-ordering path needs.
from routes.restaurant import (
    MAX_RESTAURANT_IMAGE_BYTES,
    RESTAURANT_IMAGE_EXTENSIONS,
    SUPPORTED_RESTAURANT_IMAGE_TYPES,
    _add_order_event,
    _fetch_order_response,
    _resolve_menu_image_url,
)

log = logging.getLogger("docintel.restaurant.events.route")

router = APIRouter()


# ── Ownership / visibility helpers ──────────────────────────────────────────
# Mirrors routes.restaurant's _restaurant_catalog_manage_sql (duplicated
# rather than imported to keep this module decoupled from that ~6k-line
# file): a restaurant is manageable by its registered contact email matching
# the caller's login email, or by the caller being an 'owner' workspace
# member of the restaurant's workspace.
def _restaurant_manage_sql(alias: str, user_param: str = "$1", email_param: str = "$2") -> str:
    return (
        f"((COALESCE({alias}.email, '') <> '' AND COALESCE({email_param}::text, '') <> '' "
        f"AND LOWER({alias}.email)=LOWER({email_param}::text)) OR EXISTS ("
        f"SELECT 1 FROM workspace_members wm WHERE wm.workspace_id={alias}.workspace_id "
        f"AND wm.user_id={user_param}::uuid AND wm.role='owner'"
        f"))"
    )


async def _restaurant_for_owner(db, restaurant_id: str, current_user: dict) -> Any:
    user_id = str(current_user["id"])
    user_email = str(current_user.get("email") or "")
    row = await db.fetchrow(
        f"SELECT r.* FROM restaurants r WHERE r.id=$1 AND {_restaurant_manage_sql('r', '$2', '$3')}",
        restaurant_id, user_id, user_email,
    )
    if not row:
        raise HTTPException(403, "Only a matching restaurant email or workspace owner can manage this restaurant's events")
    return row


async def _stall_for_owner(db, restaurant_id: str, stall_id: str, current_user: dict) -> tuple[Any, Any]:
    restaurant_row = await _restaurant_for_owner(db, restaurant_id, current_user)
    stall_row = await db.fetchrow(
        "SELECT * FROM restaurant_event_stalls WHERE id=$1 AND restaurant_id=$2",
        stall_id, restaurant_id,
    )
    if not stall_row:
        raise HTTPException(404, "Stall not found")
    return restaurant_row, stall_row


def _parse_dt(value: str) -> datetime:
    """asyncpg binds a timestamptz parameter by Python type, not by the
    SQL-side ::timestamptz cast -- a raw ISO string crashes with
    asyncpg.exceptions.DataError ("expected a datetime.date or
    datetime.datetime instance, got 'str'") even though the cast makes the
    query look like it should coerce it. EventCreateRequest/EventUpdateRequest
    deliberately keep start_at/end_at as plain strings (what the frontend's
    <input type="datetime-local"> sends, e.g. "2026-11-14T15:27", with no
    seconds or offset) -- parse to a real datetime here, once, right before
    it's bound as a query argument."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"Invalid date/time: {value!r} (expected ISO 8601, e.g. 2026-11-14T15:27)")


def _require_event_open_for_edits(event_row: Any) -> None:
    if event_row["status"] == "closed":
        raise HTTPException(409, "This event is closed -- stall and menu assignments are frozen")


# ── Request models ───────────────────────────────────────────────────────────
class EventCreateRequest(BaseModel):
    name: str
    venue: str = ""
    description: str = ""
    start_at: str  # ISO 8601
    end_at: str    # ISO 8601
    banner_gcs_path: Optional[str] = None


class EventUpdateRequest(BaseModel):
    name: Optional[str] = None
    venue: Optional[str] = None
    description: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    banner_gcs_path: Optional[str] = None


class StallRejectRequest(BaseModel):
    reason: str = ""


class StallApproveRequest(BaseModel):
    stall_number: str = ""
    location_zone: Optional[str] = None  # admin's final placement; omit to keep the owner's preferred zone


class StallCreateRequest(BaseModel):
    event_id: str
    stall_name: str = ""  # blank -> defaults to "<Restaurant Name>_<Event Name>"
    location_zone: str = ""
    space_power_needs: str = ""


class StallUpdateRequest(BaseModel):
    stall_name: Optional[str] = None
    location_zone: Optional[str] = None
    space_power_needs: Optional[str] = None
    hours: Optional[dict] = None
    accepting_orders: Optional[bool] = None  # owner pauses/resumes new orders during a rush


class EventMenuItemRequest(BaseModel):
    menu_item_id: Optional[str] = None  # link to an existing restaurant menu item...
    item_name: Optional[str] = None     # ...or a net-new, event-only item (name+price required)
    category: str = ""
    description: str = ""
    price: Optional[float] = None       # required for a net-new item; optional price override when linked
    currency: str = "USD"
    image_gcs_path: Optional[str] = None
    availability: str = "available"


class EventMenuItemUpdateRequest(BaseModel):
    item_name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    availability: Optional[str] = None
    image_gcs_path: Optional[str] = None


class EventOrderItemRequest(BaseModel):
    event_menu_item_id: str
    quantity: int = 1
    instructions: str = ""


class EventOrderDraftRequest(BaseModel):
    items: list[EventOrderItemRequest]
    customer_name: str = ""
    customer_phone: str = ""
    customer_email: str = ""
    pickup_time_request: str = ""
    special_instructions: str = ""


# ── Row shaping ──────────────────────────────────────────────────────────────
def _event_row(row: Any, *, full: bool = True) -> dict[str, Any]:
    out = {
        "id": str(row["id"]),
        "name": row["name"],
        "venue": row["venue"],
        "start_at": row["start_at"].isoformat() if row["start_at"] else None,
        "end_at": row["end_at"].isoformat() if row["end_at"] else None,
        "status": row["status"],
        "banner_gcs_path": row.get("banner_gcs_path"),
    }
    if full:
        out.update({
            "description": row["description"],
            "published_at": row["published_at"].isoformat() if row.get("published_at") else None,
            "closed_at": row["closed_at"].isoformat() if row.get("closed_at") else None,
            "reopened_at": row["reopened_at"].isoformat() if row.get("reopened_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })
    return out


def _stall_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "event_id": str(row["event_id"]),
        "restaurant_id": str(row["restaurant_id"]),
        "stall_name": row["stall_name"],
        "stall_number": row["stall_number"],
        "location_zone": row["location_zone"],
        "space_power_needs": row["space_power_needs"],
        "hours": row["hours"],
        "status": row["status"],
        "accepting_orders": row["accepting_orders"],
        "rejection_reason": row["rejection_reason"],
        "requested_at": row["requested_at"].isoformat() if row.get("requested_at") else None,
        "approved_at": row["approved_at"].isoformat() if row.get("approved_at") else None,
    }


async def _menu_item_row(row: Any, inherited_image_gcs_path: str | None = None) -> dict[str, Any]:
    """`image_gcs_path` stays the item's OWN photo (None unless the owner
    uploaded/overrode one directly on this event item). `image_url` is what
    the UI actually renders -- the item's own photo if it has one, else the
    linked regular-menu-item's photo (same requirement as the public stall
    menu endpoint below), signed via the same resolver regular menu photos
    use."""
    own_path = row["image_gcs_path"]
    effective_path = own_path or inherited_image_gcs_path
    return {
        "id": str(row["id"]),
        "stall_id": str(row["stall_id"]),
        "menu_item_id": str(row["menu_item_id"]) if row.get("menu_item_id") else None,
        "item_name": row["item_name"],
        "category": row["category"],
        "description": row["description"],
        "price": float(row["price"]) if row["price"] is not None else None,
        "currency": row["currency"],
        "image_gcs_path": own_path,
        "image_url": await _resolve_menu_image_url(effective_path),
        "has_inherited_photo": bool(not own_path and inherited_image_gcs_path),
        "availability": row["availability"],
    }


# ── Admin: event CRUD + lifecycle ───────────────────────────────────────────
@router.post("/events")
async def create_event(body: EventCreateRequest, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        """
        INSERT INTO restaurant_events (name, venue, description, start_at, end_at, banner_gcs_path, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        body.name, body.venue, body.description, _parse_dt(body.start_at), _parse_dt(body.end_at),
        body.banner_gcs_path, str(current_user["id"]),
    )
    return _event_row(row)


@router.get("/events")
async def list_events(current_user: AdminUser, status: Optional[str] = Query(None), db=Depends(get_db)):
    if status:
        rows = await db.fetch(
            "SELECT * FROM restaurant_events WHERE status=$1 ORDER BY start_at DESC", status,
        )
    else:
        rows = await db.fetch("SELECT * FROM restaurant_events ORDER BY start_at DESC")
    return {"events": [_event_row(r) for r in rows]}


@router.get("/events/available")
async def list_available_events(current_user: CurrentUser, db=Depends(get_db)):
    """Events currently accepting stall requests -- what a restaurant owner
    picks from in onboarding step 1."""
    rows = await db.fetch(
        "SELECT * FROM restaurant_events WHERE status='onboarding' ORDER BY start_at ASC",
    )
    return {"events": [_event_row(r, full=False) for r in rows]}


@router.get("/events/live")
async def list_live_events(current_user: OptionalUser, db=Depends(get_db)):
    """Customer-facing discovery -- guest-accessible like the rest of ADAR
    Eats' browsing surfaces."""
    rows = await db.fetch(
        "SELECT * FROM restaurant_events WHERE status='live' ORDER BY start_at ASC",
    )
    return {"events": [_event_row(r, full=False) for r in rows]}


@router.get("/events/{event_id}")
async def get_event(event_id: str, current_user: OptionalUser, db=Depends(get_db)):
    row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    is_admin = bool(current_user and current_user.get("role") == "admin")
    if is_admin:
        stall_rows = await db.fetch("SELECT * FROM restaurant_event_stalls WHERE event_id=$1", event_id)
        return {**_event_row(row), "stalls": [_stall_row(s) for s in stall_rows]}
    if row["status"] == "closed":
        # Direct/deep links to a closed event show "ended" instead of the
        # live page -- the receipt stays in the customer's own order
        # history, but the event page itself is gone.
        return {"id": str(row["id"]), "name": row["name"], "status": "closed", "ended": True,
                "message": "This event has ended."}
    if row["status"] in ("draft", "onboarding"):
        raise HTTPException(404, "Event not found")
    stall_rows = await db.fetch(
        "SELECT * FROM restaurant_event_stalls WHERE event_id=$1 AND status='active'", event_id,
    )
    return {**_event_row(row), "stalls": [_stall_row(s) for s in stall_rows]}


@router.put("/events/{event_id}")
async def update_event(event_id: str, body: EventUpdateRequest, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    if row["status"] == "closed":
        raise HTTPException(409, "This event is closed and can no longer be edited")
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return _event_row(row)
    set_clauses = []
    values: list[Any] = []
    for i, (key, value) in enumerate(fields.items(), start=1):
        if key in ("start_at", "end_at") and value is not None:
            value = _parse_dt(value)
        set_clauses.append(f"{key}=${i}")
        values.append(value)
    values.append(event_id)
    updated = await db.fetchrow(
        f"UPDATE restaurant_events SET {', '.join(set_clauses)}, updated_at=NOW() WHERE id=${len(values)} RETURNING *",
        *values,
    )
    return _event_row(updated)


@router.post("/events/{event_id}/open-onboarding")
async def open_event_onboarding(event_id: str, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    if row["status"] != "draft":
        raise HTTPException(409, f"Event must be in draft to open onboarding (currently {row['status']})")
    updated = await db.fetchrow(
        "UPDATE restaurant_events SET status='onboarding', updated_at=NOW() WHERE id=$1 RETURNING *", event_id,
    )
    return _event_row(updated)


@router.post("/events/{event_id}/publish")
async def publish_event(event_id: str, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    if row["status"] != "onboarding":
        raise HTTPException(409, f"Event must be in onboarding to publish (currently {row['status']})")
    async with db.transaction():
        updated = await db.fetchrow(
            "UPDATE restaurant_events SET status='live', published_at=NOW(), updated_at=NOW() WHERE id=$1 RETURNING *",
            event_id,
        )
        # Approved stalls go live with the event; anything still just
        # "requested" (never reviewed) stays that way -- publishing an
        # event doesn't silently approve pending stall requests.
        await db.execute(
            "UPDATE restaurant_event_stalls SET status='active', updated_at=NOW() WHERE event_id=$1 AND status='approved'",
            event_id,
        )
    return _event_row(updated)


@router.post("/events/{event_id}/close")
async def close_event(event_id: str, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    if row["status"] != "live":
        raise HTTPException(409, f"Event must be live to close (currently {row['status']})")
    async with db.transaction():
        updated = await db.fetchrow(
            "UPDATE restaurant_events SET status='closed', closed_at=NOW(), updated_at=NOW() WHERE id=$1 RETURNING *",
            event_id,
        )
        await db.execute(
            "UPDATE restaurant_event_stalls SET status='closed', updated_at=NOW() WHERE event_id=$1 AND status='active'",
            event_id,
        )
    return _event_row(updated)


@router.post("/events/{event_id}/reopen")
async def reopen_event(event_id: str, current_user: AdminUser, db=Depends(get_db)):
    """Confirmed exception to the lifecycle: Closed is not permanent -- an
    admin can reopen (e.g. for an annual recurring event), lifting the
    freeze on stall/menu assignments and moving it back to Live."""
    row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not row:
        raise HTTPException(404, "Event not found")
    if row["status"] != "closed":
        raise HTTPException(409, f"Event must be closed to reopen (currently {row['status']})")
    async with db.transaction():
        updated = await db.fetchrow(
            "UPDATE restaurant_events SET status='live', reopened_at=NOW(), updated_at=NOW() WHERE id=$1 RETURNING *",
            event_id,
        )
        await db.execute(
            "UPDATE restaurant_event_stalls SET status='active', updated_at=NOW() WHERE event_id=$1 AND status='closed'",
            event_id,
        )
    return _event_row(updated)


# ── Admin: stall approval queue ──────────────────────────────────────────────
@router.get("/events/{event_id}/stalls")
async def list_event_stalls_admin(event_id: str, current_user: AdminUser, db=Depends(get_db)):
    rows = await db.fetch(
        """
        SELECT s.*, r.name AS restaurant_name
        FROM restaurant_event_stalls s JOIN restaurants r ON r.id = s.restaurant_id
        WHERE s.event_id=$1 ORDER BY s.requested_at ASC
        """,
        event_id,
    )
    return {"stalls": [{**_stall_row(r), "restaurant_name": r["restaurant_name"]} for r in rows]}


@router.post("/events/{event_id}/stalls/{stall_id}/approve")
async def approve_stall(event_id: str, stall_id: str, body: StallApproveRequest, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT * FROM restaurant_event_stalls WHERE id=$1 AND event_id=$2", stall_id, event_id,
    )
    if not row:
        raise HTTPException(404, "Stall request not found")
    if row["status"] != "requested":
        raise HTTPException(409, f"Stall request must be pending to approve (currently {row['status']})")
    zone = body.location_zone if body.location_zone is not None else row["location_zone"]
    updated = await db.fetchrow(
        """
        UPDATE restaurant_event_stalls
        SET status='approved', stall_number=$3, location_zone=$4, approved_at=NOW(), updated_at=NOW()
        WHERE id=$1 AND event_id=$2
        RETURNING *
        """,
        stall_id, event_id, body.stall_number, zone,
    )
    return _stall_row(updated)


@router.post("/events/{event_id}/stalls/{stall_id}/reject")
async def reject_stall(event_id: str, stall_id: str, body: StallRejectRequest, current_user: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        "SELECT * FROM restaurant_event_stalls WHERE id=$1 AND event_id=$2", stall_id, event_id,
    )
    if not row:
        raise HTTPException(404, "Stall request not found")
    if row["status"] != "requested":
        raise HTTPException(409, f"Stall request must be pending to reject (currently {row['status']})")
    updated = await db.fetchrow(
        """
        UPDATE restaurant_event_stalls
        SET status='rejected', rejection_reason=$3, rejected_at=NOW(), updated_at=NOW()
        WHERE id=$1 AND event_id=$2
        RETURNING *
        """,
        stall_id, event_id, body.reason,
    )
    return _stall_row(updated)


# ── Owner: stall onboarding ──────────────────────────────────────────────────
@router.post("/restaurants/{restaurant_id}/stalls")
async def request_stall(restaurant_id: str, body: StallCreateRequest, current_user: CurrentUser, db=Depends(get_db)):
    restaurant_row = await _restaurant_for_owner(db, restaurant_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", body.event_id)
    if not event_row:
        raise HTTPException(404, "Event not found")
    if event_row["status"] != "onboarding":
        raise HTTPException(409, f"Event isn't currently accepting stall requests (status={event_row['status']})")
    stall_name = body.stall_name.strip() or f"{restaurant_row['name']}_{event_row['name']}"
    try:
        row = await db.fetchrow(
            """
            INSERT INTO restaurant_event_stalls (event_id, restaurant_id, stall_name, location_zone, space_power_needs)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            body.event_id, restaurant_id, stall_name, body.location_zone, body.space_power_needs,
        )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, "This restaurant already has a stall request for this event")
        raise
    return _stall_row(row)


@router.get("/restaurants/{restaurant_id}/stalls")
async def list_my_stalls(restaurant_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _restaurant_for_owner(db, restaurant_id, current_user)
    rows = await db.fetch(
        """
        SELECT s.*, e.name AS event_name, e.status AS event_status, e.start_at AS event_start_at
        FROM restaurant_event_stalls s JOIN restaurant_events e ON e.id = s.event_id
        WHERE s.restaurant_id=$1 ORDER BY e.start_at DESC
        """,
        restaurant_id,
    )
    return {"stalls": [
        {**_stall_row(r), "event_name": r["event_name"], "event_status": r["event_status"],
         "event_start_at": r["event_start_at"].isoformat() if r["event_start_at"] else None}
        for r in rows
    ]}


@router.get("/restaurants/{restaurant_id}/stalls/{stall_id}")
async def get_my_stall(restaurant_id: str, stall_id: str, current_user: CurrentUser, db=Depends(get_db)):
    _, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    menu_rows = await db.fetch(
        """
        SELECT em.*, mi.image_gcs_path AS linked_image_gcs_path
        FROM restaurant_event_menu_items em
        LEFT JOIN restaurant_menu_items mi ON mi.id = em.menu_item_id
        WHERE em.stall_id=$1 ORDER BY em.created_at ASC
        """,
        stall_id,
    )
    return {**_stall_row(stall_row), "menu_items": [await _menu_item_row(m, m["linked_image_gcs_path"]) for m in menu_rows]}


@router.put("/restaurants/{restaurant_id}/stalls/{stall_id}")
async def update_my_stall(restaurant_id: str, stall_id: str, body: StallUpdateRequest, current_user: CurrentUser, db=Depends(get_db)):
    restaurant_row, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", stall_row["event_id"])
    fields = body.model_dump(exclude_unset=True)
    # Pausing/resuming orders is independent of the admin-driven lifecycle
    # freeze below -- it's exactly the lever an owner needs mid-rush, while
    # the event is live, so it's not gated on "is this stall still open for
    # edits" the way renaming/relocating it is.
    pacing_only = set(fields.keys()) <= {"accepting_orders"}
    if not pacing_only:
        _require_event_open_for_edits(event_row)
        if stall_row["status"] in ("rejected",):
            raise HTTPException(409, "This stall request was rejected and can no longer be edited")
    if not fields:
        return _stall_row(stall_row)
    set_clauses = []
    values: list[Any] = []
    for i, (key, value) in enumerate(fields.items(), start=1):
        if key == "hours":
            set_clauses.append(f"hours=${i}::jsonb")
            values.append(json.dumps(value or {}))
        else:
            set_clauses.append(f"{key}=${i}")
            values.append(value)
    values.append(stall_id)
    updated = await db.fetchrow(
        f"UPDATE restaurant_event_stalls SET {', '.join(set_clauses)}, updated_at=NOW() WHERE id=${len(values)} RETURNING *",
        *values,
    )
    return _stall_row(updated)


# ── Owner: event menu items ──────────────────────────────────────────────────
@router.get("/restaurants/{restaurant_id}/stalls/{stall_id}/menu-items")
async def list_stall_menu_items(restaurant_id: str, stall_id: str, current_user: CurrentUser, db=Depends(get_db)):
    await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    rows = await db.fetch(
        """
        SELECT em.*, mi.image_gcs_path AS linked_image_gcs_path
        FROM restaurant_event_menu_items em
        LEFT JOIN restaurant_menu_items mi ON mi.id = em.menu_item_id
        WHERE em.stall_id=$1 ORDER BY em.created_at ASC
        """,
        stall_id,
    )
    return {"menu_items": [await _menu_item_row(r, r["linked_image_gcs_path"]) for r in rows]}


@router.post("/restaurants/{restaurant_id}/stalls/{stall_id}/menu-items")
async def create_stall_menu_item(restaurant_id: str, stall_id: str, body: EventMenuItemRequest, current_user: CurrentUser, db=Depends(get_db)):
    _, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", stall_row["event_id"])
    _require_event_open_for_edits(event_row)
    source = None
    if body.menu_item_id:
        source = await db.fetchrow(
            "SELECT * FROM restaurant_menu_items WHERE id=$1 AND restaurant_id=$2", body.menu_item_id, restaurant_id,
        )
        if not source:
            raise HTTPException(404, "Menu item not found on this restaurant's regular menu")
    elif not body.item_name or body.price is None:
        raise HTTPException(400, "A net-new event-only item needs both item_name and price")
    row = await db.fetchrow(
        """
        INSERT INTO restaurant_event_menu_items
            (stall_id, menu_item_id, item_name, category, description, price, currency, image_gcs_path, availability)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING *
        """,
        stall_id, body.menu_item_id, body.item_name, body.category, body.description,
        body.price, body.currency, body.image_gcs_path, body.availability,
    )
    # A new item linked to a regular menu item inherits that item's photo
    # (shown via image_url) until/unless the owner uploads an event-specific
    # one -- same "same photo if available" behavior as the public endpoint.
    inherited = source["image_gcs_path"] if source else None
    return await _menu_item_row(row, inherited)


@router.put("/restaurants/{restaurant_id}/stalls/{stall_id}/menu-items/{item_id}")
async def update_stall_menu_item(restaurant_id: str, stall_id: str, item_id: str, body: EventMenuItemUpdateRequest, current_user: CurrentUser, db=Depends(get_db)):
    _, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", stall_row["event_id"])
    _require_event_open_for_edits(event_row)
    existing = await db.fetchrow(
        "SELECT * FROM restaurant_event_menu_items WHERE id=$1 AND stall_id=$2", item_id, stall_id,
    )
    if not existing:
        raise HTTPException(404, "Event menu item not found")
    inherited = None
    if existing["menu_item_id"]:
        linked = await db.fetchrow(
            "SELECT image_gcs_path FROM restaurant_menu_items WHERE id=$1", existing["menu_item_id"],
        )
        inherited = linked["image_gcs_path"] if linked else None
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return await _menu_item_row(existing, inherited)
    if not existing["menu_item_id"] and ("item_name" in fields and fields["item_name"] is None):
        raise HTTPException(400, "A standalone event-only item must keep a name")
    set_clauses = []
    values: list[Any] = []
    for i, (key, value) in enumerate(fields.items(), start=1):
        set_clauses.append(f"{key}=${i}")
        values.append(value)
    values.append(item_id)
    updated = await db.fetchrow(
        f"UPDATE restaurant_event_menu_items SET {', '.join(set_clauses)}, updated_at=NOW() WHERE id=${len(values)} RETURNING *",
        *values,
    )
    return await _menu_item_row(updated, inherited)


@router.delete("/restaurants/{restaurant_id}/stalls/{stall_id}/menu-items/{item_id}")
async def delete_stall_menu_item(restaurant_id: str, stall_id: str, item_id: str, current_user: CurrentUser, db=Depends(get_db)):
    _, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", stall_row["event_id"])
    _require_event_open_for_edits(event_row)
    result = await db.execute(
        "DELETE FROM restaurant_event_menu_items WHERE id=$1 AND stall_id=$2", item_id, stall_id,
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Event menu item not found")
    return {"deleted": True}


# A stall menu item's OWN photo, upload/replace/remove. Mirrors
# routes.restaurant's menu-item photo endpoints exactly (same validation,
# size cap, bucket layout convention) -- an item linked to a regular menu
# item shows that item's photo (via image_url, computed above) until the
# owner uploads one here; uploading here overrides it for this event only,
# and removing it here falls back to the linked item's photo again rather
# than going photo-less.
@router.post("/restaurants/{restaurant_id}/stalls/{stall_id}/menu-items/{item_id}/photo")
async def upload_stall_menu_item_photo(
    restaurant_id: str,
    stall_id: str,
    item_id: str,
    current_user: CurrentUser,
    photo: UploadFile = File(...),
    db=Depends(get_db),
):
    restaurant_row, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", stall_row["event_id"])
    _require_event_open_for_edits(event_row)
    item_row = await db.fetchrow(
        "SELECT * FROM restaurant_event_menu_items WHERE id=$1 AND stall_id=$2", item_id, stall_id,
    )
    if not item_row:
        raise HTTPException(404, "Event menu item not found")

    content_type = (photo.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if content_type not in SUPPORTED_RESTAURANT_IMAGE_TYPES:
        raise HTTPException(400, f"Unsupported image format: {content_type}")
    data = await photo.read()
    if not data:
        raise HTTPException(400, "No image received")
    if len(data) > MAX_RESTAURANT_IMAGE_BYTES:
        raise HTTPException(413, f"Image is too large. Max {MAX_RESTAURANT_IMAGE_BYTES // 1024 // 1024} MB")

    extension = RESTAURANT_IMAGE_EXTENSIONS.get(content_type, "jpg")
    owner_id = str(restaurant_row["user_id"])
    image_gcs_path = (
        f"users/{owner_id}/restaurants/{restaurant_id}/events/{stall_row['event_id']}/"
        f"stalls/{stall_id}/menu/{item_id}/{uuid.uuid4().hex}.{extension}"
    )
    await gcs.upload_bytes(image_gcs_path, data, content_type)
    await db.execute(
        "UPDATE restaurant_event_menu_items SET image_gcs_path=$2, updated_at=NOW() WHERE id=$1",
        item_id, image_gcs_path,
    )
    await audit(
        db,
        user_id=str(current_user["id"]),
        action="restaurant_event_menu_item_photo_upload",
        resource_type="restaurant_event_menu_item",
        resource_id=item_id,
        metadata={"restaurant_id": restaurant_id, "stall_id": stall_id, "content_type": content_type, "size": len(data)},
    )
    return {"item_id": item_id, "image_url": await _resolve_menu_image_url(image_gcs_path)}


@router.delete("/restaurants/{restaurant_id}/stalls/{stall_id}/menu-items/{item_id}/photo")
async def delete_stall_menu_item_photo(
    restaurant_id: str, stall_id: str, item_id: str, current_user: CurrentUser, db=Depends(get_db),
):
    _, stall_row = await _stall_for_owner(db, restaurant_id, stall_id, current_user)
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", stall_row["event_id"])
    _require_event_open_for_edits(event_row)
    item_row = await db.fetchrow(
        "SELECT * FROM restaurant_event_menu_items WHERE id=$1 AND stall_id=$2", item_id, stall_id,
    )
    if not item_row:
        raise HTTPException(404, "Event menu item not found")
    await db.execute(
        "UPDATE restaurant_event_menu_items SET image_gcs_path=NULL, updated_at=NOW() WHERE id=$1", item_id,
    )
    inherited = None
    if item_row["menu_item_id"]:
        linked = await db.fetchrow(
            "SELECT image_gcs_path FROM restaurant_menu_items WHERE id=$1", item_row["menu_item_id"],
        )
        inherited = linked["image_gcs_path"] if linked else None
    return {"item_id": item_id, "image_url": await _resolve_menu_image_url(inherited)}


# ── Public: stall menu for ordering / the event-scoped Order Assistant ──────
@router.get("/events/{event_id}/stalls/{stall_id}/menu")
async def get_stall_menu_public(event_id: str, stall_id: str, current_user: OptionalUser, db=Depends(get_db)):
    """Guest-accessible, scoped to exactly this one stall in this one event
    -- never leaks a restaurant's regular menu or another event's stalls.
    Backs both the customer ordering screen and the event-scoped Order
    Assistant's menu context."""
    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not event_row or event_row["status"] != "live":
        raise HTTPException(404, "Event not found")
    stall_row = await db.fetchrow(
        "SELECT * FROM restaurant_event_stalls WHERE id=$1 AND event_id=$2 AND status='active'", stall_id, event_id,
    )
    if not stall_row:
        raise HTTPException(404, "Stall not found")
    rows = await db.fetch(
        """
        SELECT em.*,
               COALESCE(em.item_name, mi.item_name)     AS resolved_name,
               COALESCE(em.category, mi.category)       AS resolved_category,
               COALESCE(em.description, mi.description) AS resolved_description,
               COALESCE(em.price, mi.price)              AS resolved_price,
               COALESCE(em.image_gcs_path, mi.image_gcs_path) AS resolved_image
        FROM restaurant_event_menu_items em
        LEFT JOIN restaurant_menu_items mi ON mi.id = em.menu_item_id
        WHERE em.stall_id=$1 AND em.availability='available'
        ORDER BY em.created_at ASC
        """,
        stall_id,
    )
    menu_items = []
    for r in rows:
        menu_items.append({
            "id": str(r["id"]),
            "item_name": r["resolved_name"],
            "category": r["resolved_category"],
            "description": r["resolved_description"],
            "price": float(r["resolved_price"]) if r["resolved_price"] is not None else None,
            "currency": r["currency"],
            # Signed, directly renderable URL -- same resolver/convention
            # used for a restaurant's regular menu item photos -- already
            # coalesced above to the item's own photo if set, else the
            # linked regular-menu-item's photo.
            "image_url": await _resolve_menu_image_url(r["resolved_image"]),
        })
    return {"stall": _stall_row(stall_row), "menu_items": menu_items}


# ── Customer: placing an order at a stall ───────────────────────────────────
# Deliberately a NEW, separate draft endpoint rather than a branch inside
# routes.restaurant's create_restaurant_order_draft -- that function's
# existing logic (UUID parsing, ad-hoc chat items, workspace consistency
# checks) is intricate and already proven for regular carryout orders, and
# event ordering has a genuinely different item source (event menu items,
# not the regular catalog) and a different set of guards (event must be
# live, stall must be active AND accepting new orders). Once this endpoint
# creates the restaurant_orders/restaurant_order_items rows (tagged with
# event_id/stall_id), the existing submit/checkout/accept/reject/ready/
# complete endpoints and all notification plumbing in routes/restaurant.py
# work on them completely unchanged -- payment, same flow as today, per
# the confirmed requirement.
@router.post("/events/{event_id}/stalls/{stall_id}/order-draft")
async def create_event_order_draft(
    event_id: str,
    stall_id: str,
    body: EventOrderDraftRequest,
    request: Request,
    current_user: CurrentUser,
    db=Depends(get_db),
):
    user_id = str(current_user["id"])
    if not body.items:
        raise HTTPException(400, "Add at least one menu item to the cart")

    event_row = await db.fetchrow("SELECT * FROM restaurant_events WHERE id=$1", event_id)
    if not event_row or event_row["status"] != "live":
        raise HTTPException(404, "Event not found")
    stall_row = await db.fetchrow(
        "SELECT * FROM restaurant_event_stalls WHERE id=$1 AND event_id=$2", stall_id, event_id,
    )
    if not stall_row or stall_row["status"] != "active":
        raise HTTPException(404, "Stall not found")
    if not stall_row["accepting_orders"]:
        raise HTTPException(409, "This stall has paused new orders right now -- try again shortly")

    restaurant_row = await db.fetchrow("SELECT * FROM restaurants WHERE id=$1", stall_row["restaurant_id"])
    if not restaurant_row:
        raise HTTPException(404, "Restaurant not found")

    try:
        item_ids = [uuid.UUID(str(i.event_menu_item_id)) for i in body.items]
    except ValueError:
        raise HTTPException(400, "Invalid event menu item id")
    qty_by_item = {str(i.event_menu_item_id): max(1, int(i.quantity or 1)) for i in body.items}
    instructions_by_item = {str(i.event_menu_item_id): i.instructions or "" for i in body.items}

    menu_rows = await db.fetch(
        """
        SELECT em.*,
               COALESCE(em.item_name, mi.item_name)     AS resolved_name,
               COALESCE(em.category, mi.category)       AS resolved_category,
               COALESCE(em.price, mi.price)              AS resolved_price,
               mi.id                                     AS catalog_menu_item_id
        FROM restaurant_event_menu_items em
        LEFT JOIN restaurant_menu_items mi ON mi.id = em.menu_item_id
        WHERE em.stall_id=$1 AND em.id = ANY($2::uuid[]) AND em.availability='available'
        """,
        stall_id, item_ids,
    )
    found_ids = {str(row["id"]) for row in menu_rows}
    missing = {str(i) for i in item_ids} - found_ids
    if missing:
        raise HTTPException(404, "One or more event menu items were not found or are unavailable")

    subtotal = Decimal("0.00")
    currency = "USD"
    order_id = str(uuid.uuid4())
    stall_label = stall_row["stall_name"] or f"{restaurant_row['name']}_{event_row['name']}"

    async with db.transaction():
        for row in menu_rows:
            price = row["resolved_price"]
            qty = qty_by_item.get(str(row["id"]), 1)
            currency = row["currency"] or currency
            if price is not None:
                subtotal += Decimal(str(price)) * qty

        await db.execute(
            """
            INSERT INTO restaurant_orders
              (id, restaurant_id, customer_user_id, workspace_id, status, fulfillment_type,
               customer_name, customer_phone, customer_email, pickup_time_request,
               special_instructions, subtotal, currency, metadata, event_id, stall_id)
            VALUES ($1,$2,$3,$4,'draft','carryout',$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14)
            """,
            order_id,
            str(restaurant_row["id"]),
            user_id,
            restaurant_row["workspace_id"],
            body.customer_name or "",
            body.customer_phone or "",
            body.customer_email or current_user.get("email") or "",
            body.pickup_time_request or "",
            body.special_instructions or "",
            subtotal,
            currency,
            json.dumps({
                "source": "eats_event_order",
                "event_name": event_row["name"],
                "stall_name": stall_label,
            }),
            event_id,
            stall_id,
        )

        for row in menu_rows:
            event_menu_item_id = str(row["id"])
            qty = qty_by_item.get(event_menu_item_id, 1)
            unit_price = row["resolved_price"]
            line_total = Decimal("0.00") if unit_price is None else Decimal(str(unit_price)) * qty
            await db.execute(
                """
                INSERT INTO restaurant_order_items
                  (order_id, menu_item_id, event_menu_item_id, item_name, category, quantity,
                   unit_price, line_total, currency, instructions, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
                """,
                order_id,
                row["catalog_menu_item_id"],
                event_menu_item_id,
                row["resolved_name"],
                row["resolved_category"] or "",
                qty,
                unit_price,
                line_total,
                row["currency"] or currency,
                instructions_by_item.get(event_menu_item_id, ""),
                json.dumps({"restaurant_name": restaurant_row["name"], "event_name": event_row["name"], "stall_name": stall_label}),
            )
        await _add_order_event(
            db, order_id, user_id, "draft_created", None, "draft", "",
            {"item_count": len(menu_rows), "event_id": event_id, "stall_id": stall_id},
        )

    await audit(
        db,
        user_id=user_id,
        action="restaurant_event_order_draft",
        resource_type="restaurant_order",
        resource_id=order_id,
        metadata={"restaurant_id": str(restaurant_row["id"]), "event_id": event_id, "stall_id": stall_id, "subtotal": float(subtotal)},
        ip_address=ip_from(request),
        user_agent=ua_from(request),
    )
    return await _fetch_order_response(db, order_id, user_id, workspace_id=restaurant_row["workspace_id"])
