import httpx
import pytest

from docintel_mcp.config import Settings
from docintel_mcp.token_verifier import DocIntelTokenVerifier


def settings() -> Settings:
    return Settings(
        api_base_url="https://docintel.test",
        public_url="https://mcp.docintel.test",
        issuer_url="https://docintel.test",
        host="127.0.0.1",
        port=8081,
        timeout_seconds=30,
        enabled_capabilities=frozenset({"documents:read", "knowledge:query"}),
        allowed_origins=frozenset(),
        allowed_hosts=frozenset({"localhost:*"}),
        log_level="INFO",
    )


@pytest.mark.asyncio
async def test_verifier_uses_authoritative_docintel_identity():
    async def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer valid-token"
        return httpx.Response(200, json={"id": "user-1", "email": "user@example.com", "role": "user"})

    verifier = DocIntelTokenVerifier(settings(), httpx.MockTransport(handler))
    result = await verifier.verify_token("valid-token")

    assert result is not None
    assert result.subject == "user-1"
    assert result.token == "valid-token"
    assert result.scopes == ["documents:read", "knowledge:query"]


@pytest.mark.asyncio
async def test_verifier_rejects_backend_unauthorized_token():
    async def handler(_request: httpx.Request):
        return httpx.Response(401, json={"detail": "Invalid or expired token"})

    verifier = DocIntelTokenVerifier(settings(), httpx.MockTransport(handler))
    assert await verifier.verify_token("expired-token") is None
