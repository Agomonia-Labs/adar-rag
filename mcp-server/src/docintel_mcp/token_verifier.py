from __future__ import annotations

import time

import httpx
from mcp.server.auth.provider import AccessToken

from .config import Settings


class DocIntelTokenVerifier:
    """Validate an audience-bound MCP token and obtain a backend credential."""

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
                response = await client.post(
                    "/internal/oauth/introspect",
                    headers={"X-MCP-Introspection-Secret": self.settings.introspection_secret},
                    json={"token": token},
                )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            result = response.json()
            if not result.get("active"):
                return None
            subject = str(result["sub"])
        except (ValueError, KeyError, TypeError):
            return None
        return AccessToken(
            token=str(result["backend_token"]),
            client_id=str(result["client_id"]),
            subject=subject,
            scopes=str(result.get("scope", "")).split(),
            expires_at=int(result.get("exp", time.time() + 60)),
            claims={"email": result.get("email"), "role": result.get("role")},
        )
