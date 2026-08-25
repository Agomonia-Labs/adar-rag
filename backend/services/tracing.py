from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import time
import functools
import inspect
from typing import Any
from uuid import uuid4

from database.connection import get_pool
from services.telemetry import current_otel_ids, enrich_current_span, traced_span

TRACE_FULL_CONTENT = os.getenv("TRACE_FULL_CONTENT", "false").lower() == "true"
TRACE_PREVIEW_CHARS = int(os.getenv("TRACE_PREVIEW_CHARS", "600"))
TRACE_FIELD_CHARS = int(os.getenv("TRACE_FIELD_CHARS", "6000"))

current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)
log = logging.getLogger("docintel.tracing")

_SECRET_PATTERNS = [
    re.compile(r"(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", re.I),
    re.compile(r"(api[_-]?key\s*[:=]\s*)[^\s,;]+", re.I),
    re.compile(r"(token\s*[:=]\s*)[^\s,;]+", re.I),
    re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"),
]


def new_trace_id() -> str:
    return f"trc_{uuid4().hex}"


def new_span_id() -> str:
    return f"spn_{uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


def hash_text(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def redact_text(value: Any, limit: int = TRACE_FIELD_CHARS) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if not TRACE_FULL_CONTENT and len(text) > limit:
        text = text[:limit] + f"... [truncated {len(text) - limit} chars]"
    for pattern in _SECRET_PATTERNS:
        if "@" in pattern.pattern:
            text = pattern.sub(r"\1***@\2", text)
        else:
            text = pattern.sub(r"\1[REDACTED]", text)
    return text


def safe_json(value: Any, limit: int = TRACE_FIELD_CHARS) -> dict | list:
    if value is None:
        return {}
    redacted = redact_text(value, limit)
    try:
        parsed = json.loads(redacted) if isinstance(redacted, str) else redacted
    except Exception:
        parsed = {"value": redacted}
    return parsed if isinstance(parsed, (dict, list)) else {"value": parsed}


def trace_span(
    name: str | None = None,
    *,
    metadata: dict[str, Any] | None = None,
    input_mapper=None,
    output_mapper=None,
):
    """Instrument an async operation in OTEL and the legacy Admin UI projection."""
    def decorate(func):
        if not inspect.iscoroutinefunction(func):
            raise TypeError("trace_span supports async functions; use services.telemetry.otel_span for synchronous functions")
        span_name = name or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            values = dict(metadata or {})
            if input_mapper:
                try:
                    values.update(input_mapper(*args, **kwargs) or {})
                except Exception:
                    log.exception("Trace input mapper failed for %s", span_name)
            async with span(span_name, metadata=values):
                result = await func(*args, **kwargs)
                if output_mapper:
                    try:
                        enrich_current_span(output_mapper(result) or {}, event="docintel.operation.output")
                    except Exception:
                        log.exception("Trace output mapper failed for %s", span_name)
                return result
        return wrapper
    return decorate


async def start_trace(
    request_type: str,
    *,
    trace_id: str | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
    session_id: str | None = None,
    input_text: str | None = None,
    client_info: dict | None = None,
    metadata: dict | None = None,
) -> str:
    trace_id = trace_id or new_trace_id()
    current_trace_id.set(trace_id)
    preview = redact_text(input_text, TRACE_PREVIEW_CHARS)
    enrich_current_span({
        "docintel.trace.id": trace_id,
        "docintel.request.type": request_type,
        "enduser.id": user_id,
        "docintel.workspace.id": workspace_id,
        "docintel.session.id": session_id,
        **(metadata or {}),
    })
    try:
        async with get_pool().acquire() as conn:
            await conn.execute(
            """INSERT INTO trace_flows
               (trace_id, request_type, user_id, workspace_id, session_id,
                input_text_hash, input_text_preview, client_info, metadata)
               VALUES ($1,$2,$3::uuid,$4::uuid,$5::uuid,$6,$7,$8::jsonb,$9::jsonb)
               ON CONFLICT (trace_id) DO UPDATE SET
                 user_id = COALESCE(trace_flows.user_id, EXCLUDED.user_id),
                 workspace_id = COALESCE(trace_flows.workspace_id, EXCLUDED.workspace_id),
                 session_id = COALESCE(trace_flows.session_id, EXCLUDED.session_id),
                 input_text_hash = COALESCE(trace_flows.input_text_hash, EXCLUDED.input_text_hash),
                 input_text_preview = COALESCE(trace_flows.input_text_preview, EXCLUDED.input_text_preview),
                 client_info = trace_flows.client_info || EXCLUDED.client_info,
                 metadata = trace_flows.metadata || EXCLUDED.metadata""",
            trace_id,
            request_type,
            user_id,
            workspace_id,
            session_id,
            hash_text(input_text),
            preview,
            json.dumps(safe_json(client_info or {}), ensure_ascii=False),
            json.dumps(safe_json(metadata or {}), ensure_ascii=False),
            )
    except Exception:
        log.exception("Legacy trace projection failed to start trace %s", trace_id)
    return trace_id


async def finish_trace(trace_id: str, status: str = "success", error_message: str | None = None) -> None:
    enrich_current_span({"docintel.trace.status": status}, event="docintel.trace.finished")
    try:
        async with get_pool().acquire() as conn:
            await conn.execute(
            """UPDATE trace_flows
               SET status=$2, error_message=$3, ended_at=NOW()
               WHERE trace_id=$1""",
            trace_id,
            status,
            redact_text(error_message, 1200) if error_message else None,
            )
    except Exception:
        log.exception("Legacy trace projection failed to finish trace %s", trace_id)


@contextlib.asynccontextmanager
async def span(name: str, *, trace_id: str | None = None, parent_span_id: str | None = None, metadata: dict | None = None):
    trace_id = trace_id or current_trace_id.get()
    if not trace_id:
        yield None
        return
    span_id = new_span_id()
    parent_span_id = parent_span_id or current_span_id.get()
    started = time.perf_counter()
    with traced_span(name, attributes={"docintel.trace.id": trace_id, **(metadata or {})}):
        otel_trace_id, otel_span_id = current_otel_ids()
        projection_metadata = {
            **(metadata or {}),
            "otel_trace_id": otel_trace_id,
            "otel_span_id": otel_span_id,
        }
        try:
            async with get_pool().acquire() as conn:
                await conn.execute(
                    """INSERT INTO trace_spans (span_id, trace_id, parent_span_id, name, metadata)
                       VALUES ($1,$2,$3,$4,$5::jsonb)""",
                    span_id, trace_id, parent_span_id, name,
                    json.dumps(safe_json(projection_metadata), ensure_ascii=False),
                )
        except Exception:
            log.exception("Legacy span projection failed to start %s", name)
        span_token = current_span_id.set(span_id)
        try:
            yield span_id
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            try:
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        """UPDATE trace_spans
                           SET status='error', error=$2::jsonb, ended_at=NOW(), duration_ms=$3
                           WHERE span_id=$1""",
                        span_id,
                        json.dumps(safe_json({"type": type(exc).__name__, "message": str(exc)}), ensure_ascii=False),
                        duration,
                    )
            except Exception:
                log.exception("Legacy span projection failed to record error for %s", name)
            raise
        else:
            duration = int((time.perf_counter() - started) * 1000)
            try:
                async with get_pool().acquire() as conn:
                    await conn.execute(
                        """UPDATE trace_spans
                           SET status='success', ended_at=NOW(), duration_ms=$2
                           WHERE span_id=$1""",
                        span_id, duration,
                    )
            except Exception:
                log.exception("Legacy span projection failed to finish %s", name)
        finally:
            current_span_id.reset(span_token)


async def record_llm_event(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    provider: str,
    model: str | None,
    operation: str,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    tool_request: Any = None,
    tool_response: Any = None,
    llm_response: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    finish_reason: str | None = None,
    error: str | None = None,
) -> None:
    trace_id = trace_id or current_trace_id.get()
    if not trace_id:
        return
    enrich_current_span({
        "gen_ai.system": provider,
        "gen_ai.request.model": model,
        "gen_ai.operation.name": operation,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "docintel.llm.finish_reason": finish_reason,
        "docintel.llm.latency_ms": latency_ms,
        "docintel.llm.error": bool(error),
    }, event="gen_ai.operation.completed")
    try:
        async with get_pool().acquire() as conn:
            await conn.execute(
            """INSERT INTO trace_llm_events
               (event_id, trace_id, span_id, provider, model, operation,
                system_prompt, user_prompt, tool_request_json, tool_response_json,
                llm_response, input_tokens, output_tokens, latency_ms, finish_reason,
                redaction_status, error)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,$12,$13,$14,$15,$16,$17)""",
            new_event_id(),
            trace_id,
            span_id,
            provider,
            model,
            operation,
            redact_text(system_prompt),
            redact_text(user_prompt),
            json.dumps(safe_json(tool_request or {}), ensure_ascii=False),
            json.dumps(safe_json(tool_response or {}), ensure_ascii=False),
            redact_text(llm_response),
            input_tokens,
            output_tokens,
            latency_ms,
            finish_reason,
            "full" if TRACE_FULL_CONTENT else "redacted",
            redact_text(error, 1200) if error else None,
            )
    except Exception:
        log.exception("Legacy LLM event projection failed for trace %s", trace_id)
