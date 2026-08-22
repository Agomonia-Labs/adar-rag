# Employee Growth and Mobility MCP Workflow

## 1. Login and Inputs

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
export WORKSPACE_ID="<workspace-id>"
export JOB_DESCRIPTION_ID="<target-role-job-description-id>"
export EVIDENCE_IDS_JSON='["<resume-id>","<performance-review-id>","<skills-profile-id>"]'
```

Evidence may include resumes, performance reviews, skills profiles,
certifications, training records, and project records.

## 2. Start and Monitor

```bash
export ALL_DOCUMENT_IDS_JSON="$(jq -cn --argjson evidence "$EVIDENCE_IDS_JSON" --arg jd "$JOB_DESCRIPTION_ID" '$evidence + [$jd]')"
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" --arg jd "$JOB_DESCRIPTION_ID" --argjson documents "$ALL_DOCUMENT_IDS_JSON" \
  '{workflow:"employee_mobility",workspace_id:$workspace,document_ids:$documents,inputs:{job_description_id:$jd,candidate_name:"Avery Morgan",notes:"Evaluate internal mobility readiness."}}')" \
  | tool_data | tee /tmp/mobility-run.json | jq
export RUN_ID="$(jq -r '.run_id // .id' /tmp/mobility-run.json)"
mcp_tool get_vertical_run "$(jq -cn --arg id "$RUN_ID" '{vertical:"talent",run_id:$id}')" \
  | tool_data | tee /tmp/mobility-latest.json | jq
```

## 3. Review, Approve, and Generate PDF

```bash
jq '.packet // .result.packet // .result_data.packet' /tmp/mobility-latest.json > /tmp/mobility-packet.json
mcp_tool save_vertical_review "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/mobility-packet.json \
  '{vertical:"talent",run_id:$id,packet:$packet[0],notes:"Manager and talent review completed."}')" | tool_data | jq
mcp_tool approve_vertical_run "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/mobility-packet.json \
  '{vertical:"talent",run_id:$id,packet:$packet[0],notes:"Mobility packet approved.",confirm:true}')" | tool_data | jq
mcp_tool generate_vertical_packet "$(jq -cn --arg id "$RUN_ID" \
  '{vertical:"talent",run_id:$id,packet_type:"mobility"}')" | tool_data | jq
```
