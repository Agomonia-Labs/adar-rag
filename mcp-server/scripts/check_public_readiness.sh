#!/usr/bin/env bash
set -euo pipefail

MCP_PUBLIC_URL="${DOCINTEL_MCP_PUBLIC_URL:?Set DOCINTEL_MCP_PUBLIC_URL}"
ISSUER_URL="${DOCINTEL_MCP_ISSUER_URL:?Set DOCINTEL_MCP_ISSUER_URL}"
MCP_URL="${MCP_PUBLIC_URL%/}/mcp"
RESOURCE_METADATA_URL="${MCP_PUBLIC_URL%/}/.well-known/oauth-protected-resource/mcp"

for command in curl jq; do
  command -v "$command" >/dev/null || { echo "ERROR: $command is required" >&2; exit 1; }
done

check_json() {
  local url="$1"
  curl --fail-with-body --silent --show-error "$url"
}

echo "[1/4] Health"
check_json "${MCP_PUBLIC_URL%/}/health" | jq -e '.status == "ok"' >/dev/null

echo "[2/4] OAuth protected-resource metadata"
resource_metadata="$(check_json "$RESOURCE_METADATA_URL")"
jq . <<<"$resource_metadata"
jq -e --arg resource "$MCP_URL" --arg issuer "${ISSUER_URL%/}" '
  .resource == $resource and (.authorization_servers | index($issuer) != null)
' <<<"$resource_metadata" >/dev/null

echo "[3/4] OAuth authorization-server metadata"
issuer_metadata="$(check_json "${ISSUER_URL%/}/.well-known/oauth-authorization-server")"
jq . <<<"$issuer_metadata"
jq -e '
  (.authorization_endpoint | type == "string") and
  (.token_endpoint | type == "string") and
  ((.registration_endpoint | type == "string") or
   (.client_id_metadata_document_supported == true))
' <<<"$issuer_metadata" >/dev/null

echo "[4/4] Unauthenticated MCP challenge"
headers="$(mktemp)"
trap 'rm -f "$headers"' EXIT
status="$(curl --silent --show-error --output /dev/null --dump-header "$headers" \
  --write-out '%{http_code}' "$MCP_URL")"
[[ "$status" == "401" ]] || { echo "Expected 401, received $status" >&2; exit 1; }
grep -qi 'www-authenticate:.*resource_metadata=' "$headers"

echo "Public MCP discovery checks passed. OAuth flow and tool authorization still require end-to-end client testing."
