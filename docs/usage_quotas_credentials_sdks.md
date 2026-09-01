# Usage, Quotas, Credential Hardening, and SDKs

For complete production test commands and acceptance checks, use
[Usage, Quotas, Credentials, and SDK End-to-End Testing](usage_quotas_credentials_sdks_end_to_end_testing.md).

This increment uses DocIntel's existing Cloud Run services, Cloud SQL/PostgreSQL, scheduler loop, Secret Manager, GCS, and OpenTelemetry deployment. It does not introduce Redis, an API gateway, or another worker tier.

## Architecture

1. OAuth authenticates a user or confidential application and issues an audience-bound token.
2. Public REST middleware validates application, organization, scope, and workspace grants.
3. MCP uses an internal authenticated reserve/reconcile bridge so tool and resource calls enter the same usage ledger.
4. PostgreSQL atomically reserves quota units before work begins and reconciles them after completion.
5. `usage_events` remains the canonical ledger. It records application, organization, workspace, scope, operation, status, latency, bytes, tokens, request ID, and trace ID.
6. The existing scheduler releases expired reservations left by interrupted requests.
7. Developer Applications > Usage & Security provides usage totals, operation breakdowns, quota policy management, credential rotation, revocation, and CIDR restrictions.

Policy matching supports organization, application, workspace, OAuth scope, and normalized operation. Multiple matching policies are enforced together.

## Credential behavior

- New confidential applications receive one secret shown once.
- Rotation creates a new secret and keeps prior active secrets valid for a bounded overlap period (24 hours by default).
- Each secret has an independent hint, expiry, last-used timestamp, and revocation state.
- The final active secret cannot be revoked.
- An optional IPv4/IPv6 CIDR allowlist is enforced during `client_credentials` token issuance. An empty allowlist allows all source addresses.
- Store raw secrets in Secret Manager. DocIntel stores only SHA-256 hashes and short display hints.

## Deployment

Use the existing sequence because MCP depends on the new backend internal usage endpoints:

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --backend
bash deploy.sh --mcp
bash deploy.sh --frontend
```

Backend startup applies additive PostgreSQL schema changes. No new environment variable is required. Existing `MCP_INTROSPECTION_SECRET` / `DOCINTEL_MCP_INTROSPECTION_SECRET` values must continue to match between backend and MCP.

## End-to-end test

1. Open **Developer Applications** and select **Usage & Security**.
2. Select an active confidential application.
3. Create a policy such as 10 requests per minute. Leave scope and operation empty to cover every application call.
4. Obtain a service token with `client_credentials`, then call a public API endpoint repeatedly.
5. Confirm successful responses include `X-DocIntel-Usage-Units` and, when a policy matches, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`.
6. Confirm the next request beyond the limit returns HTTP 429 with `quota_exceeded` and `Retry-After`.
7. Run an MCP tool with the same OAuth application and verify its `mcp.<scope>` operation appears in Usage & Security.
8. Rotate the secret. Confirm both old and new secrets work during overlap, then revoke the old credential and confirm only the new secret works.
9. Add your current public IP as `/32`; confirm token issuance works from that address and is rejected from addresses outside the configured CIDRs.

Example service token request:

```bash
curl -sS -X POST "https://auth.docintel.adar.agomoniai.com/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "scope=workspaces:read documents:read" \
  --data-urlencode "resource=https://docintel.adar.agomoniai.com/api/v1" | jq
```

## OpenAPI and SDKs

Export the public-only OpenAPI contract from production:

```bash
./.venv/bin/python scripts/generate_public_sdks.py \
  --url https://docintel.adar.agomoniai.com/openapi.json
```

The public URL requires the Firebase `/openapi.json` Cloud Run rewrite included
in `firebase.json`. Before that hosting configuration is deployed, use the
backend Cloud Run service URL ending in `/openapi.json`.

Starter clients are available under `sdks/python`, `sdks/javascript`, and `sdks/java`. They consistently apply bearer authentication, team workspace selection, JSON error handling, and idempotency keys. The generated OpenAPI document under `sdks/openapi` is the contract for producing fully packaged clients with a standard generator.

## Operational checks

```sql
SELECT client_id, operation, COUNT(*), COUNT(*) FILTER (WHERE status_code >= 400)
FROM usage_events
WHERE created_at > NOW() - INTERVAL '1 day'
GROUP BY client_id, operation
ORDER BY COUNT(*) DESC;

SELECT policy_id, window_start, used_units, reserved_units
FROM usage_quota_counters
ORDER BY window_start DESC;
```

Correlate `usage_events.trace_id` with Trace Explorer to investigate slow, failed, or quota-rejected work. Keep raw prompts and document contents out of usage dimensions to avoid sensitive data exposure and high-cardinality indexes.
