# ADAR DocIntel Public API

For a complete runnable verification sequence, see
[REST API End-to-End Testing](rest_api_end_to_end_testing.md).
For organization-owned machine identities, explicit workspace grants, secret
rotation, and audit, see
[Enterprise OAuth Applications](enterprise_oauth_applications.md).

The versioned REST API gives enterprise applications the same governed document
and knowledge workflows used by DocIntel's UI and MCP server. REST and MCP use
the same OAuth authorization server and user scope assignments, but access
tokens are audience-bound and cannot be exchanged between the two resources.

## Endpoints

- OAuth issuer: `https://auth.docintel.adar.agomoniai.com`
- REST resource: `https://docintel.adar.agomoniai.com/api/v1`
- REST discovery: `https://auth.docintel.adar.agomoniai.com/.well-known/oauth-protected-resource/api`
- Authorization metadata: `https://auth.docintel.adar.agomoniai.com/.well-known/oauth-authorization-server`

## Initial Coverage

| Operation | Method and path | Scope |
| --- | --- | --- |
| API catalog | `GET /api/v1` | `documents:read` |
| Current OAuth identity | `GET /api/v1/me` | Any valid API scope |
| Personal, owned, and shared contexts | `GET /api/v1/me/workspaces` | `workspaces:read` |
| Select and validate workspace | `POST /api/v1/workspace-context` | `workspaces:read` |
| List workspaces | `GET /api/v1/workspaces` | `workspaces:read` |
| Get workspace | `GET /api/v1/workspaces/{id}` | `workspaces:read` |
| Create, rename, or delete workspace | `/api/v1/workspaces` | `workspaces:write` |
| Add, update, or remove members | `/api/v1/workspaces/{id}/members` | `workspaces:write` |
| List documents | `GET /api/v1/documents` | `documents:read` |
| Workspace documents | `GET /api/v1/workspaces/{id}/documents` | `documents:read` |
| Document metadata | `GET /api/v1/documents/{id}` | `documents:read` |
| Document chunks | `GET /api/v1/documents/{id}/chunks` | `documents:read` |
| Create signed upload | `POST /api/v1/uploads` | `documents:write` |
| Complete upload | `POST /api/v1/uploads/complete` | `documents:write` |
| Start embedding | `POST /api/v1/documents/{id}/embedding` | `documents:write` |
| Delete document | `DELETE /api/v1/documents/{id}` | `documents:write` |
| Grounded streaming query | `POST /api/v1/knowledge/query/stream` | `knowledge:query` |
| Streaming summary | `POST /api/v1/summaries/documents/{id}/stream` | `knowledge:generate` |
| Operations catalog | `GET /api/v1/operations/catalog` | `workflows:read` |
| Workflow schema | `GET /api/v1/workflows/{workflow}/schema` | `workflows:read` |
| Validate workflow inputs | `POST /api/v1/workflows/{workflow}/validate` | `workflows:read` |
| Batch upload | `POST /api/v1/batches/uploads` | `batches:write` |
| Batch embedding/classification | `POST /api/v1/batches/{operation}` | `batches:write` |
| Batch status/results | `GET /api/v1/batches/{id}` | `batches:read` |
| Retry, resume, or cancel batch | `POST /api/v1/batches/{id}/{action}` | `batches:write` |
| Lifecycle events | `GET /api/v1/events` | `events:read` |
| Webhook subscriptions | `/api/v1/event-subscriptions` | `events:read` or `events:write` |
| Human review tasks | `/api/v1/reviews` | `reviews:write` or `reviews:approve` |
| Knowledge artifacts | `/api/v1/artifacts` | `artifacts:read` or `artifacts:write` |
| Document versions | `/api/v1/documents/{id}/versions` | `versions:read` or `versions:write` |
| Trace evaluation | `POST /api/v1/evaluations` | `evaluations:run` |

For runnable examples of the operations increment, see
[REST API Operations Testing](rest_api_operations_testing.md).
For identity switching, Personal context, and team membership examples, see
[REST API Workspace Access](rest_api_workspace_access.md).

## Register an Application

An authenticated DocIntel user can manage applications under
`/api/v1/developer/apps`. Public applications use Authorization Code with PKCE.
Confidential service applications require the separately approved
`service:manage` scope and receive a client secret only once.

```bash
curl -sS -X POST "$API_BASE/api/v1/developer/apps" \
  -H "Authorization: Bearer $DOCINTEL_LOGIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "name":"Finance integration",
    "client_type":"public",
    "redirect_uris":["http://127.0.0.1:8765/callback"],
    "scopes":["workspaces:read","documents:read","documents:write","knowledge:query"]
  }' | jq
```

The regular DocIntel login token is used only to manage developer applications.
API calls require an OAuth access token issued with the REST resource indicator.

## OAuth Authorization Code with PKCE

The repository login helper can run the complete browser, MFA, consent, callback,
and token exchange flow for the REST audience:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login --oauth-target api

echo "API token loaded: ${#API_ACCESS_TOKEN} characters"
docintel_api_request GET /me | jq
docintel_api_request GET /me/workspaces | jq
docintel_api_select_workspace personal
docintel_api_request GET /documents | jq
```

`GET /documents` is context-aware: it returns Personal documents when
`DOCINTEL_WORKSPACE_ID=personal` and team documents after a validated team
workspace selection.

Override the production resource when testing another deployment:

```bash
source deploy.sh --oauth-login \
  --oauth-target api \
  --api-url http://localhost:8000/api/v1
```

Send the browser to `/authorize` with the registered `client_id`, callback,
approved scopes, PKCE S256 challenge, and this resource:

```text
https://docintel.adar.agomoniai.com/api/v1
```

Exchange the returned one-time code at `/token` with the original verifier.
The `resource` and `redirect_uri` must exactly match the authorization request.

## Upload, Chunk, and Embed

Create a direct upload session. File bytes go directly to cloud storage rather
than through the API process.

```bash
UPLOAD_SESSION="$(curl -sS -X POST "$API_BASE/api/v1/uploads" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn --arg workspace_id "$WORKSPACE_ID" '{
    filename:"policy.pdf",
    content_type:"application/pdf",
    file_size:12345,
    workspace_id:$workspace_id,
    redact_pii:false
  }')")"

curl -sS -X PUT "$(jq -r '.upload_url' <<<"$UPLOAD_SESSION")" \
  -H "Content-Type: application/pdf" \
  --data-binary @policy.pdf

curl -sS -X POST "$API_BASE/api/v1/uploads/complete" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg doc_id "$(jq -r '.doc_id' <<<"$UPLOAD_SESSION")" \
    --arg path "$(jq -r '.gcs_source_path' <<<"$UPLOAD_SESSION")" \
    --arg workspace_id "$WORKSPACE_ID" '{
      doc_id:$doc_id,
      gcs_source_path:$path,
      filename:"policy.pdf",
      content_type:"application/pdf",
      file_size:12345,
      workspace_id:$workspace_id,
      redact_pii:false
    }')" | jq
```

Poll `GET /api/v1/documents/{id}` until its status becomes `chunked`, then:

```bash
curl -sS -X POST "$API_BASE/api/v1/documents/$DOCUMENT_ID/embedding" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Poll until the status becomes `embedded` before running a grounded query.

## Security Model

- OAuth scopes must be approved for the user before authorization.
- Tokens are restricted to the REST audience, issuer, client, user, expiry, and scopes.
- Current scope grants are checked again on every API request, so revoked access stops immediately.
- Existing document ownership and workspace roles remain authoritative.
- Client secrets are hashed at rest and shown only when created or rotated.
- MCP access tokens are rejected by REST endpoints, and REST tokens are rejected by MCP.
