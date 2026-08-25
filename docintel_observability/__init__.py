"""Shared OpenTelemetry utilities for DocIntel services."""

from .telemetry import (
    configure_telemetry,
    current_otel_ids,
    current_trace_id,
    enrich_current_span,
    inject_trace_headers,
    otel_span,
    safe_attributes,
    shutdown_telemetry,
    telemetry_enabled,
    traced_span,
)

__all__ = [
    "configure_telemetry", "current_otel_ids", "current_trace_id",
    "enrich_current_span", "inject_trace_headers", "otel_span",
    "safe_attributes", "shutdown_telemetry", "telemetry_enabled", "traced_span",
]
