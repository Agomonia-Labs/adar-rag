# Conversation Recording Assistant through MCP

This runbook covers the governed text-turn lifecycle behind the DocIntel
Conversation Recording Assistant. Audio capture and speech-to-text happen in
the calling client. MCP manages consent, turns, review, approval, knowledgebase
publication, status, and deletion.

## Required OAuth scopes

```text
sessions:write
reviews:approve
knowledge:query
```

Start from the repository root and complete OAuth login:

```bash
cd /Users/brajadas/project/adar-rag
source deploy.sh --oauth-login
```

## 1. Start a recording session

Use `en-US` for English or `bn-BD` for Bangla.

```bash
START_RESPONSE="$(mcp_tool start_conversation_recording "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id,language_code:"en-US"}'
)")"

printf '%s\n' "$START_RESPONSE" | tool_data | tee /tmp/conversation-start.json | jq
export CONVERSATION_SESSION_ID="$(jq -r '.id // .session_id // empty' /tmp/conversation-start.json)"
```

## 2. Confirm consent

```bash
mcp_tool confirm_conversation_consent "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  '{session_id:$id,confirmed:true}'
)" | tool_data | jq
```

## 3. Add transcribed turns

```bash
mcp_tool add_conversation_turn "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  --arg text "I am calling to provide an update about the account." \
  '{session_id:$id,transcript:$text}'
)" | tool_data | jq
```

Repeat this command for each utterance. The response contains the assistant's
next prompt and updated collection state.

## 4. Finish collection and review

```bash
mcp_tool finish_conversation_recording "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  '{session_id:$id}'
)" | tool_data | jq

mcp_tool get_conversation_recording "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  '{session_id:$id}'
)" | tool_data | tee /tmp/conversation-review.json | jq
```

The transcript resource provides a compact review payload:

```bash
mcp_request "$(jq -cn \
  --arg uri "docintel://conversations/$CONVERSATION_SESSION_ID/transcript" \
  '{jsonrpc:"2.0",id:1,method:"resources/read",params:{uri:$uri}}'
)" | jq
```

## 5. Approve the edited transcript

Approval is the publication boundary. It requires `reviews:approve` and
`confirm:true`.

```bash
export REVIEWED_TRANSCRIPT="$(jq -r '.editable_transcript // empty' /tmp/conversation-review.json)"

if [[ -z "${REVIEWED_TRANSCRIPT//[[:space:]]/}" ]]; then
  echo "Transcript is empty. Add at least one turn, finish the conversation, and fetch it again."
  exit 1
fi

mcp_tool approve_conversation_transcript "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  --arg transcript "$REVIEWED_TRANSCRIPT" \
  '{session_id:$id,transcript:$transcript,confirm:true}'
)" | tool_data | jq
```

After deploying this MCP version, a blank `transcript` is reconstructed from
the persisted conversation turns. Supplying the edited transcript explicitly
is still recommended because it preserves the reviewer's final changes.

After approval, DocIntel creates a document, chunks the reviewed transcript,
embeds it, and exposes its processing state through the conversation record.

## 6. Monitor and query the published knowledge

```bash
mcp_tool get_conversation_recording "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  '{session_id:$id}'
)" | tool_data | tee /tmp/conversation-published.json | jq

export DOCUMENT_ID="$(jq -r '.document_id // empty' /tmp/conversation-published.json)"

mcp_tool search_knowledgebase "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  --arg document_id "$DOCUMENT_ID" \
  '{question:"What information was provided and what should happen next?",workspace_id:$workspace_id,document_ids:[$document_id],history:[],redact_pii:false}'
)" | tool_data | jq
```

## 7. List or delete recordings

```bash
mcp_tool list_conversation_recordings "$(jq -cn \
  --arg workspace_id "$WORKSPACE_ID" \
  '{workspace_id:$workspace_id}'
)" | tool_data | jq

mcp_tool delete_conversation_recording "$(jq -cn \
  --arg id "$CONVERSATION_SESSION_ID" \
  '{session_id:$id,confirm:true}'
)" | tool_data | jq
```

Deletion removes the recording and its owned transcript, document, chunks,
vectors, and derived records through the existing DocIntel deletion lifecycle.
