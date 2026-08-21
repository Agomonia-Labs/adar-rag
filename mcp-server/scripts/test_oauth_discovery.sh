#!/usr/bin/env bash
set -euo pipefail

ISSUER="${DOCINTEL_MCP_ISSUER_URL:?Set DOCINTEL_MCP_ISSUER_URL}"
MCP_URL="${DOCINTEL_MCP_URL:?Set DOCINTEL_MCP_URL}"
CALLBACK="${DOCINTEL_OAUTH_TEST_CALLBACK:-http://127.0.0.1:8765/callback}"

echo "[1/3] Authorization-server metadata"
curl -fsS "${ISSUER%/}/.well-known/oauth-authorization-server" | jq -e '
  .authorization_endpoint and .token_endpoint and .registration_endpoint and
  (.code_challenge_methods_supported | index("S256"))
'

echo "[2/3] Protected-resource metadata"
curl -fsS "${MCP_URL%/mcp}/.well-known/oauth-protected-resource/mcp" | jq -e \
  --arg issuer "${ISSUER%/}" '
    any(.authorization_servers[]?; rtrimstr("/") == ($issuer | rtrimstr("/")))
  '

echo "[3/3] Dynamic client registration"
registration=$(curl -fsS -X POST "${ISSUER%/}/register" \
  -H 'Content-Type: application/json' \
  --data "$(jq -cn --arg redirect "$CALLBACK" '{client_name:"DocIntel OAuth smoke test",redirect_uris:[$redirect],token_endpoint_auth_method:"none"}')")
jq . <<<"$registration"
jq -e '.client_id and (.token_endpoint_auth_method == "none")' <<<"$registration" >/dev/null

echo "OAuth discovery and registration checks passed."
