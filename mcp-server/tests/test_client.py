import json

import httpx
import pytest

from docintel_mcp.client import DocIntelApiClient
from docintel_mcp.errors import DocIntelMcpError


@pytest.mark.asyncio
async def test_list_workspaces_forwards_token_and_returns_memberships():
    async def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer secret-token"
        assert request.url.path == "/api/workspaces/"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "workspace-1",
                    "name": "Finance",
                    "my_role": "editor",
                    "doc_count": 4,
                    "member_count": 2,
                }
            ],
        )

    async with DocIntelApiClient(
        "https://docintel.test",
        "secret-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.list_workspaces()

    assert result[0]["id"] == "workspace-1"
    assert result[0]["my_role"] == "editor"


@pytest.mark.asyncio
async def test_workspace_documents_forwards_token_and_workspace_path():
    async def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer secret-token"
        assert request.url.path == "/api/workspaces/workspace-1/documents"
        return httpx.Response(200, json=[{"id": "doc-1", "status": "embedded"}])

    async with DocIntelApiClient(
        "https://docintel.test",
        "secret-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.list_documents("workspace-1")

    assert result == [{"id": "doc-1", "status": "embedded"}]


@pytest.mark.asyncio
async def test_grounded_answer_collects_sse_tokens_sources_and_trace_id():
    async def handler(request: httpx.Request):
        assert request.url.path == "/api/chat/stream"
        events = [
            {"type": "token", "text": "Grounded "},
            {"type": "token", "text": "answer"},
            {"type": "done", "sources": [{"document_id": "doc-1", "chunk_index": 2}]},
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream", "x-trace-id": "trace-1"})

    async with DocIntelApiClient(
        "https://docintel.test",
        "secret-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.ask("What happened?", ["doc-1"], "workspace-1")

    assert result["answer"] == "Grounded answer"
    assert result["sources"][0]["chunk_index"] == 2
    assert result["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_backend_forbidden_is_preserved_as_safe_error():
    async def handler(_request: httpx.Request):
        return httpx.Response(403, json={"detail": "Document is not accessible"})

    async with DocIntelApiClient(
        "https://docintel.test",
        "secret-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(DocIntelMcpError) as error:
            await client.get_document("doc-private")

    assert error.value.code == "forbidden"
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_session_rejects_document_from_another_workspace():
    async def handler(request: httpx.Request):
        if request.url.path == "/api/documents/doc-1":
            return httpx.Response(200, json={"id": "doc-1", "workspace_id": "workspace-2"})
        raise AssertionError("Session API must not be called after a workspace mismatch")

    async with DocIntelApiClient(
        "https://docintel.test",
        "secret-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(DocIntelMcpError) as error:
            await client.create_session("Test", ["doc-1"], "workspace-1")

    assert error.value.code == "workspace_mismatch"
