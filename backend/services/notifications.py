# services/notifications.py — transactional email notifications
from __future__ import annotations
import logging
from typing import Any
from services.email import is_email_configured, send_email

log = logging.getLogger("docintel.notifications")


async def send_embed_complete(user_email: str, doc_name: str, chunk_count: int) -> None:
    """Notify user that document embedding has finished."""
    subject = f"✅ Embedding complete — {doc_name}"
    body = f"""Your document is ready for semantic search.

Document: {doc_name}
Chunks embedded: {chunk_count}

You can now open আদর DocIntel and start chatting with this document.

— আদর DocIntel
"""
    try:
        await send_email(to=user_email, subject=subject, body=body)
        log.info(f"Embed notification sent to {user_email} for {doc_name}")
    except Exception as e:
        log.warning(f"Embed notification failed for {user_email}: {e}")


async def send_verification_email(user_email: str, token: str, app_url: str) -> None:
    """Send email address verification link."""
    link = f"{app_url.rstrip('/')}/verify-email?token={token}"
    subject = "Verify your আদর DocIntel email address"
    body = f"""Welcome to আদর DocIntel!

Please verify your email address by clicking the link below:

{link}

This link expires in 24 hours. If you didn't create an account, you can ignore this email.

— আদর DocIntel
"""
    await send_email(to=user_email, subject=subject, body=body)


async def send_workspace_invite(
    invitee_email: str,
    workspace_name: str,
    inviter_name: str,
    role: str,
    app_url: str,
) -> None:
    """Notify user they've been added to a workspace."""
    subject = f"You've been invited to '{workspace_name}' on আদর DocIntel"
    body = f"""{inviter_name} has added you to the workspace "{workspace_name}" as {role}.

You can access this workspace by logging in to আদর DocIntel:

{app_url}

— আদর DocIntel
"""
    try:
        await send_email(to=invitee_email, subject=subject, body=body)
    except Exception as e:
        log.warning(f"Workspace invite notification failed for {invitee_email}: {e}")


async def send_restaurant_order_email(
    to_email: str,
    *,
    audience: str,
    restaurant_name: str,
    order_id: str,
    status: str,
    message: str,
    restaurant_id: str = "",
    customer_name: str = "",
    subtotal: str = "",
    order_items: list[dict[str, Any]] | None = None,
    app_url: str = "",
) -> bool:
    """Send restaurant carryout order email notifications."""
    if not is_email_configured():
        log.warning(
            "Restaurant order email not sent because GMAIL_USER/GMAIL_APP_PASSWORD are not configured order_id=%s to=%s",
            order_id,
            to_email,
        )
        return False
    subject_prefix = "New carryout order" if audience == "restaurant_owner" else "Carryout order update"
    subject = f"{subject_prefix} — {restaurant_name}"
    body = f"""{message}

Restaurant: {restaurant_name}
Restaurant ID: {restaurant_id or 'Not available'}
Order ID: {order_id}
Status: {status}
"""
    if customer_name:
        body += f"Customer: {customer_name}\n"
    if subtotal:
        body += f"Subtotal: {subtotal}\n"
    if order_items:
        body += "\nItemized order:\n"
        for item in order_items:
            name = str(item.get("item_name") or "Menu item").strip()
            category = str(item.get("category") or "").strip()
            quantity = int(item.get("quantity_ordered") or item.get("quantity") or 1)
            currency = str(item.get("currency") or "USD").strip()
            unit_price = item.get("unit_price")
            line_total = item.get("line_total")
            price_text = ""
            if unit_price is not None:
                try:
                    price_text = f" @ {currency} {float(unit_price):.2f}"
                except (TypeError, ValueError):
                    price_text = f" @ {unit_price}"
            total_text = ""
            if line_total is not None:
                try:
                    total_text = f" = {currency} {float(line_total):.2f}"
                except (TypeError, ValueError):
                    total_text = f" = {line_total}"
            category_text = f" [{category}]" if category else ""
            body += f"- {quantity} x {name}{category_text}{price_text}{total_text}\n"
            instructions = str(item.get("instructions") or "").strip()
            if instructions:
                body += f"  Notes: {instructions}\n"
    if app_url:
        body += f"\nOpen DocIntel:\n{app_url}\n"
    body += "\n— আদর DocIntel\n"
    try:
        await send_email(to=to_email, subject=subject, body=body)
        log.info(
            "Restaurant order email sent to %s restaurant_id=%s order_id=%s audience=%s",
            to_email,
            restaurant_id,
            order_id,
            audience,
        )
        return True
    except Exception as e:
        log.warning("Restaurant order email failed for %s order_id=%s: %s", to_email, order_id, e)
        return False
