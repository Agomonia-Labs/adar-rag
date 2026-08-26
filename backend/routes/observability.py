from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.dependencies import AdminUser
from database.connection import get_db
from services.observability import run_observability_cycle

router = APIRouter()


class SloInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    metric_name: str = Field(min_length=2, max_length=100)
    target: float
    comparator: Literal["gte", "lte"]
    window_minutes: int = Field(default=60, ge=5, le=43200)
    minimum_request_count: int = Field(default=10, ge=0, le=1000000)
    severity: Literal["info", "warning", "critical"] = "warning"
    enabled: bool = True


@router.get("/overview")
async def overview(admin: AdminUser, db=Depends(get_db), hours: int = 24):
    hours = max(1, min(hours, 720))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    metrics = await db.fetch(
        """SELECT DISTINCT ON(metric_name,dimension_key) metric_name,dimension_key,dimensions,
                  sample_count,metric_value,p50,p95,p99,bucket_start
           FROM observability_metric_rollups WHERE bucket_start >= $1
           ORDER BY metric_name,dimension_key,bucket_start DESC""",
        since,
    )
    slo_counts = await db.fetchrow(
        """SELECT COUNT(*) FILTER(WHERE enabled)::int AS enabled,
                  COUNT(*) FILTER(WHERE r.compliant=FALSE)::int AS breached,
                  COUNT(*) FILTER(WHERE r.compliant=TRUE)::int AS healthy
           FROM observability_slo_definitions d LEFT JOIN LATERAL(
             SELECT compliant FROM observability_slo_results WHERE slo_id=d.id ORDER BY evaluated_at DESC LIMIT 1
           ) r ON TRUE"""
    )
    alerts = await db.fetchrow(
        "SELECT COUNT(*) FILTER(WHERE status='open')::int AS open, COUNT(*) FILTER(WHERE status='acknowledged')::int AS acknowledged FROM observability_alerts"
    )
    quality = await db.fetchrow(
        "SELECT COUNT(*)::int AS evaluations,AVG(score) AS average_score FROM trace_evaluation_correlations WHERE created_at >= $1",
        since,
    )
    checkpoint = await db.fetchrow("SELECT * FROM observability_checkpoints WHERE job_name='aggregate_slos'")
    return {"hours": hours, "metrics": [dict(row) for row in metrics], "slos": dict(slo_counts),
            "alerts": dict(alerts), "quality": dict(quality), "checkpoint": dict(checkpoint) if checkpoint else None}


@router.get("/metrics")
async def metrics(admin: AdminUser, db=Depends(get_db), metric_name: str | None = None, hours: int = 24):
    hours = max(1, min(hours, 720))
    rows = await db.fetch(
        """SELECT * FROM observability_metric_rollups
           WHERE bucket_start >= NOW()-($1::text || ' hours')::interval
             AND ($2::text IS NULL OR metric_name=$2) ORDER BY bucket_start DESC LIMIT 2000""",
        str(hours), metric_name,
    )
    return [dict(row) for row in rows]


@router.get("/slos")
async def list_slos(admin: AdminUser, db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT d.*,r.measured_value,r.request_count,r.compliant,r.error_budget_remaining,r.burn_rate,r.evaluated_at
           FROM observability_slo_definitions d LEFT JOIN LATERAL(
             SELECT * FROM observability_slo_results WHERE slo_id=d.id ORDER BY evaluated_at DESC LIMIT 1
           ) r ON TRUE ORDER BY d.name"""
    )
    return [dict(row) for row in rows]


@router.post("/slos")
async def create_slo(payload: SloInput, admin: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        """INSERT INTO observability_slo_definitions
           (name,description,metric_name,target,comparator,window_minutes,minimum_request_count,severity,enabled)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
        payload.name, payload.description, payload.metric_name, payload.target, payload.comparator,
        payload.window_minutes, payload.minimum_request_count, payload.severity, payload.enabled,
    )
    return dict(row)


@router.put("/slos/{slo_id}")
async def update_slo(slo_id: str, payload: SloInput, admin: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        """UPDATE observability_slo_definitions SET name=$2,description=$3,metric_name=$4,target=$5,
           comparator=$6,window_minutes=$7,minimum_request_count=$8,severity=$9,enabled=$10,updated_at=NOW()
           WHERE id=$1::uuid RETURNING *""",
        slo_id, payload.name, payload.description, payload.metric_name, payload.target, payload.comparator,
        payload.window_minutes, payload.minimum_request_count, payload.severity, payload.enabled,
    )
    if not row:
        raise HTTPException(404, "SLO not found")
    return dict(row)


@router.get("/alerts")
async def list_alerts(admin: AdminUser, db=Depends(get_db), status: str | None = None):
    rows = await db.fetch(
        """SELECT a.*,d.name AS slo_name FROM observability_alerts a
           JOIN observability_slo_definitions d ON d.id=a.slo_id
           WHERE ($1::text IS NULL OR a.status=$1) ORDER BY a.last_seen_at DESC LIMIT 500""", status,
    )
    return [dict(row) for row in rows]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, admin: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        """UPDATE observability_alerts SET status='acknowledged',acknowledged_by=$2::uuid,
           acknowledged_at=NOW(),last_seen_at=NOW() WHERE id=$1::uuid RETURNING *""",
        alert_id, str(admin["id"]),
    )
    if not row:
        raise HTTPException(404, "Alert not found")
    return dict(row)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, admin: AdminUser, db=Depends(get_db)):
    row = await db.fetchrow(
        "UPDATE observability_alerts SET status='resolved',resolved_at=NOW(),last_seen_at=NOW() WHERE id=$1::uuid RETURNING *",
        alert_id,
    )
    if not row:
        raise HTTPException(404, "Alert not found")
    return dict(row)


@router.post("/run")
async def run_now(admin: AdminUser, db=Depends(get_db)):
    return await run_observability_cycle(db, send_notifications=False)
