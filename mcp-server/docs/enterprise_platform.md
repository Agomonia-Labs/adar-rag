# Enterprise MCP Lifecycle

This increment adds governed lifecycle capabilities around DocIntel's existing
document, video, RAG, batch, and vertical-workflow tools. The backend remains
the authorization authority; MCP never treats an identifier as proof of access.

## Capability discovery

```bash
mcp_tool get_enterprise_capabilities '{}' | tool_data | jq
mcp_tool get_workflow_schema '{"workflow":"healthcare_prior_auth"}' | tool_data | jq
mcp_tool validate_workflow_inputs '{
  "workflow":"healthcare_prior_auth",
  "inputs":{
    "document_ids":["YOUR_DOCUMENT_ID"],
    "policy_document_ids":["YOUR_POLICY_DOCUMENT_ID"]
  }
}' | tool_data | jq
```

The browser Playground accepts multiline commands and shell-style `\` line
continuations, but it intentionally does not execute `$(...)`, `jq -n`, or
terminal environment variables. Use literal JSON IDs in browser commands. The
CLI helper may use shell variables and command substitution normally.

Workflow definitions are versioned and report required inputs, review policy,
and supported packet types. Validation does not start a workflow.

## OAuth choices

Interactive users use authorization code with S256 PKCE. Automated enterprise
jobs use `client_credentials` with a confidential service application created
from **Developer Applications**. Organization service identities are
audience-bound to MCP, limited to assigned scopes, and restricted to explicit
team-workspace grants. MCP reloads these grants during token introspection and
rejects personal or cross-workspace access.

Create an organization and confidential application as described in
`docs/enterprise_oauth_applications.md`. Legacy administrator-created service
clients remain supported for existing integrations, but organization apps are
the recommended governed deployment model.

Exchange the one-time client secret for an MCP token:

```bash
curl -sS -X POST "$OAUTH_ISSUER/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "client_secret=$CLIENT_SECRET" \
  --data-urlencode "scope=workspaces:read documents:read batches:read batches:write events:read" \
  --data-urlencode "resource=$MCP_URL" | jq
```

For organization applications, `list_workspaces` is filtered to granted
workspaces. Personal context is denied, and mutations are preflighted against
the live application grant before they reach the target operation.

## Idempotent batch operations

Supply a stable `idempotency_key` when creating upload, embedding,
classification, or workspace-summary jobs. Repeating the same request returns
the original result. Reusing a key with different arguments returns a conflict.

```bash
mcp_tool start_batch_embedding "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg key "embed-$WORKSPACE_ID-2026-08-26" \
  --argjson ids '["DOCUMENT_ID_1","DOCUMENT_ID_2"]' \
  '{workspace_id:$workspace_id,document_ids:$ids,concurrency:2,force:false,idempotency_key:$key}')" \
  | tool_data | jq
```

Use `get_batch_status`, `retry_batch_job`, `resume_batch_job`, or
`cancel_batch_job` for lifecycle control.

## Events and subscriptions

Events have a monotonically increasing sequence number. Consumers can poll
from their last committed cursor without losing lifecycle transitions:

```bash
mcp_tool list_operation_events '{"after_sequence":0,"limit":100}' | tool_data | jq
```

HTTPS webhook subscriptions provide push delivery. Each request includes a
SHA-256 HMAC signature generated with the secret returned when the subscription
is created. Keep cursor polling as the recovery path if webhook delivery fails.

```bash
mcp_tool create_event_subscription '{
  "callback_url":"https://integration.example.com/docintel/events",
  "event_types":["batch.completed","batch.completed_with_errors"]
}' | tool_data | jq
```

## Human review

Review tasks separate machine preparation from accountable decisions. Create a
task, optionally assign it, then record an approve, reject, or request-changes
decision. Every state transition is ownership checked and timestamped.

```bash
mcp_tool create_review_task '{
  "workspace_id":"WORKSPACE_ID",
  "vertical":"healthcare",
  "run_id":"RUN_ID",
  "title":"Review prior authorization packet"
}' | tool_data | jq

mcp_tool submit_review_decision '{
  "task_id":"REVIEW_TASK_ID",
  "decision":"approved",
  "note":"Evidence and coding reviewed."
}' | tool_data | jq
```

## Artifacts and document versions

Knowledge artifacts preserve reusable, reviewed output such as summaries,
decisions, and packet metadata. Document versions link a replacement document
to its lineage and record changed pages supplied by the caller.

```bash
mcp_tool save_knowledge_artifact '{
  "workspace_id":"WORKSPACE_ID",
  "artifact_type":"reviewed_summary",
  "title":"Approved lease risk summary",
  "content":{"status":"approved","summary":"..."}
}' | tool_data | jq

mcp_tool register_document_version '{
  "document_id":"NEW_DOCUMENT_ID",
  "previous_document_id":"OLD_DOCUMENT_ID",
  "change_summary":"Renewal language updated",
  "changed_pages":[4,5]
}' | tool_data | jq
```

Version registration records lineage; it does not yet perform page-diff OCR or
selective changed-page re-embedding.

## Federated retrieval, citations, and evaluation

`search_federated_knowledgebase` accepts explicit document IDs from authorized
workspaces and returns normalized citations. A citation can include document,
chunk, page, timestamp, score, and source label fields. Access is checked for
every document by the backend retrieval path.

`evaluate_trace_quality` correlates a completed trace with deterministic checks
for execution success, answer presence, and citation structure. It is an
evaluation baseline, not a replacement for domain-specific golden datasets or
human quality review.

```bash
mcp_tool evaluate_trace_quality '{"trace_id":"TRACE_ID"}' | tool_data | jq
mcp_tool get_my_trace '{"trace_id":"TRACE_ID"}' | tool_data | jq
```

## Production boundaries

- Webhooks require public HTTPS endpoints; cursor polling is the durable backup.
- Payloads use bounded previews and exclude credentials, full prompts, tokens,
  and private reasoning.
- Service-client secrets must be stored in a secret manager and rotated.
- Cloud-drive, email, repository, and provider-specific ingestion adapters are
  separate integrations and are not enabled merely by this lifecycle layer.
- Fine-grained workspace and document authorization remains enforced by the
  existing DocIntel backend on every call.
