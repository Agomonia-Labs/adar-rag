from __future__ import annotations

import contextlib
import functools
import hashlib
import inspect
import logging
import os
import re
from collections.abc import Iterator
from typing import Any

log = logging.getLogger("docintel.telemetry")
_configured = False
_provider = None

try:
    from opentelemetry import propagate, trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    OTEL_AVAILABLE = True
except ImportError:
    propagate = trace = None
    OTEL_AVAILABLE = False


def telemetry_enabled() -> bool:
    return OTEL_AVAILABLE and os.getenv("OTEL_ENABLED", "false").lower() == "true"


def configure_telemetry(*, default_service_name: str, default_service_version: str) -> bool:
    global _configured, _provider
    if _configured:
        return telemetry_enabled()
    _configured = True
    if not telemetry_enabled():
        if os.getenv("OTEL_ENABLED", "false").lower() == "true" and not OTEL_AVAILABLE:
            log.warning("OTEL_ENABLED is true but OpenTelemetry packages are unavailable")
        return False
    try:
        resource = Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", default_service_name),
            "service.version": os.getenv("OTEL_SERVICE_VERSION", default_service_version),
            "deployment.environment.name": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "development"),
            "cloud.provider": os.getenv("OTEL_CLOUD_PROVIDER", "gcp"),
            "cloud.region": os.getenv("REGION", os.getenv("GOOGLE_CLOUD_REGION", "unknown")),
        })
        _provider = TracerProvider(resource=resource)
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(_provider)
        HTTPXClientInstrumentor().instrument()
        log.info("OpenTelemetry enabled for %s", resource.attributes.get("service.name"))
        return True
    except Exception:
        log.exception("OpenTelemetry initialization failed; service will continue without OTEL export")
        return False


def shutdown_telemetry() -> None:
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=5000)
        _provider.shutdown()
    except Exception:
        log.exception("OpenTelemetry shutdown failed")


def safe_attributes(values: dict[str, Any] | None, *, prefix: str = "docintel") -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in (values or {}).items():
        name = str(key) if "." in str(key) else f"{prefix}.{key}"
        if value is None:
            continue
        lowered = name.lower()
        if any(token in lowered for token in ("authorization", "password", "secret", "api_key", "apikey", "access_token", "refresh_token")):
            output[name] = "[REDACTED]"
            continue
        if any(token in lowered for token in ("prompt", "question", "content", "response", "document_text", "chunk_text")) and not _capture_content():
            rendered = str(value)
            output[f"{name}.sha256"] = hashlib.sha256(rendered.encode("utf-8", errors="ignore")).hexdigest()
            output[f"{name}.length"] = len(rendered)
            continue
        if isinstance(value, (str, bool, int, float)):
            output[name] = _bounded(_redact_scalar(value))
        elif isinstance(value, (list, tuple, set)):
            scalar = [_bounded(item) for item in value if isinstance(item, (str, bool, int, float))]
            if scalar:
                output[name] = scalar[:50]
        else:
            output[f"{name}.type"] = type(value).__name__
    return output


def _bounded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    limit = int(os.getenv("OTEL_ATTRIBUTE_VALUE_CHARS", "512"))
    return value if len(value) <= limit else value[:limit] + "..."


def _capture_content() -> bool:
    return os.getenv("OTEL_CAPTURE_CONTENT", "false").lower() == "true"


def _redact_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
    return re.sub(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", r"***@\2", value)


@contextlib.contextmanager
def traced_span(name: str, *, attributes: dict[str, Any] | None = None, kind=None) -> Iterator[Any]:
    if not telemetry_enabled():
        yield None
        return
    tracer = trace.get_tracer("adar.docintel")
    span_kind = kind or trace.SpanKind.INTERNAL
    try:
        manager = tracer.start_as_current_span(name, kind=span_kind, attributes=safe_attributes(attributes))
    except Exception:
        log.exception("OpenTelemetry span setup failed: %s", name)
        yield None
        return
    with manager as current:
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)[:512]))
            raise


def otel_span(name: str | None = None, *, attributes=None, input_mapper=None, output_mapper=None):
    def decorate(func):
        span_name = name or f"{func.__module__}.{func.__name__}"
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with traced_span(span_name, attributes=_mapped_input(attributes, input_mapper, args, kwargs)) as current:
                    result = await func(*args, **kwargs)
                    _set_output(current, output_mapper, result)
                    return result
            return async_wrapper
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with traced_span(span_name, attributes=_mapped_input(attributes, input_mapper, args, kwargs)) as current:
                result = func(*args, **kwargs)
                _set_output(current, output_mapper, result)
                return result
        return sync_wrapper
    return decorate


def _mapped_input(static, mapper, args, kwargs) -> dict[str, Any]:
    values = dict(static or {})
    if mapper:
        try:
            values.update(mapper(*args, **kwargs) or {})
        except Exception:
            log.exception("OTEL input mapper failed")
    return values


def _set_output(current, mapper, result) -> None:
    if current is None or mapper is None:
        return
    try:
        for key, value in safe_attributes(mapper(result) or {}).items():
            current.set_attribute(key, value)
    except Exception:
        log.exception("OTEL output mapper failed")


def current_otel_ids() -> tuple[str | None, str | None]:
    if not telemetry_enabled():
        return None, None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def current_trace_id() -> str | None:
    return current_otel_ids()[0]


def enrich_current_span(attributes: dict[str, Any] | None = None, *, event: str | None = None) -> None:
    if not telemetry_enabled():
        return
    try:
        current = trace.get_current_span()
        for key, value in safe_attributes(attributes).items():
            current.set_attribute(key, value)
        if event:
            current.add_event(event, safe_attributes(attributes))
    except Exception:
        log.exception("Could not enrich current OTEL span")


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    carrier = dict(headers or {})
    if telemetry_enabled():
        try:
            propagate.inject(carrier)
        except Exception:
            log.exception("Could not inject OTEL propagation headers")
    return carrier
