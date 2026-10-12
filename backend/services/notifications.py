# services/notifications.py — transactional email notifications
from __future__ import annotations
import logging
from typing import Any
from services.email import is_email_configured, send_email

log = logging.getLogger("docintel.notifications")


async def send_observability_alert(user_email: str, alert: dict[str, Any]) -> bool:
    """Notify an administrator when a new SLO violation opens."""
    if not is_email_configured():
        log.info("Observability alert email skipped because email is not configured")
        return False
    subject = f"DocIntel observability {alert.get('severity', 'warning')}: {alert.get('title', 'SLO violation')}"
    body = f"""ADAR DocIntel detected an observability threshold violation.

{alert.get('description') or 'An enabled service-level objective is outside its target.'}

Observed value: {alert.get('observed_value')}
Threshold: {alert.get('threshold_value')}
First seen: {alert.get('first_seen_at')}

Open Admin Dashboard > Observability to investigate and acknowledge the alert.

- ADAR DocIntel
"""
    try:
        await send_email(to=user_email, subject=subject, body=body)
        return True
    except Exception as exc:
        log.warning("Observability alert notification failed for %s: %s", user_email, exc)
        return False


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


async def send_video_processing_notification(
    user_email: str,
    *,
    doc_name: str,
    status: str,
    duration_seconds: float | int | None = None,
    segment_count: int = 0,
    chunk_count: int = 0,
    embed_status: str = "",
    error_message: str = "",
) -> bool:
    """Notify user when video processing completes or fails."""
    completed = status == "completed"
    subject = (
        f"Video intelligence ready — {doc_name}"
        if completed else
        f"Video intelligence failed — {doc_name}"
    )
    minutes = ""
    if duration_seconds:
        try:
            minutes = f"{float(duration_seconds) / 60:.1f} minutes"
        except (TypeError, ValueError):
            minutes = ""

    body = (
        "Your video is ready for DocIntel review and Q&A.\n\n"
        if completed else
        "DocIntel could not finish processing your video.\n\n"
    )
    body += f"Video: {doc_name}\n"
    if minutes:
        body += f"Duration: {minutes}\n"
    if completed:
        body += f"Timeline segments: {segment_count}\n"
        body += f"Generated chunks: {chunk_count}\n"
        body += f"Embedding status: {embed_status or 'not requested'}\n\n"
        if embed_status == "embedded":
            body += "You can now ask questions about this video in normal DocIntel Chat.\n"
        else:
            body += "Open Video Intelligence to review the timeline, then embed when ready for normal DocIntel Chat.\n"
    else:
        body += f"Error: {error_message or 'Processing failed'}\n\n"
        body += "Open DocIntel, check the video status, and rerun processing after correcting the issue.\n"

    body += "\n— আদর DocIntel\n"

    try:
        await send_email(to=user_email, subject=subject, body=body)
        log.info("Video processing notification sent to %s doc=%s status=%s", user_email, doc_name, status)
        return True
    except Exception as e:
        log.warning("Video processing notification failed for %s doc=%s status=%s: %s", user_email, doc_name, status, e)
        return False


async def send_call_processing_notification(
    user_email: str,
    *,
    call_name: str,
    status: str,
    segment_count: int = 0,
    error_message: str = "",
) -> bool:
    completed = status == "completed"
    subject = f"Conversation intelligence ready - {call_name}" if completed else f"Conversation processing failed - {call_name}"
    body = (
        f"Your recorded conversation has been processed into DocIntel.\n\n"
        f"Call: {call_name}\nTranscript segments: {segment_count}\n\n"
        "You can review the transcript and ask grounded questions from the DocIntel knowledgebase.\n"
        if completed else
        f"DocIntel could not process the recorded conversation.\n\nCall: {call_name}\nError: {error_message or 'Processing failed'}\n"
    )
    body += "\n- ADAR DocIntel\n"
    try:
        await send_email(to=user_email, subject=subject, body=body)
        return True
    except Exception as exc:
        log.warning("Call processing notification failed for %s: %s", user_email, exc)
        return False


async def send_learning_question_notification(
    user_email: str,
    *,
    course_title: str,
    question: str,
    action: str = "assigned",
) -> bool:
    """Notify a course participant when a human-review question needs attention or receives an answer."""
    if not user_email:
        return False
    answered = action == "answered"
    subject = f"Learning question {'answered' if answered else 'needs your review'} - {course_title}"
    body = (
        f"A reviewed response is available for a question in {course_title}.\n\n"
        if answered else
        f"A learner has routed a question to you in {course_title}.\n\n"
    )
    body += f"Question: {question}\n\nOpen DocIntel Learning Intelligence to {'review the answer' if answered else 'respond with human guidance'}.\n\n- ADAR DocIntel\n"
    try:
        await send_email(to=user_email, subject=subject, body=body)
        return True
    except Exception as exc:
        log.warning("Learning question notification failed for %s: %s", user_email, exc)
        return False


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


async def send_account_deleted_email(user_email: str) -> None:
    """Confirms a self-service account deletion (auth/router.py's
    delete_own_account) actually went through -- sent to the email address
    captured before the user row was deleted, since by the time this runs
    the account no longer exists to look it up again. Best-effort: a failed
    send here must never undo or block the deletion itself, so exceptions
    are caught and logged, same as send_workspace_invite above."""
    subject = "Your আদর DocIntel account has been cancelled"
    body = """Your আদর DocIntel account has been permanently deleted, along with your
personal documents, videos, and conversations, at your request.

If you didn't request this, or you believe this was a mistake, please
contact support right away -- this action cannot be reversed and a deleted
account cannot be restored.

— আদর DocIntel
"""
    try:
        await send_email(to=user_email, subject=subject, body=body)
    except Exception as e:
        log.warning(f"Account-deleted confirmation email failed for {user_email}: {e}")


async def send_finance_tax_packet_notification(
    user_email: str,
    *,
    action: str,
    run_id: str,
    client_name: str = "",
    tax_year: str = "",
    reviewer_name: str = "",
    notes: str = "",
) -> bool:
    """Notify reviewer when a finance/tax workflow packet is approved or withdrawn."""
    action_label = "approved and saved" if action == "approved" else "withdrawn and cleared"
    subject = f"DocIntel tax packet {action_label}"
    body = f"""Your DocIntel finance/tax packet was {action_label}.

Client: {client_name or 'Client'}
Tax year: {tax_year or 'Needs review'}
Workflow run ID: {run_id}
Reviewer: {reviewer_name or user_email}
"""
    if notes:
        body += f"Reviewer notes: {notes}\n"
    body += """
Open DocIntel to continue the tax submission workflow.

— আদর DocIntel
"""
    try:
        await send_email(to=user_email, subject=subject, body=body)
        log.info("Finance/tax packet notification sent to %s run_id=%s action=%s", user_email, run_id, action)
        return True
    except Exception as e:
        log.warning("Finance/tax packet notification failed for %s run_id=%s action=%s: %s", user_email, run_id, action, e)
        return False


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


async def send_dine_in_owner_email(
    to_email: str,
    *,
    restaurant_name: str,
    restaurant_id: str,
    table_name: str,
    party_size: int,
    start_time_formatted: str,
    caller_name: str = "",
    caller_phone: str = "",
    caller_email: str = "",
) -> bool:
    """New dine-in reservation notification for the restaurant owner --
    mirrors send_restaurant_order_email's shape/style. The owner already
    gets an in-app bell row and an Expo push for this event (see
    create_dine_in_reservation in routes/restaurant.py); this is the email
    leg of that same event, so all three channels fire together."""
    if not is_email_configured():
        log.warning(
            "Dine-in owner email not sent because GMAIL_USER/GMAIL_APP_PASSWORD are not configured restaurant_id=%s to=%s",
            restaurant_id,
            to_email,
        )
        return False
    subject = f"New dine-in reservation — {restaurant_name}"
    body = f"""A new dine-in reservation was just booked at {restaurant_name}.

Table: {table_name}
Party size: {party_size}
When: {start_time_formatted}
"""
    if caller_name:
        body += f"Guest: {caller_name}\n"
    if caller_phone:
        body += f"Phone: {caller_phone}\n"
    if caller_email:
        body += f"Email: {caller_email}\n"
    body += "\nOpen ADAR Eats to see it on your Dine-In bookings screen.\n"
    body += "\n— ADAR Eats\n"
    try:
        await send_email(to=to_email, subject=subject, body=body)
        log.info("Dine-in owner email sent to %s restaurant_id=%s", to_email, restaurant_id)
        return True
    except Exception as e:
        log.warning("Dine-in owner email failed for %s restaurant_id=%s: %s", to_email, restaurant_id, e)
        return False


async def send_dine_in_reminder_email(
    to_email: str,
    *,
    restaurant_name: str,
    restaurant_id: str,
    table_name: str,
    party_size: int,
    start_time_formatted: str,
) -> bool:
    """Reminder email sent some hours ahead of a confirmed dine-in
    reservation (see services/dine_in_reminder_scheduler.py). Written in
    ADAR Eats' own restaurant-facing voice rather than adar-core's reused,
    domain-neutral appointment-confirmation template (which renders a
    table as "Provider" and the reservation as a "Visit type") -- this is
    the one email in the dine-in flow that's purpose-written for a
    restaurant guest, not borrowed scheduling-engine copy."""
    if not is_email_configured():
        log.warning(
            "Dine-in reminder email not sent because GMAIL_USER/GMAIL_APP_PASSWORD are not configured restaurant_id=%s to=%s",
            restaurant_id,
            to_email,
        )
        return False
    subject = f"Reminder: your table at {restaurant_name}"
    body = f"""Just a reminder about your upcoming reservation.

Restaurant: {restaurant_name}
Table: {table_name}
Party size: {party_size}
When: {start_time_formatted}

See you soon!

— ADAR Eats
"""
    try:
        await send_email(to=to_email, subject=subject, body=body)
        log.info("Dine-in reminder email sent to %s restaurant_id=%s", to_email, restaurant_id)
        return True
    except Exception as e:
        log.warning("Dine-in reminder email failed for %s restaurant_id=%s: %s", to_email, restaurant_id, e)
        return False


async def send_dine_in_cancelled_email(
    to_email: str,
    *,
    audience: str,  # 'restaurant_owner' | 'customer'
    restaurant_name: str,
    restaurant_id: str,
    table_name: str,
    party_size: int,
    start_time_formatted: str,
    cancelled_by: str = "",  # 'restaurant_owner' | 'customer' | '' (unknown)
    reason: str = "",
    caller_name: str = "",
) -> bool:
    """Dine-in cancellation notification, audience-parameterized like
    send_restaurant_order_email. When the CUSTOMER initiated the cancel,
    adar-core's own cancel_booking already emails them directly
    (send_appointment_cancelled_email, fired whenever caller_email was set
    on the original booking) -- callers should only invoke this for the
    customer leg of an OWNER-initiated cancel, to avoid a duplicate email;
    the owner has no adar-core-side email of their own, so this is always
    used for the owner leg regardless of who initiated it."""
    if not is_email_configured():
        log.warning(
            "Dine-in cancellation email not sent because GMAIL_USER/GMAIL_APP_PASSWORD are not configured restaurant_id=%s to=%s audience=%s",
            restaurant_id, to_email, audience,
        )
        return False
    subject = f"Dine-in reservation cancelled — {restaurant_name}"
    if audience == "restaurant_owner":
        who = "by the guest" if cancelled_by == "customer" else ""
        body = f"""A dine-in reservation at {restaurant_name} was cancelled{(' ' + who) if who else ''}.

Table: {table_name}
Party size: {party_size}
Was scheduled for: {start_time_formatted}
"""
        if caller_name:
            body += f"Guest: {caller_name}\n"
    else:
        who = "by the restaurant" if cancelled_by == "restaurant_owner" else ""
        body = f"""Your dine-in reservation at {restaurant_name} has been cancelled{(' ' + who) if who else ''}.

Table: {table_name}
Party size: {party_size}
Was scheduled for: {start_time_formatted}
"""
    if reason:
        body += f"Reason: {reason}\n"
    body += "\n— ADAR Eats\n"
    try:
        await send_email(to=to_email, subject=subject, body=body)
        log.info("Dine-in cancellation email sent to %s restaurant_id=%s audience=%s", to_email, restaurant_id, audience)
        return True
    except Exception as e:
        log.warning("Dine-in cancellation email failed for %s restaurant_id=%s: %s", to_email, restaurant_id, e)
        return False


async def send_dine_in_rescheduled_email(
    to_email: str,
    *,
    audience: str,  # 'restaurant_owner' | 'customer'
    restaurant_name: str,
    restaurant_id: str,
    table_name: str,
    party_size: int,
    start_time_formatted: str,
    old_start_time_formatted: str = "",
    rescheduled_by: str = "",  # 'restaurant_owner' | 'customer' | ''
    caller_name: str = "",
) -> bool:
    """Dine-in reschedule notification, audience-parameterized. A
    reschedule is implemented as cancel-old + create-new (adar-core has no
    atomic reschedule primitive), so the customer's own inbox will also
    show adar-core's two separate emails for that pair of calls when they
    initiated it themselves -- this purpose-written email is what tells
    whichever party did NOT initiate the change, phrased as one single
    "moved" event rather than a cancellation followed by a new booking."""
    if not is_email_configured():
        log.warning(
            "Dine-in reschedule email not sent because GMAIL_USER/GMAIL_APP_PASSWORD are not configured restaurant_id=%s to=%s audience=%s",
            restaurant_id, to_email, audience,
        )
        return False
    subject = f"Dine-in reservation moved — {restaurant_name}"
    if audience == "restaurant_owner":
        who = "by the guest" if rescheduled_by == "customer" else ""
        body = f"""A dine-in reservation at {restaurant_name} was moved to a new time{(' ' + who) if who else ''}.

Table: {table_name}
Party size: {party_size}
"""
        if caller_name:
            body += f"Guest: {caller_name}\n"
    else:
        who = "by the restaurant" if rescheduled_by == "restaurant_owner" else ""
        body = f"""Your dine-in reservation at {restaurant_name} has been moved to a new time{(' ' + who) if who else ''}.

Table: {table_name}
Party size: {party_size}
"""
    if old_start_time_formatted:
        body += f"Previously: {old_start_time_formatted}\n"
    body += f"Now: {start_time_formatted}\n"
    body += "\n— ADAR Eats\n"
    try:
        await send_email(to=to_email, subject=subject, body=body)
        log.info("Dine-in reschedule email sent to %s restaurant_id=%s audience=%s", to_email, restaurant_id, audience)
        return True
    except Exception as e:
        log.warning("Dine-in reschedule email failed for %s restaurant_id=%s: %s", to_email, restaurant_id, e)
        return False
