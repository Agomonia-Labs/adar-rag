# DocIntel MCP tool catalog

| Tool | Capability | Existing API |
| --- | --- | --- |
| `list_documents` | `documents:read` | `GET /api/documents/` or workspace documents |
| `get_document` | `documents:read` | `GET /api/documents/{id}` |
| `search_knowledgebase` | `knowledge:query` | `POST /api/chat/stream` |
| `create_chat_session` | `sessions:write` | `POST /api/chat/sessions/` |
| `ask` | `knowledge:query` | Session read plus `POST /api/chat/stream` |

The first release intentionally excludes upload, deletion, workflow mutation,
and packet approval. Those operations require idempotency, stronger OAuth
scopes, asynchronous job contracts, and additional audit controls.

