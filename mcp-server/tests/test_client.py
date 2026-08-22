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


@pytest.mark.asyncio
async def test_direct_upload_lifecycle_uses_document_endpoints():
    requests: list[tuple[str, str, dict | None]] = []

    async def handler(request: httpx.Request):
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, payload))
        if request.url.path.endswith("upload-session"):
            return httpx.Response(200, json={"doc_id": "doc-1", "upload_url": "https://storage.test/put"})
        if request.url.path.endswith("upload-complete"):
            return httpx.Response(200, json={"doc_id": "doc-1", "status": "chunking"})
        if request.url.path.endswith("/embed"):
            return httpx.Response(200, json={"doc_id": "doc-1", "message": "Embedding started"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted": "doc-1", "warnings": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        session = await client.create_upload_session("report.pdf", "application/pdf", 1234, "workspace-1", True)
        completed = await client.complete_upload({
            "doc_id": "doc-1", "filename": "report.pdf", "content_type": "application/pdf",
            "file_size": 1234, "gcs_source_path": "users/u/documents/doc-1/source/report.pdf",
            "workspace_id": "workspace-1", "redact_pii": True,
        })
        embedded = await client.trigger_embedding("doc-1")
        deleted = await client.delete_document("doc-1")

    assert session["doc_id"] == completed["doc_id"] == embedded["doc_id"] == "doc-1"
    assert deleted["deleted"] == "doc-1"
    assert requests[0][2]["redact_pii"] is True
    assert [item[:2] for item in requests] == [
        ("POST", "/api/documents/upload-session"),
        ("POST", "/api/documents/upload-complete"),
        ("POST", "/api/documents/doc-1/embed"),
        ("DELETE", "/api/documents/doc-1"),
    ]


@pytest.mark.asyncio
async def test_session_lifecycle_uses_persistent_session_endpoints():
    async def handler(request: httpx.Request):
        if request.method == "GET" and request.url.path == "/api/chat/sessions/":
            assert request.url.params["workspace_id"] == "workspace-1"
            return httpx.Response(200, json=[{"id": "session-1", "title": "Original"}])
        if request.method == "PATCH":
            assert json.loads(request.content) == {"title": "Renamed"}
            return httpx.Response(200, json={"ok": True})
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        sessions = await client.list_sessions("workspace-1")
        updated = await client.update_session("session-1", {"title": "Renamed"})
        deleted = await client.delete_session("session-1")

    assert sessions[0]["id"] == "session-1"
    assert updated == {"ok": True}
    assert deleted == {"ok": True}


@pytest.mark.asyncio
async def test_video_lifecycle_and_timestamp_question_use_video_endpoints():
    async def handler(request: httpx.Request):
        if request.url.path == "/api/video/upload-session":
            return httpx.Response(200, json={"doc_id": "video-1", "upload_url": "https://storage.test/video"})
        if request.url.path == "/api/video/upload-complete":
            return httpx.Response(200, json={"doc_id": "video-1", "status": "uploaded"})
        if request.url.path == "/api/video/video-1/process":
            assert json.loads(request.content)["transcript_language"] == "hi-IN"
            return httpx.Response(200, json={"doc_id": "video-1", "message": "Video processing started"})
        if request.url.path == "/api/video/video-1/status":
            return httpx.Response(200, json={"doc_id": "video-1", "processing_status": "completed", "progress_pct": 100})
        if request.url.path == "/api/video/video-1/timeline":
            return httpx.Response(200, json={"segments": [{"start_seconds": 60, "transcript": "Opening"}], "frames": []})
        if request.url.path == "/api/video/video-1/ask":
            return httpx.Response(200, json={"answer": "The topic starts at 01:00.", "sources": [{"start_seconds": 60}]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        upload = await client.create_video_upload_session("clip.mp4", "video/mp4", 5000, "workspace-1")
        complete = await client.complete_video_upload({"doc_id": "video-1"})
        process = await client.process_video("video-1", {"rights_confirmed": True, "transcript_language": "hi-IN"})
        status = await client.get_video_status("video-1")
        timeline = await client.get_video_timeline("video-1")
        answer = await client.ask_video("video-1", "When does it start?", 8)

    assert upload["doc_id"] == complete["doc_id"] == process["doc_id"] == "video-1"
    assert status["progress_pct"] == 100
    assert timeline["segments"][0]["start_seconds"] == 60
    assert answer["sources"][0]["start_seconds"] == 60


@pytest.mark.asyncio
async def test_summary_and_comparison_collect_streaming_results():
    async def handler(request: httpx.Request):
        if request.url.path.startswith("/api/summarize/"):
            events = [
                {"type": "meta", "stage": "map", "batch": 1, "of": 2},
                {"type": "token", "text": "Executive "},
                {"type": "token", "text": "summary"},
                {"type": "done"},
            ]
        elif request.url.path == "/api/compare/stream":
            events = [
                {"type": "status", "message": "Loading documents"},
                {"type": "result", "data": {"similarity_score": 0.75, "sections": []}},
            ]
        else:
            raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream", "x-trace-id": "trace-gen"})

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        summary = await client.summarize_document("doc-1", "executive", "", [], False)
        comparison = await client.compare_documents("doc-1", "doc-2", False)

    assert summary["summary"] == "Executive summary"
    assert summary["progress"][0]["stage"] == "map"
    assert summary["trace_id"] == "trace-gen"
    assert comparison["comparison"]["similarity_score"] == 0.75


@pytest.mark.asyncio
async def test_vertical_workflow_start_routes_to_existing_backend_contracts():
    requests: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request):
        payload = json.loads(request.content) if request.content else {}
        requests.append((request.url.path, payload))
        return httpx.Response(200, json={"run_id": f"run-{len(requests)}", "status": "running"})

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        await client.start_vertical_workflow(
            "healthcare_prior_auth", ["encounter-1", "policy-1"], "workspace-1", {},
        )
        await client.start_vertical_workflow(
            "finance_tax_readiness", ["w2-1", "return-1"], "workspace-1",
            {"client_name": "Avery", "tax_year": "2026"},
        )
        await client.start_vertical_workflow(
            "talent_readiness", ["resume-1", "jd-1"], "workspace-1",
            {"job_description_id": "jd-1", "candidate_name": "Avery"},
        )
        await client.start_vertical_workflow(
            "lease_intelligence", ["lease-1"], "workspace-1", {"amendment_document_id": "amendment-1"},
        )

    assert requests == [
        ("/api/healthcare/encounter-1/prior-auth-workflow", {"policy_document_ids": ["policy-1"]}),
        ("/api/finance-tax/tax-submission-runs", {
            "document_ids": ["w2-1", "return-1"], "client_name": "Avery", "tax_year": "2026",
            "filing_status": "", "notes": "",
        }),
        ("/api/talent/runs", {
            "resume_document_ids": ["resume-1"], "job_description_id": "jd-1",
            "candidate_name": "Avery", "notes": "", "workflow_type": "candidate_readiness",
        }),
        ("/api/lease/lease-1/agent-workflow", {"amendment_document_id": "amendment-1"}),
    ]


@pytest.mark.asyncio
async def test_human_review_and_approval_use_separate_backend_actions():
    requests: list[tuple[str, str, dict]] = []

    async def handler(request: httpx.Request):
        payload = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, payload))
        return httpx.Response(200, json={"run_id": "run-1", "status": "reviewed"})

    packet = {"candidate_profile": {"name": "Avery"}}
    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        await client.save_vertical_review("talent", "run-1", packet, "Reviewed", None)
        await client.approve_vertical_run("talent", "run-1", packet, "Approved", None)

    assert requests == [
        ("PATCH", "/api/talent/runs/run-1/review", {"packet": packet, "notes": "Reviewed"}),
        ("POST", "/api/talent/runs/run-1/approve", {"packet": packet, "notes": "Approved"}),
    ]


@pytest.mark.asyncio
async def test_packet_generation_creates_queryable_document_artifacts():
    async def handler(request: httpx.Request):
        if request.url.path == "/api/healthcare/agent-runs/health-1/prior-auth-packet/pdf":
            return httpx.Response(200, json={"ok": True, "document": {"doc_id": "packet-health"}})
        if request.url.path == "/api/finance-tax/agent-runs/finance-1/advisor-packet/pdf":
            assert json.loads(request.content)["packet"]["tax_organizer"] == {}
            return httpx.Response(200, json={"ok": True, "document": {"doc_id": "packet-finance"}})
        if request.url.path == "/api/talent/runs/talent-1/packet/ingest":
            return httpx.Response(200, json={"ok": True, "document": {"id": "packet-talent"}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    async with DocIntelApiClient("https://docintel.test", "token", transport=httpx.MockTransport(handler)) as client:
        healthcare = await client.generate_vertical_packet("healthcare", "health-1", "prior_auth", None)
        finance = await client.generate_vertical_packet("finance_tax", "finance-1", "advisor", {"tax_organizer": {}})
        talent = await client.generate_vertical_packet("talent", "talent-1", "candidate", None)

    assert healthcare["document"]["doc_id"] == "packet-health"
    assert finance["document"]["doc_id"] == "packet-finance"
    assert talent["document"]["id"] == "packet-talent"
