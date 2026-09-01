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
        introspection_secret="shared-secret",
    )


@pytest.mark.asyncio
async def test_verifier_exchanges_mcp_token_for_backend_identity():
    async def handler(request: httpx.Request):
        assert request.url.path == "/internal/oauth/introspect"
        assert request.headers["x-mcp-introspection-secret"] == "shared-secret"
        assert request.content == b'{"token":"valid-token"}'
        return httpx.Response(200, json={
            "active": True,
            "sub": "user-1",
            "client_id": "client-1",
            "scope": "documents:read knowledge:query",
            "exp": 2000000000,
            "email": "user@example.com",
            "role": "user",
            "token_kind": "service",
            "organization_id": "org-1",
            "workspace_ids": ["workspace-1"],
            "backend_token": "internal-token",
        })

    verifier = DocIntelTokenVerifier(settings(), httpx.MockTransport(handler))
    result = await verifier.verify_token("valid-token")

    assert result is not None
    assert result.subject == "user-1"
    assert result.token == "internal-token"
    assert result.client_id == "client-1"
    assert result.scopes == ["documents:read", "knowledge:query"]
    assert result.claims["token_kind"] == "service"
    assert result.claims["organization_id"] == "org-1"
    assert result.claims["workspace_ids"] == ["workspace-1"]


@pytest.mark.asyncio
async def test_verifier_rejects_backend_unauthorized_token():
    async def handler(_request: httpx.Request):
        return httpx.Response(401, json={"detail": "Invalid or expired token"})

    verifier = DocIntelTokenVerifier(settings(), httpx.MockTransport(handler))
    assert await verifier.verify_token("expired-token") is None
