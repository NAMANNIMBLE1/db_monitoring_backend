"""Unified monitoring endpoints for dynamic service heartbeats.

Accepts heartbeats from both Windows and Linux agents that use the new
dynamic service checking system.  Data is stored with services as a
JSON column rather than individual per-service columns.
"""

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import rate_limit
from config import settings
from database import get_db
from models.registered_agent import RegisteredAgent
from services.agent_service import update_last_seen, validate_token
from services.unified_monitoring_service import (
    get_unified_monitoring_history,
    insert_unified_monitoring_data,
)
from utils.timezone import make_aware, now_ist, to_naive_ist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/unified", tags=["unified-monitoring"])


# ── Schemas ──────────────────────────────────────────────────────


class UnifiedMonitoringData(BaseModel):
    services: Dict[str, str] = Field(default_factory=dict)
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    load_average: Optional[float] = None
    uptime: Optional[int] = None
    event_log_errors: Optional[int] = None


class UnifiedHeartbeatRequest(BaseModel):
    agent_id: str = Field(..., max_length=64)
    ip_address: str = Field(..., max_length=45)
    timestamp: datetime
    data: UnifiedMonitoringData


class HeartbeatResponse(BaseModel):
    status: str


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


# ── Endpoints ────────────────────────────────────────────────────


@router.post("/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(rate_limit)])
async def unified_heartbeat(
    body: UnifiedHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept a single heartbeat with dynamic service statuses."""
    # 1. Authenticate
    token = _extract_bearer_token(request)
    agent = await validate_token(db, token, body.ip_address, body.agent_id)
    if agent is None:
        logger.warning(
            "Heartbeat auth FAILED: agent_id=%s, ip=%s",
            body.agent_id, body.ip_address,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info(
        "Heartbeat from '%s' (%s) agent_id=%s",
        agent.hostname, body.ip_address, body.agent_id,
    )

    # 1b. Block check
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

    # 3. Convert timestamp
    store_ts = to_naive_ist(body.timestamp)

    # 4. Insert
    try:
        await insert_unified_monitoring_data(
            db=db,
            ip_address=body.ip_address,
            timestamp=store_ts,
            data=body.data.model_dump(exclude_none=False),
            agent_id=body.agent_id,
        )
    except Exception:
        logger.exception("Error inserting unified heartbeat data")
        raise HTTPException(status_code=500, detail="Failed to store monitoring data")

    # 5. Update last_seen
    await update_last_seen(db, body.agent_id)

    return HeartbeatResponse(status="ok")


@router.post(
    "/heartbeat/batch",
    response_model=BatchHeartbeatResponse,
    dependencies=[Depends(rate_limit)],
)
async def unified_heartbeat_batch(
    body: List[UnifiedHeartbeatRequest],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept a batch of unified heartbeats (offline buffer sync)."""
    if not body:
        raise HTTPException(status_code=400, detail="Empty batch")

    # Authenticate via first entry
    token = _extract_bearer_token(request)
    first = body[0]
    agent = await validate_token(db, token, first.ip_address, first.agent_id)
    if agent is None:
        logger.warning(
            "Batch auth FAILED: agent_id=%s, ip=%s — token/ip/agent_id mismatch",
            first.agent_id, first.ip_address,
        )
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info(
        "Batch from '%s' (%s) agent_id=%s, entries=%d",
        agent.hostname, first.ip_address, first.agent_id, len(body),
    )
    if agent.is_blocked:
        raise HTTPException(status_code=403, detail="Agent is blocked")

    inserted = 0
    skipped = 0

    for entry in body:
        if entry.agent_id != first.agent_id or entry.ip_address != first.ip_address:
            skipped += 1
            continue

        try:
            store_ts = to_naive_ist(entry.timestamp)
            was_inserted = await insert_unified_monitoring_data(
                db=db,
                ip_address=entry.ip_address,
                timestamp=store_ts,
                data=entry.data.model_dump(exclude_none=False),
                agent_id=entry.agent_id,
                ignore_duplicate=True,
            )
            if was_inserted:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            logger.exception("Error inserting batch entry at %s", entry.timestamp)
            skipped += 1

    await update_last_seen(db, first.agent_id)

    return BatchHeartbeatResponse(
        status="ok",
        total=len(body),
        inserted=inserted,
        skipped=skipped,
    )


def _parse_range_params(
    hours: Optional[int],
    start: Optional[str],
    end: Optional[str],
) -> dict:
    kwargs: dict = {}
    if start:
        kwargs["start"] = to_naive_ist(datetime.fromisoformat(start))
    if end:
        kwargs["end"] = to_naive_ist(datetime.fromisoformat(end))
    if not kwargs:
        kwargs["hours"] = hours or 24
    return kwargs


@router.get("/monitor/{ip}")
async def get_unified_monitor(
    ip: str,
    hours: Optional[int] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return unified monitoring data for a given IP."""
    result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.ip_address == ip)
    )
    agent = result.scalars().first()
    if agent is None:
        return {"error": "Agent not installed on this machine"}

    try:
        history = await get_unified_monitoring_history(
            db, ip, **_parse_range_params(hours, start, end)
        )
        return history
    except Exception:
        logger.exception("Error fetching unified monitoring history for %s", ip)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/monitor/{ip}/export")
async def export_unified_monitor_csv(
    ip: str,
    hours: Optional[int] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Stream unified monitoring history as a CSV file."""
    result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.ip_address == ip)
    )
    agent = result.scalars().first()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        history = await get_unified_monitoring_history(
            db, ip, **_parse_range_params(hours, start, end)
        )
    except Exception:
        logger.exception("Error fetching unified monitoring history for CSV export %s", ip)
        raise HTTPException(status_code=500, detail="Internal server error")

    if not history:
        raise HTTPException(status_code=404, detail="No data available")

    # Build flat CSV: expand services JSON into individual columns
    # Collect all service keys across all rows
    all_service_keys = set()
    for row in history:
        if row.get("services") and isinstance(row["services"], dict):
            all_service_keys.update(row["services"].keys())
    all_service_keys = sorted(all_service_keys)

    base_fields = ["timestamp", "ip_address", "cpu_usage", "memory_usage",
                    "disk_usage", "load_average", "uptime", "event_log_errors"]
    fieldnames = base_fields + all_service_keys

    def generate():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for row in history:
            flat = {k: row.get(k) for k in base_fields}
            services = row.get("services") or {}
            for sk in all_service_keys:
                flat[sk] = services.get(sk, "")
            writer.writerow(flat)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"unified_monitoring_{ip}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
