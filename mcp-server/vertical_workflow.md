# DocIntel MCP Vertical Workflow Guide

This guide starts after deployment and covers OAuth login, workflow discovery,
workflow execution, human review, approval, PDF packet generation, embedding,
and grounded Q&A. Healthcare Prior Authorization is used as the complete
example.

## 1. OAuth Login

OAuth mode must be sourced so the access token and MCP helper functions remain
in the current terminal:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
```

Confirm the login and helpers:

```bash
echo "Token length: ${#MCP_ACCESS_TOKEN}"
type mcp_tool
type tool_data
```

## 2. Discover Vertical Workflows

```bash
mcp_tool list_vertical_workflows '{}' | tool_data | jq
```

The result describes each workflow's required inputs, review support, approval
support, and generated packet types.

## 3. Select a Workspace

```bash
mcp_tool list_workspaces '{}' \
  | tool_data \
  | tee /tmp/docintel-workspaces.json \
  | jq
```

Select a workspace from the result:

```bash
export WORKSPACE_ID="$(
  jq -r '.workspaces[0].id' /tmp/docintel-workspaces.json
)"

echo "$WORKSPACE_ID"
```

## 4. Select Source Documents

```bash
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}'
)" | tool_data | tee /tmp/docintel-documents.json | jq
```

Display document IDs, names, classifications, and processing states:

```bash
jq -r '.documents[] | [.id, .original_name, .doc_type, .status] | @tsv' \
  /tmp/docintel-documents.json
```

Set the encounter and payer-policy document IDs:

```bash
export ENCOUNTER_DOCUMENT_ID="<encounter-document-id>"
export POLICY_DOCUMENT_ID="<payer-policy-document-id>"
```

Both documents must be `chunked`, `embedding`, or `embedded`.

## 5. Start Prior Authorization

```bash
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg encounter "$ENCOUNTER_DOCUMENT_ID" \
  --arg policy "$POLICY_DOCUMENT_ID" \
  '{
    workflow:"healthcare_prior_auth",
    workspace_id:$workspace_id,
    document_ids:[$encounter,$policy],
    inputs:{policy_document_ids:[$policy]}
  }'
)" | tool_data | tee /tmp/prior-auth-run.json | jq
```

Capture the run ID:

```bash
export RUN_ID="$(
  jq -r '.run_id // .id // .agent_run.run_id // .agent_run.id' \
    /tmp/prior-auth-run.json
)"

echo "$RUN_ID"
```

## 6. Monitor Workflow Progress

Check once:

```bash
mcp_tool get_vertical_run "$(jq -cn \
  --arg run_id "$RUN_ID" \
  '{vertical:"healthcare",run_id:$run_id}'
)" | tool_data | tee /tmp/prior-auth-status.json | jq
```

Or poll until the workflow leaves `running`:

```bash
while true; do
  RESULT="$(
    mcp_tool get_vertical_run "$(jq -cn \
      --arg run_id "$RUN_ID" \
      '{vertical:"healthcare",run_id:$run_id}'
    )" | tool_data
  )"

  echo "$RESULT" | jq '{
    run_id:(.run_id // .id),
    status,
    current_step,
    error_message
  }'

  STATUS="$(echo "$RESULT" | jq -r '.status // empty')"
  [[ "$STATUS" != "running" ]] && break
  sleep 10
done
```

Continue when the run reaches `pending_approval` or another review-ready state.
Investigate `error_message` if it reaches `failed`.

## 7. Prepare the Review Packet

Retrieve the latest run:

```bash
mcp_tool get_vertical_run "$(jq -cn \
  --arg run_id "$RUN_ID" \
  '{vertical:"healthcare",run_id:$run_id}'
)" | tool_data > /tmp/prior-auth-latest.json
```

Extract the editable review packet:

```bash
jq '
  .result.review_packet //
  .result_data.review_packet //
  .review_packet //
  .result.approved_packet //
  .result_data.approved_packet
' /tmp/prior-auth-latest.json > /tmp/review-packet.json

jq . /tmp/review-packet.json
```

Review and modify `/tmp/review-packet.json` before saving it. Do not approve
unverified AI-generated content.

## 8. Save Human Review

Saving a review does not approve the packet:

```bash
mcp_tool save_vertical_review "$(jq -cn \
  --arg run_id "$RUN_ID" \
  --slurpfile packet /tmp/review-packet.json \
  '{
    vertical:"healthcare",
    run_id:$run_id,
    packet:$packet[0],
    notes:"Prior authorization packet reviewed through MCP."
  }'
)" | tool_data | jq
```

Healthcare persona-based review can include `persona` when required by the
workspace's governance configuration.

## 9. Approve the Packet

Approval is a separate and explicit human action. The MCP tool requires
`confirm:true`:

```bash
mcp_tool approve_vertical_run "$(jq -cn \
  --arg run_id "$RUN_ID" \
  --slurpfile packet /tmp/review-packet.json \
  '{
    vertical:"healthcare",
    run_id:$run_id,
    packet:$packet[0],
    notes:"Human review completed and packet approved.",
    confirm:true
  }'
)" | tool_data | jq
```

## 10. Generate the PDF Packet

```bash
mcp_tool generate_vertical_packet "$(jq -cn \
  --arg run_id "$RUN_ID" \
  '{
    vertical:"healthcare",
    run_id:$run_id,
    packet_type:"prior_auth"
  }'
)" | tool_data | tee /tmp/generated-packet.json | jq
```

Capture the generated document ID:

```bash
export PACKET_DOCUMENT_ID="$(
  jq -r '.document.doc_id // .document.id' /tmp/generated-packet.json
)"

echo "$PACKET_DOCUMENT_ID"
```

The generation response may also contain a short-lived `download_url`.

## 11. Inspect and Embed the Generated Packet

```bash
mcp_tool get_document "$(jq -cn \
  --arg id "$PACKET_DOCUMENT_ID" \
  '{document_id:$id}'
)" | tool_data | jq
```

If the packet is `chunked` but not yet `embedded`, start embedding:

```bash
mcp_tool embed_document "$(jq -cn \
  --arg id "$PACKET_DOCUMENT_ID" \
  '{document_id:$id}'
)" | tool_data | jq
```

Monitor it with:

```bash
mcp_tool get_ingestion_status "$(jq -cn \
  --arg id "$PACKET_DOCUMENT_ID" \
  '{document_id:$id}'
)" | tool_data | jq
```

## 12. Ask Questions Against the Packet

```bash
mcp_tool search_knowledgebase "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg document_id "$PACKET_DOCUMENT_ID" \
  '{
    question:"Summarize the requested service, supporting evidence, missing information, and next actions.",
    workspace_id:$workspace_id,
    document_ids:[$document_id],
    redact_pii:false
  }'
)" | tool_data | jq
```

## Other Supported Workflows

Use `list_vertical_workflows` as the authoritative capability catalog.

| Workflow | Vertical | Packet type |
| --- | --- | --- |
| `healthcare_prior_auth` | `healthcare` | `prior_auth` or `missing_information` |
| `healthcare_clinical` | `healthcare` | `after_visit_summary` |
| `finance_tax_readiness` | `finance_tax` | `advisor` |
| `talent_readiness` | `talent` | `candidate` |
| `employee_mobility` | `talent` | `mobility` |
| `lease_intelligence` | `lease` | No PDF packet in this increment |

Finance/tax and lease currently expose approval without a separate MCP draft
review action. Healthcare and talent expose both draft review and approval.

