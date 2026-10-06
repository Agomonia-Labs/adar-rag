# services/push.py — mobile push notifications via Expo's push service.
#
# Expo's push API fronts both Apple (APNs) and Google (FCM) for any app
# built with expo-notifications, so one HTTP call here reaches iOS and
# Android devices alike -- no separate APNs/FCM credentials to manage on
# this backend. This is fire-and-forget delivery, matching the existing
# email path (send_restaurant_order_email in routes/restaurant.py): a
# push failure is logged and swallowed, never raised, so a notification
# problem can never break the order workflow that triggered it.
from __future__ import annotations
import logging
from typing import Any

import httpx

log = logging.getLogger("docintel.push")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _is_expo_token(token: str) -> bool:
    return token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")


async def send_expo_push(
    tokens: list[str],
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> int:
    """Send one push notification to one or more Expo push tokens.
    Returns how many tokens Expo accepted the message for (0 on any
    failure -- never raises, callers don't need to wrap this in
    try/except themselves)."""
    valid = [t for t in dict.fromkeys(tokens) if t and _is_expo_token(t)]
    if not valid:
        return 0
    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "data": data or {},
            "sound": "default",
            "priority": "high",
        }
        for token in valid
    ]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            log.warning("Expo push send failed status=%s body=%s", resp.status_code, resp.text[:500])
            return 0
        payload = resp.json()
        tickets = payload.get("data") or []
        if isinstance(tickets, dict):
            tickets = [tickets]
        accepted = 0
        for ticket in tickets:
            if not isinstance(ticket, dict):
                continue
            if ticket.get("status") == "ok":
                accepted += 1
            else:
                log.warning("Expo push ticket error: %s", ticket.get("message") or ticket)
        return accepted
    except Exception as exc:
        log.warning("Expo push send raised: %s", exc)
        return 0
