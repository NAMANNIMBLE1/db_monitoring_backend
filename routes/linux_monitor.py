import csv
import io
import logging
from datetime import datetime, timedelta, timezone
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
from services.linux_monitoring_service import (
    get_linux_monitoring_history,
    insert_linux_monitoring_data,
)
from utils.timezone import now_ist, to_naive_ist, make_aware

logger = logging.getLogger(__name__)

router = APIRouter(tags=["linux-monitoring"])


# ── Request / Response schemas ──


class LinuxMonitoringData(BaseModel):
    ssh_status: Optional[str] = None
    systemd_status: Optional[str] = None
    network_status: Optional[str] = None
    ntp_status: Optional[str] = None
    logging_status: Optional[str] = None
    cron_status: Optional[str] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    load_average: Optional[float] = None
    uptime: Optional[int] = None


class LinuxHeartbeatRequest(BaseModel):
    agent_id: str = Field(..., max_length=64)
    ip_address: str = Field(..., max_length=45)
    timestamp: datetime
    data: LinuxMonitoringData


class HeartbeatResponse(BaseModel):
    status: str


class BatchHeartbeatResponse(BaseModel):
    status: str
    total: int
    inserted: int
    skipped: int


# ── Helper ──


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be 'Bearer <token>'")
    return auth_header[7:]


def _parse_range_params(
    hours: Optional[int],
    start: Optional[str],
    end: Optional[str],
) -> dict:
    """Return kwargs suitable for ``get_linux_monitoring_history``."""
    kwargs: dict = {}
    if start:
        kwargs["start"] = to_naive_ist(datetime.fromisoformat(start))
    if end:
        kwargs["end"] = to_naive_ist(datetime.fromisoformat(end))
    if not kwargs:
        kwargs["hours"] = hours or 24
    return kwargs


# ── Endpoints ──


@router.post("/linux/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(rate_limit)])
async def linux_heartbeat(
    body: LinuxHeartbeatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Accept a single heartbeat + monitoring payload from a Linux agent.
    """
    # 1. Authenticate
    token = _extract_bearer_token(request)
    agent = await validate_token(db, token, body.ip_address, body.agent_id)
    if agent is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 1b. Block check
    if agent.is_blocked:
        raise HTTPException(status_code=403, detail="Agent is blocked")

    # 2. Replay-protection: compare both as aware datetimes
    now = now_ist()
    incoming = make_aware(body.timestamp)
    delta = abs((now - incoming).total_seconds())
    if delta > settings.TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Timestamp out of tolerance ({delta:.0f}s drift, max {settings.TIMESTAMP_TOLERANCE_SECONDS}s)",
        )

    # 3. Convert timestamp to naive IST for DB storage
    store_ts = to_naive_ist(body.timestamp)

    # 4. Insert monitoring data + heartbeat
    try:
        await insert_linux_monitoring_data(
            db=db,
            ip_address=body.ip_address,
            timestamp=store_ts,
            data=body.data.model_dump(exclude_none=False),
            agent_id=body.agent_id,
        )
    except Exception:
        logger.exception("Error inserting Linux heartbeat data")
        raise HTTPException(status_code=500, detail="Failed to store monitoring data")

    # 5. Update last_seen
    await update_last_seen(db, body.agent_id)

    return HeartbeatResponse(status="ok")


@router.post("/linux/heartbeat/batch", response_model=BatchHeartbeatResponse, dependencies=[Depends(rate_limit)])
async def linux_heartbeat_batch(
    body: List[LinuxHeartbeatRequest],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Accept a batch of heartbeat entries from a Linux agent (offline buffer sync).
    Duplicate (ip_address, timestamp) pairs are silently skipped.
    """
    if not body:
        raise HTTPException(status_code=400, detail="Empty batch")

    # Authenticate using the first entry's credentials
    token = _extract_bearer_token(request)
    first = body[0]
    agent = await validate_token(db, token, first.ip_address, first.agent_id)
    if agent is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Block check
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
            was_inserted = await insert_linux_monitoring_data(
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
            logger.exception("Error inserting Linux batch entry at %s", entry.timestamp)
            skipped += 1

    await update_last_seen(db, first.agent_id)

    return BatchHeartbeatResponse(
        status="ok",
        total=len(body),
        inserted=inserted,
        skipped=skipped,
    )


@router.get("/linux/monitor/{ip}")
async def get_linux_monitor(
    ip: str,
    hours: Optional[int] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Return Linux monitoring data for a given IP address.

    Supports ``?hours=N`` or ``?start=ISO&end=ISO`` for date-range filtering.
    Defaults to the last 24 hours.
    """
    result = await db.execute(
        select(RegisteredAgent).where(
            RegisteredAgent.ip_address == ip,
            RegisteredAgent.os_type == "linux",
        )
    )
    agent = result.scalars().first()
    if agent is None:
        return {"error": "Linux agent not installed on this machine"}

    try:
        history = await get_linux_monitoring_history(db, ip, **_parse_range_params(hours, start, end))
        return history
    except Exception:
        logger.exception("Error fetching Linux monitoring history for %s", ip)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/linux/monitor/{ip}/export")
async def export_linux_monitor_csv(
    ip: str,
    hours: Optional[int] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Stream Linux monitoring history as a CSV file download."""
    result = await db.execute(
        select(RegisteredAgent).where(
            RegisteredAgent.ip_address == ip,
            RegisteredAgent.os_type == "linux",
        )
    )
    agent = result.scalars().first()
    if agent is None:
        raise HTTPException(status_code=404, detail="Linux agent not found")

    try:
        history = await get_linux_monitoring_history(db, ip, **_parse_range_params(hours, start, end))
    except Exception:
        logger.exception("Error fetching Linux monitoring history for CSV export %s", ip)
        raise HTTPException(status_code=500, detail="Internal server error")

    if not history:
        raise HTTPException(status_code=404, detail="No data available")

    fieldnames = list(history[0].keys())

    def generate():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for row in history:
            writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"linux_monitoring_{ip}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
