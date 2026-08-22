# Public DocIntel MCP Rollout

The public MCP endpoint is an OAuth resource server. Network-level public
access to Cloud Run does not make DocIntel data public; every MCP operation must
still carry an audience-bound access token and pass DocIntel authorization.

## Stage 1: Resource Server and Discovery

Deploy the MCP service at a dedicated HTTPS origin, for example:

```text
https://mcp.docintel.adar.agomoniai.com/mcp
```

Configure these values explicitly:

```bash
export DOCINTEL_API_BASE_URL="https://docintel-backend.example.com"
export DOCINTEL_MCP_PUBLIC_URL="https://mcp.docintel.adar.agomoniai.com"
export DOCINTEL_MCP_ISSUER_URL="https://auth.docintel.adar.agomoniai.com"
export MCP_ALLOWED_HOSTS="mcp.docintel.adar.agomoniai.com"
export MCP_ALLOWED_ORIGINS="https://docintel.adar.agomoniai.com"
```

The MCP SDK publishes protected-resource metadata at:

```text
/.well-known/oauth-protected-resource/mcp
```

An unauthenticated `/mcp` request must return `401` with a
`WWW-Authenticate` header containing its `resource_metadata` URL.

## Stage 2: OAuth Authorization Server

The issuer advertised by Stage 1 must provide:

```text
/.well-known/oauth-authorization-server
/authorize
/token
```

It must support authorization code with PKCE, exact redirect URI validation,
short-lived access tokens, refresh-token rotation, revocation, and the OAuth
`resource` parameter. Support one client registration strategy:

- Pre-registered clients
- Client ID Metadata Documents
- Dynamic Client Registration

Do not advertise a `registration_endpoint` until it is implemented and policy
protected.

DocIntel implements the authorization server in `backend/routes/oauth.py`:

```text
GET  /.well-known/oauth-authorization-server
POST /register
GET  /authorize
POST /authorize
POST /authorize/verify
POST /token
POST /revoke
POST /internal/oauth/introspect
```

The browser flow reuses the DocIntel user directory and email MFA. Public
clients use authorization code with S256 PKCE and exact redirect URI matching.
Access tokens expire after 15 minutes by default. Refresh tokens rotate on
every use, and replay revokes the token family.

## Stage 3: MCP Token Boundary

Issue access tokens specifically for the MCP resource:

```text
aud = https://mcp.docintel.adar.agomoniai.com/mcp
```

Validate signature, issuer, audience, expiry, client, subject, and granted
scopes. Do not forward the inbound MCP token to the DocIntel backend. Exchange
it for a backend-specific credential or use an authenticated internal identity
assertion carrying the verified subject.

Scopes:

```text
workspaces:read
documents:read
documents:write
knowledge:query
knowledge:generate
sessions:write
video:read
video:process
```

## Stage 4: Gateway and Operations

Place an external HTTPS load balancer/API gateway and Cloud Armor in front of
Cloud Run. Configure per-client and per-user quotas, request limits, abuse
controls, structured audit events, latency/error metrics, and alerts. Never log
bearer tokens, document contents, questions, or generated answers at the
gateway.

## Stage 5: Release Gate

Run the discovery check:

```bash
chmod +x scripts/check_public_readiness.sh
DOCINTEL_MCP_PUBLIC_URL="https://mcp.docintel.adar.agomoniai.com" \
DOCINTEL_MCP_ISSUER_URL="https://auth.docintel.adar.agomoniai.com" \
./scripts/check_public_readiness.sh
```

Then test authorization and all tools with at least two different users and
workspaces. Verify that invalid audience, expired tokens, missing scopes,
cross-workspace IDs, revoked clients, and rotated refresh tokens fail cleanly.

Do not label the endpoint production-public until all five stages pass.

After adding a new OAuth scope, existing access tokens do not gain it. Redeploy
the backend and MCP service, then run `source scripts/oauth_login.sh` again to
authorize a new token containing `documents:write`, `knowledge:generate`,
`video:read`, and `video:process`.

## Deployment Sequence

Create the shared token-exchange secret without rotating the existing DocIntel
JWT key:

```bash
export PROJECT_ID="bdas-493785"
export BACKEND_SERVICE_ACCOUNT="<backend-service-account>"
export MCP_SERVICE_ACCOUNT="<mcp-service-account>"
bash deploy/mcp/setup-oauth-secret.sh
```

Deploy the backend. `scripts/deploy-backend.sh` now supplies:

```text
OAUTH_ISSUER_URL=https://auth.docintel.adar.agomoniai.com
OAUTH_MCP_RESOURCE=https://mcp.docintel.adar.agomoniai.com/mcp
MCP_INTROSPECTION_SECRET=<Secret Manager reference>
```

Map `auth.docintel.adar.agomoniai.com` to the backend Cloud Run service, then
create this Route 53 record:

```text
auth.docintel.adar.agomoniai.com CNAME ghs.googlehosted.com
```

Wait for `Ready=True` and `CertificateProvisioned=True`. Deploy the MCP service
with `deploy/mcp/deploy-mcp.sh`, using the same secret and the auth issuer URL.
Validate discovery and dynamic client registration:

```bash
chmod +x mcp-server/scripts/test_oauth_discovery.sh
DOCINTEL_MCP_ISSUER_URL="https://auth.docintel.adar.agomoniai.com" \
DOCINTEL_MCP_URL="https://mcp.docintel.adar.agomoniai.com/mcp" \
mcp-server/scripts/test_oauth_discovery.sh
```

Connect the client to `https://mcp.docintel.adar.agomoniai.com/mcp`. It should
discover the issuer, register, open the DocIntel login/MFA page, exchange its
authorization code using PKCE, and refresh tokens without manual token copying.
