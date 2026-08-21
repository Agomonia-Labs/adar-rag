from routes.oauth import _b64url_sha256, _valid_redirect_uri


def test_pkce_s256_matches_rfc7636_example():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert _b64url_sha256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_redirect_uri_requires_https_except_loopback():
    assert _valid_redirect_uri("https://client.example/callback")
    assert _valid_redirect_uri("http://127.0.0.1:49152/callback")
    assert _valid_redirect_uri("http://localhost:3000/callback")
    assert not _valid_redirect_uri("http://client.example/callback")
    assert not _valid_redirect_uri("https://client.example/callback#fragment")
