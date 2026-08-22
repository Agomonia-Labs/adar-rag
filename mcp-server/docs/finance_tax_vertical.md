# Finance and Tax Readiness MCP Workflow

## 1. Login and Inputs

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
export WORKSPACE_ID="<workspace-id>"
export DOCUMENT_IDS_JSON='["<w2-id>","<tax-return-id>","<mortgage-id>"]'
```

## 2. Start and Monitor

```bash
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" --argjson documents "$DOCUMENT_IDS_JSON" \
  '{workflow:"finance_tax_readiness",workspace_id:$workspace,document_ids:$documents,inputs:{client_name:"Avery Morgan",tax_year:"2026",filing_status:"Married filing jointly",notes:"Prepare readiness review."}}')" \
  | tool_data | tee /tmp/finance-run.json | jq
export RUN_ID="$(jq -r '.run_id // .id' /tmp/finance-run.json)"
mcp_tool get_vertical_run "$(jq -cn --arg id "$RUN_ID" '{vertical:"finance_tax",run_id:$id}')" \
  | tool_data | tee /tmp/finance-latest.json | jq
```

Use `list_vertical_runs` when you need to rediscover finance runs:

```bash
mcp_tool list_vertical_runs "$(jq -cn '{vertical:"finance_tax",status:"all",limit:25}')" | tool_data | jq
```

## 3. Approve and Generate Advisor PDF

Finance/tax currently uses the existing UI or returned run packet for edits; it
does not expose a separate MCP draft-review endpoint.

```bash
jq '.result.approved_packet // .result.review_packet // .result // .result_data' \
  /tmp/finance-latest.json > /tmp/finance-packet.json
mcp_tool approve_vertical_run "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/finance-packet.json \
  '{vertical:"finance_tax",run_id:$id,packet:$packet[0],notes:"CPA/EA review completed.",confirm:true}')" | tool_data | jq
mcp_tool generate_vertical_packet "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/finance-packet.json \
  '{vertical:"finance_tax",run_id:$id,packet_type:"advisor",packet:$packet[0]}')" \
  | tool_data | tee /tmp/finance-pdf.json | jq
```
