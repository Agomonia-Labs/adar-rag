# Enterprise MCP Testing

This runbook tests the enterprise MCP lifecycle through the browser Playground
and the terminal CLI. Replace every `YOUR_*` placeholder before running a
browser command.

## Browser and CLI syntax

The MCP Playground supports:

```text
mcp_tool <tool-name> '<literal-json>' | tool_data | jq '.'
mcp_request '<literal-json-rpc>'
```

It accepts multiline commands and `\` line continuations. It intentionally
does not execute `export`, `$VARIABLES`, `$(...)`, `jq -n`, arbitrary shell
commands, or unrestricted `jq` programs. Those features work only in a real
terminal after `source deploy.sh --oauth-login`.

## 1. Deploy and verify

From the repository root:

```bash
cd /Users/brajadas/project/adar-rag
bash deploy.sh --backend
bash deploy.sh --mcp
bash deploy.sh --frontend
```

Verify production health:

```bash
curl -sS https://docintel.adar.agomoniai.com/api/health | jq
curl -sS https://mcp.docintel.adar.agomoniai.com/health | jq
```

Expected: both responses report `status: ok`.

## 2. Grant OAuth scopes

Grant the test user these scopes in the Admin Dashboard:

```text
workspaces:read
documents:read
documents:write
knowledge:query
knowledge:generate
sessions:write
video:read
video:process
workflows:read
workflows:write
reviews:write
reviews:approve
packets:write
batches:read
batches:write
events:read
events:write
artifacts:read
artifacts:write
versions:read
versions:write
evaluations:run
```

Existing tokens do not gain newly approved scopes. In the Playground, choose
the appropriate profile and click **Connect OAuth** or **Update access**. For
the CLI, request a fresh token:

```bash
source deploy.sh --oauth-login
```

The CLI token and browser Playground session are separate. CLI OAuth does not
mark the browser Playground as connected.

## 3. MCP discovery

Initialize the protocol:

```text
mcp_request '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"docintel-enterprise-test","version":"1.0"}}}'
```

List tools and resources:

```text
mcp_request '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
mcp_request '{"jsonrpc":"2.0","id":3,"method":"resources/list","params":{}}'
```

Discover enterprise capabilities:

```text
mcp_tool get_enterprise_capabilities '{}' | tool_data | jq '.'
```

Expected capabilities include `events`, `idempotency`, `reviews`, `artifacts`,
`versions`, `evaluations`, and `service_oauth`.

## 4. Find workspaces and documents

```text
mcp_tool list_workspaces '{}' | tool_data | jq '.'
```

Copy an accessible workspace ID, then run:

```text
mcp_tool list_documents '{"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
```

Copy at least two document IDs for later tests:

```text
YOUR_DOCUMENT_ID_1
YOUR_DOCUMENT_ID_2
```

Read one document and its chunks:

```text
mcp_tool get_document '{"document_id":"YOUR_DOCUMENT_ID_1"}' | tool_data | jq '.'
mcp_tool get_document_chunks '{"document_id":"YOUR_DOCUMENT_ID_1"}' | tool_data | jq '.'
```

## 5. Workflow contracts

```text
mcp_tool get_workflow_schema '{"workflow":"healthcare_prior_auth"}' | tool_data | jq '.'
```

Validate complete inputs without starting a run:

```text
mcp_tool validate_workflow_inputs '{"workflow":"healthcare_prior_auth","inputs":{"document_ids":["YOUR_DOCUMENT_ID_1"],"policy_document_ids":["YOUR_DOCUMENT_ID_2"]}}' | tool_data | jq '.'
```

Expected: `valid: true`. Remove `policy_document_ids` to verify that the
contract reports the missing input.

Other workflow keys include:

```text
healthcare_clinical
healthcare_prior_auth
finance_tax_readiness
talent_readiness
employee_mobility
lease_intelligence
```

## 6. Idempotent batch processing

Start embedding with a stable idempotency key:

```text
mcp_tool start_batch_embedding '{"workspace_id":"YOUR_WORKSPACE_ID","document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"concurrency":2,"force":false,"idempotency_key":"manual-embed-test-001"}' | tool_data | jq '.'
```

Copy the returned batch job ID. Run the exact command again. Expected:

```json
{"idempotent_replay": true}
```

Reuse the same key with different document IDs. Expected error:

```text
idempotency_conflict
```

Monitor and control the job:

```text
mcp_tool get_batch_status '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
mcp_tool get_batch_results '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
mcp_tool resume_batch_job '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
```

Use retry or cancel only when the job state permits it:

```text
mcp_tool retry_batch_failures '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
mcp_tool cancel_batch_job '{"batch_job_id":"YOUR_BATCH_JOB_ID"}' | tool_data | jq '.'
```

## 7. Durable operation events

```text
mcp_tool list_operation_events '{"after_sequence":0,"limit":100}' | tool_data | jq '.'
```

Copy `next_sequence` and continue from that cursor:

```text
mcp_tool list_operation_events '{"after_sequence":YOUR_INTEGER_SEQUENCE,"limit":100}' | tool_data | jq '.'
```

Read the same cursor as an MCP resource:

```text
mcp_request '{"jsonrpc":"2.0","id":10,"method":"resources/read","params":{"uri":"docintel://events/0"}}'
```

## 8. Webhook subscriptions

The callback must be a publicly reachable HTTPS endpoint:

```text
mcp_tool create_event_subscription '{"event_types":["batch.completed","batch.completed_with_errors"],"workspace_id":"YOUR_WORKSPACE_ID","webhook_url":"https://YOUR_PUBLIC_HOST/docintel/events"}' | tool_data | jq '.'
```

Store the returned webhook secret; it is shown only once. List subscriptions:

```text
mcp_tool list_event_subscriptions '{}' | tool_data | jq '.'
```

Delete the test subscription:

```text
mcp_tool delete_event_subscription '{"subscription_id":"YOUR_SUBSCRIPTION_ID"}' | tool_data | jq '.'
```

The receiver should validate `X-DocIntel-Signature-SHA256` using the returned
secret. Cursor polling remains the recovery mechanism if delivery fails.

## 9. Human review queue

Obtain an existing vertical run:

```text
mcp_tool list_vertical_runs '{"vertical":"healthcare","limit":10}' | tool_data | jq '.'
```

Create a review task:

```text
mcp_tool create_review_task '{"vertical":"healthcare","run_id":"YOUR_VERTICAL_RUN_ID","title":"Enterprise MCP review test","workspace_id":"YOUR_WORKSPACE_ID","priority":"normal","metadata":{"source":"manual_test"}}' | tool_data | jq '.'
```

List, assign, and decide:

```text
mcp_tool list_review_tasks '{"status":"pending"}' | tool_data | jq '.'
mcp_tool assign_review_task '{"task_id":"YOUR_REVIEW_TASK_ID"}' | tool_data | jq '.'
mcp_tool submit_review_decision '{"task_id":"YOUR_REVIEW_TASK_ID","decision":"approved","reviewer_notes":"Reviewed through the enterprise MCP test."}' | tool_data | jq '.'
```

Supported decisions are `approved`, `changes_requested`, and `rejected`.

## 10. Knowledge artifacts

```text
mcp_tool save_knowledge_artifact '{"workspace_id":"YOUR_WORKSPACE_ID","artifact_type":"reviewed_summary","title":"Enterprise MCP test summary","content":{"summary":"Governed artifact test","review_status":"reviewed"},"source_document_ids":["YOUR_DOCUMENT_ID_1"],"status":"reviewed"}' | tool_data | jq '.'
```

List artifacts through a tool and resource:

```text
mcp_tool list_knowledge_artifacts '{"workspace_id":"YOUR_WORKSPACE_ID"}' | tool_data | jq '.'
mcp_request '{"jsonrpc":"2.0","id":11,"method":"resources/read","params":{"uri":"docintel://artifacts/YOUR_WORKSPACE_ID"}}'
```

## 11. Document version lineage

Use two related documents that the current user can access:

```text
mcp_tool register_document_version '{"document_id":"YOUR_DOCUMENT_ID_2","previous_document_id":"YOUR_DOCUMENT_ID_1","change_summary":"Manual MCP versioning test","changed_pages":[1,2]}' | tool_data | jq '.'
mcp_tool list_document_versions '{"document_id":"YOUR_DOCUMENT_ID_2"}' | tool_data | jq '.'
```

Resource form:

```text
mcp_request '{"jsonrpc":"2.0","id":12,"method":"resources/read","params":{"uri":"docintel://documents/YOUR_DOCUMENT_ID_2/versions"}}'
```

This records lineage and changed-page metadata. It does not run page-diff OCR
or selective changed-page embedding.

## 12. Federated retrieval and citations

```text
mcp_tool search_federated_knowledgebase '{"question":"Compare the important facts, risks, and next actions.","workspace_ids":["YOUR_WORKSPACE_ID"],"document_ids":["YOUR_DOCUMENT_ID_1","YOUR_DOCUMENT_ID_2"],"redact_pii":false}' | tool_data | jq '.'
```

Verify normalized sources where available:

```text
citation_id
document_id
document_name
chunk_id
chunk_index
page_number
start_seconds
end_seconds
retrieval_score
rerank_score
confidence
source_url
excerpt
```

## 13. Trace inspection and evaluation

Generate a trace with grounded search:

```text
mcp_tool search_knowledgebase '{"question":"Summarize the important information.","workspace_id":"YOUR_WORKSPACE_ID","document_ids":["YOUR_DOCUMENT_ID_1"],"redact_pii":false}' | tool_data | jq '.'
```

List recent requester-owned traces:

```text
mcp_tool list_my_traces '{"workspace_id":"YOUR_WORKSPACE_ID","limit":20}' | tool_data | jq '.'
```

Copy a trace ID, then inspect and evaluate it:

```text
mcp_tool get_my_trace '{"trace_id":"YOUR_TRACE_ID"}' | tool_data | jq '.'
mcp_tool evaluate_trace_quality '{"trace_id":"YOUR_TRACE_ID","evaluation_type":"groundedness"}' | tool_data | jq '.'
```

Expected evaluation fields include score, outcome, span success rate, response
presence, citation count, and normalized citations. This deterministic check is
a baseline and does not replace golden datasets or expert evaluation.

## 14. OAuth service client

An administrator creates unattended service clients through the backend API:

```bash
curl -sS -X POST "$DOCINTEL_API_URL/api/admin/oauth/service-clients" \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "client_name":"nightly-ingestion-test",
    "owner_user_id":"SERVICE_OWNER_USER_ID",
    "scopes":["workspaces:read","documents:read","batches:read","batches:write","events:read"]
  }' | tee /tmp/docintel-service-client.json | jq
```

Store the returned secret immediately. Exchange it for a token:

```bash
curl -sS -X POST "$OAUTH_ISSUER/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "scope=workspaces:read documents:read batches:read batches:write events:read" \
  --data-urlencode "resource=$MCP_URL" | jq
```

Test token introspection or call MCP `initialize` using the resulting bearer
token. Revoke the service client after the test.

## 15. CLI variable-based equivalents

In a real terminal, initialize helpers and IDs:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login

export WORKSPACE_ID="YOUR_WORKSPACE_ID"
export DOCUMENT_ID_1="YOUR_DOCUMENT_ID_1"
export DOCUMENT_ID_2="YOUR_DOCUMENT_ID_2"
```

The CLI can safely construct JSON with `jq`:

```bash
mcp_tool validate_workflow_inputs "$(jq -cn \
  --arg first "$DOCUMENT_ID_1" \
  --arg policy "$DOCUMENT_ID_2" \
  '{workflow:"healthcare_prior_auth",inputs:{document_ids:[$first],policy_document_ids:[$policy]}}')" \
  | tool_data | jq
```

Do not paste this variable-based form into the browser Playground.

## 16. Expected authorization failures

Verify security boundaries with controlled negative tests:

- Remove `events:read`, obtain a fresh token, and confirm event listing returns
  `insufficient_scope`.
- Use an inaccessible workspace or document ID and confirm the API returns 404
  without exposing resource ownership.
- Reuse an idempotency key with changed arguments and confirm a conflict.
- Attempt an HTTP or private-address webhook and confirm validation rejects it.
- Attempt to evaluate another user's trace and confirm it is not found.
- Revoke an OAuth or service client and confirm its token becomes inactive.

Restore required grants and obtain a fresh token after negative testing.

## 17. Automated regression tests

```bash
cd /Users/brajadas/project/adar-rag
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m pytest mcp-server/tests -q
cd frontend && npm run build
```

Also validate changed files and deployment scripts:

```bash
cd /Users/brajadas/project/adar-rag
git diff --check
bash -n deploy.sh scripts/deploy-backend.sh deploy/mcp/deploy-mcp.sh mcp-server/scripts/oauth_login.sh
```
