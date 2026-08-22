# Talent Readiness MCP Workflow

## 1. Login and Inputs

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
export WORKSPACE_ID="<workspace-id>"
export RESUME_DOCUMENT_ID="<resume-id>"
export JOB_DESCRIPTION_ID="<job-description-id>"
```

## 2. Start and Monitor

```bash
mcp_tool start_vertical_workflow "$(jq -cn \
  --arg workspace "$WORKSPACE_ID" --arg resume "$RESUME_DOCUMENT_ID" --arg jd "$JOB_DESCRIPTION_ID" \
  '{workflow:"talent_readiness",workspace_id:$workspace,document_ids:[$resume,$jd],inputs:{job_description_id:$jd,candidate_name:"Avery Morgan",notes:"Prepare evidence-backed role match."}}')" \
  | tool_data | tee /tmp/talent-run.json | jq
export RUN_ID="$(jq -r '.run_id // .id' /tmp/talent-run.json)"
mcp_tool get_vertical_run "$(jq -cn --arg id "$RUN_ID" '{vertical:"talent",run_id:$id}')" \
  | tool_data | tee /tmp/talent-latest.json | jq
```

## 3. Recruiter Review and Approval

```bash
jq '.packet // .result.packet // .result_data.packet' /tmp/talent-latest.json > /tmp/talent-packet.json
mcp_tool save_vertical_review "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/talent-packet.json \
  '{vertical:"talent",run_id:$id,packet:$packet[0],notes:"Recruiter evidence review completed."}')" | tool_data | jq
mcp_tool approve_vertical_run "$(jq -cn --arg id "$RUN_ID" --slurpfile packet /tmp/talent-packet.json \
  '{vertical:"talent",run_id:$id,packet:$packet[0],notes:"Recruiter approval completed.",confirm:true}')" | tool_data | jq
```

## 4. Generate and Ingest Candidate PDF

```bash
mcp_tool generate_vertical_packet "$(jq -cn --arg id "$RUN_ID" \
  '{vertical:"talent",run_id:$id,packet_type:"candidate"}')" \
  | tool_data | tee /tmp/talent-pdf.json | jq
```

The packet is ingested as a governed DocIntel document and can participate in
subsequent retrieval and chat.
