# DocIntel MCP Manual Test Guide

This runbook tests each DocIntel MCP capability separately. Run the tests in
order because later steps reuse document and session IDs returned by earlier
steps.

## OAuth Login Helper

For the production OAuth flow, source the helper in your current shell. It
performs discovery, dynamic registration, PKCE, browser login, email MFA, state
validation, callback handling, and token exchange:

```bash
source mcp-server/scripts/oauth_login.sh
```

On success it exports `MCP_ACCESS_TOKEN`, `MCP_REFRESH_TOKEN`, `CLIENT_ID`, and
the endpoint variables, and defines `mcp_request`. Refresh and rotate tokens
without another browser login using:

```bash
docintel_mcp_refresh_token
```

## 1. Prerequisites

Install the two command-line dependencies:

```bash
command -v curl
command -v jq
```

Start the MCP server in another terminal and confirm that it is listening on
port `8081`. The MCP server must be configured with the DocIntel backend that
issued your access token.

```bash
curl -sS http://localhost:8081/health | jq
```

Expected result:

```json
{
  "status": "ok"
}
```

## 2. Configure the Test Shell

Export the MCP endpoint and the access token returned by the DocIntel login and
MFA flow. Do not paste the token into this file or commit it to Git.

```bash
export MCP_URL="http://localhost:8081/mcp"
export MCP_PROTOCOL_VERSION="2025-06-18"
export DOCINTEL_ACCESS_TOKEN="<your-access-token>"

echo "Token loaded: ${#DOCINTEL_ACCESS_TOKEN} characters"
```

Add this helper once in the current terminal. Every later test uses it.

```bash
mcp_request() {
  local payload="$1"

  curl --fail-with-body --silent --show-error \
    --request POST "$MCP_URL" \
    --header "Authorization: Bearer $DOCINTEL_ACCESS_TOKEN" \
    --header "Content-Type: application/json" \
    --header "Accept: application/json, text/event-stream" \
    --header "MCP-Protocol-Version: $MCP_PROTOCOL_VERSION" \
    --data "$payload"
}
```

## 3. Initialize MCP

```bash
mcp_request "$(jq -cn \
  --arg version "$MCP_PROTOCOL_VERSION" \
  '{
    jsonrpc:"2.0",
    id:1,
    method:"initialize",
    params:{
      protocolVersion:$version,
      capabilities:{},
      clientInfo:{name:"docintel-manual-test",version:"1.0"}
    }
  }')" | jq
```

Expected: a JSON-RPC `result` containing `protocolVersion`, `serverInfo`, and
`capabilities`. An `invalid_token` response means the token is missing,
expired, or was issued by a different backend than the one configured for the
MCP server.

## 4. List Available Tools

```bash
mcp_request '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | jq -r '.result.tools[] | [.name, .description] | @tsv'
```

Expected tools:

- `list_workspaces`
- `list_documents`
- `get_document`
- `create_document_upload`
- `complete_document_upload`
- `get_ingestion_status`
- `get_document_chunks`
- `embed_document`
- `delete_document`
- `create_video_upload`
- `complete_video_upload`
- `list_videos`
- `process_video`
- `get_video_status`
- `get_video_timeline`
- `get_video_transcript`
- `get_video_frames`
- `get_video_frame_url`
- `search_video`
- `summarize_document`
- `summarize_documents`
- `compare_documents`
- `search_knowledgebase`
- `create_chat_session`
- `list_chat_sessions`
- `get_chat_session`
- `update_chat_session`
- `delete_chat_session`
- `ask`

## 5A. List Accessible Workspaces

Run this before workspace-scoped document tests so you can select a valid
workspace ID returned for the authenticated user.

```bash
mcp_request '{
  "jsonrpc":"2.0",
  "id":4,
  "method":"tools/call",
  "params":{
    "name":"list_workspaces",
    "arguments":{}
  }
}' | tee /tmp/docintel-list-workspaces.json | jq
```

Expected: a workspace count and accessible workspaces containing fields such
as `id`, `name`, `my_role`, `doc_count`, and `member_count`. Export one returned
ID for later tests:

```bash
export WORKSPACE_ID="<workspace-uuid>"
```

## 5. List Resource Templates

```bash
mcp_request '{"jsonrpc":"2.0","id":3,"method":"resources/templates/list","params":{}}' \
  | jq -r '.result.resourceTemplates[] | [.uriTemplate, .name] | @tsv'
```

Expected templates:

- `docintel://workspaces/{workspace_id}/documents`
- `docintel://documents/{document_id}`
- `docintel://documents/{document_id}/chunks`
- `docintel://sessions/{session_id}`
- `docintel://videos/{document_id}`
- `docintel://videos/{document_id}/timeline`
- `docintel://videos/{document_id}/transcript`
- `docintel://videos/{document_id}/frames`

## 6. List Personal Documents

```bash
mcp_request '{
  "jsonrpc":"2.0",
  "id":4,
  "method":"tools/call",
  "params":{
    "name":"list_documents",
    "arguments":{"workspace_id":null}
  }
}' | tee /tmp/docintel-list-documents.json | jq
```

Expected: `isError` is absent or `false`, and the tool payload contains the
documents accessible to the authenticated user.

## 7. List Documents in One Workspace

Set a workspace ID that the authenticated user can access:

```bash
export WORKSPACE_ID="<workspace-uuid>"

mcp_request "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{
    jsonrpc:"2.0",
    id:5,
    method:"tools/call",
    params:{
      name:"list_documents",
      arguments:{workspace_id:$workspace_id}
    }
  }')" | tee /tmp/docintel-workspace-documents.json | jq
```

Select an embedded document ID from the response and export it:

```bash
export DOCUMENT_ID="<embedded-document-uuid>"
```

## 8. Get Document Metadata

```bash
mcp_request "$(jq -cn \
  --arg document_id "$DOCUMENT_ID" \
  '{
    jsonrpc:"2.0",
    id:6,
    method:"tools/call",
    params:{
      name:"get_document",
      arguments:{document_id:$document_id}
    }
  }')" | jq
```

Expected: the selected document's ID, name, status, classification, workspace,
and chunk metadata. The document must belong to the current user or an
accessible workspace.

## 9. Read the Document Resource

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://documents/$DOCUMENT_ID" \
  '{jsonrpc:"2.0",id:7,method:"resources/read",params:{uri:$uri}}')" | jq
```

Expected: `.result.contents` contains the document resource as JSON text.

## Direct Upload and Ingestion Lifecycle

Create an upload session using the exact local byte count and MIME type. The
returned URL is short-lived and must receive the same `Content-Type` header:

```bash
export FILE_PATH="/absolute/path/to/document.pdf"
export FILE_NAME="$(basename "$FILE_PATH")"
export FILE_SIZE="$(stat -f%z "$FILE_PATH")"
export CONTENT_TYPE="application/pdf"

mcp_request "$(jq -cn \
  --arg filename "$FILE_NAME" --arg content_type "$CONTENT_TYPE" \
  --argjson file_size "$FILE_SIZE" --arg workspace_id "$WORKSPACE_ID" \
  '{jsonrpc:"2.0",id:20,method:"tools/call",params:{name:"create_document_upload",arguments:{filename:$filename,content_type:$content_type,file_size:$file_size,workspace_id:$workspace_id}}}')" \
  | tee /tmp/docintel-upload-session.json | jq
```

Parse the JSON text returned by MCP, PUT the file directly to storage, then
call `complete_document_upload` with the unchanged values:

```bash
export UPLOAD_RESULT="$(jq -r '.result.content[0].text' /tmp/docintel-upload-session.json)"
export DOCUMENT_ID="$(jq -r '.doc_id' <<<"$UPLOAD_RESULT")"
export UPLOAD_URL="$(jq -r '.upload_url' <<<"$UPLOAD_RESULT")"
export GCS_SOURCE_PATH="$(jq -r '.gcs_source_path' <<<"$UPLOAD_RESULT")"

curl --fail-with-body -X PUT "$UPLOAD_URL" \
  -H "Content-Type: $CONTENT_TYPE" --upload-file "$FILE_PATH"

mcp_request "$(jq -cn \
  --arg doc_id "$DOCUMENT_ID" --arg filename "$FILE_NAME" \
  --arg content_type "$CONTENT_TYPE" --argjson file_size "$FILE_SIZE" \
  --arg gcs_source_path "$GCS_SOURCE_PATH" --arg workspace_id "$WORKSPACE_ID" \
  '{jsonrpc:"2.0",id:21,method:"tools/call",params:{name:"complete_document_upload",arguments:{doc_id:$doc_id,filename:$filename,content_type:$content_type,file_size:$file_size,gcs_source_path:$gcs_source_path,workspace_id:$workspace_id}}}')" | jq
```

Poll `get_ingestion_status` until the document is `chunked`, call
`embed_document`, and poll again until it is `embedded`. Deletion requires an
explicit `confirm:true` argument.

## 10. Read the Workspace Resource

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://workspaces/$WORKSPACE_ID/documents" \
  '{jsonrpc:"2.0",id:8,method:"resources/read",params:{uri:$uri}}')" | jq
```

Expected: workspace-scoped document information. Access to another user's
workspace must be rejected by DocIntel authorization.

## 11. Search the Knowledgebase Without a Session

This test invokes retrieval and the configured language model.

```bash
mcp_request "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg document_id "$DOCUMENT_ID" \
  --arg question "Summarize the important facts, risks, and next actions." \
  '{
    jsonrpc:"2.0",
    id:9,
    method:"tools/call",
    params:{
      name:"search_knowledgebase",
      arguments:{
        question:$question,
        workspace_id:$workspace_id,
        document_ids:[$document_id],
        redact_pii:false
      }
    }
  }')" | jq
```

Expected: a grounded answer, sources, and a trace ID. Confirm that returned
sources belong only to the selected document or workspace.

## 12. Create a Chat Session

This operation creates a persistent DocIntel session.

```bash
mcp_request "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg document_id "$DOCUMENT_ID" \
  '{
    jsonrpc:"2.0",
    id:10,
    method:"tools/call",
    params:{
      name:"create_chat_session",
      arguments:{
        title:"Manual MCP test session",
        workspace_id:$workspace_id,
        document_ids:[$document_id]
      }
    }
  }')" | tee /tmp/docintel-create-session.json | jq
```

Copy the returned session ID:

```bash
export SESSION_ID="<returned-session-uuid>"
```

## 13. Ask a Session-Backed Question

```bash
mcp_request "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg document_id "$DOCUMENT_ID" \
  --arg session_id "$SESSION_ID" \
  --arg question "What is the most important follow-up question?" \
  '{
    jsonrpc:"2.0",
    id:11,
    method:"tools/call",
    params:{
      name:"ask",
      arguments:{
        question:$question,
        workspace_id:$workspace_id,
        document_ids:[$document_id],
        session_id:$session_id,
        redact_pii:false
      }
    }
  }')" | jq
```

Expected: a grounded answer and sources, with the exchange saved in the
selected session.

## 14. Read the Saved Session Resource

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://sessions/$SESSION_ID" \
  '{jsonrpc:"2.0",id:12,method:"resources/read",params:{uri:$uri}}')" | jq
```

Expected: the session resource contains the session metadata and persisted
conversation history.

## 15. Negative Authorization Tests

First verify that an invalid token is rejected:

```bash
DOCINTEL_ACCESS_TOKEN="invalid-token" \
  mcp_request '{"jsonrpc":"2.0","id":13,"method":"tools/list","params":{}}' \
  | jq
```

Expected: `invalid_token` or an authentication-required error.

Then restore the valid token and request a random document:

```bash
export DOCINTEL_ACCESS_TOKEN="<your-valid-access-token>"

mcp_request '{
  "jsonrpc":"2.0",
  "id":14,
  "method":"tools/call",
  "params":{
    "name":"get_document",
    "arguments":{"document_id":"00000000-0000-0000-0000-000000000000"}
  }
}' | jq
```

Expected: a controlled not-found or access-denied tool response, not a server
traceback or leaked record.

## Pass Criteria

The manual test passes when:

- Health and initialization succeed.
- All six tools and all three resource templates are advertised.
- Document and workspace boundaries are enforced.
- Metadata and resource reads return the selected records.
- Search returns a grounded answer with sources and a trace ID.
- Session creation, session-backed Q&A, and session resource reads succeed.
- Invalid tokens and inaccessible IDs fail cleanly.
- No response exposes credentials, internal stack traces, or data from an
  unauthorized workspace.

The MCP server currently has no upload or delete tool, so this runbook does not
alter documents. The session test creates one saved chat session.
