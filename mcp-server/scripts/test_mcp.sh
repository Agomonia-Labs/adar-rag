#!/usr/bin/env bash
set -euo pipefail

MCP_URL="${MCP_URL:-http://localhost:8081/mcp}"
MCP_HEALTH_URL="${MCP_HEALTH_URL:-${MCP_URL%/mcp}/health}"
MCP_PROTOCOL_VERSION="${MCP_PROTOCOL_VERSION:-2025-06-18}"
DOCINTEL_ACCESS_TOKEN="${DOCINTEL_ACCESS_TOKEN:?Export DOCINTEL_ACCESS_TOKEN before running this script}"
WORKSPACE_ID="${WORKSPACE_ID:-}"
DOCUMENT_ID="${DOCUMENT_ID:-}"
RUN_GENERATIVE="${RUN_GENERATIVE:-true}"
RUN_SESSION="${RUN_SESSION:-false}"
QUESTION="${QUESTION:-What are the most important facts, decisions, risks, and follow-up actions in this document?}"

for command in curl jq; do
  command -v "$command" >/dev/null || {
    echo "ERROR: '$command' is required" >&2
    exit 1
  }
done

request_id=0
passed=0
skipped=0

heading() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }
pass() { passed=$((passed + 1)); printf '\033[32mPASS\033[0m %s\n' "$1"; }
skip() { skipped=$((skipped + 1)); printf '\033[33mSKIP\033[0m %s\n' "$1"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1" >&2; exit 1; }

mcp_request() {
  local method="$1"
  local params="$2"
  request_id=$((request_id + 1))
  jq -cn \
    --argjson id "$request_id" \
    --arg method "$method" \
    --argjson params "$params" \
    '{jsonrpc:"2.0",id:$id,method:$method,params:$params}' |
    curl --fail-with-body --silent --show-error \
      --request POST "$MCP_URL" \
      --header "Authorization: Bearer $DOCINTEL_ACCESS_TOKEN" \
      --header "Content-Type: application/json" \
      --header "Accept: application/json, text/event-stream" \
      --header "MCP-Protocol-Version: $MCP_PROTOCOL_VERSION" \
      --data-binary @-
}

assert_rpc() {
  local response="$1"
  local label="$2"
  if jq -e '.error != null' <<<"$response" >/dev/null; then
    jq '.error' <<<"$response" >&2
    fail "$label returned a JSON-RPC error"
  fi
  if jq -e '.result.isError == true' <<<"$response" >/dev/null; then
    jq '.result.content' <<<"$response" >&2
    fail "$label returned an MCP tool error"
  fi
}

tool_payload() {
  jq -c '
    if .result.structuredContent? then
      (.result.structuredContent.result // .result.structuredContent)
    elif (.result.content[0].text? | type) == "string" then
      (.result.content[0].text | try fromjson catch {text: .})
    else
      .result
    end
  '
}

assert_tool_payload() {
  local payload="$1"
  local label="$2"
  if jq -e '.ok == false' <<<"$payload" >/dev/null; then
    jq '.error' <<<"$payload" >&2
    fail "$label returned a DocIntel error"
  fi
}

tool_call() {
  local name="$1"
  local arguments="$2"
  mcp_request "tools/call" "$(jq -cn --arg name "$name" --argjson arguments "$arguments" '{name:$name,arguments:$arguments}')"
}

resource_read() {
  local uri="$1"
  mcp_request "resources/read" "$(jq -cn --arg uri "$uri" '{uri:$uri}')"
}

heading "Configuration"
printf 'MCP URL: %s\n' "$MCP_URL"
printf 'Workspace: %s\n' "${WORKSPACE_ID:-personal documents}"
printf 'Generative tests: %s\n' "$RUN_GENERATIVE"
printf 'Persistent session tests: %s\n' "$RUN_SESSION"
printf 'Token loaded: %s characters (value hidden)\n' "${#DOCINTEL_ACCESS_TOKEN}"

heading "Health"
health="$(curl --fail-with-body --silent --show-error "$MCP_HEALTH_URL")" || fail "MCP health endpoint is unavailable"
jq . <<<"$health"
jq -e '.status == "ok"' <<<"$health" >/dev/null || fail "MCP health response is not healthy"
pass "MCP health endpoint"

heading "Initialize"
initialize="$(mcp_request "initialize" "$(jq -cn --arg version "$MCP_PROTOCOL_VERSION" '{protocolVersion:$version,capabilities:{},clientInfo:{name:"docintel-smoke-test",version:"1.0"}}')")"
assert_rpc "$initialize" "initialize"
jq '.result | {protocolVersion,serverInfo,capabilities}' <<<"$initialize"
pass "MCP authenticated initialization"

heading "Tool Discovery"
tools="$(mcp_request "tools/list" '{}')"
assert_rpc "$tools" "tools/list"
jq -r '.result.tools[]?.name' <<<"$tools"
for expected in \
  list_workspaces list_documents get_document get_ingestion_status get_document_chunks \
  create_document_upload complete_document_upload embed_document delete_document \
  create_video_upload complete_video_upload list_videos process_video get_video_status \
  get_video_timeline get_video_transcript get_video_frames get_video_frame_url search_video \
  summarize_document summarize_documents compare_documents \
  search_knowledgebase create_chat_session list_chat_sessions get_chat_session \
  update_chat_session delete_chat_session ask; do
  jq -e --arg expected "$expected" '.result.tools | any(.name == $expected)' <<<"$tools" >/dev/null || fail "Tool '$expected' is missing"
done
pass "All expected tools are advertised"

heading "Resource Discovery"
templates="$(mcp_request "resources/templates/list" '{}')"
assert_rpc "$templates" "resources/templates/list"
jq -r '.result.resourceTemplates[]?.uriTemplate' <<<"$templates"
for expected in \
  'docintel://workspaces/{workspace_id}/documents' \
  'docintel://documents/{document_id}' \
  'docintel://documents/{document_id}/chunks' \
  'docintel://sessions/{session_id}' \
  'docintel://videos/{document_id}' \
  'docintel://videos/{document_id}/timeline' \
  'docintel://videos/{document_id}/transcript' \
  'docintel://videos/{document_id}/frames'; do
  jq -e --arg expected "$expected" '.result.resourceTemplates | any(.uriTemplate == $expected)' <<<"$templates" >/dev/null || fail "Resource '$expected' is missing"
done
pass "All expected resource templates are advertised"

heading "Workspace Listing"
workspaces_response="$(tool_call "list_workspaces" '{}')"
assert_rpc "$workspaces_response" "list_workspaces"
workspaces_payload="$(tool_payload <<<"$workspaces_response")"
assert_tool_payload "$workspaces_payload" "list_workspaces"
jq '{count,workspaces:[.workspaces[]? | {id,name,my_role,doc_count,member_count}]}' <<<"$workspaces_payload"
pass "Accessible workspaces listed"

heading "Document Listing"
workspace_json="null"
[[ -n "$WORKSPACE_ID" ]] && workspace_json="$(jq -Rn --arg value "$WORKSPACE_ID" '$value')"
list_response="$(tool_call "list_documents" "$(jq -cn --argjson workspace_id "$workspace_json" '{workspace_id:$workspace_id}')")"
assert_rpc "$list_response" "list_documents"
list_payload="$(tool_payload <<<"$list_response")"
assert_tool_payload "$list_payload" "list_documents"
jq '{workspace_id,count,documents:[.documents[]? | {id,original_name,status,file_type,doc_type,doc_domain}]}' <<<"$list_payload"
pass "Accessible documents listed"

if [[ -z "$DOCUMENT_ID" ]]; then
  DOCUMENT_ID="$(jq -r '[.documents[]? | select(.status == "embedded")][0].id // empty' <<<"$list_payload")"
fi

if [[ -z "$DOCUMENT_ID" ]]; then
  skip "No embedded document is available; document-specific tests cannot run"
  printf '\nSummary: %s passed, %s skipped.\n' "$passed" "$skipped"
  exit 0
fi
printf 'Selected embedded document: %s\n' "$DOCUMENT_ID"

heading "Document Metadata Tool"
document_response="$(tool_call "get_document" "$(jq -cn --arg document_id "$DOCUMENT_ID" '{document_id:$document_id}')")"
assert_rpc "$document_response" "get_document"
document_payload="$(tool_payload <<<"$document_response")"
assert_tool_payload "$document_payload" "get_document"
jq '{id,original_name,status,file_type,doc_type,doc_domain,doc_language,workspace_id,chunk_count}' <<<"$document_payload"
pass "Document metadata retrieved"

heading "Document Resource"
document_resource="$(resource_read "docintel://documents/$DOCUMENT_ID")"
assert_rpc "$document_resource" "document resource"
jq '.result.contents[0] | {uri,mimeType,text}' <<<"$document_resource"
pass "Document resource read"

if [[ -n "$WORKSPACE_ID" ]]; then
  heading "Workspace Documents Resource"
  workspace_resource="$(resource_read "docintel://workspaces/$WORKSPACE_ID/documents")"
  assert_rpc "$workspace_resource" "workspace documents resource"
  jq '.result.contents[0] | {uri,mimeType,text}' <<<"$workspace_resource"
  pass "Workspace documents resource read"
else
  skip "Workspace resource test requires WORKSPACE_ID"
fi

if [[ "$RUN_GENERATIVE" == "true" ]]; then
  heading "Grounded Knowledge Search"
  search_response="$(tool_call "search_knowledgebase" "$(jq -cn \
    --arg question "$QUESTION" \
    --arg document_id "$DOCUMENT_ID" \
    --arg workspace_id "$WORKSPACE_ID" \
    '{question:$question,document_ids:[$document_id],workspace_id:(if $workspace_id == "" then null else $workspace_id end),redact_pii:false}')")"
  assert_rpc "$search_response" "search_knowledgebase"
  search_payload="$(tool_payload <<<"$search_response")"
  assert_tool_payload "$search_payload" "search_knowledgebase"
  jq '{answer,sources,trace_id}' <<<"$search_payload"
  jq -e '.answer | type == "string" and length > 0' <<<"$search_payload" >/dev/null || fail "Grounded search returned no answer"
  pass "Grounded knowledge search"
else
  skip "Generative search disabled with RUN_GENERATIVE=$RUN_GENERATIVE"
fi

if [[ "$RUN_SESSION" == "true" ]]; then
  heading "Persistent Chat Session"
  session_response="$(tool_call "create_chat_session" "$(jq -cn \
    --arg document_id "$DOCUMENT_ID" \
    --arg workspace_id "$WORKSPACE_ID" \
    '{title:"MCP smoke-test session",document_ids:[$document_id],workspace_id:(if $workspace_id == "" then null else $workspace_id end)}')")"
  assert_rpc "$session_response" "create_chat_session"
  session_payload="$(tool_payload <<<"$session_response")"
  assert_tool_payload "$session_payload" "create_chat_session"
  SESSION_ID="$(jq -r '.id // empty' <<<"$session_payload")"
  [[ -n "$SESSION_ID" ]] || fail "Session creation returned no ID"
  jq '{id,title,document_ids}' <<<"$session_payload"
  pass "Chat session created"

  heading "Session-backed Q&A"
  ask_response="$(tool_call "ask" "$(jq -cn \
    --arg question "What is one important follow-up question based on this document?" \
    --arg document_id "$DOCUMENT_ID" \
    --arg workspace_id "$WORKSPACE_ID" \
    --arg session_id "$SESSION_ID" \
    '{question:$question,document_ids:[$document_id],workspace_id:(if $workspace_id == "" then null else $workspace_id end),session_id:$session_id,redact_pii:false}')")"
  assert_rpc "$ask_response" "ask"
  ask_payload="$(tool_payload <<<"$ask_response")"
  assert_tool_payload "$ask_payload" "ask"
  jq '{answer,sources,trace_id}' <<<"$ask_payload"
  jq -e '.answer | type == "string" and length > 0' <<<"$ask_payload" >/dev/null || fail "Session-backed ask returned no answer"
  pass "Session-backed grounded question"

  heading "Session Resource"
  session_resource="$(resource_read "docintel://sessions/$SESSION_ID")"
  assert_rpc "$session_resource" "session resource"
  jq '.result.contents[0] | {uri,mimeType,text}' <<<"$session_resource"
  pass "Saved session resource read"
else
  skip "Persistent session tests disabled; set RUN_SESSION=true to enable them"
fi

printf '\n\033[1;32mMCP smoke test complete: %s passed, %s skipped.\033[0m\n' "$passed" "$skipped"
[[ "$RUN_SESSION" == "true" ]] && printf 'Saved smoke-test session ID: %s\n' "$SESSION_ID"
