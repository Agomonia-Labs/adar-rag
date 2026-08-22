# MCP Video Upload, Processing, Summary, and Comparison Tests

This runbook tests DocIntel MCP video intelligence, document summaries, and
document comparison against the deployed production services.

## 1. Local Regression Test

```bash
cd /Users/brajadas/project/adar-rag

.venv/bin/python -m pytest -q \
  mcp-server/tests \
  backend/tests/test_oauth_rules.py
```

Expected result: `16 passed`.

## 2. Authenticate and Load Helpers

After deploying the backend and MCP server, obtain a fresh token containing
`knowledge:generate`, `video:read`, and `video:process`:

```bash
unset MCP_ACCESS_TOKEN MCP_REFRESH_TOKEN DOCINTEL_ACCESS_TOKEN
source mcp-server/scripts/oauth_login.sh
```

The script defines these shell helpers:

```text
mcp_request
mcp_tool
tool_data
```

Verify them:

```bash
type mcp_request
type mcp_tool
type tool_data
echo "Token loaded: ${#MCP_ACCESS_TOKEN} characters"
```

## 3. Discover MCP Tools

```bash
mcp_request '{
  "jsonrpc":"2.0",
  "id":1,
  "method":"tools/list",
  "params":{}
}' | jq -r '.result.tools[].name' | sort
```

Confirm that the summary, comparison, and video tools appear.

## 4. Select Two Embedded Documents

For personal documents:

```bash
export WORKSPACE_ID=""
```

For a workspace:

```bash
export WORKSPACE_ID="<workspace-uuid>"
```

List documents:

```bash
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:(if $workspace_id == "" then null else $workspace_id end)}')" \
  | tee /tmp/docintel-current-documents.json \
  | tool_data
```

Set two different embedded document IDs:

```bash
export DOCUMENT_ID_1="<embedded-document-id>"
export DOCUMENT_ID_2="<another-embedded-document-id>"
```

## 5. Test Single-Document Summaries

```bash
for TYPE in executive detailed bullets sections; do
  echo "Testing summary: $TYPE"
  mcp_tool summarize_document "$(jq -cn \
    --arg id "$DOCUMENT_ID_1" \
    --arg type "$TYPE" \
    '{document_id:$id,summary_type:$type,redact_pii:false}')" \
    | tool_data
done
```

Test a custom summary:

```bash
mcp_tool summarize_document "$(jq -cn \
  --arg id "$DOCUMENT_ID_1" \
  '{
    document_id:$id,
    summary_type:"custom",
    custom_prompt:"Identify key facts, risks, decisions, and required actions.",
    redact_pii:false
  }')" | tool_data
```

Expected fields include `summary`, `progress`, and `trace_id`.

## 6. Test Multiple-Document Summary

```bash
mcp_tool summarize_documents "$(jq -cn \
  --arg first "$DOCUMENT_ID_1" \
  --arg second "$DOCUMENT_ID_2" \
  '{
    document_ids:[$first,$second],
    summary_type:"executive",
    redact_pii:false
  }')" | tool_data
```

## 7. Test Document Comparison

```bash
mcp_tool compare_documents "$(jq -cn \
  --arg first "$DOCUMENT_ID_1" \
  --arg second "$DOCUMENT_ID_2" \
  '{
    document_id_1:$first,
    document_id_2:$second,
    redact_pii:false
  }')" | tool_data
```

Verify `similarity_score`, `summary`, `doc1_unique`, `doc2_unique`, `sections`,
and `trace_id`.

## 8. Prepare a Video Upload

```bash
export VIDEO_PATH="/absolute/path/to/video.mp4"
export VIDEO_NAME="$(basename "$VIDEO_PATH")"
export VIDEO_SIZE="$(stat -f%z "$VIDEO_PATH")"
export VIDEO_CONTENT_TYPE="video/mp4"
```

## 9. Create a Signed Video Upload

```bash
mcp_tool create_video_upload "$(jq -cn \
  --arg filename "$VIDEO_NAME" \
  --arg content_type "$VIDEO_CONTENT_TYPE" \
  --argjson file_size "$VIDEO_SIZE" \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    filename:$filename,
    content_type:$content_type,
    file_size:$file_size,
    workspace_id:(if $workspace_id == "" then null else $workspace_id end)
  }')" | tee /tmp/docintel-video-upload.json | tool_data
```

Extract the upload values:

```bash
export VIDEO_UPLOAD_RESULT="$(tool_data < /tmp/docintel-video-upload.json)"
export VIDEO_ID="$(jq -r '.doc_id' <<<"$VIDEO_UPLOAD_RESULT")"
export VIDEO_UPLOAD_URL="$(jq -r '.upload_url' <<<"$VIDEO_UPLOAD_RESULT")"
export VIDEO_GCS_PATH="$(jq -r '.gcs_source_path' <<<"$VIDEO_UPLOAD_RESULT")"
```

Upload directly to cloud storage:

```bash
curl --fail-with-body \
  --request PUT "$VIDEO_UPLOAD_URL" \
  --header "Content-Type: $VIDEO_CONTENT_TYPE" \
  --upload-file "$VIDEO_PATH"
```

## 10. Complete the Video Upload

```bash
mcp_tool complete_video_upload "$(jq -cn \
  --arg doc_id "$VIDEO_ID" \
  --arg filename "$VIDEO_NAME" \
  --arg content_type "$VIDEO_CONTENT_TYPE" \
  --argjson file_size "$VIDEO_SIZE" \
  --arg gcs_source_path "$VIDEO_GCS_PATH" \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    doc_id:$doc_id,
    filename:$filename,
    content_type:$content_type,
    file_size:$file_size,
    gcs_source_path:$gcs_source_path,
    workspace_id:(if $workspace_id == "" then null else $workspace_id end),
    process_after_upload:false
  }')" | tool_data
```

## 11. Start Video Processing

```bash
mcp_tool process_video "$(jq -cn \
  --arg id "$VIDEO_ID" \
  '{
    document_id:$id,
    rights_confirmed:true,
    transcript_language:"auto",
    max_frames:12,
    segment_seconds:60,
    embed_after_processing:true
  }')" | tool_data
```

Use `en-US`, `hi-IN`, `bn-IN`, `ur-PK`, or another configured language code
instead of `auto` when the video's language is known.

## 12. Monitor Video Progress

```bash
while true; do
  RESULT="$(mcp_tool get_video_status "$(jq -cn \
    --arg id "$VIDEO_ID" '{document_id:$id}')" | tool_data)"

  jq '{
    processing_status,
    progress_step,
    progress_pct,
    progress_message,
    progress_updated_at,
    error_message,
    document_error
  }' <<<"$RESULT"

  STATUS="$(jq -r '.processing_status // empty' <<<"$RESULT")"
  [[ "$STATUS" == "completed" || "$STATUS" == "error" ]] && break
  sleep 15
done
```

## 13. Test Timeline, Transcript, and Frames

```bash
mcp_tool get_video_timeline "$(jq -cn \
  --arg id "$VIDEO_ID" '{document_id:$id}')" | tool_data

mcp_tool get_video_transcript "$(jq -cn \
  --arg id "$VIDEO_ID" '{document_id:$id}')" | tool_data

mcp_tool get_video_frames "$(jq -cn \
  --arg id "$VIDEO_ID" '{document_id:$id}')" | tool_data
```

Request a short-lived URL for frame zero:

```bash
mcp_tool get_video_frame_url "$(jq -cn \
  --arg id "$VIDEO_ID" \
  '{document_id:$id,frame_index:0}')" | tool_data
```

## 14. Test Timestamp-Grounded Video Q&A

The video document must be embedded:

```bash
mcp_tool search_video "$(jq -cn \
  --arg id "$VIDEO_ID" \
  '{
    document_id:$id,
    question:"What happens between 1:00 and 3:00?",
    limit:8
  }')" | tool_data
```

Confirm that the answer is grounded and its sources contain video timestamps.

## 15. Public Smoke Test

```bash
export DOCINTEL_ACCESS_TOKEN="$MCP_ACCESS_TOKEN"

RUN_GENERATIVE=true \
RUN_SESSION=true \
mcp-server/scripts/test_mcp.sh
```
