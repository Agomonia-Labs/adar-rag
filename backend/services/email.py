# services/email.py — Google SMTP (Gmail or Google Workspace)
from __future__ import annotations
import os, logging, hashlib, secrets, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import timezone

log = logging.getLogger("docintel.email")

GMAIL_USER     = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")  # strip spaces
EMAIL_FROM_NAME= os.getenv("EMAIL_FROM_NAME", "আদর DocIntel")
APP_URL        = os.getenv("APP_URL", "https://docintel.adar.agomoniai.com")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ── HTML template ─────────────────────────────────────────────────────────────
def _html_reset(name: str, link: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Inter,sans-serif;background:#0a1a0a;padding:40px 20px;margin:0">
  <div style="max-width:480px;margin:0 auto;background:#162616;border-radius:16px;
              padding:40px 36px;border:1px solid rgba(74,222,128,.2)">
    <div style="text-align:center;margin-bottom:28px">
      <span style="font-size:36px">🌿</span>
      <h1 style="font-size:22px;font-weight:800;color:#4ade80;margin:10px 0 4px">আদর DocIntel</h1>
      <p style="font-size:12px;color:#6b7280;margin:0">Document Intelligence Platform</p>
    </div>
    <h2 style="font-size:18px;font-weight:700;color:#f0fdf4;margin:0 0 12px">Reset your password</h2>
    <p style="font-size:14px;color:#d1fae5;line-height:1.6;margin:0 0 24px">
      Hi {name},<br><br>
      Click the button below to reset your password.
      This link expires in <strong style="color:#fbbf24">1 hour</strong>.
    </p>
    <div style="text-align:center;margin:28px 0">
      <a href="{link}"
         style="display:inline-block;background:#15803d;color:#fff;
                padding:13px 32px;border-radius:24px;text-decoration:none;
                font-weight:700;font-size:15px">Reset Password →</a>
    </div>
    <p style="font-size:12px;color:#6b7280;line-height:1.6;margin:0">
      If you didn't request this, ignore this email.<br><br>
      Or copy: <span style="color:#4ade80;word-break:break-all;font-size:11px">{link}</span>
    </p>
    <hr style="border:none;border-top:1px solid rgba(255,255,255,.07);margin:24px 0">
    <p style="font-size:11px;color:#4b5563;text-align:center;margin:0">
      {EMAIL_FROM_NAME} · Agomonia Labs
    </p>
  </div>
</body></html>"""


# ── Core send function ────────────────────────────────────────────────────────
def _smtp_send(to_email: str, subject: str, html: str) -> None:
    """
    Synchronous send via Google SMTP.
    Raises on any failure so caller can log and decide what to do.

    Requirements for admin@agomoniai.com (Google Workspace):
      1. 2-Step Verification enabled on the account
      2. App Password generated at myaccount.google.com → Security → App passwords
      3. GMAIL_USER=admin@agomoniai.com
      4. GMAIL_APP_PASSWORD=<16-char app password, spaces optional>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{EMAIL_FROM_NAME} <{GMAIL_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())


async def send_email(to: str, subject: str, body: str) -> None:
    """Generic plain-text email sender used by notifications and verification."""
    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning(f"Email not configured — would have sent '{subject}' to {to}")
        return
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _smtp_send, to, subject, f"<pre style='font-family:sans-serif;font-size:14px'>{body}</pre>")


async def send_reset_email(to_email: str, to_name: str, reset_link: str) -> bool:
    """Send password reset email. Returns True on success."""

    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning(
            f"\n{'='*60}\n"
            f"⚠  No Gmail credentials configured (GMAIL_USER / GMAIL_APP_PASSWORD)\n"
            f"   Dev mode — use this URL manually:\n"
            f"   {reset_link}\n"
            f"{'='*60}"
        )
        return True  # Allow flow to complete in dev mode

    subject = "Reset your আদর DocIntel password"
    html    = _html_reset(to_name, reset_link)

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _smtp_send, to_email, subject, html)
        log.info(f"✓ Reset email sent to {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        log.error(
            f"✗ Gmail SMTP authentication failed for {GMAIL_USER}\n"
            f"  Error: {e}\n"
            f"  Fix: Ensure 2-Step Verification is ON and use an App Password\n"
            f"       (not your regular account password)\n"
            f"  Generate App Password: myaccount.google.com → Security → App passwords"
        )
        return False
    except smtplib.SMTPRecipientsRefused as e:
        log.error(f"✗ Recipient refused: {to_email} — {e}")
        return False
    except smtplib.SMTPConnectError as e:
        log.error(f"✗ Cannot connect to {SMTP_HOST}:{SMTP_PORT} — {e}")
        return False
    except TimeoutError:
        log.error(f"✗ SMTP connection timed out (15s) to {SMTP_HOST}:{SMTP_PORT}")
        return False
    except Exception as e:
        log.error(f"✗ Unexpected email error: {type(e).__name__}: {e}")
        return False


async def test_smtp_connection() -> dict:
    """
    Test SMTP connection without sending an email.
    Called by the admin test endpoint.
    """
    if not GMAIL_USER or not GMAIL_APP_PASS:
        return {"ok": False, "error": "GMAIL_USER or GMAIL_APP_PASSWORD not set"}

    def _test():
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(GMAIL_USER, GMAIL_APP_PASS)
        return True

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _test)
        return {"ok": True, "user": GMAIL_USER, "host": f"{SMTP_HOST}:{SMTP_PORT}"}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "error": f"Authentication failed — {e}. Check App Password."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── Token helpers ─────────────────────────────────────────────────────────────
def generate_reset_token() -> tuple[str, str]:
    raw    = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed

def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def reset_url(raw_token: str) -> str:
    return f"{APP_URL}/reset-password?token={raw_token}"