# Developer Applications End-to-End Testing

This guide validates DocIntel organization management, confidential OAuth
applications, workspace boundaries, scope requests, administrator approval,
client-credentials tokens, suspension enforcement, audit events, secret
rotation, and application revocation.

Use a dedicated non-admin test user so the scope-approval path is exercised
correctly.

## 1. Deploy

Deploy the backend first so the database schema is created before the new UI is
used. Deploy the frontend afterward.

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --backend
bash deploy.sh --frontend
```

The backend creates the `oauth_service_scope_requests` table automatically.

## 2. Prepare the Application Owner

As a DocIntel administrator:

1. Open **Admin Dashboard > MCP Access**.
2. Select the test application owner.
3. Grant `service:manage`.
4. Grant `workspaces:read`, `documents:read`, and `knowledge:query`.
5. Do not initially grant `knowledge:generate`. This is used to test the
   application scope-approval workflow.
6. Have the test user sign in again so the current session reflects the
   assigned access.

## 3. Test Organization Management

As the test application owner:

1. Open **Tools > Developer Applications > Organizations**.
2. Create an organization named `DocIntel Integration Test`.
3. Click **Manage**.
4. Add an existing registered DocIntel user.
5. Change that member through the `viewer`, `developer`, and `admin` roles.
6. Verify an organization admin cannot promote a member to owner.
7. As the original owner, promote a second member to owner.
8. Verify the final remaining owner cannot be removed or demoted.
9. Rename the organization and confirm the list updates.
10. Suspend the organization and verify its members and history remain visible.
11. Reactivate the organization.

Expected results:

- Owners can manage ownership and organization status.
- Admins can manage non-owner memberships.
- Developers and viewers can inspect the organization but cannot edit it.
- The final owner is protected from removal or demotion.
- A suspended organization retains its configuration but blocks service access.

## 4. Create a Confidential Application

Open **Developer Applications > Applications** and create an application with:

```text
Name: E2E Test Integration
Organization: DocIntel Integration Test
Workspace: A non-personal team workspace
Scopes: workspaces:read documents:read knowledge:query
```

Save the credentials shown after creation:

```bash
export CLIENT_ID="svc_..."
export CLIENT_SECRET="..."
export WORKSPACE_ID="..."
```

The client secret is displayed only once. Store production secrets in a secret
manager rather than source control, deployment logs, browser storage, or shell
history.

## 5. Obtain a Service Token

```bash
export OAUTH_ISSUER="https://auth.docintel.adar.agomoniai.com"
export API_RESOURCE="https://docintel.adar.agomoniai.com/api/v1"

TOKEN_RESPONSE="$(
  curl -sS -X POST "$OAUTH_ISSUER/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$CLIENT_SECRET" \
    --data-urlencode "scope=workspaces:read documents:read knowledge:query" \
    --data-urlencode "resource=$API_RESOURCE"
)"

printf '%s\n' "$TOKEN_RESPONSE" | jq
export ACCESS_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -r '.access_token // empty')"

test -n "$ACCESS_TOKEN" || {
  echo "Token generation failed"
  exit 1
}
```

Client Credentials does not return a refresh token. Request a new short-lived
token when the existing token expires or application access changes.

## 6. Validate Identity and Workspace Access

Inspect the service identity:

```bash
curl -sS "$API_RESOURCE/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-DocIntel-Workspace-ID: $WORKSPACE_ID" | jq
```

List documents in the granted workspace:

```bash
curl -sS "$API_RESOURCE/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-DocIntel-Workspace-ID: $WORKSPACE_ID" | jq
```

Expected results:

- The response contains the correct `client_id` and scope set.
- Only documents from the explicitly granted team workspace are returned.
- The organization service identity cannot use a personal workspace.
- An ungranted workspace is rejected.

## 7. Test Application Editing

Open **Developer Applications > Applications > Manage**:

1. Add a second permitted team workspace.
2. Remove the original workspace temporarily and save.
3. Obtain a new access token.
4. Verify the removed workspace is rejected.
5. Restore the original workspace.
6. Remove and restore an already approved scope.
7. Obtain another token and verify its scope claims match the saved application.

Existing tokens do not acquire new claims. Always request a new token after a
scope or workspace change.

## 8. Request an Additional Scope

In **Application Access > Request additional scopes**:

1. Select `knowledge:generate`.
2. Enter a reason describing the least-privilege integration requirement.
3. Submit the request.
4. Verify the application request history shows `pending`.

If the owner already has `knowledge:generate`, the scope is enabled immediately
instead of entering the administrator queue. Revoke that test assignment first
when validating the approval path.

## 9. Approve the Scope as an Administrator

1. Open **Admin Dashboard > MCP Access**.
2. Locate **Pending application scope requests**.
3. Confirm the organization, application, requesting user, scope, and reason.
4. Approve the request with an optional review note.
5. Return to the application and verify the request shows `approved`.

Approval updates both the owner's active assignment and the selected
confidential application. It does not mutate an existing access token.

## 10. Obtain a Token with the Approved Scope

```bash
TOKEN_RESPONSE="$(
  curl -sS -X POST "$OAUTH_ISSUER/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$CLIENT_SECRET" \
    --data-urlencode "scope=workspaces:read documents:read knowledge:query knowledge:generate" \
    --data-urlencode "resource=$API_RESOURCE"
)"

printf '%s\n' "$TOKEN_RESPONSE" | jq
export ACCESS_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -r '.access_token // empty')"

test -n "$ACCESS_TOKEN" || {
  echo "Token generation failed"
  exit 1
}
```

## 11. Test Summary Generation

Select an embedded document from the granted workspace:

```bash
export DOCUMENT_ID="<document-id>"

curl -N -X POST \
  "$API_RESOURCE/summaries/documents/$DOCUMENT_ID/stream" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-DocIntel-Workspace-ID: $WORKSPACE_ID" \
  -H "Content-Type: application/json" \
  --data '{
    "summary_type":"executive",
    "custom_prompt":"",
    "chunk_indices":[],
    "redact_pii":false
  }'
```

Expected result: DocIntel streams the summary instead of returning
`insufficient_scope`.

## 12. Test Scope Denial

1. Request another unassigned scope from the application.
2. Deny it from **Admin Dashboard > MCP Access**.
3. Verify the application request history shows `denied` and the review note.
4. Attempt to request a service token containing the denied scope.
5. Confirm token issuance is rejected.

## 13. Test Organization Suspension

1. Suspend the organization.
2. Retry the `/documents` call using the existing service token.
3. Confirm the request is rejected even though the token has not expired.
4. Reactivate the organization.
5. Obtain a fresh service token.
6. Confirm the granted workspace is accessible again.

This validates that organization status is checked during service-token use and
is not merely copied into a long-lived browser state.

## 14. Test Audit and Credential Lifecycle

From the application screen:

1. Open **Audit**.
2. Verify events for application creation, scope requests, approval or denial,
   workspace updates, scope updates, and secret rotation.
3. Rotate the client secret.
4. Confirm the previous secret can no longer obtain a token.
5. Confirm the replacement secret works.
6. Revoke the application.
7. Confirm neither secret can obtain another token.

## 15. Final Acceptance Checklist

- [ ] Organization owner, admin, developer, and viewer boundaries are enforced.
- [ ] Final-owner removal and demotion are rejected.
- [ ] Suspension blocks service access without deleting configuration.
- [ ] Application scopes and workspace grants can be edited.
- [ ] Personal workspace access is rejected for organization applications.
- [ ] Additional scopes can be requested with a reason.
- [ ] Administrators can approve or deny application scope requests.
- [ ] Approval updates both owner assignment and application policy.
- [ ] Existing tokens do not silently gain newly approved scopes.
- [ ] Newly issued tokens contain only requested and approved scopes.
- [ ] Ungranted workspaces are rejected.
- [ ] Audit events are visible and readable.
- [ ] Rotated secrets invalidate the prior secret.
- [ ] Revoked applications cannot obtain tokens.

## Related Documentation

- [Enterprise OAuth Applications](enterprise_oauth_applications.md)
- [REST API End-to-End Testing](rest_api_end_to_end_testing.md)
- [REST API Workspace Access](rest_api_workspace_access.md)
