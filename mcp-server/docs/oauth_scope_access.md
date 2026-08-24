# MCP OAuth Scope Access

DocIntel separates scopes supported by the MCP server from scopes assigned to a
specific user. A non-admin user cannot obtain a scope merely by including it in
an OAuth authorization URL. Once approved, that user scope applies across CLI,
MCP Playground, and other dynamically registered DocIntel MCP clients.

## Authorization Model

```text
Client requests scopes
  -> DocIntel validates supported scope names
  -> user completes password and MFA verification
  -> DocIntel compares requested scopes with active user grants
  -> missing scopes become pending access requests
  -> administrator approves or denies each request
  -> user repeats OAuth login
  -> authorization code and tokens contain approved scopes only
  -> MCP introspects the token on access
  -> each tool/resource verifies its required scope
```

Scope grants are user-level, which supports dynamically registered public PKCE
clients without repeated approval. The requesting client ID remains recorded
for authorization, token binding, and audit history, but it does not determine
whether the user owns a scope. Administrators have the supported scope catalog
for operational bootstrap; regular users receive only active, unexpired grants.

The CLI OAuth helper still persists its dynamically registered client ID in
`~/.config/docintel/mcp-oauth-client.json` for stable client identity and audit
history. `DOCINTEL_OAUTH_CLIENT_ID` can explicitly select a registered client.

## User Request Flow

The normal OAuth login automatically creates pending requests when scopes are
missing. After identity and MFA verification, the authorization page lists the
scopes awaiting approval. The user retries OAuth after an administrator acts.

The MCP Playground no longer requests every supported scope by default. Before
connecting, the user selects one of these access profiles:

- **Read and query** for workspace/document discovery, knowledge queries,
  summaries, and read-only video, workflow, and batch status.
- **Content operations** for document upload, generation, sessions, video
  processing, and batch execution in addition to read access.
- **Governed workflows** for vertical workflow changes, human review approval,
  and packet generation in addition to content operations.

Each profile still requires the corresponding user grants.

An existing access token does not gain a newly approved scope automatically.
In MCP Playground, select the required profile and choose **Update access**.
For CLI usage, repeat `source mcp-server/scripts/oauth_login.sh`. Both paths
issue a new token from the user's current grants.

A signed-in DocIntel user can also request scopes through the API:

```bash
curl -sS -X POST https://docintel.adar.agomoniai.com/api/oauth/scopes/requests \
  -H "Authorization: Bearer $DOCINTEL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg client_id "$CLIENT_ID" \
    '{
      client_id:$client_id,
      scopes:["workspaces:read","documents:read","knowledge:query"],
      reason:"Read accessible documents and run grounded knowledge queries"
    }')" | jq
```

View the current user's requests and grants for that client:

```bash
curl -sS \
  "https://docintel.adar.agomoniai.com/api/oauth/scopes/me?client_id=$CLIENT_ID" \
  -H "Authorization: Bearer $DOCINTEL_ACCESS_TOKEN" | jq
```

## Administrator Review

The DocIntel Admin Dashboard includes **MCP Access** with pending requests and
active grants. Approving a request creates or renews a user-level grant.
Denying it records the decision without issuing permission. Revocation disables
the grant and revokes affected refresh-token families.

The same operations are available through the authenticated admin API:

```bash
curl -sS \
  "https://docintel.adar.agomoniai.com/api/admin/oauth/scope-requests?status=pending" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" | jq
```

Approve one request:

```bash
curl -sS -X POST \
  "https://docintel.adar.agomoniai.com/api/admin/oauth/scope-requests/$REQUEST_ID/decision" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"decision":"approved","reviewer_note":"Approved for document Q&A integration"}' | jq
```

Deny one request by changing `decision` to `denied`. Administrators can inspect
active grants with:

```bash
curl -sS \
  "https://docintel.adar.agomoniai.com/api/admin/oauth/scope-grants" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" | jq
```

Revoke an active grant:

```bash
curl -sS -X DELETE \
  "https://docintel.adar.agomoniai.com/api/admin/oauth/scope-grants/$GRANT_ID" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" | jq
```

## Enforcement Points

1. Authorization code issuance requires all requested scopes to be assigned.
2. Authorization code exchange rechecks assignments.
3. Refresh-token rotation rechecks assignments.
4. Token introspection rejects tokens containing revoked or expired grants.
5. MCP tools and resources check their specific capability before calling the
   DocIntel API.
6. Existing DocIntel workspace and document authorization remains in force, so
an MCP scope never grants access to a workspace or document by itself.

The MCP HTTP transport validates that a bearer token is active but does not
require every server capability on every request. Requiring the global enabled
capability list at the transport boundary would make a least-privilege token
unusable. For example, `summarize_document` requires only
`knowledge:generate`; it does not require `batches:write`.

After approval, assignment, or revocation, affected refresh-token families are
revoked. The user should repeat OAuth login to receive a token reflecting the
current grants.

The client ID remains useful when tracing an authorization attempt:

```bash
printf 'OAuth client: %s\n' "$CLIENT_ID"
```

To retry with a specific registered client while diagnosing OAuth:

```bash
export DOCINTEL_OAUTH_CLIENT_ID="APPROVED_CLIENT_ID"
source mcp-server/scripts/oauth_login.sh
```

## Deployment

Deploy both components because this increment changes the authorization backend
and the Admin Dashboard:

```bash
cd /Users/brajadas/project/adar-rag
./deploy.sh --backend
./deploy.sh --frontend
```

The backend startup schema creates `oauth_scope_requests` and
`oauth_scope_grants`. Existing non-admin OAuth sessions will no longer pass
introspection unless their user scopes are assigned. Administrators
should approve required requests and have users reconnect OAuth.
