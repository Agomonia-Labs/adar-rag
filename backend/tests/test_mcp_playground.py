import pytest
from fastapi import HTTPException

from services.mcp_playground.command_parser import apply_pipelines, format_resource_data, parse_command
from services.mcp_playground.command_policy import validate_request
from services.mcp_playground.example_catalog import example_catalog


@pytest.fixture
def workspace_tool_response():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "structuredContent": {
                "result": {"workspaces": [{"id": "ws-1", "name": "Demo"}]}
            }
        },
    }


def test_parses_helper_command_and_formats_tool_data(workspace_tool_response):
    parsed = parse_command("mcp_tool list_workspaces '{}' | tool_data | jq '.workspaces'")

    assert parsed.request["method"] == "tools/call"
    assert parsed.request["params"]["name"] == "list_workspaces"
    assert apply_pipelines(workspace_tool_response, parsed.pipelines) == [
        {"id": "ws-1", "name": "Demo"}
    ]


def test_parses_pasted_shell_line_continuations():
    parsed = parse_command("""mcp_tool get_workflow_schema \\
      '{"workflow":"healthcare_prior_auth"}' \\
      | tool_data | jq""")

    assert parsed.request["params"] == {
        "name": "get_workflow_schema",
        "arguments": {"workflow": "healthcare_prior_auth"},
    }


def test_explains_that_browser_playground_does_not_execute_shell_substitution():
    command = "mcp_tool validate_workflow_inputs \"$(jq -cn --arg id '$DOCUMENT_ID_1' '{}')\""
    with pytest.raises(HTTPException) as exc:
        parse_command(command)
    assert "does not execute shell substitutions" in str(exc.value.detail)


@pytest.mark.parametrize("command", [
    "bash -lc 'whoami'",
    "mcp_tool list_workspaces '{}' | cat",
    "mcp_request '[]'",
])
def test_rejects_shell_and_unsupported_syntax(command):
    with pytest.raises(HTTPException) as exc:
        parse_command(command)
    assert exc.value.status_code == 400


def test_destructive_tool_requires_confirmation():
    request = parse_command("mcp_tool delete_document '{\"document_id\":\"doc-1\"}'").request

    with pytest.raises(HTTPException) as exc:
        validate_request(request, confirm=False)
    assert exc.value.status_code == 409
    validate_request(request, confirm=True)


def test_rejects_non_playground_json_rpc_method():
    request = {"jsonrpc": "2.0", "id": 1, "method": "completion/complete"}
    with pytest.raises(HTTPException) as exc:
        validate_request(request, confirm=False)
    assert exc.value.status_code == 400


def test_catalog_covers_registered_tools_and_resources():
    catalog = example_catalog()
    tools = {item["tool"] for item in catalog if item["category"] not in {"Discovery", "Resources"}}
    expected = {
        "list_vertical_workflows", "start_vertical_workflow", "get_vertical_run", "list_vertical_runs",
        "save_vertical_review", "approve_vertical_run", "generate_vertical_packet", "list_workspaces",
        "list_documents", "get_document", "create_document_upload", "complete_document_upload",
        "get_ingestion_status", "get_document_chunks", "embed_document", "delete_document",
        "create_video_upload", "complete_video_upload", "list_videos", "process_video", "get_video_status",
        "get_video_timeline", "get_video_transcript", "get_video_frames", "get_video_frame_url",
        "search_video", "search_knowledgebase", "summarize_document", "summarize_documents",
        "compare_documents", "create_chat_session", "list_chat_sessions", "get_chat_session",
        "update_chat_session", "delete_chat_session", "ask",
        "create_batch_upload", "complete_batch_upload", "start_batch_embedding", "start_batch_classification",
        "start_workspace_summary", "list_batch_jobs", "get_batch_status", "get_batch_results",
        "retry_batch_failures", "cancel_batch_job",
    }
    assert tools == expected
    assert sum(item["category"] == "Resources" for item in catalog) == 12


def test_formats_json_resource_text_as_structured_data():
    response = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "contents": [{
                "uri": "docintel://documents/doc-1",
                "mimeType": "application/json",
                "text": '{"id":"doc-1","filename":"lease.pdf","status":"embedded"}',
            }]
        },
    }

    assert format_resource_data(response) == {
        "id": "doc-1",
        "filename": "lease.pdf",
        "status": "embedded",
    }


def test_preserves_plain_text_resource_with_metadata():
    response = {
        "result": {
            "contents": [{
                "uri": "docintel://documents/doc-1",
                "mimeType": "text/plain",
                "text": "Plain resource content",
            }]
        }
    }

    assert format_resource_data(response) == [{
        "uri": "docintel://documents/doc-1",
        "mimeType": "text/plain",
        "data": "Plain resource content",
    }]
