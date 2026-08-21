# ADAR DocIntel MCP Server

This service exposes the existing DocIntel knowledgebase through MCP without
duplicating document, RAG, authorization, or workflow logic. It forwards the
caller's DocIntel bearer token to the backend, where tenant and workspace
authorization remains authoritative.

## MVP surface

Tools:

- `list_workspaces`
- `list_documents`
- `get_document`
- `search_knowledgebase`
- `create_chat_session`
- `ask`

Resources:

- `docintel://workspaces/{workspace_id}/documents`
- `docintel://documents/{document_id}`
- `docintel://sessions/{session_id}`

`search_knowledgebase` currently uses the grounded DocIntel chat pipeline. It
returns the generated answer and supporting sources from hybrid retrieval and
re-ranking; it is not a second vector-search implementation.

## Run locally

```bash
cd mcp-server
python -m venv .venv
.venv/bin/pip install -e '.[test]'
cp .env.example .env
.venv/bin/docintel-mcp
```

Connect an MCP client to `http://localhost:8081/mcp` and send the existing
DocIntel access token as `Authorization: Bearer <token>`.

## Security boundary

- Caller identity is never accepted from tool arguments or custom headers.
- MCP validates the token through `/api/auth/me`; the backend then checks every
  document/workspace operation again.
- `workspace_id` narrows a request; it does not grant access.
- Server capabilities are an operator-controlled allowlist, not caller scopes.
- Tokens and document contents are excluded from gateway logs.
- Correlation IDs are forwarded through `X-Trace-Id`.

The Cloud Run service permits network-level unauthenticated access so the MCP
protocol can use the `Authorization` header for the DocIntel token. This does
not make DocIntel data public: every data tool/resource rejects a missing token,
and the backend remains the authorization authority. Put API Gateway, Cloud
Armor, quotas, or an OAuth-aware proxy in front when exposing the service.

Production OAuth discovery, service-account clients, per-principal scopes,
quotas, and API Gateway policy are the next hardening milestone.

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
