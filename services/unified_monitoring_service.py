"""Data-access layer for the unified_monitoring table.

Handles insert, query, and null-row operations for the dynamic
service monitoring system.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.monitoring import AgentHeartbeat
from models.unified_monitoring import UnifiedMonitoring
from utils.timezone import now_ist

logger = logging.getLogger(__name__)

# Metric columns that map 1-to-1 from payload to DB columns
_METRIC_COLUMNS = [
    "cpu_usage",
    "memory_usage",
    "disk_usage",
    "load_average",
    "uptime",
    "event_log_errors",
]


async def insert_unified_monitoring_data(
    db: AsyncSession,
    ip_address: str,
    timestamp: datetime,
    data: dict,
    agent_id: Optional[str] = None,
    ignore_duplicate: bool = False,
) -> bool:
    """Insert a unified monitoring row.

    The ``data`` dict must contain a ``services`` dict (service key → status)
    plus optional metric keys (cpu_usage, memory_usage, etc.).

    Returns True if a new row was inserted, False if duplicate was ignored.
    """
    try:
        services_json = json.dumps(data.get("services", {}))

        if ignore_duplicate:
            col_names = ["ip_address", "timestamp", "services"]
            params = {
                "ip_address": ip_address,
                "timestamp": timestamp,
                "services": services_json,
            }
            for col in _METRIC_COLUMNS:
                if col in data and data[col] is not None:
                    col_names.append(col)
                    params[col] = data[col]

            col_list = ", ".join(col_names)
            col_placeholders = ", ".join([f":{c}" for c in col_names])
            stmt = text(
                f"INSERT INTO unified_monitoring ({col_list}) "
                f"VALUES ({col_placeholders}) "
                f"ON DUPLICATE KEY UPDATE id=id"
            )
            result = await db.execute(stmt, params)
            inserted = result.rowcount == 1
        else:
            row = UnifiedMonitoring(
                ip_address=ip_address,
                timestamp=timestamp,
                services=services_json,
            )
            for col in _METRIC_COLUMNS:
                if col in data and data[col] is not None:
                    setattr(row, col, data[col])
            db.add(row)
            await db.flush()
            inserted = True

        # Also record a heartbeat entry
        if agent_id:
            heartbeat = AgentHeartbeat(
                agent_id=agent_id,
                ip_address=ip_address,
                timestamp=timestamp,
            )
            db.add(heartbeat)

        await db.commit()
        logger.debug(
            "Unified monitoring data %s for %s at %s",
            "inserted" if inserted else "duplicate-skipped",
            ip_address,
            timestamp,
        )
        return inserted

    except Exception:
        await db.rollback()
        logger.exception("Failed to insert unified monitoring data for %s", ip_address)
        raise


async def insert_null_unified_monitoring_row(
    db: AsyncSession,
    ip_address: str,
    timestamp: datetime,
) -> bool:
    """Insert a row with NULL metrics and NULL services for a stale agent."""
    try:
        stmt = text(
            "INSERT INTO unified_monitoring (ip_address, timestamp) "
            "VALUES (:ip_address, :timestamp) "
            "ON DUPLICATE KEY UPDATE id=id"
        )
        result = await db.execute(stmt, {"ip_address": ip_address, "timestamp": timestamp})
        await db.commit()
        inserted = result.rowcount == 1
        if inserted:
            logger.info(
                "Inserted NULL unified monitoring row for stale agent %s at %s",
                ip_address, timestamp,
            )
        return inserted
    except Exception:
        await db.rollback()
        logger.exception("Failed to insert null unified monitoring row for %s", ip_address)
        raise


async def get_unified_monitoring_history(
    db: AsyncSession,
    ip_address: str,
    hours: int = 24,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[dict]:
    """Return unified monitoring rows for *ip_address*.

    Supports ``hours`` shorthand or explicit ``start``/``end`` range.
    """
    if start or end:
        conditions = [UnifiedMonitoring.ip_address == ip_address]
        if start:
            conditions.append(UnifiedMonitoring.timestamp >= start)
        if end:
            conditions.append(UnifiedMonitoring.timestamp <= end)
        result = await db.execute(
            select(UnifiedMonitoring)
            .where(*conditions)
            .order_by(UnifiedMonitoring.timestamp.desc())
        )
    else:
        cutoff = now_ist() - timedelta(hours=hours)
        result = await db.execute(
            select(UnifiedMonitoring)
            .where(
                UnifiedMonitoring.ip_address == ip_address,
                UnifiedMonitoring.timestamp >= cutoff,
            )
            .order_by(UnifiedMonitoring.timestamp.desc())
        )

    rows = result.scalars().all()
    return [row.to_dict() for row in rows]
