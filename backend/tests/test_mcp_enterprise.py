import json

import pytest

from routes.mcp_enterprise import WORKFLOWS, _require_document_access, _require_workspace_access
from services.mcp_enterprise import bounded_payload, idempotent_result, normalize_citations, request_hash


def test_normalized_citations_provide_stable_document_page_and_timestamp_contract():
    result = normalize_citations([{
        "doc_id": "doc-1", "filename": "lease.pdf", "index": 3, "page": 8,
        "start_seconds": 60, "end_seconds": 75, "score": "0.91", "content": "Evidence text",
    }])

    assert result == [{
        "citation_id": "citation-1", "document_id": "doc-1", "document_name": "lease.pdf",
        "chunk_id": "", "chunk_index": 3, "page_number": 8, "start_seconds": 60,
        "end_seconds": 75, "retrieval_score": 0.91, "rerank_score": None,
        "confidence": None, "source_url": "", "excerpt": "Evidence text",
    }]


def test_event_payload_does_not_store_prompts_tokens_or_credentials():
    result = bounded_payload({"status": "completed", "progress_pct": 100, "prompt": "private", "token": "secret"})
    assert result == {"status": "completed", "progress_pct": 100}


def test_workflow_catalog_is_versioned_and_declares_review_contracts():
    assert WORKFLOWS["healthcare_prior_auth"]["version"]
    assert WORKFLOWS["healthcare_prior_auth"]["review"] is True
    assert "policy_document_ids" in WORKFLOWS["healthcare_prior_auth"]["required"]


class ReplayDb:
    def __init__(self, row): self.row = row
    async def fetchrow(self, *_args): return self.row


@pytest.mark.anyio
async def test_idempotency_replays_matching_request_and_rejects_changed_request():
    payload = {"document_ids": ["doc-1"]}
    row = {"request_hash": request_hash(payload), "response_data": json.dumps({"batch_job_id": "batch-1"}),
           "resource_type": "batch", "resource_id": "batch-1", "status": "completed"}
    replay = await idempotent_result(ReplayDb(row), "user-1", "batch_embedding", "stable-key", payload)
    conflict = await idempotent_result(ReplayDb(row), "user-1", "batch_embedding", "stable-key", {"document_ids": ["doc-2"]})

    assert replay == {"batch_job_id": "batch-1", "idempotent_replay": True}
    assert conflict["error"]["code"] == "idempotency_conflict"


class AccessDb:
    def __init__(self, allowed): self.allowed = allowed
    async def fetchval(self, *_args): return self.allowed


@pytest.mark.anyio
async def test_workspace_and_document_guards_hide_unauthorized_resources():
    await _require_workspace_access(AccessDb(True), "workspace-1", "user-1")
    await _require_document_access(AccessDb(True), "document-1", "user-1")

    with pytest.raises(Exception) as workspace_error:
        await _require_workspace_access(AccessDb(False), "workspace-1", "user-1")
    with pytest.raises(Exception) as document_error:
        await _require_document_access(AccessDb(False), "document-1", "user-1")

    assert workspace_error.value.status_code == 404
    assert document_error.value.status_code == 404
