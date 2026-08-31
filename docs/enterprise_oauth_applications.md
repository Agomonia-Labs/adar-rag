# Enterprise OAuth Applications

DocIntel organization applications provide machine-to-machine access to the
public REST API. Each confidential application belongs to an organization,
receives an explicit scope set, and is restricted to explicitly granted team
workspaces.

Use **Tools > Developer Applications** in DocIntel to create organizations and
applications, rotate or revoke credentials, and inspect credential audit events.
The client secret is displayed only when the application is created or rotated.

## Security Boundary

- A service token represents an application, not an interactive user session.
- The application owner must retain the requested DocIntel scope assignments.
- An organization must remain active.
- Every selected workspace must be listed in the application's workspace grants.
- The application owner must still be a member of the selected workspace.
- Personal workspace access is not available to organization service identities.
- Secrets are hashed at rest and cannot be recovered after initial display.
- Revoked applications cannot obtain new tokens.
- Organization service applications currently target the REST API only. MCP
  remains user-delegated until organization workspace enforcement is enabled
  across its complete tool and resource surface.

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

Request only scopes assigned to the application. The resource must be the REST
API audience.

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
| List or update members | `GET/PUT /api/v1/developer/organizations/{id}/members` |
| Remove a member | `DELETE /api/v1/developer/organizations/{id}/members/{user_id}` |
| List or register applications | `GET/POST /api/v1/developer/apps` |
| Application detail | `GET /api/v1/developer/apps/{client_id}` |
| Replace scopes | `PUT /api/v1/developer/apps/{client_id}/scopes` |
| Replace workspace grants | `PUT /api/v1/developer/apps/{client_id}/workspaces` |
| Rotate secret | `POST /api/v1/developer/apps/{client_id}/rotate-secret` |
| Application audit | `GET /api/v1/developer/apps/{client_id}/audit` |
| Revoke application | `DELETE /api/v1/developer/apps/{client_id}` |
