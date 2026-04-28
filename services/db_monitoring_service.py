"""Data-access layer for the db_monitoring and db_monitoring_alert tables.

Handles insert, query, overview, and health-score operations for the
database monitoring system.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, text, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.db_monitoring import DbMonitoring, DbMonitoringAlert
from models.device import MonitoredDevice
from models.device_group import DeviceGroupMember
from models.registered_agent import RegisteredAgent
from utils.timezone import now_ist


async def _get_hostname_map(db: AsyncSession) -> dict:
    """Build ip_address -> hostname lookup from registered agents."""
    result = await db.execute(
        select(RegisteredAgent.ip_address, RegisteredAgent.hostname)
    )
    return {row[0]: row[1] for row in result.fetchall()}

logger = logging.getLogger(__name__)


# ── Insert operations ────────────────────────────────────────────


async def insert_db_monitoring_data(
    db: AsyncSession,
    ip_address: str,
    timestamp: datetime,
    instance_name: str,
    db_type: str,
    db_host: str,
    db_port: int,
    is_reachable: bool,
    metrics: dict,
) -> bool:
    """Insert a DB monitoring row.

    Uses INSERT ... ON DUPLICATE KEY UPDATE to handle re-sends gracefully.
    Returns True if a new row was inserted.
    """
    try:
        metrics_json = json.dumps(metrics) if metrics else None

        stmt = text(
            "INSERT INTO db_monitoring "
            "(ip_address, timestamp, db_type, instance_name, db_host, db_port, is_reachable, metrics) "
            "VALUES (:ip, :ts, :db_type, :inst, :db_host, :db_port, :reachable, :metrics) "
            "ON DUPLICATE KEY UPDATE "
            "db_host=VALUES(db_host), db_port=VALUES(db_port), "
            "is_reachable=VALUES(is_reachable), metrics=VALUES(metrics)"
        )
        result = await db.execute(stmt, {
            "ip": ip_address,
            "ts": timestamp,
            "db_type": db_type,
            "inst": instance_name,
            "db_host": db_host,
            "db_port": db_port,
            "reachable": is_reachable,
            "metrics": metrics_json,
        })
        await db.commit()
        inserted = result.rowcount == 1
        logger.debug(
            "DB monitoring data %s for %s/%s at %s",
            "inserted" if inserted else "updated",
            ip_address, instance_name, timestamp,
        )
        return inserted

    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to insert DB monitoring data for %s/%s",
            ip_address, instance_name,
        )
        raise


async def insert_null_db_monitoring_row(
    db: AsyncSession,
    ip_address: str,
    timestamp: datetime,
    instance_name: str,
    db_type: str,
) -> bool:
    """Insert a NULL-metrics row for a stale agent's DB instance."""
    try:
        stmt = text(
            "INSERT INTO db_monitoring (ip_address, timestamp, db_type, instance_name, is_reachable) "
            "VALUES (:ip, :ts, :db_type, :inst, FALSE) "
            "ON DUPLICATE KEY UPDATE id=id"
        )
        result = await db.execute(stmt, {
            "ip": ip_address,
            "ts": timestamp,
            "db_type": db_type,
            "inst": instance_name,
        })
        await db.commit()
        inserted = result.rowcount == 1
        if inserted:
            logger.info(
                "Inserted NULL DB monitoring row for stale agent %s/%s at %s",
                ip_address, instance_name, timestamp,
            )
        return inserted
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to insert null DB monitoring row for %s/%s",
            ip_address, instance_name,
        )
        raise


# ── Query operations ─────────────────────────────────────────────


async def get_db_overview(db: AsyncSession, group_id: Optional[int] = None) -> List[dict]:
    """Return the latest DB monitoring record per (ip_address, instance_name).

    Used by the dashboard to show all DB instances at a glance.
    Includes hostname from registered_agent for display.
    """
    hostname_map = await _get_hostname_map(db)

    base_query = select(
        DbMonitoring.ip_address,
        DbMonitoring.instance_name,
        func.max(DbMonitoring.timestamp).label("max_ts"),
    )

    if group_id is not None:
        group_ip_subq = (
            select(MonitoredDevice.ip_address)
            .join(DeviceGroupMember, DeviceGroupMember.device_id == MonitoredDevice.id)
            .where(DeviceGroupMember.group_id == group_id)
        )
        base_query = base_query.where(DbMonitoring.ip_address.in_(group_ip_subq))

    subq = (
        base_query
        .group_by(DbMonitoring.ip_address, DbMonitoring.instance_name)
        .subquery()
    )

    result = await db.execute(
        select(DbMonitoring)
        .join(
            subq,
            and_(
                DbMonitoring.ip_address == subq.c.ip_address,
                DbMonitoring.instance_name == subq.c.instance_name,
                DbMonitoring.timestamp == subq.c.max_ts,
            ),
        )
        .order_by(DbMonitoring.ip_address, DbMonitoring.instance_name)
    )
    rows = result.scalars().all()
    items = []
    for r in rows:
        d = r.to_dict()
        d["hostname"] = hostname_map.get(r.ip_address, "")
        items.append(d)
    return items


async def get_db_instances_for_ip(
    db: AsyncSession,
    ip_address: str,
) -> List[dict]:
    """Return the latest record per instance_name for a given IP."""
    hostname_map = await _get_hostname_map(db)

    subq = (
        select(
            DbMonitoring.instance_name,
            func.max(DbMonitoring.timestamp).label("max_ts"),
        )
        .where(DbMonitoring.ip_address == ip_address)
        .group_by(DbMonitoring.instance_name)
        .subquery()
    )

    result = await db.execute(
        select(DbMonitoring)
        .join(
            subq,
            and_(
                DbMonitoring.instance_name == subq.c.instance_name,
                DbMonitoring.timestamp == subq.c.max_ts,
                DbMonitoring.ip_address == ip_address,
            ),
        )
        .order_by(DbMonitoring.instance_name)
    )
    rows = result.scalars().all()
    items = []
    for r in rows:
        d = r.to_dict()
        d["hostname"] = hostname_map.get(r.ip_address, "")
        items.append(d)
    return items


async def get_db_monitoring_history(
    db: AsyncSession,
    ip_address: str,
    instance_name: str,
    hours: int = 24,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> List[dict]:
    """Return DB monitoring rows for a specific instance with time range."""
    hostname_map = await _get_hostname_map(db)

    conditions = [
        DbMonitoring.ip_address == ip_address,
        DbMonitoring.instance_name == instance_name,
    ]

    if start or end:
        if start:
            conditions.append(DbMonitoring.timestamp >= start)
        if end:
            conditions.append(DbMonitoring.timestamp <= end)
    else:
        cutoff = now_ist() - timedelta(hours=hours)
        conditions.append(DbMonitoring.timestamp >= cutoff)

    result = await db.execute(
        select(DbMonitoring)
        .where(*conditions)
        .order_by(DbMonitoring.timestamp.desc())
    )
    rows = result.scalars().all()
    items = []
    for r in rows:
        d = r.to_dict()
        d["hostname"] = hostname_map.get(r.ip_address, "")
        items.append(d)
    return items


# ── Health score computation ─────────────────────────────────────


# Weights for composite health score (total = 1.0)
_HEALTH_WEIGHTS = {
    "availability": 0.25,
    "performance": 0.20,
    "capacity": 0.15,
    "security": 0.15,
    "replication": 0.10,
    "backup": 0.10,
    "locks": 0.05,
}


def compute_health_score(metrics: dict, is_reachable: bool) -> float:
    """Compute a 0-100 health score from collected metrics.

    Each category scores 0-100, then weighted sum produces the final score.
    """
    if not is_reachable or not metrics:
        return 0.0

    scores: Dict[str, float] = {}

    # Availability: is the DB reachable?
    scores["availability"] = 100.0 if is_reachable else 0.0

    # Performance: based on slow queries and buffer/cache hit ratio
    perf = metrics.get("query_performance", {})
    res = metrics.get("resource_utilization", {})
    slow = perf.get("slow_queries", 0)
    hit_ratio = res.get("buffer_pool_hit_ratio") or res.get("cache_hit_ratio", 100.0)
    perf_score = max(0, 100 - slow * 5)  # -5 per slow query, min 0
    if hit_ratio < 90:
        perf_score = min(perf_score, hit_ratio)
    scores["performance"] = min(perf_score, 100.0)

    # Capacity: based on storage usage
    storage = metrics.get("storage", {})
    total_mb = storage.get("total_size_mb", 0)
    # Simple heuristic: if total > 100GB, score drops
    if total_mb > 100_000:
        scores["capacity"] = 50.0
    elif total_mb > 50_000:
        scores["capacity"] = 70.0
    else:
        scores["capacity"] = 100.0

    # Security: based on failed logins
    sec = metrics.get("security", {})
    failed = sec.get("failed_logins_5min", 0)
    scores["security"] = max(0, 100 - failed * 10)

    # Replication: based on lag
    repl = metrics.get("replication", {})
    lag = repl.get("lag_seconds")
    if lag is None:
        scores["replication"] = 100.0  # No replication configured
    elif lag > 300:
        scores["replication"] = 0.0
    elif lag > 60:
        scores["replication"] = 50.0
    elif lag > 10:
        scores["replication"] = 80.0
    else:
        scores["replication"] = 100.0

    # Backup: based on last backup age
    backup = metrics.get("backup", {})
    last_backup = backup.get("last_backup_time")
    if last_backup is None:
        scores["backup"] = 30.0  # No backup info → warning
    else:
        scores["backup"] = 100.0  # Has backup info

    # Locks: based on deadlocks
    locks = metrics.get("locks", {})
    deadlocks = locks.get("deadlocks_since_start", 0)
    active_locks = locks.get("active_locks", 0)
    lock_score = 100.0
    if deadlocks > 10:
        lock_score -= 30
    if active_locks > 50:
        lock_score -= 20
    scores["locks"] = max(0, lock_score)

    # Weighted sum
    total = sum(
        scores.get(cat, 100.0) * weight
        for cat, weight in _HEALTH_WEIGHTS.items()
    )
    return round(min(total, 100.0), 2)


async def update_health_score(
    db: AsyncSession,
    row_id: int,
    score: float,
) -> None:
    """Update the health_score for a specific DB monitoring row."""
    await db.execute(
        update(DbMonitoring)
        .where(DbMonitoring.id == row_id)
        .values(health_score=score)
    )
    await db.commit()


# ── Alert operations ─────────────────────────────────────────────


async def create_alert(
    db: AsyncSession,
    ip_address: str,
    instance_name: str,
    alert_type: str,
    severity: str,
    message: str,
    details: Optional[dict] = None,
) -> int:
    """Create a new alert. Returns the alert ID."""
    alert = DbMonitoringAlert(
        ip_address=ip_address,
        instance_name=instance_name,
        alert_type=alert_type,
        severity=severity,
        message=message,
        details=json.dumps(details) if details else None,
        created_at=now_ist(),
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert.id


async def get_alerts(
    db: AsyncSession,
    ip_address: Optional[str] = None,
    instance_name: Optional[str] = None,
    alert_type: Optional[str] = None,
    resolved: Optional[bool] = None,
    limit: int = 100,
) -> List[dict]:
    """Query alerts with optional filters."""
    conditions = []
    if ip_address:
        conditions.append(DbMonitoringAlert.ip_address == ip_address)
    if instance_name:
        conditions.append(DbMonitoringAlert.instance_name == instance_name)
    if alert_type:
        conditions.append(DbMonitoringAlert.alert_type == alert_type)
    if resolved is not None:
        conditions.append(DbMonitoringAlert.is_resolved == resolved)

    query = select(DbMonitoringAlert)
    if conditions:
        query = query.where(*conditions)
    query = query.order_by(DbMonitoringAlert.created_at.desc()).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all()
    return [r.to_dict() for r in rows]


async def resolve_alert(db: AsyncSession, alert_id: int) -> bool:
    """Mark an alert as resolved. Returns True if found."""
    result = await db.execute(
        select(DbMonitoringAlert).where(DbMonitoringAlert.id == alert_id)
    )
    alert = result.scalars().first()
    if not alert:
        return False
    alert.is_resolved = True
    alert.resolved_at = now_ist()
    await db.commit()
    return True
