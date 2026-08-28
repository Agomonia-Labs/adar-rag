import json

import httpx
import pytest

from docintel_mcp.client import DocIntelApiClient


@pytest.mark.asyncio
async def test_batch_client_uses_durable_batch_endpoints():
    seen = []

    async def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"batch_job_id": "batch-1", "status": "queued"})

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        await client.start_document_batch("embedding", {"document_ids": ["doc-1"]})
        await client.get_batch_job("batch-1")
        await client.retry_batch("batch-1")

    assert seen == [
        ("POST", "/api/batches/embedding", {"document_ids": ["doc-1"]}),
        ("GET", "/api/batches/batch-1", None),
        ("POST", "/api/batches/batch-1/retry", None),
    ]


@pytest.mark.asyncio
async def test_enterprise_client_uses_events_reviews_versions_and_evaluation_endpoints():
    seen = []

    async def handler(request: httpx.Request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        await client.list_events(10, "batch", "batch-1")
        await client.create_review_task({"vertical": "healthcare", "run_id": "run-1", "title": "Review"})
        await client.register_document_version("doc-2", {"previous_document_id": "doc-1"})
        await client.evaluate_trace("trace-1", "groundedness")
        await client.resume_batch("batch-1")

    assert seen == [
        ("GET", "/api/mcp-enterprise/events"),
        ("POST", "/api/mcp-enterprise/reviews"),
        ("POST", "/api/mcp-enterprise/documents/doc-2/versions"),
        ("POST", "/api/mcp-enterprise/evaluations"),
        ("POST", "/api/batches/batch-1/resume"),
    ]
