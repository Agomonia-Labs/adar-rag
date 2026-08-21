import pytest

from docintel_mcp.auth import require_capability
from docintel_mcp.errors import DocIntelMcpError
from docintel_mcp.runtime import request_trace_id


def test_disabled_capability_is_rejected():
    with pytest.raises(DocIntelMcpError) as error:
        require_capability(frozenset({"documents:read"}), "knowledge:query")
    assert error.value.code == "capability_disabled"


def test_scope_not_granted_to_token_is_rejected():
    with pytest.raises(DocIntelMcpError) as error:
        require_capability(
            frozenset({"documents:read", "knowledge:query"}),
            "knowledge:query",
            {"documents:read"},
        )
    assert error.value.code == "insufficient_scope"


def test_trace_id_uses_mcp_request_id_without_transport_headers():
    class ContextWithoutHeaders:
        request_id = "request-123"

    assert request_trace_id(ContextWithoutHeaders()) == "request-123"
