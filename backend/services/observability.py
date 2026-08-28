from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from services.notifications import send_observability_alert

log = logging.getLogger("docintel.observability")
BUCKET_MINUTES = 5
_LOCK_ID = 2026082601


def evaluate_threshold(value: float | None, target: float, comparator: str) -> dict[str, Any]:
    if value is None:
        return {"compliant": None, "error_budget_remaining": None, "burn_rate": None}
    compliant = value >= target if comparator == "gte" else value <= target
    if comparator == "gte" and 0 <= target <= 1:
        allowed_error = max(1e-9, 1 - target)
        observed_error = max(0.0, 1 - value)
        burn_rate = observed_error / allowed_error
        remaining = max(0.0, min(1.0, (allowed_error - observed_error) / allowed_error))
    else:
        burn_rate = max(0.0, value / target) if target else None
        remaining = max(0.0, min(1.0, (target - value) / target)) if target else None
    return {
        "compliant": compliant,
        "error_budget_remaining": remaining,
        "burn_rate": burn_rate,
    }


def bounded_dimensions(dimensions: dict[str, Any] | None) -> dict[str, str]:
    allowed = {"request_type", "workflow_type", "service", "operation", "provider", "model", "status", "environment"}
    result: dict[str, str] = {}
    for key, value in (dimensions or {}).items():
        if key in allowed and value is not None:
            result[key] = str(value)[:80]
    return result


async def run_observability_cycle(db, *, send_notifications: bool = True) -> dict[str, Any]:
    locked = await db.fetchval("SELECT pg_try_advisory_lock($1)", _LOCK_ID)
    if not locked:
        return {"status": "skipped", "reason": "another observability worker owns the lock"}
    started = datetime.now(timezone.utc)
    try:
        bucket_end = started.replace(second=0, microsecond=0)
        minute = bucket_end.minute - (bucket_end.minute % BUCKET_MINUTES)
        bucket_end = bucket_end.replace(minute=minute)
        bucket_start = bucket_end - timedelta(minutes=BUCKET_MINUTES)
        metrics = await aggregate_window(db, bucket_start, bucket_end)
        await persist_rollups(db, bucket_start, BUCKET_MINUTES, metrics)
        slo_results, new_alerts = await evaluate_slos(db, bucket_end)
        await apply_retention(db)
        await db.execute(
            """INSERT INTO observability_checkpoints(job_name,last_run_at,last_status,last_error)
               VALUES ('aggregate_slos',$1,'success','')
               ON CONFLICT(job_name) DO UPDATE SET last_run_at=$1,last_status='success',last_error='',updated_at=NOW()""",
            started,
        )
        if send_notifications:
            await _notify_admins(db, new_alerts)
        return {
            "status": "success", "bucket_start": bucket_start, "bucket_end": bucket_end,
            "metric_count": len(metrics), "slo_count": len(slo_results), "new_alert_count": len(new_alerts),
        }
    except Exception as exc:
        log.exception("Observability cycle failed")
        await db.execute(
            """INSERT INTO observability_checkpoints(job_name,last_run_at,last_status,last_error)
               VALUES ('aggregate_slos',$1,'error',$2)
               ON CONFLICT(job_name) DO UPDATE SET last_run_at=$1,last_status='error',last_error=$2,updated_at=NOW()""",
            started, str(exc)[:2000],
        )
        raise
    finally:
        await db.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)


async def aggregate_window(db, start: datetime, end: datetime) -> list[dict[str, Any]]:
    request = await db.fetchrow(
        """SELECT COUNT(*)::int AS samples,
                  COUNT(*) FILTER (WHERE status='success')::int AS successes,
                  AVG(EXTRACT(EPOCH FROM (ended_at-started_at))*1000) AS average_ms,
                  MIN(EXTRACT(EPOCH FROM (ended_at-started_at))*1000) AS minimum_ms,
                  MAX(EXTRACT(EPOCH FROM (ended_at-started_at))*1000) AS maximum_ms,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at-started_at))*1000) AS p50,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at-started_at))*1000) AS p95,
                  percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (ended_at-started_at))*1000) AS p99
           FROM trace_flows WHERE started_at >= $1 AND started_at < $2 AND ended_at IS NOT NULL""",
        start, end,
    )
    retrieval = await db.fetchrow(
        """SELECT COUNT(DISTINCT s.trace_id)::int AS samples,
                  COUNT(DISTINCT s.trace_id) FILTER (
                    WHERE jsonb_typeof(e.tool_response_json->'candidates')='array'
                      AND jsonb_array_length(e.tool_response_json->'candidates') > 0
                  )::int AS with_evidence
           FROM trace_spans s
           JOIN trace_flows f ON f.trace_id=s.trace_id
           LEFT JOIN trace_llm_events e ON e.span_id=s.span_id
           WHERE s.name='hybrid_retrieval' AND f.started_at >= $1 AND f.started_at < $2""",
        start, end,
    )
    tools = await db.fetchrow(
        """SELECT COUNT(*)::int AS samples,
                  COUNT(*) FILTER (WHERE COALESCE(error,'')='' AND COALESCE(finish_reason,'') NOT IN ('error','failed'))::int AS successes
           FROM trace_llm_events
           WHERE created_at >= $1 AND created_at < $2
             AND (operation ILIKE '%tool%' OR operation ILIKE '%mcp%')""",
        start, end,
    )
    quality = await db.fetchrow(
        """SELECT COUNT(*)::int AS samples, AVG(score) AS average_score
           FROM trace_evaluation_correlations WHERE created_at >= $1 AND created_at < $2 AND score IS NOT NULL""",
        start, end,
    )
    samples = int(request["samples"] or 0)
    average_ms = _float(request["average_ms"])
    metrics = [
        _metric("request_success_rate", samples, _ratio(request["successes"], samples)),
        _metric("request_latency_ms", samples, average_ms,
                value_sum=average_ms * samples if average_ms is not None else None,
                value_min=_float(request["minimum_ms"]), value_max=_float(request["maximum_ms"]),
                p50=_float(request["p50"]), p95=_float(request["p95"]), p99=_float(request["p99"])),
        _metric("retrieval_evidence_rate", int(retrieval["samples"] or 0), _ratio(retrieval["with_evidence"], retrieval["samples"])),
        _metric("tool_success_rate", int(tools["samples"] or 0), _ratio(tools["successes"], tools["samples"])),
        _metric("evaluation_quality_score", int(quality["samples"] or 0), _float(quality["average_score"])),
    ]
    return metrics


def _metric(name: str, samples: int, value: float | None, **extra) -> dict[str, Any]:
    return {"metric_name": name, "dimension_key": "all", "dimensions": {}, "sample_count": samples,
            "metric_value": value, "value_sum": extra.get("value_sum"), "value_min": extra.get("value_min"),
            "value_max": extra.get("value_max"), "p50": extra.get("p50"), "p95": extra.get("p95"), "p99": extra.get("p99")}


async def persist_rollups(db, bucket_start: datetime, bucket_minutes: int, metrics: list[dict[str, Any]]) -> None:
    for metric in metrics:
        await db.execute(
            """INSERT INTO observability_metric_rollups
               (bucket_start,bucket_minutes,metric_name,dimension_key,dimensions,sample_count,metric_value,
                value_sum,value_min,value_max,p50,p95,p99)
               VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)
               ON CONFLICT(bucket_start,bucket_minutes,metric_name,dimension_key) DO UPDATE SET
                 dimensions=EXCLUDED.dimensions,sample_count=EXCLUDED.sample_count,metric_value=EXCLUDED.metric_value,
                 value_sum=EXCLUDED.value_sum,value_min=EXCLUDED.value_min,value_max=EXCLUDED.value_max,
                 p50=EXCLUDED.p50,p95=EXCLUDED.p95,p99=EXCLUDED.p99,updated_at=NOW()""",
            bucket_start, bucket_minutes, metric["metric_name"], metric["dimension_key"],
            json.dumps(bounded_dimensions(metric["dimensions"])), metric["sample_count"], metric["metric_value"],
            metric["value_sum"], metric["value_min"], metric["value_max"], metric["p50"], metric["p95"], metric["p99"],
        )


async def evaluate_slos(db, window_end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    definitions = await db.fetch("SELECT * FROM observability_slo_definitions WHERE enabled=TRUE ORDER BY name")
    results: list[dict[str, Any]] = []
    new_alerts: list[dict[str, Any]] = []
    for definition in definitions:
        start = window_end - timedelta(minutes=definition["window_minutes"])
        if definition["metric_name"] == "request_latency_ms":
            row = await db.fetchrow(
                """SELECT COUNT(*)::int AS samples,
                          percentile_cont(0.95) WITHIN GROUP (
                            ORDER BY EXTRACT(EPOCH FROM (ended_at-started_at))*1000
                          ) AS value
                   FROM trace_flows WHERE started_at >= $1 AND started_at < $2 AND ended_at IS NOT NULL""",
                start, window_end,
            )
        else:
            row = await db.fetchrow(
                """SELECT SUM(sample_count)::int AS samples,
                          SUM(metric_value*sample_count)/NULLIF(SUM(sample_count),0) AS value
                   FROM observability_metric_rollups WHERE metric_name=$1 AND dimension_key=$2 AND bucket_start >= $3 AND bucket_start < $4""",
                definition["metric_name"], definition["dimension_key"], start, window_end,
            )
        samples = int(row["samples"] or 0)
        value = _float(row["value"])
        outcome = evaluate_threshold(value, float(definition["target"]), definition["comparator"])
        compliant = outcome["compliant"] if samples >= definition["minimum_request_count"] else None
        result = await db.fetchrow(
            """INSERT INTO observability_slo_results
               (slo_id,window_start,window_end,measured_value,target_value,request_count,compliant,error_budget_remaining,burn_rate)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
            definition["id"], start, window_end, value, definition["target"], samples, compliant,
            outcome["error_budget_remaining"] if compliant is not None else None,
            outcome["burn_rate"] if compliant is not None else None,
        )
        results.append(dict(result))
        if compliant is False:
            alert, created = await _upsert_alert(db, definition, value)
            if created:
                new_alerts.append(alert)
        elif compliant is True:
            await db.execute(
                "UPDATE observability_alerts SET status='resolved',resolved_at=NOW(),last_seen_at=NOW() WHERE slo_id=$1 AND status IN ('open','acknowledged')",
                definition["id"],
            )
    return results, new_alerts


async def _upsert_alert(db, definition, value: float | None) -> tuple[dict[str, Any], bool]:
    existing = await db.fetchrow("SELECT * FROM observability_alerts WHERE slo_id=$1 AND status IN ('open','acknowledged') ORDER BY first_seen_at DESC LIMIT 1", definition["id"])
    description = f"{definition['name']} measured {value if value is not None else 'no value'} against target {definition['comparator']} {definition['target']}."
    if existing:
        row = await db.fetchrow(
            "UPDATE observability_alerts SET observed_value=$2,threshold_value=$3,last_seen_at=NOW(),description=$4 WHERE id=$1 RETURNING *",
            existing["id"], value, definition["target"], description,
        )
        return dict(row), False
    row = await db.fetchrow(
        """INSERT INTO observability_alerts(slo_id,severity,title,description,observed_value,threshold_value)
           VALUES($1,$2,$3,$4,$5,$6) RETURNING *""",
        definition["id"], definition["severity"], f"SLO violation: {definition['name']}", description, value, definition["target"],
    )
    return dict(row), True


async def apply_retention(db) -> None:
    rollup_days = max(7, int(os.getenv("OBSERVABILITY_ROLLUP_RETENTION_DAYS", "90")))
    result_days = max(30, int(os.getenv("OBSERVABILITY_RESULT_RETENTION_DAYS", "180")))
    await db.execute("DELETE FROM observability_metric_rollups WHERE bucket_start < NOW()-($1::text || ' days')::interval", str(rollup_days))
    await db.execute("DELETE FROM observability_slo_results WHERE evaluated_at < NOW()-($1::text || ' days')::interval", str(result_days))
    await db.execute("DELETE FROM observability_alerts WHERE status='resolved' AND resolved_at < NOW()-($1::text || ' days')::interval", str(result_days))


async def _notify_admins(db, alerts: list[dict[str, Any]]) -> None:
    if not alerts:
        return
    emails = await db.fetch("SELECT email FROM users WHERE role='admin'")
    for alert in alerts:
        for row in emails:
            await send_observability_alert(row["email"], alert)


def _ratio(numerator, denominator) -> float | None:
    return float(numerator or 0) / float(denominator) if denominator else None


def _float(value) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None
