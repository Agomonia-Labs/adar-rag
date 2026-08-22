# Healthcare Clinical MCP Workflow

This workflow analyzes a processed clinical document and creates a governed
clinical intelligence packet for human review.

## 1. Login and Select Documents

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
mcp_tool list_workspaces '{}' | tool_data | tee /tmp/workspaces.json | jq
export WORKSPACE_ID="$(jq -r '.workspaces[0].id' /tmp/workspaces.json)"
mcp_tool list_documents "$(jq -cn --arg id "$WORKSPACE_ID" '{workspace_id:$id}')" \
  | tool_data | tee /tmp/clinical-documents.json | jq
jq -r '.documents[] | [.id,.original_name,.doc_type,.status] | @tsv' /tmp/clinical-documents.json
export CLINICAL_DOCUMENT_ID="<clinical-document-id>"
```

The selected document must be `chunked`, `embedding`, or `embedded`.

## 2. Start and Monitor

```bash
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" --arg document "$CLINICAL_DOCUMENT_ID" \
  '{workflow:"healthcare_clinical",workspace_id:$workspace,document_ids:[$document],inputs:{}}')" \
  | tool_data | tee /tmp/clinical-run.json | jq
export RUN_ID="$(jq -r '.run_id // .id // .agent_run.run_id // .agent_run.id' /tmp/clinical-run.json)"
mcp_tool get_vertical_run "$(jq -cn --arg id "$RUN_ID" '{vertical:"healthcare",run_id:$id}')" \
  | tool_data | tee /tmp/clinical-latest.json | jq
```

Repeat `get_vertical_run` until status is `pending_approval` or `failed`.
```aiignore

while true; do
  mcp_tool get_vertical_run "$(jq -cn \
    --arg id "$RUN_ID" \
    '{vertical:"healthcare",run_id:$id}'
  )" \
    | tool_data \
    | tee /tmp/clinical-latest.json \
    | jq '{
        run_id: (.run_id // .id),
        status,
        current_step,
        error_message,
        updated_at
      }'

  STATUS="$(jq -r '.status // empty' /tmp/clinical-latest.json)"

  case "$STATUS" in
    pending_approval|approved|completed|failed|withdrawn)
      echo "Workflow finished with status: $STATUS"
      break
      ;;
    running)
      echo "Workflow is still running. Checking again in 10 seconds..."
      sleep 10
      ;;
    "")
      echo "No workflow status was returned."
      jq . /tmp/clinical-latest.json
      break
      ;;
    *)
      echo "Current status: $STATUS. Checking again in 10 seconds..."
      sleep 10
      ;;
  esac
done

```
after it finishes 

```aiignore
jq . /tmp/clinical-latest.json
```
## 3. Review and Approve

```bash
jq '.result.review_packet // .result_data.review_packet // .review_packet // .result.approved_packet // .result_data.approved_packet' \
  /tmp/clinical-latest.json > /tmp/clinical-packet.json
jq . /tmp/clinical-packet.json
```

After editing and validating the packet:

```bash
mcp_tool save_vertical_review "$(jq -cn --arg id "$RUN_ID" \
  --slurpfile packet /tmp/clinical-packet.json \
  '{vertical:"healthcare",run_id:$id,packet:$packet[0],notes:"Clinical review completed."}')" | tool_data | jq
mcp_tool approve_vertical_run "$(jq -cn --arg id "$RUN_ID" \
  --slurpfile packet /tmp/clinical-packet.json \
  '{vertical:"healthcare",run_id:$id,packet:$packet[0],notes:"Human approval completed.",confirm:true}')" | tool_data | jq
```

The general clinical workflow does not always contain clinical-scribe visit
data. Generate `after_visit_summary` only when the run includes an AVS; otherwise
use the approved structured clinical packet.
