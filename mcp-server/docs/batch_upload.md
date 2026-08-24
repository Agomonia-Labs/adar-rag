# MCP Batch Upload

This runbook uploads multiple files through signed cloud-storage URLs, starts
DocIntel processing, and then optionally classifies and embeds the documents.
File bytes do not pass through the MCP server.

## 1. Authenticate and Load Helpers

Run from the repository root. `source` keeps the access token and helper
functions in the current shell.

```bash
cd /Users/brajadas/project/adar-rag
source mcp-server/scripts/oauth_login.sh

type mcp_tool
type tool_data
printf 'Token loaded: %s characters\n' "${#MCP_ACCESS_TOKEN}"
```

The OAuth client needs `workspaces:read`, `documents:read`, `documents:write`,
`batches:read`, and `batches:write`.

The DocIntel backend must include the batch routes and current database schema.
For production testing, deploy the updated backend before running this guide;
deploying only the MCP service is not sufficient for backend batch changes.

## 2. Select a Workspace

```bash
mcp_tool list_workspaces '{}' | tool_data | jq
export WORKSPACE_ID="YOUR_WORKSPACE_UUID"
printf 'WORKSPACE_ID=%s\n' "$WORKSPACE_ID"
```

For a personal workspace, use its UUID. Do not pass `personal` or an empty
string as `workspace_id`.

## 3. Select Files

```bash
export FILE_1="/absolute/path/document-one.pdf"
export FILE_2="/absolute/path/document-two.pdf"
ls -lh "$FILE_1" "$FILE_2"
```

## 4. Build and Validate the Upload Request

```bash
UPLOAD_ARGS="$(jq -n \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg name1 "$(basename "$FILE_1")" \
  --arg name2 "$(basename "$FILE_2")" \
  --argjson size1 "$(wc -c < "$FILE_1" | tr -d ' ')" \
  --argjson size2 "$(wc -c < "$FILE_2" | tr -d ' ')" \
    '{
      workspace_id:$workspace_id,
      redact_pii:false,
      files:[
        {filename:$name1, content_type:"application/pdf", file_size:$size1},
        {filename:$name2, content_type:"application/pdf", file_size:$size2}
      ]
    }')"

printf '%s\n' "$UPLOAD_ARGS" | jq
```

Change each `content_type` when the file is not a PDF. Do not continue unless
the workspace ID, filenames, and positive byte sizes are correct.

## 5. Create the Batch Upload

```bash
if ! UPLOAD_RESPONSE="$(mcp_tool create_batch_upload "$UPLOAD_ARGS")"; then
  printf 'MCP batch-upload request failed\n' >&2
  return 1 2>/dev/null || exit 1
fi

printf '%s\n' "$UPLOAD_RESPONSE" | jq
printf '%s\n' "$UPLOAD_RESPONSE" | tool_data | tee /tmp/docintel-batch-upload.json | jq
```

The first command validates the raw JSON-RPC envelope. The second extracts its
structured tool result. A successful result contains `batch_job_id` and a
`files` array with signed URLs and document IDs. Use `printf`, rather than the
zsh `echo` builtin, when piping stored JSON.

## 6. Capture IDs and URLs

```bash
export BATCH_JOB_ID="$(jq -r '.batch_job_id' /tmp/docintel-batch-upload.json)"
export DOCUMENT_ID_1="$(jq -r '.files[0].document_id' /tmp/docintel-batch-upload.json)"
export DOCUMENT_ID_2="$(jq -r '.files[1].document_id' /tmp/docintel-batch-upload.json)"
export UPLOAD_URL_1="$(jq -r '.files[0].upload_url' /tmp/docintel-batch-upload.json)"
export UPLOAD_URL_2="$(jq -r '.files[1].upload_url' /tmp/docintel-batch-upload.json)"

printf 'Batch: %s\nDocument 1: %s\nDocument 2: %s\n' \
  "$BATCH_JOB_ID" "$DOCUMENT_ID_1" "$DOCUMENT_ID_2"
```

Stop if any value is empty or `null`.

## 7. Upload Directly to Cloud Storage

```bash
curl --fail-with-body -X PUT "$UPLOAD_URL_1" \
  -H "Content-Type: application/pdf" \
  --upload-file "$FILE_1"

curl --fail-with-body -X PUT "$UPLOAD_URL_2" \
  -H "Content-Type: application/pdf" \
  --upload-file "$FILE_2"
```

The `Content-Type` must match the corresponding manifest value used in step 4.

## 8. Complete Upload and Start Processing

```bash
mcp_tool complete_batch_upload "$(jq -cn \
  --arg batch_job_id "$BATCH_JOB_ID" \
  --arg document_id_1 "$DOCUMENT_ID_1" \
  --arg document_id_2 "$DOCUMENT_ID_2" \
  '{
    batch_job_id:$batch_job_id,
    document_ids:[$document_id_1,$document_id_2],
    concurrency:2
  }')" | tool_data | jq
```

## 9. Monitor Processing

Check once:

```bash
mcp_tool get_batch_status "$(jq -cn \
  --arg batch_job_id "$BATCH_JOB_ID" \
  '{batch_job_id:$batch_job_id}')" | tool_data | jq
```

Poll until the job reaches a terminal state:

```bash
while true; do
  STATUS="$(
    mcp_tool get_batch_status "$(jq -cn \
      --arg batch_job_id "$BATCH_JOB_ID" \
      '{batch_job_id:$batch_job_id}')" | tool_data
  )"

  printf '%s\n' "$STATUS" | jq '{
    status,
    progress_pct,
    current_stage,
    total_items,
    succeeded_items,
    failed_items
  }'

  STATE="$(printf '%s\n' "$STATUS" | jq -r '.status')"
  case "$STATE" in
    completed|completed_with_errors|failed|cancelled) break ;;
  esac
  sleep 5
done
```

## 10. Review or Retry Results

```bash
mcp_tool get_batch_results "$(jq -cn \
  --arg batch_job_id "$BATCH_JOB_ID" \
  '{batch_job_id:$batch_job_id}')" | tool_data | jq
```

Retry only failed items:

```bash
mcp_tool retry_batch_failures "$(jq -cn \
  --arg batch_job_id "$BATCH_JOB_ID" \
  '{batch_job_id:$batch_job_id}')" | tool_data | jq
```

## 11. Classify the Documents

```bash
CLASSIFICATION_RESPONSE="$(
  mcp_tool start_batch_classification "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg document_id_1 "$DOCUMENT_ID_1" \
    --arg document_id_2 "$DOCUMENT_ID_2" \
    '{
      workspace_id:$workspace_id,
      document_ids:[$document_id_1,$document_id_2],
      concurrency:2,
      force:false
    }')"
)"

printf '%s\n' "$CLASSIFICATION_RESPONSE" | jq
printf '%s\n' "$CLASSIFICATION_RESPONSE" \
  | tool_data \
  | tee /tmp/docintel-classification.json \
  | jq

export CLASSIFICATION_JOB_ID="$(
  jq -r '.batch_job_id // empty' /tmp/docintel-classification.json
)"

printf 'Classification job: %s\n' "$CLASSIFICATION_JOB_ID"
```

Stop if `CLASSIFICATION_JOB_ID` is empty. Monitor it with `get_batch_status`
before continuing:

```bash
mcp_tool get_batch_status "$(jq -cn \
  --arg batch_job_id "$CLASSIFICATION_JOB_ID" \
  '{batch_job_id:$batch_job_id}')" | tool_data | jq
```

## 12. Embed the Documents

```bash
EMBED_RESPONSE="$(
  mcp_tool start_batch_embedding "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg document_id_1 "$DOCUMENT_ID_1" \
    --arg document_id_2 "$DOCUMENT_ID_2" \
    '{
      workspace_id:$workspace_id,
      document_ids:[$document_id_1,$document_id_2],
      concurrency:2,
      force:false
    }')"
)"

printf '%s\n' "$EMBED_RESPONSE" | jq
printf '%s\n' "$EMBED_RESPONSE" \
  | tool_data \
  | tee /tmp/docintel-embedding.json \
  | jq

export EMBED_JOB_ID="$(
  jq -r '.batch_job_id // empty' /tmp/docintel-embedding.json
)"

printf 'Embedding job: %s\n' "$EMBED_JOB_ID"
```

Stop if `EMBED_JOB_ID` is empty. Monitor it, then call `get_batch_results` with
that job ID:

```bash
mcp_tool get_batch_status "$(jq -cn \
  --arg batch_job_id "$EMBED_JOB_ID" \
  '{batch_job_id:$batch_job_id}')" | tool_data | jq
```

## 13. Verify Documents

```bash
mcp_tool list_documents "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}')" | tool_data | jq

mcp_tool get_document "$(jq -cn \
  --arg document_id "$DOCUMENT_ID_1" \
  '{document_id:$document_id}')" | tool_data | jq
```

## Complete Sequence

```text
OAuth login
  -> select workspace
  -> create batch upload
  -> PUT each file to its signed URL
  -> complete batch upload
  -> monitor processing
  -> review or retry item results
  -> batch classify
  -> batch embed
  -> verify documents
```

Do not classify or embed until upload processing finishes. Use `force:true` only
when completed classification or embedding should deliberately be repeated.

## Troubleshooting

### `upstream_error` with status 500

If the request arguments are valid but the extracted result is:

```json
{
  "ok": false,
  "error": {
    "code": "upstream_error",
    "message": "DocIntel request failed",
    "status_code": 500
  }
}
```

the MCP command reached DocIntel successfully and the failure occurred in the
backend. Confirm that the backend revision containing the batch-upload staging
fix has been deployed. The fix reserves the document UUID in batch item input
data and attaches the `documents(id)` foreign key only after upload completion.

Check the backend health and recent Cloud Run logs:

```bash
curl -sS https://docintel.adar.agomoniai.com/api/health | jq

gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="docintel-backend"
   timestamp>="-15m"' \
  --project=bdas-493785 \
  --limit=100 \
  --order=desc \
  --format='value(timestamp,textPayload,jsonPayload.message)'
```

If the logs report a `batch_job_items_document_id_fkey` violation, production
is still running the older backend implementation. Deploy the backend and retry
step 5 with the already validated `UPLOAD_ARGS`; the MCP server does not need a
new deployment for that backend-only fix.

### Empty or invalid workspace

Run `list_workspaces` again and use the workspace UUID. An empty string and the
literal label `personal` are not valid workspace identifiers.

### Invalid JSON or control-character error

Use `printf '%s\n' "$RESPONSE"` instead of `echo "$RESPONSE"`. The zsh `echo`
builtin can interpret escape sequences contained inside a JSON string. This
rule applies to `UPLOAD_RESPONSE`, `CLASSIFICATION_RESPONSE`, `EMBED_RESPONSE`,
and every other stored MCP JSON-RPC response.
