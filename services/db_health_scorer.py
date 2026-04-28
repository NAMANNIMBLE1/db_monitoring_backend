"""Background task that computes DB health scores and generates alerts.

Runs every 5 minutes:
1. Reads latest db_monitoring rows without a health_score.
2. Computes weighted health score per instance.
3. Detects SQL injection patterns in collected query strings.
4. Checks backup SLA violations.
5. Generates alerts in db_monitoring_alert table.
"""

import asyncio
import json
import logging
import re
from datetime import timedelta

from sqlalchemy import select, and_

from database import AsyncSessionLocal
from models.db_monitoring import DbMonitoring, DbMonitoringAlert
from services.db_monitoring_service import (
    compute_health_score,
    create_alert,
    update_health_score,
)
from utils.timezone import now_ist

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None

# SQL injection patterns (applied to collected query text)
_SQLI_PATTERNS = [
    re.compile(r"UNION\s+SELECT", re.IGNORECASE),
    re.compile(r"OR\s+1\s*=\s*1", re.IGNORECASE),
    re.compile(r";\s*(DROP|DELETE|UPDATE|INSERT)\s+", re.IGNORECASE),
    re.compile(r"WAITFOR\s+DELAY", re.IGNORECASE),
    re.compile(r"xp_cmdshell", re.IGNORECASE),
    re.compile(r"--\s*$", re.MULTILINE),
    re.compile(r"'\s*OR\s+'", re.IGNORECASE),
]

# Backup SLA: max hours since last backup before alerting
_BACKUP_SLA_HOURS = 24


async def _score_and_alert() -> None:
    """One iteration of health scoring and alert generation."""
    async with AsyncSessionLocal() as db:
        try:
            # Find rows without a health score (newly inserted)
            result = await db.execute(
                select(DbMonitoring)
                .where(DbMonitoring.health_score == None)  # noqa: E711
                .where(DbMonitoring.metrics != None)  # noqa: E711
                .limit(500)
            )
            rows = result.scalars().all()

            if not rows:
                return

            scored = 0
            for row in rows:
                try:
                    metrics = json.loads(row.metrics) if row.metrics else {}
                except (json.JSONDecodeError, ValueError):
                    metrics = {}

                # Compute health score
                score = compute_health_score(metrics, row.is_reachable or False)
                await update_health_score(db, row.id, score)
                scored += 1

                # Generate critical health alert
                if score < 30 and row.is_reachable:
                    await create_alert(
                        db,
                        ip_address=row.ip_address,
                        instance_name=row.instance_name,
                        alert_type="health_critical",
                        severity="critical",
                        message=f"Health score critically low: {score:.1f}/100",
                        details={"health_score": score, "db_type": row.db_type},
                    )

                # SQL injection detection
                query_perf = metrics.get("query_performance", {})
                top_queries = query_perf.get("top_queries", [])
                for q in top_queries:
                    query_text = q.get("query", "") if isinstance(q, dict) else str(q)
                    for pattern in _SQLI_PATTERNS:
                        if pattern.search(query_text):
                            await create_alert(
                                db,
                                ip_address=row.ip_address,
                                instance_name=row.instance_name,
                                alert_type="sql_injection",
                                severity="critical",
                                message=f"Potential SQL injection detected: {pattern.pattern}",
                                details={"query_snippet": query_text[:500]},
                            )
                            break  # one alert per query is enough

                # Backup SLA check
                backup = metrics.get("backup", {})
                last_backup = backup.get("last_backup_time")
                if last_backup is None and row.is_reachable:
                    await create_alert(
                        db,
                        ip_address=row.ip_address,
                        instance_name=row.instance_name,
                        alert_type="backup_sla",
                        severity="warning",
                        message="No backup information available",
                        details={"db_type": row.db_type},
                    )

                # Connection pool exhaustion alert (>85% usage)
                conns = metrics.get("connections", {})
                usage_pct = conns.get("usage_pct", 0)
                if isinstance(usage_pct, (int, float)) and usage_pct > 85:
                    await create_alert(
                        db,
                        ip_address=row.ip_address,
                        instance_name=row.instance_name,
                        alert_type="connection_pool",
                        severity="critical" if usage_pct > 95 else "warning",
                        message=f"Connection pool at {usage_pct:.0f}% capacity ({conns.get('active', '?')}/{conns.get('max', '?')})",
                        details={"usage_pct": usage_pct, "active": conns.get("active"), "max": conns.get("max")},
                    )

                # Deadlock alert (any deadlocks detected)
                locks = metrics.get("locks", {})
                deadlocks = locks.get("deadlocks_since_start", 0)
                if isinstance(deadlocks, (int, float)) and deadlocks > 0:
                    active_locks = locks.get("active_locks", 0)
                    if active_locks > 50 or locks.get("row_lock_current_waits", 0) > 10:
                        await create_alert(
                            db,
                            ip_address=row.ip_address,
                            instance_name=row.instance_name,
                            alert_type="lock_contention",
                            severity="warning",
                            message=f"High lock contention: {active_locks} active locks, {locks.get('row_lock_current_waits', 0)} waiting",
                            details=locks,
                        )

                # Replication lag alert (>60 seconds)
                repl = metrics.get("replication", {})
                lag = repl.get("lag_seconds")
                if isinstance(lag, (int, float)) and lag > 60:
                    await create_alert(
                        db,
                        ip_address=row.ip_address,
                        instance_name=row.instance_name,
                        alert_type="replication_lag",
                        severity="critical" if lag > 300 else "warning",
                        message=f"Replication lag: {lag} seconds",
                        details={"lag_seconds": lag, "role": repl.get("role")},
                    )

            if scored:
                logger.info("DB health scorer: scored %d rows", scored)

        except Exception:
            logger.exception("Error in DB health scorer")


async def _scorer_loop() -> None:
    """Run the health scorer every 30 seconds."""
    logger.info("DB health scorer background task started")
    while True:
        try:
            await _score_and_alert()
        except Exception:
            logger.exception("Unhandled error in DB health scorer loop")
        await asyncio.sleep(30)


def start_db_health_scorer() -> asyncio.Task:
    """Create and return the background task."""
    global _task
    _task = asyncio.create_task(_scorer_loop())
    return _task


def stop_db_health_scorer() -> None:
    """Cancel the background task."""
    global _task
    if _task is not None:
        _task.cancel()
        logger.info("DB health scorer background task cancelled")
        _task = None
