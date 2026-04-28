import logging
from datetime import datetime, timedelta
from typing import List, Optional

from utils.timezone import now_ist

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.linux_monitoring import LinuxPortMonitoring
from models.monitoring import AgentHeartbeat

logger = logging.getLogger(__name__)

# Column names in LinuxPortMonitoring that hold service status / metrics
_DATA_COLUMNS = [
    "ssh_status",
    "systemd_status",
    "network_status",
    "ntp_status",
    "logging_status",
    "cron_status",
    "cpu_usage",
    "memory_usage",
    "disk_usage",
    "load_average",
    "uptime",
]


async def insert_linux_monitoring_data(
    db: AsyncSession,
    ip_address: str,
    timestamp: datetime,
    data: dict,
    agent_id: Optional[str] = None,
    ignore_duplicate: bool = False,
) -> bool:
    """
    Insert a single Linux monitoring row and an accompanying heartbeat row.

    When *ignore_duplicate* is True the insert silently succeeds even if a row
    with the same (ip_address, timestamp) already exists (INSERT IGNORE
    behaviour implemented via a raw ON DUPLICATE KEY UPDATE id=id).

    Returns True if a new row was inserted, False if it was a duplicate that
    was ignored.
    """
    try:
        if ignore_duplicate:
            col_names = ["ip_address", "timestamp"] + [
                c for c in _DATA_COLUMNS if c in data
            ]
            col_placeholders = ", ".join([f":{c}" for c in col_names])
            col_list = ", ".join(col_names)
            params = {"ip_address": ip_address, "timestamp": timestamp}
            for c in _DATA_COLUMNS:
                if c in data:
                    params[c] = data[c]

            stmt = text(
                f"INSERT INTO linux_port_monitoring ({col_list}) "
                f"VALUES ({col_placeholders}) "
                f"ON DUPLICATE KEY UPDATE id=id"
            )
            result = await db.execute(stmt, params)
            inserted = result.rowcount == 1
        else:
            row = LinuxPortMonitoring(
                ip_address=ip_address,
                timestamp=timestamp,
            )
            for col in _DATA_COLUMNS:
                if col in data:
                    setattr(row, col, data[col])
            db.add(row)
            await db.flush()
            inserted = True

        if agent_id:
            heartbeat = AgentHeartbeat(
                agent_id=agent_id,
                ip_address=ip_address,
                timestamp=timestamp,
            )
            db.add(heartbeat)

        await db.commit()
        logger.debug(
            "Linux monitoring data %s for %s at %s",
            "inserted" if inserted else "duplicate-skipped",
            ip_address,
            timestamp,
        )
        return inserted

    except Exception:
        await db.rollback()
        logger.exception("Failed to insert Linux monitoring data for %s", ip_address)
        raise


async def insert_null_linux_monitoring_row(
    db: AsyncSession,
    ip_address: str,
    timestamp: datetime,
) -> bool:
    """
    Insert a Linux monitoring row with all NULL service/metric values.

    Used by the stale-checker to mark a missed reporting window.  Uses INSERT
    IGNORE semantics so duplicates are silently skipped.
    """
    try:
        stmt = text(
            "INSERT INTO linux_port_monitoring (ip_address, timestamp) "
            "VALUES (:ip_address, :timestamp) "
            "ON DUPLICATE KEY UPDATE id=id"
        )
        result = await db.execute(stmt, {"ip_address": ip_address, "timestamp": timestamp})
        await db.commit()
        inserted = result.rowcount == 1
        if inserted:
            logger.info("Inserted NULL Linux monitoring row for stale agent %s at %s", ip_address, timestamp)
        return inserted
    except Exception:
        await db.rollback()
        logger.exception("Failed to insert null Linux monitoring row for %s", ip_address)
        raise


async def get_linux_monitoring_history(
    db: AsyncSession,
    ip_address: str,
    hours: int = 24,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[dict]:
    """Return Linux monitoring rows for *ip_address*.

    When *start* and/or *end* are supplied they take precedence over *hours*.
    Both should be naive IST datetimes (matching DB storage convention).
    """
    if start or end:
        conditions = [LinuxPortMonitoring.ip_address == ip_address]
        if start:
            conditions.append(LinuxPortMonitoring.timestamp >= start)
        if end:
            conditions.append(LinuxPortMonitoring.timestamp <= end)
        result = await db.execute(
            select(LinuxPortMonitoring)
            .where(*conditions)
            .order_by(LinuxPortMonitoring.timestamp.desc())
        )
    else:
        cutoff = now_ist() - timedelta(hours=hours)
        result = await db.execute(
            select(LinuxPortMonitoring)
            .where(
                LinuxPortMonitoring.ip_address == ip_address,
                LinuxPortMonitoring.timestamp >= cutoff,
            )
            .order_by(LinuxPortMonitoring.timestamp.desc())
        )
    rows = result.scalars().all()
    return [row.to_dict() for row in rows]
