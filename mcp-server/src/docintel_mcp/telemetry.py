"""MCP adapter for the shared DocIntel OpenTelemetry package."""

import sys
from pathlib import Path

try:
    import docintel_observability
except ImportError:  # Supports running the MCP package directly from a source checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import docintel_observability

from docintel_observability import current_trace_id, traced_span
from docintel_observability import configure_telemetry as _configure_shared


def configure() -> bool:
    return _configure_shared(
        default_service_name="docintel-mcp",
        default_service_version="0.1.0",
    )


__all__ = ["configure", "current_trace_id", "traced_span"]
