# REST API Workspace Access

Public REST authorization is always derived from the OAuth token subject. A
client ID identifies the application; it does not identify the signed-in user.

## 1. Log in and verify identity

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login --oauth-target api

docintel_api_request GET /me | jq
docintel_api_request GET /me/workspaces | jq
```

If login already succeeded but the helper commands are unavailable in a new
shell, reload definitions without starting OAuth again:

```bash
export DOCINTEL_OAUTH_DEFINE_ONLY=1
source mcp-server/scripts/oauth_login.sh
unset DOCINTEL_OAUTH_DEFINE_ONLY
```

The helper uses `api_path` internally because `path` is a special zsh variable
tied to `$PATH`. Older helper versions can fail with
`docintel_api_request: command not found: curl`; reload the current file if that
message appears.

`/me/workspaces` returns Personal first, followed by owned and shared team
workspaces. Personal uses `id: null` and `key: "personal"`; it contains only
documents owned by the OAuth user with no workspace assignment.

## 2. Select a context

```bash
docintel_api_select_workspace personal

export TEAM_WORKSPACE_ID="YOUR-WORKSPACE-UUID"
docintel_api_select_workspace "$TEAM_WORKSPACE_ID"
```

The helper validates membership and exports `DOCINTEL_WORKSPACE_ID` and
`DOCINTEL_WORKSPACE_HEADER`. Requests made with `docintel_api_request` include
the `X-DocIntel-Workspace-ID` header. Resource endpoints still perform their
own ownership and membership checks.

`GET /documents` follows the selected context. With Personal selected it lists
only the OAuth user's unassigned documents. With a team workspace selected it
lists documents in that workspace:

```bash
docintel_api_select_workspace personal
docintel_api_request GET /documents | jq

docintel_api_select_workspace "$TEAM_WORKSPACE_ID"
docintel_api_request GET /documents | jq
```

The listing response includes its resolved context:

```json
{
  "data": [],
  "context": {
    "workspace_id": null,
    "workspace_type": "personal"
  }
}
```

An empty array means there are no documents in that selected context.

## 3. Create and administer a workspace

These operations require `workspaces:write`.

```bash
docintel_api_request POST /workspaces '{"name":"Finance Review"}' | jq

docintel_api_request POST "/workspaces/$TEAM_WORKSPACE_ID/members" \
  '{"email":"reviewer@example.com","role":"viewer"}' | jq

docintel_api_request PATCH "/workspaces/$TEAM_WORKSPACE_ID/members/$MEMBER_USER_ID" \
  '{"role":"editor"}' | jq

docintel_api_request DELETE "/workspaces/$TEAM_WORKSPACE_ID/members/$MEMBER_USER_ID" | jq
```

The current application membership model activates an invited, registered user
immediately and sends a notification. There is no separate pending acceptance
state in this increment.

## 4. Leave a shared workspace

```bash
docintel_api_request POST "/workspaces/$TEAM_WORKSPACE_ID/leave" '{}' | jq
```

Workspace owners cannot leave. Personal content is filtered by the OAuth
token's user ID, team content requires membership, and viewer/editor/owner role
checks remain enforced. Public workspace changes also record OAuth client,
scope, workspace, operation, and outcome in the audit log.
