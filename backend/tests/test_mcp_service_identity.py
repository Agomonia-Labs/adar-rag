import pytest
from jose import jwt

from auth.service import ALGORITHM, SECRET_KEY
from routes import oauth


USER_ID = "11111111-1111-1111-1111-111111111111"
ORG_ID = "22222222-2222-2222-2222-222222222222"
WORKSPACE_ID = "33333333-3333-3333-3333-333333333333"


class FormRequest:
    def __init__(self, values):
        self.values = values
        self.headers = {}
        self.client = None

    async def form(self):
        return self.values


class ServiceTokenDb:
    def __init__(self):
        self.audit = []

    async def execute(self, sql, *args):
        if "oauth_service_audit_events" in sql:
            self.audit.append(args)
        return "INSERT 0 1"

    async def fetchrow(self, sql, *_args):
        if "FROM oauth_service_clients s" in sql:
            return {
                "client_id": "svc-client",
                "client_secret_hash": oauth._hash("secret"),
                "owner_user_id": USER_ID,
                "scope": "documents:read knowledge:query",
                "organization_id": ORG_ID,
                "expires_at": None,
            }
        return None

    async def fetch(self, *_args):
        return []


@pytest.mark.anyio
async def test_organization_service_client_can_request_mcp_audience():
    db = ServiceTokenDb()
    result = await oauth.token(FormRequest({
        "grant_type": "client_credentials",
        "client_id": "svc-client",
        "client_secret": "secret",
        "scope": "documents:read",
        "resource": oauth.MCP_RESOURCE,
    }), db)

    claims = jwt.decode(
        result["access_token"], SECRET_KEY, algorithms=[ALGORITHM],
        audience=oauth.MCP_RESOURCE, issuer=oauth.ISSUER,
    )
    assert claims["token_kind"] == "service"
    assert claims["organization_id"] == ORG_ID
    assert result["scope"] == "documents:read"
    assert db.audit


class IntrospectionRequest:
    headers = {"X-MCP-Introspection-Secret": "test-secret"}

    def __init__(self, token):
        self.value = token

    async def json(self):
        return {"token": self.value}


class IntrospectionDb:
    async def execute(self, *_args):
        return "CREATE TABLE"

    async def fetchrow(self, sql, *_args):
        if "FROM users" in sql:
            return {"id": USER_ID, "email": "service@example.com", "role": "admin"}
        if "FROM oauth_clients" in sql and "UNION ALL" in sql:
            return {
                "client_id": "svc-client",
                "token_kind": "service",
                "organization_id": ORG_ID,
                "client_scope": "documents:read knowledge:query",
            }
        return None

    async def fetch(self, sql, *_args):
        if "oauth_service_workspace_grants" in sql:
            return [{"workspace_id": WORKSPACE_ID}]
        return []


@pytest.mark.anyio
async def test_mcp_introspection_returns_live_service_workspace_grants(monkeypatch):
    monkeypatch.setattr(oauth, "MCP_INTROSPECTION_SECRET", "test-secret")
    token_value, _ = oauth._access_token(
        USER_ID, "svc-client", "documents:read", oauth.MCP_RESOURCE,
        extra_claims={"token_kind": "service", "organization_id": ORG_ID},
    )

    result = await oauth.introspect(IntrospectionRequest(token_value), IntrospectionDb())

    assert result["active"] is True
    assert result["token_kind"] == "service"
    assert result["organization_id"] == ORG_ID
    assert result["workspace_ids"] == [WORKSPACE_ID]
    assert result["backend_token"]
