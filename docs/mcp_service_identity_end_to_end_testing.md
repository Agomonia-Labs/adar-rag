# MCP Service Identity End-to-End Testing

This guide validates organization confidential applications, OAuth client
credentials, MCP-audience tokens, scope enforcement, workspace isolation,
document access, grounded Q&A, and live application revocation.

Use a non-production organization, application, team workspace, and documents
for these tests. Never place a client secret in source control or deployment
logs.

## 1. Deploy

Deploy the backend before MCP so the token issuer and introspection endpoint are
available when the MCP revision starts.

```bash
cd /Users/brajadas/project/adar-rag

bash deploy.sh --backend
bash deploy.sh --mcp
```

Verify both services:

```bash
curl -sS https://docintel.adar.agomoniai.com/api/health | jq
curl -sS https://mcp.docintel.adar.agomoniai.com/health | jq
```

## 2. Prepare Owner Access

In **Admin Dashboard > MCP Access**, grant the confidential application owner:

```text
service:manage
workspaces:read
documents:read
documents:write
knowledge:query
```

For document summary testing, also grant:

```text
knowledge:generate
```

## 3. Create an Organization Application

Open **Tools > Developer Applications** and:

1. Create or select an organization.
2. Create a confidential application.
3. Select a non-personal team workspace.
4. Assign the required scopes.
5. Save the one-time client secret securely.

Load the resulting identifiers:

```bash
export CLIENT_ID="svc_..."
export CLIENT_SECRET="..."
export WORKSPACE_ID="..."
```

## 4. Obtain an MCP Token

```bash
export OAUTH_ISSUER="https://auth.docintel.adar.agomoniai.com"
export MCP_URL="https://mcp.docintel.adar.agomoniai.com/mcp"

TOKEN_RESPONSE="$(
  curl -sS -X POST "$OAUTH_ISSUER/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$CLIENT_SECRET" \
    --data-urlencode "scope=workspaces:read documents:read documents:write knowledge:query" \
    --data-urlencode "resource=$MCP_URL"
)"

printf '%s\n' "$TOKEN_RESPONSE" | jq

export MCP_ACCESS_TOKEN="$(
  printf '%s' "$TOKEN_RESPONSE" | jq -r '.access_token // empty'
)"

test -n "$MCP_ACCESS_TOKEN" || {
  echo "MCP token generation failed"
  exit 1
}

echo "MCP token loaded: ${#MCP_ACCESS_TOKEN} characters"
```

Client Credentials does not return a refresh token. Obtain a new short-lived
token after expiration or whenever scopes or workspace grants change.

## 5. Load the MCP CLI Helpers

Load `mcp_request`, `mcp_tool`, and `tool_data` without starting interactive
PKCE OAuth:

```bash
DOCINTEL_OAUTH_DEFINE_ONLY=1 \
  source mcp-server/scripts/oauth_login.sh
```

Verify that the helper can see the service token:

```bash
echo "MCP token available: ${#MCP_ACCESS_TOKEN} characters"
```

## 6. Initialize MCP

```bash
mcp_request '{
  "jsonrpc":"2.0",
  "id":1,
  "method":"initialize",
  "params":{
    "protocolVersion":"2025-06-18",
    "capabilities":{},
    "clientInfo":{
      "name":"service-identity-test",
      "version":"1.0"
    }
  }
}' | jq
```

Expected result:

- `protocolVersion` is `2025-06-18`.
- Server information identifies ADAR DocIntel.
- The response does not contain `invalid_token`.

## 7. Discover Tools and Resources

```bash
mcp_request '{
  "jsonrpc":"2.0",
  "id":2,
  "method":"tools/list",
  "params":{}
}' | jq '.result.tools[] | {name,description}'

mcp_request '{
  "jsonrpc":"2.0",
  "id":3,
  "method":"resources/list",
  "params":{}
}' | jq
```

Tool discovery does not grant permission to execute every tool. The access
token scopes remain authoritative during each call.

## 8. Verify Workspace Filtering

```bash
mcp_tool list_workspaces '{}' | tool_data | jq
```

Only team workspaces explicitly granted to the confidential application should
appear.

List documents in the granted workspace:

```bash
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}'
)" | tool_data | tee /tmp/docintel-service-documents.json | jq
```

Select one document:

```bash
export DOCUMENT_ID="$(
  jq -r '.documents[0].id // empty' /tmp/docintel-service-documents.json
)"

test -n "$DOCUMENT_ID" || {
  echo "No document was found in the granted workspace"
  exit 1
}
```

## 9. Test Document Access

```bash
mcp_tool get_document "$(jq -cn \
  --arg document_id "$DOCUMENT_ID" \
  '{document_id:$document_id}'
)" | tool_data | jq

mcp_tool get_document_chunks "$(jq -cn \
  --arg document_id "$DOCUMENT_ID" \
  '{document_id:$document_id}'
)" | tool_data | jq
```

Expected result:

- The returned document belongs to `$WORKSPACE_ID`.
- Personal documents and documents from ungranted workspaces are not returned.

## 10. Test Grounded Q&A

```bash
mcp_tool search_knowledgebase "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg document_id "$DOCUMENT_ID" \
  '{
    question:"Summarize the important facts, risks, and next actions.",
    workspace_id:$workspace_id,
    document_ids:[$document_id],
    redact_pii:false
  }'
)" | tool_data | jq
```

Expected result:

- The answer is grounded in the selected document.
- Sources identify accessible document evidence.
- No document outside the application's workspace grants is used.

## 11. Verify Personal Workspace Rejection

```bash
mcp_tool list_documents '{"workspace_id":"personal"}' \
  | tool_data | jq
```

Expected error:

```text
workspace_required
```

An organization service identity cannot use personal workspace context.

## 12. Verify Ungranted Workspace Rejection

```bash
mcp_tool list_documents \
  '{"workspace_id":"00000000-0000-0000-0000-000000000001"}' \
  | tool_data | jq
```

Expected error:

```text
workspace_forbidden
```

## 13. Verify Scope Enforcement

If the application token does not contain `knowledge:generate`, run:

```bash
mcp_tool summarize_document "$(jq -cn \
  --arg document_id "$DOCUMENT_ID" \
  '{
    document_id:$document_id,
    summary_type:"executive",
    redact_pii:false
  }'
)" | tool_data | jq
```

Expected error:

```text
insufficient_scope
```

After a DocIntel administrator grants `knowledge:generate`:

1. Add `knowledge:generate` to the confidential application.
2. Request a new MCP token containing the scope.
3. Reload `MCP_ACCESS_TOKEN`.
4. Rerun `summarize_document`.

Existing tokens do not acquire newly approved scopes.

## 14. Verify Live Workspace Revocation

Keep the current MCP token loaded, then remove `$WORKSPACE_ID` from the
application in **Developer Applications**.

Retry:

```bash
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}'
)" | tool_data | jq
```

Expected result: authentication or workspace authorization fails even though
the JWT has not expired. MCP introspection reloads live workspace grants.

Restore the workspace grant before continuing.

## 15. Verify Application and Organization Revocation

Test each control independently:

1. Revoke the confidential application and retry `list_workspaces`.
2. Create or reactivate an application, suspend its organization, and retry.
3. Remove the application owner's workspace membership and retry.

Expected result for each case:

```text
invalid_token
```

Restore the test configuration only if it is needed for subsequent testing.

## 16. Verify Observability

Open **My Traces** for the application owner or the administrator Trace
Explorer. Confirm MCP calls include spans for:

```text
mcp.tool.execute
mcp.identity.authorized
```

The identity span should include bounded attributes such as token kind, client
ID, organization ID, required scope, and workspace-grant count. It must not
contain the access token, client secret, or complete document content.

## Expected Security Outcomes

- Organization applications can request audience-bound MCP tokens.
- Only scopes assigned to both the owner and application remain usable.
- Only explicitly granted team workspaces are discoverable.
- Personal workspace access is denied.
- ID-based destructive operations preflight the target workspace.
- Cross-workspace response data is rejected before reaching the MCP caller.
- Scope, workspace, membership, organization, and application revocation are
  enforced through live introspection.

