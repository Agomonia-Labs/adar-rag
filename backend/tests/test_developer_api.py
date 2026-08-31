from routes.developer_api import DeveloperAppCreate, _hash_secret


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

