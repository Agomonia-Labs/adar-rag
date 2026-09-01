from datetime import datetime, timezone

import pytest

from services.service_credentials import hash_secret, ip_allowed, secret_hint, verify_secret
from services.usage_governance import (
    QUOTA_COUNTER_RESERVE_SQL,
    UsageContext,
    _window_start,
    operation_for_request,
)


class CredentialDb:
    def __init__(self, row=None, cidrs=()):
        self.row = row
        self.cidrs = [{"cidr": value} for value in cidrs]
        self.executed = []

    async def fetchrow(self, *_args):
        return self.row

    async def fetch(self, *_args):
        return self.cidrs

    async def execute(self, *args):
        self.executed.append(args)


def test_operation_name_removes_api_prefix_and_normalizes_ids():
    value = operation_for_request(
        "POST", "/api/v1/documents/31b1d14a-b3a8-4748-b6e1-1f61310e46e9/summaries",
    )
    assert value == "post.documents/{id}/summaries"


def test_quota_window_is_stable_and_utc():
    now = datetime(2026, 9, 1, 12, 34, 56, tzinfo=timezone.utc)
    assert _window_start(now, 3600) == datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_usage_context_supports_enterprise_dimensions():
    context = UsageContext(user_id="u", client_id="c", organization_id="o", workspace_id="w")
    assert context.principal_type == "user"
    assert context.operation == "api.request"


def test_quota_counter_query_has_explicit_asyncpg_parameter_types():
    assert "$1::uuid" in QUOTA_COUNTER_RESERVE_SQL
    assert "$2::timestamptz" in QUOTA_COUNTER_RESERVE_SQL
    assert "$3::bigint" in QUOTA_COUNTER_RESERVE_SQL
    assert "$4::bigint" in QUOTA_COUNTER_RESERVE_SQL


@pytest.mark.asyncio
async def test_secret_verification_updates_last_used_for_active_secret():
    db = CredentialDb(row={"id": "secret-id"})
    assert await verify_secret(db, "client", "correct-secret") is True
    assert db.executed


@pytest.mark.asyncio
async def test_secret_verification_supports_legacy_primary_hash():
    db = CredentialDb()
    assert await verify_secret(db, "client", "correct-secret", hash_secret("correct-secret")) is True
    assert await verify_secret(db, "client", "wrong-secret", hash_secret("correct-secret")) is False


@pytest.mark.asyncio
async def test_ip_allowlist_is_optional_and_supports_networks():
    assert await ip_allowed(CredentialDb(cidrs=()), "client", "203.0.113.8") is True
    db = CredentialDb(cidrs=("203.0.113.0/24", "2001:db8::/32"))
    assert await ip_allowed(db, "client", "203.0.113.8") is True
    assert await ip_allowed(db, "client", "198.51.100.8") is False


def test_secret_hints_do_not_expose_complete_secret():
    assert secret_hint("abcdefghijklmnopqrstuvwxyz") == "...uvwxyz"
