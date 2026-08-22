# ADAR DocIntel MCP Server

This service exposes the existing DocIntel knowledgebase through MCP without
duplicating document, RAG, authorization, or workflow logic. It forwards the
caller's DocIntel bearer token to the backend, where tenant and workspace
authorization remains authoritative.

## MCP surface

Tools:

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
- `list_vertical_workflows`
- `start_vertical_workflow`
- `get_vertical_run`
- `list_vertical_runs`
- `save_vertical_review`
- `approve_vertical_run`
- `generate_vertical_packet`

Resources:

- `docintel://workspaces/{workspace_id}/documents`
- `docintel://documents/{document_id}`
- `docintel://documents/{document_id}/chunks`
- `docintel://sessions/{session_id}`
- `docintel://videos/{document_id}`
- `docintel://videos/{document_id}/timeline`
- `docintel://videos/{document_id}/transcript`
- `docintel://videos/{document_id}/frames`
- `docintel://workflows/catalog`
- `docintel://workflows/{vertical}/runs/{run_id}`

Direct uploads use a two-step flow: obtain a short-lived signed PUT URL, upload
the bytes directly to cloud storage, then call `complete_document_upload` to
verify the object and start chunking. This keeps large payloads out of MCP and
the backend HTTP proxy.

`search_knowledgebase` currently uses the grounded DocIntel chat pipeline. It
returns the generated answer and supporting sources from hybrid retrieval and
re-ranking; it is not a second vector-search implementation.

Video tools reuse DocIntel's existing cloud upload, transcript, frame sampling,
timeline segmentation, embedding, and timestamp-grounded Q&A pipeline.
Summary and comparison tools collect the existing backend SSE streams into
structured MCP results while preserving progress and trace IDs.

Vertical tools expose healthcare, prior authorization, finance/tax readiness,
talent readiness, employee mobility, and lease intelligence through one
discoverable contract. Review edits and approval are intentionally separate;
`approve_vertical_run` also requires `confirm=true`. Generated healthcare,
finance, and talent PDF packets are stored as governed DocIntel documents so
they can be downloaded, embedded, retrieved, and audited like other content.

## Run locally

```bash
cd mcp-server
python -m venv .venv
.venv/bin/pip install -e '.[test]'
cp .env.example .env
.venv/bin/docintel-mcp
```

Connect an MCP client to `http://localhost:8081/mcp`. Local manual testing can
use an existing token; production clients use the OAuth flow described below.

## Security boundary

- Caller identity is never accepted from tool arguments or custom headers.
- MCP exchanges an audience-bound token through `/internal/oauth/introspect`;
  the public token is never forwarded to application APIs.
- `workspace_id` narrows a request; it does not grant access.
- Every tool requires both an operator-enabled capability and a caller-granted
  OAuth scope.
- Tokens and document contents are excluded from gateway logs.
- Correlation IDs are forwarded through `X-Trace-Id`.

The Cloud Run service permits network-level unauthenticated access so the MCP
protocol can perform OAuth discovery and carry its access token. Production
authorization uses DocIntel login, email MFA, authorization code with S256
PKCE, short-lived audience-bound tokens, rotating refresh tokens, and scoped
tools. This does not make DocIntel data public. Put API Gateway, Cloud Armor,
and quotas in front when exposing the service.

The staged production release gate is documented in
[`PUBLIC_DEPLOYMENT.md`](PUBLIC_DEPLOYMENT.md).

## End-to-end smoke test

For commands that test each capability separately, see
[`MANUAL_TESTING.md`](MANUAL_TESTING.md).

With the MCP server running and a DocIntel access token exported:

```bash
export DOCINTEL_ACCESS_TOKEN="<token>"
./scripts/test_mcp.sh
```

Use a specific workspace or document:

```bash
WORKSPACE_ID="<workspace-id>" DOCUMENT_ID="<document-id>" ./scripts/test_mcp.sh
```

Grounded search runs by default. Session creation and persistence are opt-in
because they write a new chat session to DocIntel:

```bash
RUN_SESSION=true ./scripts/test_mcp.sh
```

Disable model-backed requests for a read-only connectivity test:

```bash
RUN_GENERATIVE=false ./scripts/test_mcp.sh
```
