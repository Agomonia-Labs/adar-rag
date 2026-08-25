import asyncio

from services.telemetry import otel_span, safe_attributes
from services.tracing import trace_span


def test_safe_attributes_redacts_secrets_and_hashes_content(monkeypatch):
    monkeypatch.setenv("OTEL_CAPTURE_CONTENT", "false")
    values = safe_attributes({
        "access_token": "secret-token",
        "question": "What is in this document?",
        "workspace_id": "workspace-1",
        "email": "person@example.com",
    })

    assert values["docintel.access_token"] == "[REDACTED]"
    assert "docintel.question" not in values
    assert values["docintel.question.length"] == 25
    assert values["docintel.workspace_id"] == "workspace-1"
    assert values["docintel.email"] == "***@example.com"


def test_decorator_is_transparent_when_otel_is_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "false")

    @otel_span("test.sync")
    def sync_value(value):
        return value + 1

    @otel_span("test.async")
    async def async_value(value):
        return value + 2

    assert sync_value(2) == 3
    assert asyncio.run(async_value(2)) == 4


def test_projection_decorator_rejects_sync_functions():
    try:
        @trace_span("test.invalid")
        def sync_function():
            return None
    except TypeError as exc:
        assert "async functions" in str(exc)
    else:
        raise AssertionError("trace_span must reject synchronous projection functions")
