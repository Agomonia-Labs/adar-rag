# Usage, Quotas, Credentials, and SDK End-to-End Testing

This runbook validates the DocIntel usage ledger, quota enforcement, credential
rotation and revocation, source-IP restrictions, MCP accounting, public OpenAPI
export, and the Python, JavaScript, and Java SDK starters.

Use a dedicated confidential application and a non-production team workspace.
Quota tests intentionally reject requests.

## 1. Prerequisites

```bash
cd /Users/brajadas/project/adar-rag
command -v curl jq openssl python3 node
command -v javac || true
```

The application owner needs `service:manage`. Grant the application at least:

```text
workspaces:read documents:read knowledge:query
```

Add `documents:write` and `knowledge:generate` for upload, embedding, and
summarization tests.

## 2. Automated Regression Tests

```bash
cd /Users/brajadas/project/adar-rag

PYTHONPATH=backend ./.venv/bin/python -m pytest \
  backend/tests/test_usage_governance.py \
  backend/tests/test_developer_api.py \
  backend/tests/test_mcp_service_identity.py -q

PYTHONPATH=backend ./.venv/bin/python -m pytest backend/tests mcp-server/tests -q

cd frontend
npm run build
cd ..
```

Expected: all tests and the Vite production build pass.

## 3. Deploy

MCP depends on the backend usage bridge, and frontend hosting contains the
`/openapi.json` rewrite. Deploy in this order:

```bash
bash deploy.sh --backend
bash deploy.sh --mcp
bash deploy.sh --frontend
```

No new environment variable is required. Backend and MCP values for
`MCP_INTROSPECTION_SECRET` / `DOCINTEL_MCP_INTROSPECTION_SECRET` must match.

## 4. Health and Discovery

```bash
export API_BASE="https://docintel.adar.agomoniai.com"
export API_RESOURCE="$API_BASE/api/v1"
export OAUTH_ISSUER="https://auth.docintel.adar.agomoniai.com"
export MCP_URL="https://mcp.docintel.adar.agomoniai.com/mcp"

curl -sS "$API_BASE/api/health" | jq
curl -sS "$OAUTH_ISSUER/.well-known/oauth-authorization-server" | jq
curl -sS "$OAUTH_ISSUER/.well-known/oauth-protected-resource/api" | jq
curl -sS "https://mcp.docintel.adar.agomoniai.com/health" | jq
```

OAuth metadata must advertise `client_credentials`; protected-resource
metadata must identify `$API_RESOURCE`.

## 5. Create a Test Application

In **Tools > Developer Applications > Applications**:

1. Select a non-personal team workspace.
2. Create `Quota and SDK E2E Test` as a confidential application.
3. Select the approved scopes.
4. Store the displayed secret immediately; it is shown only once.

```bash
export CLIENT_ID="svc_..."
export CLIENT_SECRET="..."
export WORKSPACE_ID="..."
```

## 6. Obtain an API Token

```bash
get_service_token() {
  local secret="${1:-$CLIENT_SECRET}"
  local scopes="${2:-workspaces:read documents:read knowledge:query}"

  TOKEN_RESPONSE="$(curl -sS -X POST "$OAUTH_ISSUER/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$secret" \
    --data-urlencode "scope=$scopes" \
    --data-urlencode "resource=$API_RESOURCE")"

  printf '%s\n' "$TOKEN_RESPONSE" | jq
  ACCESS_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -r '.access_token // empty')"
  export ACCESS_TOKEN
  [[ -n "$ACCESS_TOKEN" ]] || { echo "Token request failed"; return 1; }
  echo "Access token loaded: ${#ACCESS_TOKEN} characters"
}

get_service_token
```

Client Credentials has no refresh token. Get a new token after a scope,
workspace, organization, secret, or CIDR change.

## 7. Safe API Test Helper

This preserves status, headers, and non-JSON errors:

```bash
api_get() {
  local path="$1"
  curl -sS -D /tmp/docintel-response.headers \
    -o /tmp/docintel-response.body -w 'HTTP %{http_code}\n' \
    "$API_RESOURCE$path" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "X-DocIntel-Workspace-ID: $WORKSPACE_ID"

  sed -n '1,30p' /tmp/docintel-response.headers
  jq . /tmp/docintel-response.body 2>/dev/null || \
    sed -n '1,80p' /tmp/docintel-response.body
}

api_get /me
api_get /documents
```

Expected:

- `/me` reports the expected `client_id` and scopes.
- `/documents` contains only `$WORKSPACE_ID` data.
- Success includes `X-DocIntel-Usage-Units`.
- An ungranted workspace returns HTTP 403.

```bash
VALID_WORKSPACE_ID="$WORKSPACE_ID"
export WORKSPACE_ID="00000000-0000-0000-0000-000000000001"
api_get /documents
export WORKSPACE_ID="$VALID_WORKSPACE_ID"
```

## 8. Verify the Usage Ledger

```bash
for attempt in 1 2 3; do
  api_get /documents >/tmp/docintel-usage-$attempt.txt
done
```

Open **Developer Applications > Usage & Security**. Confirm request totals,
`get.documents`, workspace, failures, bytes, latency, and token totals appear.
Usage is recorded even when no quota policy matches.

## 9. Create and Test a Quota

Create this policy in **Usage & Security > Quota Policies**:

```text
Name: Five document reads per minute
Window: 60 seconds
Limit: 5
Workspace: selected team workspace
Scope: documents:read
Operation: get.documents
```

Run six requests in a fresh quota window:

```bash
for attempt in 1 2 3 4 5 6; do
  echo "===== Request $attempt ====="
  curl -sS -D /tmp/quota-$attempt.headers \
    -o /tmp/quota-$attempt.body -w 'HTTP %{http_code}\n' \
    "$API_RESOURCE/documents" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "X-DocIntel-Workspace-ID: $WORKSPACE_ID"
  grep -iE 'x-docintel-usage|x-ratelimit|retry-after' \
    /tmp/quota-$attempt.headers || true
  jq . /tmp/quota-$attempt.body 2>/dev/null || \
    sed -n '1,40p' /tmp/quota-$attempt.body
done
```

Expected:

- Allowed calls include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and
  `X-RateLimit-Reset`.
- The next call returns HTTP 429, `quota_exceeded`, and `Retry-After`.
- Calls work after the window resets.

Existing calls may make 429 occur before attempt six. Test separate policies
for application, workspace, scope, operation, and combined dimensions. A
non-matching endpoint must not consume a specific `get.documents` policy.
Multiple matching policies are enforced together.

## 10. Test MCP Accounting

```bash
source deploy.sh --oauth-login --oauth-target mcp

mcp_tool list_workspaces '{}' | tool_data | jq
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}')" | tool_data | jq
```

Confirm normalized `mcp.*` operations appear in **Usage & Security**. Create a
temporary matching policy and verify excess MCP calls return structured
`quota_exceeded`, not an unstructured server error.

## 11. Test Secret Rotation

In **Usage & Security > Credentials**, rotate the secret and store the new value:

```bash
export OLD_CLIENT_SECRET="$CLIENT_SECRET"
export NEW_CLIENT_SECRET="..."

get_service_token "$OLD_CLIENT_SECRET"
get_service_token "$NEW_CLIENT_SECRET"
```

Both must work during overlap. The UI must show only secret hints and lifecycle
timestamps. Revoke the old credential, then test again:

```bash
get_service_token "$OLD_CLIENT_SECRET" && \
  echo "ERROR: revoked secret worked" || echo "Old secret rejected"

get_service_token "$NEW_CLIENT_SECRET"
export CLIENT_SECRET="$NEW_CLIENT_SECRET"
```

The old secret must return `invalid_client`; the new secret must work. Revoking
the final active credential must be rejected.

## 12. Test the Source-IP Allowlist

```bash
export CURRENT_IP="$(curl -sS https://api.ipify.org)"
echo "$CURRENT_IP"
```

Add `$CURRENT_IP/32` in **Usage & Security > Source IPs** (`/128` for IPv6) and
confirm `get_service_token` works. Temporarily replace it with `192.0.2.1/32`
and confirm token issuance returns `invalid_client`. Restore the correct CIDR.

CIDRs are checked during token issuance. Existing tokens remain usable until
expiration unless another live policy blocks them. An empty allowlist permits
all source addresses.

## 13. Verify and Export OpenAPI

```bash
curl -sS -D /tmp/openapi.headers -o /tmp/openapi.json \
  "$API_BASE/openapi.json"
grep -iE 'HTTP/|content-type' /tmp/openapi.headers
jq -r '.openapi, .info.title, (.paths | length)' /tmp/openapi.json
```

Expected: `application/json` and a nonzero path count. If HTML is returned,
deploy `bash deploy.sh --frontend`; the SPA fallback is intercepting the path.

Direct backend fallback:

```bash
export BACKEND_RUN_URL="https://docintel-backend-tzwvc47f5q-uc.a.run.app"
curl -sS "$BACKEND_RUN_URL/openapi.json" | jq -r '.openapi'
```

Generate and validate the public-only contract:

```bash
./.venv/bin/python scripts/generate_public_sdks.py \
  --url "$API_BASE/openapi.json"

jq -r '.info.title, (.paths | length)' \
  sdks/openapi/docintel-public-api.json

jq -e '[.paths | keys[] |
  select(startswith("/api/v1/developer/"))] | length == 0' \
  sdks/openapi/docintel-public-api.json
```

Use `--url "$BACKEND_RUN_URL/openapi.json"` until the Firebase rewrite is live.

## 14. Python SDK Smoke Test

```bash
PYTHONPATH=sdks/python \
DOCINTEL_ACCESS_TOKEN="$ACCESS_TOKEN" \
DOCINTEL_WORKSPACE_ID="$WORKSPACE_ID" \
python3 - <<'PY'
import json, os
from docintel_client import DocIntelClient

client = DocIntelClient(
    "https://docintel.adar.agomoniai.com",
    os.environ["DOCINTEL_ACCESS_TOKEN"],
    os.environ.get("DOCINTEL_WORKSPACE_ID"),
)
print(json.dumps(client.me(), indent=2))
print(json.dumps(client.documents(), indent=2))
PY
```

## 15. JavaScript SDK Smoke Test

```bash
DOCINTEL_ACCESS_TOKEN="$ACCESS_TOKEN" \
DOCINTEL_WORKSPACE_ID="$WORKSPACE_ID" \
node --input-type=module <<'JS'
import { DocIntelClient } from './sdks/javascript/docintel.js';
const client = new DocIntelClient({
  baseUrl: 'https://docintel.adar.agomoniai.com',
  accessToken: process.env.DOCINTEL_ACCESS_TOKEN,
  workspaceId: process.env.DOCINTEL_WORKSPACE_ID || null,
});
console.log(JSON.stringify(await client.me(), null, 2));
console.log(JSON.stringify(await client.documents(), null, 2));
JS
```

## 16. Java SDK Smoke Test

```bash
rm -rf /tmp/docintel-java-test
mkdir -p /tmp/docintel-java-test/com/agomonia/docintel
cp sdks/java/DocIntelClient.java \
  /tmp/docintel-java-test/com/agomonia/docintel/DocIntelClient.java

cat >/tmp/docintel-java-test/TestDocIntel.java <<'JAVA'
import com.agomonia.docintel.DocIntelClient;
public class TestDocIntel {
  public static void main(String[] args) throws Exception {
    DocIntelClient client = new DocIntelClient(
      "https://docintel.adar.agomoniai.com",
      System.getenv("DOCINTEL_ACCESS_TOKEN"),
      System.getenv("DOCINTEL_WORKSPACE_ID"));
    System.out.println(client.me());
    System.out.println(client.documents());
  }
}
JAVA

cd /tmp/docintel-java-test
javac com/agomonia/docintel/DocIntelClient.java TestDocIntel.java
DOCINTEL_ACCESS_TOKEN="$ACCESS_TOKEN" \
DOCINTEL_WORKSPACE_ID="$WORKSPACE_ID" java TestDocIntel
cd /Users/brajadas/project/adar-rag
```

All SDK responses must identify the same principal and workspace data.

## 17. Database Verification

Run through Cloud SQL Studio or an authorized `psql` session:

```sql
SELECT client_id, workspace_id, operation, status_code,
       COUNT(*) AS requests, SUM(quantity) AS units,
       AVG(latency_ms)::bigint AS average_latency_ms
FROM usage_events
WHERE created_at > NOW() - INTERVAL '1 day'
GROUP BY client_id, workspace_id, operation, status_code
ORDER BY requests DESC;

SELECT p.policy_name, p.scope, p.operation, p.limit_value,
       c.window_start, c.used_units, c.reserved_units
FROM usage_quota_policies p
LEFT JOIN usage_quota_counters c ON c.policy_id = p.id
ORDER BY c.window_start DESC NULLS LAST;

SELECT client_id, operation, status, units_reserved, units_used,
       expires_at, reconciled_at
FROM usage_reservations
ORDER BY created_at DESC LIMIT 100;

SELECT client_id, name, secret_hint, expires_at, last_used_at, revoked_at
FROM oauth_service_client_secrets
ORDER BY created_at DESC;

SELECT client_id, cidr, created_at
FROM oauth_service_ip_allowlists
ORDER BY client_id, cidr;
```

Completed reservations must reconcile, reserved units must return to zero, and
interrupted reservations must eventually become `expired`. Raw secrets must
never be stored. Correlate `usage_events.trace_id` with Trace Explorer.

## 18. Production Troubleshooting

Backend HTTP 500:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="docintel-backend"
   AND severity>=ERROR' \
  --project="bdas-493785" --limit=30 --freshness=30m \
  --format='value(timestamp,textPayload,jsonPayload.message)'
```

MCP errors:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="docintel-mcp"
   AND severity>=ERROR' \
  --project="bdas-493785" --limit=30 --freshness=30m \
  --format='value(timestamp,textPayload,jsonPayload.message)'
```

Common diagnoses:

- `jq: parse error`: inspect the saved body; HTML and plain-text errors are not JSON.
- OpenAPI returns HTML: deploy the frontend rewrite or use the backend URL.
- HTTP 401 `invalid_client`: inspect secret, app state, expiry, and CIDRs.
- HTTP 403 `insufficient_scope`: approve the scope and obtain a new token.
- HTTP 403 workspace denial: verify the app grant and workspace header.
- Unexpected HTTP 429: inspect every matching active policy.
- Asyncpg parameter error: verify the deployed quota reservation query contains
  explicit UUID, timestamp, and bigint casts.

## 19. Cleanup

1. Revoke temporary quota policies.
2. Remove temporary CIDR restrictions.
3. Revoke old credentials after overlap validation.
4. Revoke the dedicated test application if it is no longer needed.

```bash
rm -rf /tmp/docintel-java-test
rm -f /tmp/docintel-response.* /tmp/quota-*.headers /tmp/quota-*.body
```

## 20. Acceptance Checklist

- [ ] Backend, MCP, and frontend checks pass.
- [ ] Confidential application receives an API-audience token.
- [ ] Identity, scope, and workspace boundaries are correct.
- [ ] REST and MCP calls appear in the shared usage view.
- [ ] Quota headers and structured HTTP 429 responses are correct.
- [ ] Traffic resumes after quota reset.
- [ ] Secret overlap, revocation, and final-secret protection work.
- [ ] CIDR allow and deny tests work.
- [ ] Public OpenAPI excludes internal developer routes.
- [ ] Python, JavaScript, and Java clients return matching data.
- [ ] Counters reconcile and expired reservations are cleaned up.
- [ ] Usage events correlate with OpenTelemetry traces.

## Related Documentation

- [Architecture and Operations](usage_quotas_credentials_sdks.md)
- [Developer Applications Testing](developer_applications_end_to_end_testing.md)
- [REST API Testing](rest_api_end_to_end_testing.md)
- [MCP Service Identity Testing](mcp_service_identity_end_to_end_testing.md)
- [Webhook Testing](webhook_end_to_end_testing.md)
- [SDK Overview](../sdks/README.md)
