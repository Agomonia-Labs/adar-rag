"""Backend adapter for the shared DocIntel OpenTelemetry package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import docintel_observability
except ImportError:  # Supports `cd backend && uvicorn main:app` from a source checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import docintel_observability

from docintel_observability import (  # re-exported for stable backend imports
    current_otel_ids,
    enrich_current_span,
    inject_trace_headers,
    otel_span,
    safe_attributes,
    shutdown_telemetry,
    telemetry_enabled,
    traced_span,
)
from docintel_observability import configure_telemetry as _configure_shared


def configure_telemetry(app=None, *, default_service_name: str = "docintel-backend") -> bool:
    enabled = _configure_shared(
        default_service_name=default_service_name,
        default_service_version="4.0.0",
    )
    if app is not None and enabled and not getattr(app.state, "otel_instrumented", False):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=os.getenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "health,docs,openapi.json"),
        )
        app.state.otel_instrumented = True
    return enabled


__all__ = [
    "configure_telemetry", "current_otel_ids", "enrich_current_span",
    "inject_trace_headers", "otel_span", "safe_attributes",
    "shutdown_telemetry", "telemetry_enabled", "traced_span",
]
