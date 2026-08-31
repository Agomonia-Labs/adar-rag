# ADAR DocIntel REST API End-to-End Testing

This guide tests the public REST API from OAuth login through document upload,
chunking, embedding, summarization, grounded Q&A, token refresh, authorization
boundaries, and optional cleanup.

## Prerequisites

Install or verify these commands:

```bash
command -v curl jq python3 openssl
```

The backend must be deployed with:

```text
OAUTH_ISSUER_URL=https://auth.docintel.adar.agomoniai.com
OAUTH_API_RESOURCE=https://docintel.adar.agomoniai.com/api/v1
```

The user must have these approved OAuth scopes:

```text
workspaces:read documents:read documents:write knowledge:query knowledge:generate
```

## 1. OAuth Login for the REST Audience

Source the deployment helper so the generated variables remain in the current
shell:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login --oauth-target api
```

Complete browser login, email MFA, and scope consent. The command exports:

```text
API_ACCESS_TOKEN
API_REFRESH_TOKEN
API_TOKEN_SCOPE
API_TOKEN_EXPIRES_IN
DOCINTEL_ACCESS_TOKEN
DOCINTEL_REFRESH_TOKEN
```

Set common test variables:

```bash
export API_BASE="https://docintel.adar.agomoniai.com"
export OAUTH_ISSUER="https://auth.docintel.adar.agomoniai.com"
export API_RESOURCE="$API_BASE/api/v1"
export ACCESS_TOKEN="$API_ACCESS_TOKEN"

echo "API token loaded: ${#ACCESS_TOKEN} characters"
echo "Granted scopes: $API_TOKEN_SCOPE"
```

To test a local backend instead:

```bash
source deploy.sh --oauth-login \
  --oauth-target api \
  --api-url http://localhost:8000/api/v1

export API_BASE="http://localhost:8000"
export ACCESS_TOKEN="$API_ACCESS_TOKEN"
```

## 2. Discovery and Health

```bash
curl -sS "$API_BASE/api/health" | jq

curl -sS \
  "$OAUTH_ISSUER/.well-known/oauth-authorization-server" | jq

curl -sS \
  "$OAUTH_ISSUER/.well-known/oauth-protected-resource/api" | jq
```

The protected-resource response must identify:

```text
https://docintel.adar.agomoniai.com/api/v1
```

## 3. API Catalog

```bash
curl -sS "$API_BASE/api/v1" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Expected result: API name, version, OAuth client ID, granted scopes, and endpoint
capabilities.

## 4. Workspaces

List accessible workspaces:

```bash
WORKSPACES="$(
  curl -sS "$API_BASE/api/v1/workspaces" \
    -H "Authorization: Bearer $ACCESS_TOKEN"
)"

printf '%s\n' "$WORKSPACES" | jq
```

Select the first workspace or set a known ID manually:

```bash
export WORKSPACE_ID="$(
  printf '%s\n' "$WORKSPACES" | jq -r '.data[0].id // empty'
)"

test -n "$WORKSPACE_ID" || {
  echo "No accessible workspace was returned. Set WORKSPACE_ID manually."
  return 1 2>/dev/null || exit 1
}

echo "Workspace: $WORKSPACE_ID"
```

Get workspace details:

```bash
curl -sS "$API_BASE/api/v1/workspaces/$WORKSPACE_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 5. Documents and Chunks

List workspace documents:

```bash
DOCUMENTS="$(
  curl -sS "$API_BASE/api/v1/workspaces/$WORKSPACE_ID/documents" \
    -H "Authorization: Bearer $ACCESS_TOKEN"
)"

printf '%s\n' "$DOCUMENTS" | jq
```

Select the first embedded document:

```bash
export DOCUMENT_ID="$(
  printf '%s\n' "$DOCUMENTS" |
    jq -r '[.data[] | select(.status == "embedded")][0].id // empty'
)"

echo "Embedded document: $DOCUMENT_ID"
```

Get document metadata:

```bash
curl -sS "$API_BASE/api/v1/documents/$DOCUMENT_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Get its chunk manifest:

```bash
curl -sS "$API_BASE/api/v1/documents/$DOCUMENT_ID/chunks" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

List personal documents that do not belong to a workspace:

```bash
curl -sS "$API_BASE/api/v1/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 6. Upload a New Document

Choose a non-video test document:

```bash
export FILE="/absolute/path/to/test.pdf"
export FILE_NAME="$(basename "$FILE")"
export CONTENT_TYPE="application/pdf"

if stat -f%z "$FILE" >/dev/null 2>&1; then
  export FILE_SIZE="$(stat -f%z "$FILE")"
else
  export FILE_SIZE="$(stat -c%s "$FILE")"
fi

echo "$FILE_NAME: $FILE_SIZE bytes"
```

Create a signed direct-upload session:

```bash
UPLOAD_SESSION="$(
  curl -sS -X POST "$API_BASE/api/v1/uploads" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn \
      --arg filename "$FILE_NAME" \
      --arg content_type "$CONTENT_TYPE" \
      --arg workspace_id "$WORKSPACE_ID" \
      --argjson file_size "$FILE_SIZE" \
      '{
        filename:$filename,
        content_type:$content_type,
        file_size:$file_size,
        workspace_id:$workspace_id,
        redact_pii:false
      }'
    )"
)"

printf '%s\n' "$UPLOAD_SESSION" | jq
```

Capture the upload values and fail if session creation returned an error:

```bash
export DOCUMENT_ID="$(jq -r '.doc_id // empty' <<<"$UPLOAD_SESSION")"
export UPLOAD_URL="$(jq -r '.upload_url // empty' <<<"$UPLOAD_SESSION")"
export GCS_SOURCE_PATH="$(jq -r '.gcs_source_path // empty' <<<"$UPLOAD_SESSION")"

test -n "$DOCUMENT_ID" && test -n "$UPLOAD_URL" || {
  echo "Upload session creation failed"
  printf '%s\n' "$UPLOAD_SESSION" | jq
  return 1 2>/dev/null || exit 1
}
```

Upload file bytes directly to cloud storage:

```bash
curl --fail-with-body -X PUT "$UPLOAD_URL" \
  -H "Content-Type: $CONTENT_TYPE" \
  --data-binary @"$FILE"
```

Complete the upload and start chunking:

```bash
curl -sS -X POST "$API_BASE/api/v1/uploads/complete" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg doc_id "$DOCUMENT_ID" \
    --arg path "$GCS_SOURCE_PATH" \
    --arg filename "$FILE_NAME" \
    --arg content_type "$CONTENT_TYPE" \
    --arg workspace_id "$WORKSPACE_ID" \
    --argjson file_size "$FILE_SIZE" \
    '{
      doc_id:$doc_id,
      gcs_source_path:$path,
      filename:$filename,
      content_type:$content_type,
      file_size:$file_size,
      workspace_id:$workspace_id,
      redact_pii:false
    }'
  )" | jq
```

## 7. Monitor Chunking

```bash
while true; do
  RESPONSE="$(
    curl -sS "$API_BASE/api/v1/documents/$DOCUMENT_ID" \
      -H "Authorization: Bearer $ACCESS_TOKEN"
  )"

  STATUS="$(jq -r '.data.status // empty' <<<"$RESPONSE")"
  echo "Chunking status: $STATUS"

  case "$STATUS" in
    chunked) break ;;
    error)
      printf '%s\n' "$RESPONSE" | jq
      return 1 2>/dev/null || exit 1
      ;;
  esac

  sleep 5
done
```

## 8. Start and Monitor Embedding

```bash
curl -sS -X POST \
  "$API_BASE/api/v1/documents/$DOCUMENT_ID/embedding" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

```bash
while true; do
  RESPONSE="$(
    curl -sS "$API_BASE/api/v1/documents/$DOCUMENT_ID" \
      -H "Authorization: Bearer $ACCESS_TOKEN"
  )"

  STATUS="$(jq -r '.data.status // empty' <<<"$RESPONSE")"
  echo "Embedding status: $STATUS"

  case "$STATUS" in
    embedded) break ;;
    error)
      printf '%s\n' "$RESPONSE" | jq
      return 1 2>/dev/null || exit 1
      ;;
  esac

  sleep 5
done
```

## 9. Document Summaries

Test all standard summary types:

```bash
for TYPE in executive detailed bullets sections; do
  echo
  echo "===== $TYPE ====="

  curl -N -X POST \
    "$API_BASE/api/v1/summaries/documents/$DOCUMENT_ID/stream" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -cn --arg type "$TYPE" '{
      summary_type:$type,
      custom_prompt:"",
      chunk_indices:[],
      redact_pii:false
    }')"
done
```

Test a custom summary:

```bash
curl -N -X POST \
  "$API_BASE/api/v1/summaries/documents/$DOCUMENT_ID/stream" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "summary_type":"custom",
    "custom_prompt":"Identify important facts, risks, obligations, deadlines, and recommended actions.",
    "chunk_indices":[],
    "redact_pii":false
  }'
```

These endpoints use Server-Sent Events, so `curl -N` intentionally prints the
stream as it arrives.

## 10. Grounded Document Q&A

```bash
curl -N -X POST "$API_BASE/api/v1/knowledge/query/stream" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg document_id "$DOCUMENT_ID" \
    '{
      question:"What are the important facts, risks, and next actions?",
      workspace_id:$workspace_id,
      document_ids:[$document_id],
      history:[],
      redact_pii:false,
      agent_mode:"auto"
    }'
  )"
```

## 11. Multi-Document Q&A

Refresh the workspace document list and choose up to three embedded documents:

```bash
DOCUMENTS="$(
  curl -sS "$API_BASE/api/v1/workspaces/$WORKSPACE_ID/documents" \
    -H "Authorization: Bearer $ACCESS_TOKEN"
)"

export DOCUMENT_IDS="$(
  printf '%s\n' "$DOCUMENTS" |
    jq -c '[.data[] | select(.status == "embedded") | .id][0:3]'
)"

printf '%s\n' "$DOCUMENT_IDS" | jq
```

```bash
curl -N -X POST "$API_BASE/api/v1/knowledge/query/stream" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -cn \
    --arg workspace_id "$WORKSPACE_ID" \
    --argjson document_ids "$DOCUMENT_IDS" \
    '{
      question:"Compare these documents and identify common themes, differences, risks, and next actions.",
      workspace_id:$workspace_id,
      document_ids:$document_ids,
      history:[],
      redact_pii:false,
      agent_mode:"auto"
    }'
  )"
```

## 12. Refresh the OAuth Token

Refresh tokens are rotated. Always retain the newly returned refresh token:

```bash
docintel_oauth_refresh_token
export ACCESS_TOKEN="$API_ACCESS_TOKEN"

echo "Refreshed token length: ${#ACCESS_TOKEN}"
```

Confirm the refreshed access token:

```bash
curl -sS "$API_BASE/api/v1" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

## 13. Authorization and Security Tests

No token must return HTTP `401` and advertise OAuth protected-resource metadata:

```bash
curl -i "$API_BASE/api/v1/documents"
```

An MCP-audience token must be rejected by the REST API:

```bash
curl -i "$API_BASE/api/v1/documents" \
  -H "Authorization: Bearer $MCP_ACCESS_TOKEN"
```

An inaccessible document must return HTTP `403` or `404`:

```bash
curl -i \
  "$API_BASE/api/v1/documents/00000000-0000-0000-0000-000000000000" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

A token without `documents:write` must receive HTTP `403` when creating an
upload. Log in with read scopes only in a separate shell to test this:

```bash
source deploy.sh --oauth-login \
  --oauth-target api \
  --oauth-scopes "workspaces:read documents:read"

curl -i -X POST "$API_BASE/api/v1/uploads" \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "filename":"denied.pdf",
    "content_type":"application/pdf",
    "file_size":100,
    "workspace_id":null,
    "redact_pii":false
  }'
```

## 14. Optional Cleanup

Deletion removes the document record, vectors, and associated cloud files. Run
this only for a document created specifically by this test:

```bash
curl -sS -X DELETE "$API_BASE/api/v1/documents/$DOCUMENT_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

Confirm it is no longer accessible:

```bash
curl -i "$API_BASE/api/v1/documents/$DOCUMENT_ID" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

## Expected End State

The end-to-end test is successful when:

1. OAuth issues an access token for the REST audience.
2. Workspace and document access obey user membership and ownership.
3. File bytes upload directly to cloud storage.
4. The document transitions through `chunking`, `chunked`, `embedding`, and `embedded`.
5. Summary and Q&A endpoints stream grounded output.
6. Refresh-token rotation produces a usable replacement access token.
7. Missing, wrong-audience, under-scoped, and unauthorized requests are rejected.

