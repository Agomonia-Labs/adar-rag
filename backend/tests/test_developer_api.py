import pytest
from pydantic import ValidationError

from routes.developer_api import (
    DeveloperAppCreate,
    DeveloperOrganizationCreate,
    DeveloperOrganizationUpdate,
    DeveloperScopeRequestCreate,
    DeveloperScopeRequestDecision,
    DeveloperQuotaPolicyInput,
    DeveloperSecretRotateInput,
    _hash_secret,
)


def test_client_secret_hash_is_deterministic_and_not_plaintext():
    value = "correct-horse-battery-staple"
    assert _hash_secret(value) == _hash_secret(value)
    assert _hash_secret(value) != value


def test_developer_app_defaults_to_public_pkce_client():
    app = DeveloperAppCreate(
        name="Local integration",
        redirect_uris=["http://127.0.0.1:8765/callback"],
        scopes=["documents:read"],
    )
    assert app.client_type == "public"


def test_confidential_app_accepts_organization_and_workspace_boundaries():
    app = DeveloperAppCreate(
        name="Procurement integration",
        client_type="confidential",
        scopes=["documents:read", "knowledge:query"],
        organization_id="11111111-1111-1111-1111-111111111111",
        workspace_ids=["22222222-2222-2222-2222-222222222222"],
    )
    assert app.organization_id is not None
    assert app.workspace_ids == ["22222222-2222-2222-2222-222222222222"]


def test_organization_slug_is_optional_and_derived_by_endpoint():
    organization = DeveloperOrganizationCreate(name="Agomonia Enterprise")
    assert organization.slug is None


def test_organization_lifecycle_accepts_supported_states_only():
    assert DeveloperOrganizationUpdate(status="suspended").status == "suspended"
    with pytest.raises(ValidationError):
        DeveloperOrganizationUpdate(status="deleted")


def test_service_scope_request_requires_at_least_one_scope():
    request = DeveloperScopeRequestCreate(scopes=["knowledge:generate"], reason="Generate reviewed summaries")
    assert request.scopes == ["knowledge:generate"]
    with pytest.raises(ValidationError):
        DeveloperScopeRequestCreate(scopes=[])


def test_service_scope_decision_is_explicit():
    assert DeveloperScopeRequestDecision(decision="approved").decision == "approved"
    with pytest.raises(ValidationError):
        DeveloperScopeRequestDecision(decision="pending")


def test_quota_policy_has_bounded_window_and_limit():
    policy = DeveloperQuotaPolicyInput(
        policy_name="Hourly knowledge calls", scope="knowledge:query",
        window_seconds=3600, limit_value=1000,
    )
    assert policy.window_seconds == 3600
    with pytest.raises(ValidationError):
        DeveloperQuotaPolicyInput(policy_name="No", window_seconds=0, limit_value=1)


def test_secret_rotation_defaults_to_overlap_period():
    assert DeveloperSecretRotateInput().overlap_hours == 24
