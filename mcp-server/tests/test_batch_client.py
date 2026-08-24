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
