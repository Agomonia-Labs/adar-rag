# Lease Intelligence MCP Workflow

## 1. Login and Inputs

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
export WORKSPACE_ID="<workspace-id>"
export LEASE_DOCUMENT_ID="<lease-document-id>"
export AMENDMENT_DOCUMENT_ID="<optional-amendment-document-id>"
```

## 2. Start and Monitor

Without an amendment:

```bash
mcp_tool start_vertical_workflow "$(jq -cn --arg workspace "$WORKSPACE_ID" --arg lease "$LEASE_DOCUMENT_ID" \
  '{workflow:"lease_intelligence",workspace_id:$workspace,document_ids:[$lease],inputs:{}}')" \
  | tool_data | tee /tmp/lease-run.json | jq
```

With an amendment:

```bash
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" --arg lease "$LEASE_DOCUMENT_ID" --arg amendment "$AMENDMENT_DOCUMENT_ID" \
  '{workflow:"lease_intelligence",workspace_id:$workspace,document_ids:[$lease,$amendment],inputs:{amendment_document_id:$amendment}}')" \
  | tool_data | tee /tmp/lease-run.json | jq
```

```bash
export RUN_ID="$(jq -r '.run_id // .id' /tmp/lease-run.json)"
mcp_tool get_vertical_run "$(jq -cn --arg id "$RUN_ID" '{vertical:"lease",run_id:$id}')" \
  | tool_data | tee /tmp/lease-latest.json | jq
```

## 3. Validate and Approve

Lease currently has no separate MCP draft-review action. Validate and edit the
approved abstract through the established review surface, then provide it to
the approval tool:

```bash
jq '.result_data.approved_abstract // .result_data.abstract // .approved_abstract // .abstract' \
  /tmp/lease-latest.json > /tmp/lease-abstract.json
mcp_tool approve_vertical_run "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/lease-abstract.json \
  '{vertical:"lease",run_id:$id,packet:$packet[0],notes:"Lease abstract reviewed and approved.",confirm:true}')" | tool_data | jq
```

Lease PDF packet generation is not part of the current MCP increment. The
approved abstract, obligations, critical dates, clause flags, and risks remain
available through the structured run result.
