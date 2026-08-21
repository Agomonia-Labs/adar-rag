from __future__ import annotations

import time

import httpx
from mcp.server.auth.provider import AccessToken

from .config import Settings


class DocIntelTokenVerifier:
    """Validate a DocIntel token through the backend's authoritative auth route."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.api_base_url,
                timeout=httpx.Timeout(15),
                transport=self.transport,
            ) as client:
                response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            user = response.json()
            subject = str(user["id"])
        except (ValueError, KeyError, TypeError):
            return None
        return AccessToken(
            token=token,
            client_id=f"docintel-user:{subject}",
            subject=subject,
            scopes=sorted(self.settings.enabled_capabilities),
            expires_at=int(time.time()) + 300,
            claims={"email": user.get("email"), "role": user.get("role")},
        )
