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
knowledge:query
sessions:write
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
