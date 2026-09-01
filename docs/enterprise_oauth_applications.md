# Enterprise OAuth Applications

DocIntel organization applications provide machine-to-machine access to the
public REST API and public MCP server. Each confidential application belongs to
an organization, receives an explicit scope set, and is restricted to explicitly
granted team workspaces.

Use **Tools > Developer Applications** in DocIntel to create organizations and
applications, rotate or revoke credentials, and inspect credential audit events.
The client secret is displayed only when the application is created or rotated.
The same screen supports organization membership, role and ownership changes,
organization suspension, application scope/workspace editing, and additional
scope requests.

## Security Boundary

- A service token represents an application, not an interactive user session.
- The application owner must retain the requested DocIntel scope assignments.
- An organization must remain active.
- Every selected workspace must be listed in the application's workspace grants.
- The application owner must still be a member of the selected workspace.
- Personal workspace access is not available to organization service identities.
- Secrets are hashed at rest and cannot be recovered after initial display.
- Revoked applications cannot obtain new tokens.
- MCP introspection reloads application scopes and workspace grants on every
  authenticated request; revoked access does not wait for token expiration.
- MCP tools and resources reject personal scope, ungranted workspace context,
  and cross-workspace results for organization service identities.
- Destructive document, batch, workflow, session, video, and conversation calls
  preflight the target before mutation.

## 1. Create an Organization

The signed-in user becomes the organization owner.

```bash
curl -sS -X POST "$DOCINTEL_URL/api/v1/developer/organizations" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"name":"Acme Integration Team"}' | jq
```

Save the returned organization ID:

```bash
export ORGANIZATION_ID="<organization-id>"
```

Organization owners and admins can add an existing DocIntel user by email:

```bash
curl -sS -X PUT \
  "$DOCINTEL_URL/api/v1/developer/organizations/$ORGANIZATION_ID/members" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"email":"integration.owner@example.com","role":"admin"}' | jq
```

Owners can rename, suspend, or reactivate the organization. A final owner cannot
be removed or demoted; promote another member to owner first. Suspension retains
members, applications, and audit history while preventing organization service
tokens from being used.

## 2. Register a Confidential Application

The caller needs the `service:manage` assignment and must be allowed to manage
the organization and each requested workspace.

```bash
APP_RESPONSE="$(curl -sS -X POST \
  "$DOCINTEL_URL/api/v1/developer/apps" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg name 'Procurement integration' \
    --arg organization_id "$ORGANIZATION_ID" \
    --arg workspace_id "$WORKSPACE_ID" \
    '{
      name:$name,
      client_type:"confidential",
      organization_id:$organization_id,
      workspace_ids:[$workspace_id],
      scopes:[
        "workspaces:read",
        "documents:read",
        "documents:write",
        "knowledge:query",
        "events:read"
      ]
    }')")"

printf '%s\n' "$APP_RESPONSE" | jq
export CLIENT_ID="$(printf '%s' "$APP_RESPONSE" | jq -r '.client_id')"
export CLIENT_SECRET="$(printf '%s' "$APP_RESPONSE" | jq -r '.client_secret')"
```

Store the secret in a secret manager immediately. Do not put it in source
control, deployment logs, browser storage, or shell history.

## 3. Obtain a Service Token

Request only scopes assigned to the application. Select the REST API or MCP
resource for the protocol the integration will call.

```bash
export OAUTH_ISSUER="https://auth.docintel.adar.agomoniai.com"
export API_RESOURCE="https://docintel.adar.agomoniai.com/api/v1"

TOKEN_RESPONSE="$(curl -sS -X POST "$OAUTH_ISSUER/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "scope=workspaces:read documents:read knowledge:query events:read" \
  --data-urlencode "resource=$API_RESOURCE")"

printf '%s\n' "$TOKEN_RESPONSE" | jq
export ACCESS_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -r '.access_token')"
```

Client Credentials does not return a refresh token. Request a new short-lived
access token when the current token expires.

For MCP, request an audience-bound MCP token instead:

```bash
export MCP_RESOURCE="https://mcp.docintel.adar.agomoniai.com/mcp"

MCP_TOKEN_RESPONSE="$(curl -sS -X POST "$OAUTH_ISSUER/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "scope=workspaces:read documents:read knowledge:query events:read" \
  --data-urlencode "resource=$MCP_RESOURCE")"

export DOCINTEL_ACCESS_TOKEN="$(printf '%s' "$MCP_TOKEN_RESPONSE" | jq -r '.access_token')"
```

Initialize MCP and verify that discovery returns only application-granted
workspaces:

```bash
mcp_request '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"service-test","version":"1.0"}}}' | jq
mcp_tool list_workspaces '{}' | tool_data | jq
mcp_tool list_documents "$(jq -cn --arg id "$WORKSPACE_ID" '{workspace_id:$id}')" | tool_data | jq
```

When an application has more than one workspace grant, include `workspace_id`
on every workspace-scoped MCP call. A single grant is selected automatically
where the tool supports implicit context.

## 4. Call a Granted Workspace

Send the explicit workspace on each workspace-scoped request:

```bash
curl -sS "$API_RESOURCE/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-DocIntel-Workspace-ID: $WORKSPACE_ID" | jq
```

The API rejects personal context, an ungranted workspace, a workspace the owner
can no longer access, a suspended organization, a revoked client, and scopes
removed after the token was issued.

## 5. Manage Workspace and Scope Grants

Replace the full scope set:

```bash
curl -sS -X PUT "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID/scopes" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"scopes":["workspaces:read","documents:read","knowledge:query"]}' | jq
```

If an application needs a scope that its owner has not yet been granted, submit
an approval request instead of bypassing policy:

```bash
curl -sS -X POST \
  "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID/scope-requests" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "scopes":["knowledge:generate"],
    "reason":"Generate reviewed summaries for the procurement workspace"
  }' | jq
```

Scopes already assigned to the owner are enabled immediately. Other scopes are
queued for a DocIntel administrator, who can approve or deny them from **Admin
Dashboard > MCP Access**. Approval updates both the owner's active assignment
and this confidential application. Obtain a new client-credentials token after
approval; an existing token does not acquire new claims.

Review request status:

```bash
curl -sS "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID/scope-requests" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" | jq
```

Replace the full workspace grant set:

```bash
curl -sS -X PUT "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID/workspaces" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn --arg id "$WORKSPACE_ID" '{workspace_ids:[$id]}')" | jq
```

## 6. Rotate, Audit, or Revoke

Rotation invalidates the prior secret and returns the replacement once:

```bash
curl -sS -X POST \
  "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID/rotate-secret" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" | jq
```

Inspect security-relevant application events:

```bash
curl -sS "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID/audit" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" | jq
```

Revoke the application:

```bash
curl -sS -X DELETE "$DOCINTEL_URL/api/v1/developer/apps/$CLIENT_ID" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" | jq
```

## Management Endpoints

| Purpose | Method and path |
| --- | --- |
| List or create organizations | `GET/POST /api/v1/developer/organizations` |
| Rename, suspend, or reactivate organization | `PATCH /api/v1/developer/organizations/{id}` |
| List or update members | `GET/PUT /api/v1/developer/organizations/{id}/members` |
| Remove a member | `DELETE /api/v1/developer/organizations/{id}/members/{user_id}` |
| List or register applications | `GET/POST /api/v1/developer/apps` |
| Application detail | `GET /api/v1/developer/apps/{client_id}` |
| Replace scopes | `PUT /api/v1/developer/apps/{client_id}/scopes` |
| Replace workspace grants | `PUT /api/v1/developer/apps/{client_id}/workspaces` |
| Submit or list application scope requests | `POST/GET /api/v1/developer/apps/{client_id}/scope-requests` |
| Admin list application scope requests | `GET /api/v1/developer/admin/scope-requests` |
| Admin approve or deny application scope | `POST /api/v1/developer/admin/scope-requests/{request_id}/decision` |
| Rotate secret | `POST /api/v1/developer/apps/{client_id}/rotate-secret` |
| Application audit | `GET /api/v1/developer/apps/{client_id}/audit` |
| Revoke application | `DELETE /api/v1/developer/apps/{client_id}` |
