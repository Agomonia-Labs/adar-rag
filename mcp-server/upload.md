# DocIntel MCP Document Upload Guide

This guide covers the complete MCP flow for uploading a document, chunking it,
embedding it, asking grounded questions, and optionally deleting it.

## 1. Authenticate

```bash
cd /Users/brajadas/project/adar-rag
source mcp-server/scripts/oauth_login.sh
```

Complete login and MFA in the browser, then confirm that the token is loaded:

```bash
echo "Token loaded: ${#MCP_ACCESS_TOKEN} characters"
```

The access token must include the `documents:write` scope.

## 2. Select a File

Example using a PDF:

```bash
export FILE_PATH="/Users/brajadas/Downloads/sample.pdf"
export FILE_NAME="$(basename "$FILE_PATH")"
export FILE_SIZE="$(stat -f%z "$FILE_PATH")"
export CONTENT_TYPE="application/pdf"
```

For a personal document:

```bash
export WORKSPACE_ID=""
```

For a workspace document:

```bash
export WORKSPACE_ID="<workspace-uuid>"
```

Verify the input:

```bash
printf 'File: %s\nSize: %s bytes\nType: %s\nWorkspace: %s\n' \
  "$FILE_NAME" "$FILE_SIZE" "$CONTENT_TYPE" "${WORKSPACE_ID:-personal}"
```

## 3. Request a Signed Upload URL

```bash
mcp_request "$(jq -cn \
  --arg filename "$FILE_NAME" \
  --arg content_type "$CONTENT_TYPE" \
  --argjson file_size "$FILE_SIZE" \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    jsonrpc:"2.0",
    id:1,
    method:"tools/call",
    params:{
      name:"create_document_upload",
      arguments:{
        filename:$filename,
        content_type:$content_type,
        file_size:$file_size,
        workspace_id:(if $workspace_id == "" then null else $workspace_id end),
        redact_pii:false
      }
    }
  }')" | tee /tmp/docintel-upload-session.json | jq
```

## 4. Extract Upload Information

```bash
export UPLOAD_RESULT="$(
  jq -r '
    .result.structuredContent.result //
    .result.structuredContent //
    (.result.content[0].text | fromjson)
  ' /tmp/docintel-upload-session.json
)"

export DOCUMENT_ID="$(jq -r '.doc_id' <<<"$UPLOAD_RESULT")"
export UPLOAD_URL="$(jq -r '.upload_url' <<<"$UPLOAD_RESULT")"
export GCS_SOURCE_PATH="$(jq -r '.gcs_source_path' <<<"$UPLOAD_RESULT")"
```

Verify the values without printing the signed URL:

```bash
echo "Document ID: $DOCUMENT_ID"
echo "Storage path: $GCS_SOURCE_PATH"
echo "Upload URL loaded: ${#UPLOAD_URL} characters"
```

Do not share or log `UPLOAD_URL`; it temporarily authorizes file upload.

## 5. Upload Directly to Cloud Storage

```bash
curl --fail-with-body \
  --request PUT "$UPLOAD_URL" \
  --header "Content-Type: $CONTENT_TYPE" \
  --upload-file "$FILE_PATH"
```

A successful upload normally returns HTTP `200` with little or no body. Run
the PUT only once. Signed upload URLs expire.

## 6. Complete the Upload

This verifies the cloud object, creates the document record, and starts
chunking:

```bash
mcp_request "$(jq -cn \
  --arg doc_id "$DOCUMENT_ID" \
  --arg filename "$FILE_NAME" \
  --arg content_type "$CONTENT_TYPE" \
  --argjson file_size "$FILE_SIZE" \
  --arg gcs_source_path "$GCS_SOURCE_PATH" \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    jsonrpc:"2.0",
    id:2,
    method:"tools/call",
    params:{
      name:"complete_document_upload",
      arguments:{
        doc_id:$doc_id,
        filename:$filename,
        content_type:$content_type,
        file_size:$file_size,
        gcs_source_path:$gcs_source_path,
        workspace_id:(if $workspace_id == "" then null else $workspace_id end),
        redact_pii:false
      }
    }
  }')" | jq
```

The expected initial status is `chunking`.

## 7. Monitor Chunking

Define a reusable status function:

```bash
check_document_status() {
  mcp_request "$(jq -cn --arg document_id "$DOCUMENT_ID" '{
    jsonrpc:"2.0",
    id:3,
    method:"tools/call",
    params:{
      name:"get_ingestion_status",
      arguments:{document_id:$document_id}
    }
  }')" | jq
}
```

Run it until the status becomes `chunked`:

```bash
check_document_status
```

Possible statuses are `chunking`, `chunked`, `embedding`, `embedded`, and
`error`. If the status is `error`, inspect `error_message`. Do not upload the
file again while it is processing.

## 8. Review Generated Chunks

```bash
mcp_request "$(jq -cn --arg document_id "$DOCUMENT_ID" '{
  jsonrpc:"2.0",
  id:4,
  method:"tools/call",
  params:{
    name:"get_document_chunks",
    arguments:{document_id:$document_id}
  }
}')" | jq
```

## 9. Start Embedding

Only run this after the document status is `chunked`:

```bash
mcp_request "$(jq -cn --arg document_id "$DOCUMENT_ID" '{
  jsonrpc:"2.0",
  id:5,
  method:"tools/call",
  params:{
    name:"embed_document",
    arguments:{document_id:$document_id}
  }
}')" | jq
```

Run `check_document_status` until the status becomes `embedded`:

```bash
check_document_status
```

## 10. Ask a Grounded Question

```bash
mcp_request "$(jq -cn \
  --arg document_id "$DOCUMENT_ID" \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    jsonrpc:"2.0",
    id:6,
    method:"tools/call",
    params:{
      name:"search_knowledgebase",
      arguments:{
        question:"Summarize the important facts, risks, and next actions.",
        document_ids:[$document_id],
        workspace_id:(if $workspace_id == "" then null else $workspace_id end),
        redact_pii:false
      }
    }
  }')" | jq
```

The response should contain a grounded answer, supporting sources, chunk
references, and a trace ID.

## 11. Optional Cleanup

Document deletion requires explicit confirmation:

```bash
mcp_request "$(jq -cn --arg document_id "$DOCUMENT_ID" '{
  jsonrpc:"2.0",
  id:7,
  method:"tools/call",
  params:{
    name:"delete_document",
    arguments:{document_id:$document_id,confirm:true}
  }
}')" | jq
```

The DocIntel deletion workflow removes the database record, stored source,
chunks, and vectors. Review any returned cleanup warnings.
