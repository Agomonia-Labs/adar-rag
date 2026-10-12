# services/dine_in_reminder_scheduler.py — periodic reminder notifications
# for upcoming confirmed dine-in reservations (push + email to the guest).
#
# Mirrors services/observability_scheduler.py's shape: an in-process
# asyncio loop started once at app startup (see main.py), not a Cloud
# Scheduler-triggered endpoint -- this matches the existing pattern already
# used in this codebase (observability_scheduler, webhook_scheduler) rather
# than introducing a second scheduling paradigm.
#
# Reservations themselves live in adar-core's Firestore-backed scheduling
# engine, not in this service's Postgres, and that engine has no reminder
# concept of its own. dine_in_reservations (database/models.py) is a local
# cache written once at booking time (create_dine_in_reservation in
# routes/restaurant.py) specifically so this job never has to call back
# into adar-core, and reminder_sent_at makes each reservation eligible for
# exactly one reminder no matter how many cycles see it.
from __future__ import annotations

import asyncio
import logging
import os

from database.connection import get_pool
from services.notifications import send_dine_in_reminder_email
from services.push import send_expo_push

log = logging.getLogger("docintel.dine_in_reminder_scheduler")


async def run_dine_in_reminder_cycle(db) -> dict:
    lead_minutes = max(15, int(os.getenv("DINE_IN_REMINDER_LEAD_MINUTES", "120")))
    rows = await db.fetch(
        """
        SELECT dr.booking_id, dr.restaurant_id, dr.customer_user_id, dr.customer_email,
               dr.table_name, dr.party_size, dr.start_time, r.name AS restaurant_name
        FROM dine_in_reservations dr
        JOIN restaurants r ON r.id = dr.restaurant_id
        WHERE dr.reminder_sent_at IS NULL
          AND dr.status = 'confirmed'
          AND dr.start_time > NOW()
          AND dr.start_time <= NOW() + ($1 || ' minutes')::interval
        """,
        str(lead_minutes),
    )
    sent = 0
    failed = 0
    for row in rows:
        restaurant_id = str(row["restaurant_id"])
        restaurant_name = row.get("restaurant_name") or "the restaurant"
        table_name = row.get("table_name") or "your table"
        party_size = row.get("party_size") or 0
        start_time_formatted = row["start_time"].strftime("%A, %B %-d at %-I:%M %p")
        customer_email = (row.get("customer_email") or "").strip()
        customer_user_id = str(row["customer_user_id"]) if row.get("customer_user_id") else None

        push_ok = False
        if customer_user_id:
            try:
                tokens_rows = await db.fetch(
                    "SELECT push_token FROM user_push_tokens WHERE user_id=$1::uuid AND app='eats'",
                    customer_user_id,
                )
                tokens = [t["push_token"] for t in tokens_rows if t["push_token"]]
                if tokens:
                    accepted = await send_expo_push(
                        tokens,
                        title=restaurant_name,
                        body=f"Reminder: your table for {party_size} is coming up — {start_time_formatted}.",
                        data={"type": "dine_in_reminder", "restaurant_id": restaurant_id, "booking_id": row["booking_id"]},
                    )
                    push_ok = accepted > 0
            except Exception:
                log.warning("Dine-in reminder push failed booking_id=%s restaurant_id=%s", row["booking_id"], restaurant_id, exc_info=True)

        email_ok = False
        if customer_email:
            try:
                email_ok = await send_dine_in_reminder_email(
                    customer_email,
                    restaurant_name=restaurant_name,
                    restaurant_id=restaurant_id,
                    table_name=table_name,
                    party_size=party_size,
                    start_time_formatted=start_time_formatted,
                )
            except Exception:
                log.warning("Dine-in reminder email failed booking_id=%s restaurant_id=%s", row["booking_id"], restaurant_id, exc_info=True)

        # Mark it reminded regardless of whether push/email actually
        # succeeded -- a delivery failure (no token, email not configured,
        # Expo/SMTP error) should never cause the same reservation to be
        # retried every cycle forever; it already got its one best-effort
        # attempt on both channels, logged above for anyone investigating.
        try:
            await db.execute(
                "UPDATE dine_in_reservations SET reminder_sent_at = NOW() WHERE booking_id = $1",
                row["booking_id"],
            )
            if push_ok or email_ok:
                sent += 1
            else:
                failed += 1
        except Exception:
            log.warning("Failed to mark dine_in_reservations.reminder_sent_at booking_id=%s", row["booking_id"], exc_info=True)
            failed += 1

    return {"checked": len(rows), "reminders_sent": sent, "reminders_failed": failed}


async def dine_in_reminder_scheduler() -> None:
    interval = max(60, int(os.getenv("DINE_IN_REMINDER_INTERVAL_SECONDS", "600")))
    await asyncio.sleep(min(20, interval))
    while True:
        try:
            async with get_pool().acquire() as db:
                result = await run_dine_in_reminder_cycle(db)
                if result["checked"]:
                    log.info("Dine-in reminder cycle: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduled dine-in reminder cycle failed")
        await asyncio.sleep(interval)
