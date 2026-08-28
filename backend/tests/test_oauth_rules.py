import pytest

from routes.oauth import (
    ALLOWED_SCOPES,
    _active_scopes,
    _b64url_sha256,
    _normalize_scopes,
    _require_scope_grants,
    _valid_redirect_uri,
)


def test_pkce_s256_matches_rfc7636_example():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert _b64url_sha256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_redirect_uri_requires_https_except_loopback():
    assert _valid_redirect_uri("https://client.example/callback")
    assert _valid_redirect_uri("http://127.0.0.1:49152/callback")
    assert _valid_redirect_uri("http://localhost:3000/callback")
    assert not _valid_redirect_uri("http://client.example/callback")
    assert not _valid_redirect_uri("https://client.example/callback#fragment")


def test_scope_normalization_rejects_self_assigned_unknown_scope():
    assert _normalize_scopes(["documents:read", "knowledge:query"]) == {
        "documents:read", "knowledge:query",
    }
    with pytest.raises(Exception) as exc:
        _normalize_scopes(["admin:everything"])
    assert exc.value.status_code == 400


def test_enterprise_scopes_are_discoverable_for_admin_assignment():
    assert {"events:read", "reviews:approve", "artifacts:write", "versions:write", "evaluations:run"} <= ALLOWED_SCOPES


class ScopeDb:
    def __init__(self, role="user", scopes=()):
        self.role = role
        self.scopes = scopes
        self.fetch_args = []

    async def fetchval(self, _sql, *_args):
        return self.role

    async def fetch(self, _sql, *_args):
        self.fetch_args.append(_args)
        return [{"scope": scope} for scope in self.scopes]


@pytest.mark.anyio
async def test_admin_has_supported_scopes_without_individual_assignments():
    assert await _active_scopes(ScopeDb(role="admin"), "user-id", "client-id") == ALLOWED_SCOPES


@pytest.mark.anyio
async def test_regular_user_receives_only_active_assigned_scopes():
    db = ScopeDb(scopes=["documents:read", "knowledge:query"])
    assert await _active_scopes(db, "user-id", "client-id") == {"documents:read", "knowledge:query"}


@pytest.mark.anyio
async def test_user_scope_grants_apply_across_dynamic_oauth_clients():
    db = ScopeDb(scopes=["knowledge:generate"])

    first = await _active_scopes(db, "user-id", "cli-client-id")
    second = await _active_scopes(db, "user-id", "playground-client-id")

    assert first == second == {"knowledge:generate"}
    assert db.fetch_args == [("user-id",), ("user-id",)]


@pytest.mark.anyio
async def test_missing_user_scope_requires_approval():
    db = ScopeDb(scopes=["documents:read"])
    with pytest.raises(Exception) as exc:
        await _require_scope_grants(db, "user-id", "client-id", {"documents:read", "documents:write"})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "scope_approval_required"
    assert exc.value.detail["missing_scopes"] == ["documents:write"]
