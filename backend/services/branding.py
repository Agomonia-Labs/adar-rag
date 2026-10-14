# services/branding.py — per-client-app email branding.
#
# adar-rag's backend is shared by multiple ADAR web frontends (DocIntel,
# Eats, ...). Every transactional-email function used to hardcode
# DocIntel's own name/icon/sender identity, so an email actually triggered
# by the ADAR Eats web app -- an MFA login code, a password reset, an order
# confirmation, email verification, account deletion -- still arrived in
# the recipient's inbox labelled "আদর DocIntel", which is confusing for a
# restaurant customer or owner who has never heard of DocIntel.
#
# Each web frontend identifies itself with an `X-Client-App` request header
# (see adar-rag/ui/eats/src/services/api.js); callers read that header
# (helpers in auth/router.py, routes/password_reset.py) and pass the
# resulting `client_app` string into services/email.py and
# services/notifications.py, which resolve it via brand_for() below to get
# this product's own display name, tagline and logo -- falling back to
# DocIntel's own branding when the header is absent or unrecognized (e.g.
# DocIntel's own frontend, which never sends it).
from __future__ import annotations
import os

EATS_APP_URL = os.getenv("EATS_APP_URL", "https://eats.agomoniai.com")

DEFAULT_BRAND = {
    "name": os.getenv("EMAIL_FROM_NAME", "আদর DocIntel"),
    "tagline": "Document Intelligence Platform",
    "logo_url": None,      # falls back to the 🌿 emoji in email.py's HTML templates
    "icon_emoji": "🌿",
}

BRANDS = {
    "eats": {
        "name": "ADAR Eats",
        "tagline": "Restaurant Ordering & Management",
        "logo_url": os.getenv("EATS_LOGO_URL", f"{EATS_APP_URL.rstrip('/')}/logo.png"),
        "icon_emoji": None,
    },
}


def brand_for(client_app: str | None) -> dict:
    """Resolve an `X-Client-App` header value (already lower-cased by the
    caller, but normalized again here defensively) to its brand dict.
    Unknown or empty values fall back to DocIntel's own branding, which
    keeps every existing DocIntel email behaving exactly as before."""
    return BRANDS.get((client_app or "").strip().lower(), DEFAULT_BRAND)
