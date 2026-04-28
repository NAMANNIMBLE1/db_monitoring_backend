"""Database monitoring endpoints.

Accepts DB metric heartbeats from agents, provides query APIs for the
React frontend dashboard, and manages alerts.
"""

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import rate_limit
from config import settings
from database import get_db
from services.agent_service import update_last_seen, validate_token
from services.auth_service import filter_by_ip_permissions
from services.db_monitoring_service import (
    create_alert,
    get_alerts,
    get_db_instances_for_ip,
    get_db_monitoring_history,
    get_db_overview,
    insert_db_monitoring_data,
    resolve_alert,
)
from utils.timezone import make_aware, now_ist, to_naive_ist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/db", tags=["db-monitoring"])


# ── Schemas ──────────────────────────────────────────────────────


class DbInstanceMetrics(BaseModel):
    instance_name: str = Field(..., max_length=128)
    db_type: str = Field(..., max_length=20)
    db_host: str = Field(..., max_length=255)
    db_port: int
    is_reachable: bool
    metrics: Dict[str, Any] = Field(default_factory=dict)


class DbHeartbeatRequest(BaseModel):
    agent_id: str = Field(..., max_length=64)
    ip_address: str = Field(..., max_length=45)
    timestamp: datetime
    databases: List[DbInstanceMetrics]


class HeartbeatResponse(BaseModel):
    status: str
    inserted: int
    skipped: int


class BatchHeartbeatResponse(BaseModel):
    status: str
    total: int
    inserted: int
    skipped: int


# ── Helpers ──────────────────────────────────────────────────────


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be 'Bearer <token>'",
        )
    return auth_header[7:]


# ── Heartbeat Endpoints ─────────────────────────────────────────


@router.post("/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(rate_limit)])
async def db_heartbeat(
    body: DbHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept a single DB monitoring heartbeat from an agent."""
    # 1. Authenticate
    token = _extract_bearer_token(request)
    agent = await validate_token(db, token, body.ip_address, body.agent_id)
    if agent is None:
        logger.warning(
            "DB heartbeat auth FAILED: agent_id=%s, ip=%s",
            body.agent_id, body.ip_address,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info(
        "DB heartbeat from '%s' (%s) agent_id=%s, %d instances",
        agent.hostname, body.ip_address, body.agent_id, len(body.databases),
    )

    if agent.is_blocked:
        raise HTTPException(status_code=403, detail="Agent is blocked")

    # 2. Replay protection
    now = now_ist()
    incoming = make_aware(body.timestamp)
    delta = abs((now - incoming).total_seconds())
    if delta > settings.TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Timestamp out of tolerance ({delta:.0f}s drift, max {settings.TIMESTAMP_TOLERANCE_SECONDS}s)",
        )

    # 3. Store each DB instance
    store_ts = to_naive_ist(body.timestamp)
    inserted = 0
    skipped = 0

    for inst in body.databases:
        try:
            was_inserted = await insert_db_monitoring_data(
                db=db,
                ip_address=body.ip_address,
                timestamp=store_ts,
                instance_name=inst.instance_name,
                db_type=inst.db_type,
                db_host=inst.db_host,
                db_port=inst.db_port,
                is_reachable=inst.is_reachable,
                metrics=inst.metrics,
            )
            if was_inserted:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            logger.exception(
                "Error storing DB data for %s/%s",
                body.ip_address, inst.instance_name,
            )
            skipped += 1

    # 4. Update last_seen
    await update_last_seen(db, body.agent_id)

    return HeartbeatResponse(status="ok", inserted=inserted, skipped=skipped)


@router.post(
    "/heartbeat/batch",
    response_model=BatchHeartbeatResponse,
    dependencies=[Depends(rate_limit)],
)
async def db_heartbeat_batch(
    body: List[DbHeartbeatRequest],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept a batch of DB heartbeats (offline buffer sync)."""
    if not body:
        raise HTTPException(status_code=400, detail="Empty batch")

    # Auth via first entry
    token = _extract_bearer_token(request)
    first = body[0]
    agent = await validate_token(db, token, first.ip_address, first.agent_id)
    if agent is None:
        logger.warning(
            "DB batch auth FAILED: agent_id=%s, ip=%s",
            first.agent_id, first.ip_address,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
    if agent.is_blocked:
        raise HTTPException(status_code=403, detail="Agent is blocked")

    logger.info(
        "DB batch from '%s' (%s), %d entries",
        agent.hostname, first.ip_address, len(body),
    )

    total_inserted = 0
    total_skipped = 0

    for entry in body:
        store_ts = to_naive_ist(entry.timestamp)
        for inst in entry.databases:
            try:
                was_inserted = await insert_db_monitoring_data(
                    db=db,
                    ip_address=entry.ip_address,
                    timestamp=store_ts,
                    instance_name=inst.instance_name,
                    db_type=inst.db_type,
                    db_host=inst.db_host,
                    db_port=inst.db_port,
                    is_reachable=inst.is_reachable,
                    metrics=inst.metrics,
                )
                if was_inserted:
                    total_inserted += 1
                else:
                    total_skipped += 1
            except Exception:
                total_skipped += 1

    await update_last_seen(db, first.agent_id)

    return BatchHeartbeatResponse(
        status="ok",
        total=sum(len(e.databases) for e in body),
        inserted=total_inserted,
        skipped=total_skipped,
    )


# ── Query Endpoints ──────────────────────────────────────────────


@router.get("/overview")
async def overview(
    group_id: Optional[int] = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Dashboard: latest record per DB instance across all devices."""
    overview_data = await get_db_overview(db, group_id=group_id)
    
    # Filter by user permissions
    user_info = getattr(request.state, 'user', None)
    if user_info:
        filtered_data = filter_by_ip_permissions(
            overview_data, user_info['allowed_ips'], user_info['is_admin']
        )
        return filtered_data
    
    return overview_data


@router.get("/monitor/{ip}")
async def monitor_ip(
    ip: str,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """List all DB instances for a given device IP (latest record each)."""
    # Check user permissions
    user_info = getattr(request.state, 'user', None)
    if user_info and not user_info['is_admin'] and ip not in user_info['allowed_ips']:
        raise HTTPException(status_code=403, detail="Access denied to this device")
    
    return await get_db_instances_for_ip(db, ip)


@router.get("/monitor/{ip}/{instance_name}")
async def monitor_instance(
    ip: str,
    instance_name: str,
    hours: int = Query(24, ge=1, le=720),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Detailed metrics history for one DB instance."""
    # Check user permissions
    user_info = getattr(request.state, 'user', None)
    if user_info and not user_info['is_admin'] and ip not in user_info['allowed_ips']:
        raise HTTPException(status_code=403, detail="Access denied to this device")
    
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    return await get_db_monitoring_history(
        db, ip, instance_name,
        hours=hours, start=start_dt, end=end_dt,
    )


@router.get("/monitor/{ip}/{instance_name}/export")
async def export_instance_csv(
    ip: str,
    instance_name: str,
    hours: int = Query(24, ge=1, le=720),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Export DB monitoring data as CSV."""
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    rows = await get_db_monitoring_history(
        db, ip, instance_name,
        hours=hours, start=start_dt, end=end_dt,
    )

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            # Flatten metrics JSON for CSV
            flat = dict(row)
            if isinstance(flat.get("metrics"), dict):
                flat["metrics"] = json.dumps(flat["metrics"])
            writer.writerow(flat)
    else:
        output.write("No data\n")

    output.seek(0)
    filename = f"db-{ip}-{instance_name}-export.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Forecast Endpoint ─────────────────────────────────────────────


@router.get("/monitor/{ip}/{instance_name}/forecast")
async def forecast_storage(
    ip: str,
    instance_name: str,
    days: int = Query(30, ge=7, le=365, description="Days of history to analyze"),
    forecast_days: int = Query(90, ge=7, le=365, description="Days to forecast ahead"),
    db: AsyncSession = Depends(get_db),
):
    """Predict storage growth using linear regression on historical data.

    Returns current size, predicted size at forecast horizon, predicted
    exhaustion date (if a disk limit is estimable), and data points for charting.
    """
    from datetime import timedelta
    import numpy as np

    # Fetch history for the analysis window
    cutoff = now_ist() - timedelta(days=days)
    from sqlalchemy import select, and_
    from models.db_monitoring import DbMonitoring

    result = await db.execute(
        select(DbMonitoring.timestamp, DbMonitoring.metrics)
        .where(and_(
            DbMonitoring.ip_address == ip,
            DbMonitoring.instance_name == instance_name,
            DbMonitoring.timestamp >= cutoff.replace(tzinfo=None),
            DbMonitoring.metrics != None,  # noqa: E711
        ))
        .order_by(DbMonitoring.timestamp.asc())
    )
    rows = result.fetchall()

    if len(rows) < 2:
        return {
            "error": "Not enough data points for forecasting. Need at least 2 data points.",
            "data_points": len(rows),
        }

    # Extract (timestamp_epoch, total_size_mb) pairs
    points = []
    for ts, metrics_raw in rows:
        try:
            metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else metrics_raw
            size_mb = metrics.get("storage", {}).get("total_size_mb")
            if size_mb is not None:
                epoch = ts.timestamp() if hasattr(ts, 'timestamp') else 0
                points.append((epoch, float(size_mb)))
        except Exception:
            continue

    if len(points) < 2:
        return {
            "error": "Not enough storage data points for forecasting.",
            "data_points": len(points),
        }

    # Linear regression: size_mb = slope * epoch + intercept
    x = np.array([p[0] for p in points])
    y = np.array([p[1] for p in points])

    # Normalize x to avoid floating point issues with large epoch values
    x_min = x.min()
    x_norm = x - x_min

    n = len(x_norm)
    sum_x = x_norm.sum()
    sum_y = y.sum()
    sum_xy = (x_norm * y).sum()
    sum_x2 = (x_norm ** 2).sum()

    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-10:
        return {
            "error": "Storage size is constant — no trend to forecast.",
            "current_size_mb": float(y[-1]),
            "data_points": n,
        }

    slope = (n * sum_xy - sum_x * sum_y) / denom  # MB per second
    intercept = (sum_y - slope * sum_x) / n

    slope_mb_per_day = slope * 86400  # Convert to MB/day
    current_size = float(y[-1])
    current_epoch_norm = float(x_norm[-1])

    # Forecast future data points (daily granularity)
    forecast_points = []
    for d in range(1, forecast_days + 1):
        future_epoch_norm = current_epoch_norm + d * 86400
        predicted_mb = slope * future_epoch_norm + intercept
        forecast_date = datetime.fromtimestamp(x[-1] + d * 86400)
        forecast_points.append({
            "date": forecast_date.strftime("%Y-%m-%d"),
            "predicted_size_mb": round(float(max(predicted_mb, 0)), 2),
        })

    # Historical data points (daily averages for charting)
    from collections import defaultdict
    daily = defaultdict(list)
    for epoch, size in points:
        day = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
        daily[day].append(size)
    history_points = [
        {"date": day, "size_mb": round(sum(vals) / len(vals), 2)}
        for day, vals in sorted(daily.items())
    ]

    # Predicted size at forecast horizon
    horizon_epoch_norm = current_epoch_norm + forecast_days * 86400
    predicted_at_horizon = max(float(slope * horizon_epoch_norm + intercept), 0)

    # R-squared (model confidence)
    y_mean = y.mean()
    ss_tot = ((y - y_mean) ** 2).sum()
    y_pred = slope * x_norm + intercept
    ss_res = ((y - y_pred) ** 2).sum()
    r_squared = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0

    return {
        "current_size_mb": round(current_size, 2),
        "growth_rate_mb_per_day": round(slope_mb_per_day, 4),
        "predicted_size_mb": round(predicted_at_horizon, 2),
        "forecast_horizon_days": forecast_days,
        "r_squared": round(r_squared, 4),
        "confidence": "high" if r_squared > 0.8 else "medium" if r_squared > 0.5 else "low",
        "trend": "growing" if slope_mb_per_day > 0.01 else "shrinking" if slope_mb_per_day < -0.01 else "stable",
        "data_points_analyzed": n,
        "history": history_points,
        "forecast": forecast_points[:90],  # Cap at 90 points for chart
    }


# ── Alert Endpoints ──────────────────────────────────────────────


@router.get("/alerts")
async def list_alerts(
    ip: Optional[str] = Query(None),
    instance: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None),
    resolved: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """List alerts with optional filters."""
    return await get_alerts(
        db,
        ip_address=ip,
        instance_name=instance,
        alert_type=alert_type,
        resolved=resolved,
        limit=limit,
    )


@router.patch("/alerts/{alert_id}/resolve")
async def resolve_alert_endpoint(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Mark an alert as resolved."""
    found = await resolve_alert(db, alert_id)
    if not found:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "resolved", "id": alert_id}


# ── Schema History Endpoints ─────────────────────────────────────


class SchemaChangePayload(BaseModel):
    instance_name: str = Field(..., max_length=128)
    db_type: str = Field(..., max_length=20)
    schema_hash: str = Field(..., max_length=64)
    snapshot: Dict[str, Any]
    changes: Optional[Dict[str, Any]] = None


class SchemaHeartbeatRequest(BaseModel):
    agent_id: str = Field(..., max_length=64)
    ip_address: str = Field(..., max_length=45)
    timestamp: datetime
    schema_changes: List[SchemaChangePayload]


@router.post("/schema/report")
async def report_schema_change(
    body: SchemaHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept schema change reports from agents. Only called when a change is detected."""
    token = _extract_bearer_token(request)
    agent = await validate_token(db, token, body.ip_address, body.agent_id)
    if agent is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if agent.is_blocked:
        raise HTTPException(status_code=403, detail="Agent is blocked")

    from models.db_schema_history import DbSchemaHistory

    inserted = 0
    for sc in body.schema_changes:
        # Check if this hash already recorded (idempotent)
        existing = await db.execute(
            select(DbSchemaHistory).where(
                DbSchemaHistory.ip_address == body.ip_address,
                DbSchemaHistory.instance_name == sc.instance_name,
                DbSchemaHistory.schema_hash == sc.schema_hash,
            )
        )
        if existing.scalars().first():
            continue

        # Get next version number
        max_ver = await db.execute(
            select(func.max(DbSchemaHistory.version)).where(
                DbSchemaHistory.ip_address == body.ip_address,
                DbSchemaHistory.instance_name == sc.instance_name,
            )
        )
        next_version = (max_ver.scalar() or 0) + 1

        row = DbSchemaHistory(
            ip_address=body.ip_address,
            instance_name=sc.instance_name,
            db_type=sc.db_type,
            version=next_version,
            schema_hash=sc.schema_hash,
            snapshot=json.dumps(sc.snapshot),
            changes=json.dumps(sc.changes) if sc.changes else None,
            detected_at=to_naive_ist(body.timestamp),
        )
        db.add(row)
        inserted += 1

        # Also create an alert for schema changes
        await create_alert(
            db,
            ip_address=body.ip_address,
            instance_name=sc.instance_name,
            alert_type="schema_change",
            severity="info",
            message=f"Schema change detected (v{next_version}): {_summarize_changes(sc.changes)}",
            details=sc.changes,
        )

    if inserted:
        await db.commit()
        logger.info("Schema changes recorded: %d for %s", inserted, body.ip_address)

    return {"status": "ok", "recorded": inserted}


def _summarize_changes(changes: dict) -> str:
    if not changes:
        return "Initial snapshot"
    parts = []
    for action in ("tables_added", "tables_dropped", "columns_added", "columns_dropped", "columns_modified"):
        items = changes.get(action, [])
        if items:
            parts.append(f"{len(items)} {action.replace('_', ' ')}")
    return ", ".join(parts) if parts else "Schema updated"


@router.get("/schema/{ip}/{instance_name}")
async def get_schema_history(
    ip: str,
    instance_name: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get schema version history for a DB instance."""
    from models.db_schema_history import DbSchemaHistory
    result = await db.execute(
        select(DbSchemaHistory)
        .where(
            DbSchemaHistory.ip_address == ip,
            DbSchemaHistory.instance_name == instance_name,
        )
        .order_by(DbSchemaHistory.version.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [r.to_dict() for r in rows]


@router.get("/schema/{ip}/{instance_name}/latest")
async def get_latest_schema(
    ip: str,
    instance_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the latest schema snapshot."""
    from models.db_schema_history import DbSchemaHistory
    result = await db.execute(
        select(DbSchemaHistory)
        .where(
            DbSchemaHistory.ip_address == ip,
            DbSchemaHistory.instance_name == instance_name,
        )
        .order_by(DbSchemaHistory.version.desc())
        .limit(1)
    )
    row = result.scalars().first()
    if not row:
        return {"version": 0, "snapshot": None}
    return row.to_dict()
