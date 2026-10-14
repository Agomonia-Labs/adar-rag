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

# Per-client-app sender identity (see services/branding.py) -- lets an email
# actually triggered by the ADAR Eats web app show up as being from "ADAR
# Eats" with its own icon instead of DocIntel's.
from services.branding import brand_for

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def is_email_configured() -> bool:
    return bool(GMAIL_USER and GMAIL_APP_PASS)


# ── HTML template ─────────────────────────────────────────────────────────────
def _brand_icon_html(brand: dict) -> str:
    """A small header logo -- the Eats brand supplies a real logo image
    (its app icon, hosted on its own domain), DocIntel's falls back to the
    🌿 emoji it has always used."""
    logo_url = brand.get("logo_url")
    if logo_url:
        return (
            f'<img src="{logo_url}" width="40" height="40" alt="{brand["name"]}" '
            'style="border-radius:10px;display:block;margin:0 auto" />'
        )
    return f'<span style="font-size:36px">{brand.get("icon_emoji") or "🌿"}</span>'


def _html_reset(name: str, link: str, brand: dict | None = None) -> str:
    brand = brand or brand_for(None)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Inter,sans-serif;background:#0a1a0a;padding:40px 20px;margin:0">
  <div style="max-width:480px;margin:0 auto;background:#162616;border-radius:16px;
              padding:40px 36px;border:1px solid rgba(74,222,128,.2)">
    <div style="text-align:center;margin-bottom:28px">
      {_brand_icon_html(brand)}
      <h1 style="font-size:22px;font-weight:800;color:#4ade80;margin:10px 0 4px">{brand["name"]}</h1>
      <p style="font-size:12px;color:#6b7280;margin:0">{brand["tagline"]}</p>
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
      {brand["name"]} · Agomonia Labs
    </p>
  </div>
</body></html>"""


def _html_otp(name: str, otp: str, brand: dict | None = None) -> str:
    brand = brand or brand_for(None)
    safe_name = name or "there"
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Inter,sans-serif;background:#0a1a0a;padding:40px 20px;margin:0">
  <div style="max-width:480px;margin:0 auto;background:#162616;border-radius:16px;
              padding:40px 36px;border:1px solid rgba(74,222,128,.2)">
    <div style="text-align:center;margin-bottom:28px">
      {_brand_icon_html(brand)}
      <h1 style="font-size:22px;font-weight:800;color:#4ade80;margin:10px 0 4px">{brand["name"]}</h1>
      <p style="font-size:12px;color:#6b7280;margin:0">{brand["tagline"]}</p>
    </div>
    <h2 style="font-size:18px;font-weight:700;color:#f0fdf4;margin:0 0 12px">Sign in verification</h2>
    <p style="font-size:14px;color:#d1fae5;line-height:1.6;margin:0 0 22px">
      Hi {safe_name},<br><br>
      Use this code to complete your {brand["name"]} sign in.
    </p>
    <div style="text-align:center;margin:8px 0 24px">
      <div style="font-size:0.78rem;color:#6b7280;margin-bottom:12px;letter-spacing:0.08em;text-transform:uppercase">
        Your login code
      </div>
      <div style="font-size:2.6rem;font-weight:800;letter-spacing:0.22em;color:#dcfce7;
                  background:rgba(74,222,128,.1);border-radius:12px;padding:16px 24px;
                  display:inline-block;border:2px solid rgba(74,222,128,.28)">
        {otp}
      </div>
      <div style="font-size:0.78rem;color:#6b7280;margin-top:12px">
        Expires in <strong style="color:#fbbf24">5 minutes</strong> · Do not share this code
      </div>
    </div>
    <p style="font-size:12px;color:#6b7280;line-height:1.6;margin:0;text-align:center">
      If you did not try to sign in, you can safely ignore this email.
    </p>
    <hr style="border:none;border-top:1px solid rgba(255,255,255,.07);margin:24px 0">
    <p style="font-size:11px;color:#4b5563;text-align:center;margin:0">
      {brand["name"]} · Agomonia Labs
    </p>
  </div>
</body></html>"""


# ── Core send function ────────────────────────────────────────────────────────
def _smtp_send(to_email: str, subject: str, html: str, from_name: str | None = None) -> None:
    """
    Synchronous send via Google SMTP.
    Raises on any failure so caller can log and decide what to do.

    `from_name` overrides the sender display name (e.g. "ADAR Eats" instead
    of the default "{EMAIL_FROM_NAME}") -- the mailbox itself (GMAIL_USER)
    is shared by every ADAR product, only the display name differs.

    Requirements for admin@agomoniai.com (Google Workspace):
      1. 2-Step Verification enabled on the account
      2. App Password generated at myaccount.google.com → Security → App passwords
      3. GMAIL_USER=admin@agomoniai.com
      4. GMAIL_APP_PASSWORD=<16-char app password, spaces optional>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name or EMAIL_FROM_NAME} <{GMAIL_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())


async def send_email(to: str, subject: str, body: str, client_app: str = "") -> None:
    """Generic plain-text email sender used by notifications and verification.

    `client_app` (an `X-Client-App` header value, e.g. "eats") sets the
    sender display name via brand_for() -- so an email about a restaurant
    order or an Eats account shows up as being from "ADAR Eats" rather than
    the default DocIntel sender identity."""
    if not is_email_configured():
        log.warning(f"Email not configured — would have sent '{subject}' to {to}")
        return
    brand = brand_for(client_app)
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _smtp_send, to, subject,
        f"<pre style='font-family:sans-serif;font-size:14px'>{body}</pre>",
        brand["name"],
    )


async def send_reset_email(to_email: str, to_name: str, reset_link: str, client_app: str = "") -> bool:
    """Send password reset email. Returns True on success."""

    if not is_email_configured():
        log.warning(
            f"\n{'='*60}\n"
            f"⚠  No Gmail credentials configured (GMAIL_USER / GMAIL_APP_PASSWORD)\n"
            f"   Dev mode — use this URL manually:\n"
            f"   {reset_link}\n"
            f"{'='*60}"
        )
        return True  # Allow flow to complete in dev mode

    brand = brand_for(client_app)
    subject = f"Reset your {brand['name']} password"
    html    = _html_reset(to_name, reset_link, brand)

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _smtp_send, to_email, subject, html, brand["name"])
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


async def send_otp_email(to_email: str, to_name: str, otp: str, client_app: str = "") -> bool:
    """Send a 6-digit MFA login code. Returns True on success.

    `client_app` (an `X-Client-App` header value, e.g. "eats") selects the
    brand this code is sent under -- an ADAR Eats login code now arrives
    labelled "ADAR Eats" with its own icon instead of DocIntel's, both in
    the email body and in the sender display name."""
    if not is_email_configured():
        log.warning(
            f"\n{'='*60}\n"
            f"⚠  No Gmail credentials configured (GMAIL_USER / GMAIL_APP_PASSWORD)\n"
            f"   Dev mode — OTP for {to_email}: {otp}\n"
            f"{'='*60}"
        )
        return True

    brand = brand_for(client_app)
    subject = f"Your {brand['name']} login code: {otp}"
    html = _html_otp(to_name, otp, brand)

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _smtp_send, to_email, subject, html, brand["name"])
        log.info(f"✓ OTP email sent to {to_email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        log.error(f"✗ Gmail SMTP authentication failed for {GMAIL_USER}: {e}")
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
        log.error(f"✗ OTP email error for {to_email}: {type(e).__name__}: {e}")
        return False


async def test_smtp_connection() -> dict:
    """
    Test SMTP connection without sending an email.
    Called by the admin test endpoint.
    """
    if not is_email_configured():
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

def reset_url(raw_token: str, app_url: str = "") -> str:
    """`app_url` lets a caller point the reset link at a specific product's
    own domain (e.g. ADAR Eats) instead of the global DocIntel default."""
    return f"{(app_url or APP_URL).rstrip('/')}/reset-password?token={raw_token}"
