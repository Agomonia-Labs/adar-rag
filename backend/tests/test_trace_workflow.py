from datetime import datetime, timedelta, timezone

from routes.traces import _public_evaluation, build_trace_workflow, build_user_trace_response


def test_build_trace_workflow_explains_retrieval_rerank_and_response():
    started = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
    trace = {
        "trace_id": "trace-1",
        "request_type": "chat",
        "status": "success",
        "input_text_preview": "What are the lease risks?",
        "started_at": started,
        "ended_at": started + timedelta(seconds=2),
    }
    spans = [
        {"span_id": "retrieve", "name": "hybrid_retrieval", "status": "success", "started_at": started, "ended_at": started + timedelta(milliseconds=300), "duration_ms": 300, "metadata": {}},
        {"span_id": "rerank", "name": "gemini_rerank", "status": "success", "started_at": started + timedelta(milliseconds=300), "ended_at": started + timedelta(milliseconds=500), "duration_ms": 200, "metadata": {}},
        {"span_id": "generate", "name": "llm_generate", "status": "success", "started_at": started + timedelta(milliseconds=500), "ended_at": started + timedelta(seconds=2), "duration_ms": 1500, "metadata": {}},
    ]
    events = [
        {"span_id": "retrieve", "operation": "hybrid_retrieval", "provider": "postgres", "tool_request_json": {"limit": 20}, "tool_response_json": {"candidates": [{"id": 1}, {"id": 2}, {"id": 3}]}},
        {"span_id": "rerank", "operation": "rerank", "provider": "gemini", "tool_request_json": {"candidates": [{"id": 1}, {"id": 2}, {"id": 3}]}, "tool_response_json": {"ranked": [{"id": 2}, {"id": 1}]}},
        {"span_id": "generate", "operation": "chat_generate", "provider": "gemini", "model": "chat", "tool_request_json": {}, "tool_response_json": {}, "llm_response": "The main risk is the renewal clause."},
    ]

    workflow = build_trace_workflow(trace, spans, events)

    assert workflow["summary"]["duration_ms"] == 2000
    assert workflow["summary"]["candidate_chunk_count"] == 3
    assert workflow["summary"]["selected_chunk_count"] == 2
    assert workflow["summary"]["llm_call_count"] == 1
    assert workflow["nodes"][-1]["type"] == "response"
    assert "retrieved 3 candidate chunks" in workflow["story"]


def test_build_trace_workflow_handles_old_trace_without_spans():
    workflow = build_trace_workflow(
        {"trace_id": "old", "status": "running", "input_text_preview": "Hello"},
        [],
        [],
    )

    assert workflow["summary"]["step_count"] == 1
    assert workflow["nodes"][0]["type"] == "user_input"
    assert workflow["edges"] == []


def test_user_trace_response_hides_internal_prompts_and_tool_payloads():
    started = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
    trace = {
        "trace_id": "trace-user",
        "request_type": "chat",
        "user_id": "user-1",
        "workspace_id": None,
        "status": "success",
        "input_text_preview": "Summarize my document",
        "input_text_hash": "private-hash",
        "client_info": {"ip": "private"},
        "started_at": started,
        "ended_at": started + timedelta(seconds=1),
    }
    spans = [{
        "span_id": "generate", "trace_id": "trace-user", "name": "llm_generate",
        "status": "success", "started_at": started, "ended_at": started + timedelta(seconds=1),
        "duration_ms": 1000, "metadata": {"internal_path": "/secret"}, "error": {},
    }]
    events = [{
        "span_id": "generate", "operation": "chat_generate", "provider": "gemini", "model": "chat",
        "system_prompt": "internal system instructions", "user_prompt": "Summarize my document",
        "tool_request_json": {"sql": "SELECT private"}, "tool_response_json": {"chunks": ["private text"]},
        "llm_response": "Here is the summary.", "input_tokens": 10, "output_tokens": 5,
    }]

    result = build_user_trace_response(trace, spans, events)

    assert result["visibility"] == "requester"
    assert "input_text_hash" not in result["trace"]
    assert "client_info" not in result["trace"]
    assert result["spans"][0]["metadata"] == {}
    assert result["llm_events"][0]["system_prompt"] is None
    assert result["llm_events"][0]["tool_request_json"] == {}
    assert result["llm_events"][0]["tool_response_json"] == {}
    assert result["llm_events"][0]["llm_response"] == "Here is the summary."
    workflow_event = result["workflow"]["nodes"][1]["details"]["events"][0]
    assert workflow_event["system_prompt"] is None
    assert workflow_event["tool_request"] == {}


def test_trace_workflow_accepts_legacy_non_object_tool_payloads():
    started = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
    workflow = build_trace_workflow(
        {
            "trace_id": "legacy-json",
            "request_type": "chat",
            "status": "success",
            "input_text_preview": "Legacy request",
            "started_at": started,
            "ended_at": started + timedelta(milliseconds=400),
        },
        [{
            "span_id": "legacy-retrieval", "name": "hybrid_retrieval", "status": "success",
            "started_at": started, "ended_at": started + timedelta(milliseconds=400),
            "duration_ms": 400, "metadata": ["legacy", "metadata"],
        }],
        [{
            "span_id": "legacy-retrieval", "operation": "hybrid_retrieval", "provider": "postgres",
            "tool_request_json": ["unexpected", "list"],
            "tool_response_json": "unexpected string",
        }],
    )

    assert workflow["summary"]["candidate_chunk_count"] == 0
    assert workflow["nodes"][1]["service"] == "docintel-backend"


def test_requester_evaluation_projection_excludes_reviewer_identity():
    result = _public_evaluation({
        "evaluation_type": "agent_workflow",
        "evaluation_source": "healthcare",
        "score": 0.91,
        "outcome": "ready",
        "reviewer_id": "private-reviewer",
        "metadata": {"metric_count": 5},
        "created_at": "2026-08-26T12:00:00Z",
    })

    assert result["score"] == 0.91
    assert result["metadata"] == {"metric_count": 5}
    assert "reviewer_id" not in result
