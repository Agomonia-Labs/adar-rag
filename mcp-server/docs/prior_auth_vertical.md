# Healthcare Prior Authorization MCP Workflow

## 1. Login and Inputs

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
export WORKSPACE_ID="<workspace-id>"
export ENCOUNTER_DOCUMENT_ID="<encounter-document-id>"
export POLICY_DOCUMENT_ID="<payer-policy-document-id>"
```

Both documents must have completed chunking.

## 2. Start and Monitor

```bash
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" --arg encounter "$ENCOUNTER_DOCUMENT_ID" --arg policy "$POLICY_DOCUMENT_ID" \
  '{workflow:"healthcare_prior_auth",workspace_id:$workspace,document_ids:[$encounter,$policy],inputs:{policy_document_ids:[$policy]}}')" \
  | tool_data | tee /tmp/prior-auth-run.json | jq
export RUN_ID="$(jq -r '.run_id // .id // .agent_run.run_id // .agent_run.id' /tmp/prior-auth-run.json)"
mcp_tool get_vertical_run "$(jq -cn --arg id "$RUN_ID" '{vertical:"healthcare",run_id:$id}')" \
  | tool_data | tee /tmp/prior-auth-latest.json | jq
```

## 3. Review and Approve

```bash
jq '.result.review_packet // .result_data.review_packet // .review_packet // .result.approved_packet // .result_data.approved_packet' \
  /tmp/prior-auth-latest.json > /tmp/prior-auth-packet.json
mcp_tool save_vertical_review "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/prior-auth-packet.json \
  '{vertical:"healthcare",run_id:$id,packet:$packet[0],notes:"Prior authorization review completed."}')" | tool_data | jq
mcp_tool approve_vertical_run "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/prior-auth-packet.json \
  '{vertical:"healthcare",run_id:$id,packet:$packet[0],notes:"Packet approved by human reviewer.",confirm:true}')" | tool_data | jq
```

## 4. Generate PDF

```bash
mcp_tool generate_vertical_packet "$(jq -cn --arg id "$RUN_ID" \
  '{vertical:"healthcare",run_id:$id,packet_type:"prior_auth"}')" \
  | tool_data | tee /tmp/prior-auth-pdf.json | jq
export PACKET_DOCUMENT_ID="$(jq -r '.document.doc_id // .document.id' /tmp/prior-auth-pdf.json)"
```

Use packet type `missing_information` to generate a Missing Information Request.
The resulting PDF is a DocIntel document and can be embedded and queried.
