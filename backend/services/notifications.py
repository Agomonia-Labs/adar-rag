# services/notifications.py — transactional email notifications
from __future__ import annotations
import logging
from services.email import send_email

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