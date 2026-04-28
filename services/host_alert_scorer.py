"""Host-level alert generator.

Runs every 60 s and produces alerts on top of data already collected by
agents (no agent changes required).  Alert kinds:

* ``service_down`` — service flipped UP→DOWN between the two most recent
  unified_monitoring rows for an IP.
* ``disk_critical`` / ``memory_critical`` / ``cpu_sustained_high`` —
  current sample exceeds configured threshold (CPU requires 3 consecutive
  samples above threshold to avoid transient spikes).
* ``disk_forecast`` — linear regression on ≥ 30 days of disk samples
  predicts the warn threshold will be crossed within ``forecast_days``.
* ``agent_offline`` — registered agent hasn't sent a heartbeat for
  longer than STALE_AGENT_MINUTES.

All alerts are de-duplicated: if an unresolved alert with the same
(ip_address, alert_type, details.service_name) already exists, no new row
is created.  Conditions that have cleared auto-resolve the prior alert.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, func, select, update

from config import settings
from database import AsyncSessionLocal
from models.alert_threshold import AlertThreshold
from models.db_monitoring import DbMonitoringAlert
from models.registered_agent import RegisteredAgent
from models.unified_monitoring import UnifiedMonitoring
from services.disk_forecaster import forecast_breach
from utils.timezone import now_ist

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None

# How long between iterations
_LOOP_SECONDS = 60
# History window used to decide whether we have enough data to forecast.
_FORECAST_MIN_DAYS = 30
# Consecutive samples required above the CPU threshold before firing.
_CPU_SUSTAIN_SAMPLES = 3
# Cap the forecast sampling so we don't load gigabytes.
_FORECAST_MAX_ROWS = 3000

_DEFAULT_THRESHOLDS = {
    "disk":   {"warn": 80.0, "critical": 90.0, "forecast_days": 15},
    "cpu":    {"warn": 85.0, "critical": 95.0, "forecast_days": 15},
    "memory": {"warn": 85.0, "critical": 95.0, "forecast_days": 15},
}


# ── helpers ────────────────────────────────────────────────────────────


async def _load_thresholds(db) -> dict[str, dict[str, float]]:
    result = await db.execute(select(AlertThreshold))
    rows = result.scalars().all()
    out = {k: dict(v) for k, v in _DEFAULT_THRESHOLDS.items()}
    for r in rows:
        out[r.metric] = {
            "warn": float(r.warn_threshold),
            "critical": float(r.critical_threshold),
            "forecast_days": int(r.forecast_days),
        }
    return out


async def _existing_alert(
    db,
    ip_address: str,
    alert_type: str,
    service_name: Optional[str] = None,
) -> Optional[DbMonitoringAlert]:
    """Return the latest unresolved alert matching these identifiers, if any."""
    q = select(DbMonitoringAlert).where(
        and_(
            DbMonitoringAlert.ip_address == ip_address,
            DbMonitoringAlert.alert_type == alert_type,
            DbMonitoringAlert.is_resolved == False,  # noqa: E712
        )
    ).order_by(DbMonitoringAlert.created_at.desc())
    result = await db.execute(q)
    for row in result.scalars().all():
        if service_name is None:
            return row
        try:
            details = json.loads(row.details) if row.details else {}
        except (json.JSONDecodeError, ValueError):
            details = {}
        if details.get("service_name") == service_name:
            return row
    return None


async def _create_alert(
    db,
    *,
    ip_address: str,
    alert_type: str,
    severity: str,
    message: str,
    details: Optional[dict] = None,
) -> None:
    alert = DbMonitoringAlert(
        ip_address=ip_address,
        instance_name=None,
        alert_type=alert_type,
        severity=severity,
        message=message,
        details=json.dumps(details) if details else None,
        created_at=now_ist(),
    )
    db.add(alert)


async def _resolve_alert(db, alert: DbMonitoringAlert) -> None:
    await db.execute(
        update(DbMonitoringAlert)
        .where(DbMonitoringAlert.id == alert.id)
        .values(is_resolved=True, resolved_at=now_ist())
    )


async def _last_two_rows(db, ip: str) -> list[UnifiedMonitoring]:
    q = (
        select(UnifiedMonitoring)
        .where(UnifiedMonitoring.ip_address == ip)
        .order_by(UnifiedMonitoring.timestamp.desc())
        .limit(2)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


async def _recent_rows(db, ip: str, limit: int) -> list[UnifiedMonitoring]:
    q = (
        select(UnifiedMonitoring)
        .where(UnifiedMonitoring.ip_address == ip)
        .order_by(UnifiedMonitoring.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


def _parse_services(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ── per-IP checks ───────────────────────────────────────────────────────


async def _check_services(db, ip: str, rows: list[UnifiedMonitoring]) -> None:
    """Detect service UP→DOWN transitions and auto-resolve on recovery."""
    if not rows:
        return
    current = _parse_services(rows[0].services)
    previous = _parse_services(rows[1].services) if len(rows) > 1 else {}

    for name, state in current.items():
        state_str = str(state).upper() if state is not None else "UNKNOWN"
        prev_state = str(previous.get(name, "")).upper()
        existing = await _existing_alert(db, ip, "service_down", service_name=name)

        if state_str == "DOWN":
            # Fire only on transition (prev UP) or first time we see DOWN.
            if existing is None and prev_state != "DOWN":
                await _create_alert(
                    db,
                    ip_address=ip,
                    alert_type="service_down",
                    severity="critical",
                    message=f"Service '{name}' is DOWN",
                    details={
                        "service_name": name,
                        "previous_state": prev_state or "UNKNOWN",
                        "current_state": "DOWN",
                        "observed_at": rows[0].timestamp.isoformat(),
                    },
                )
        elif state_str == "UP" and existing is not None:
            await _resolve_alert(db, existing)


def _latest_value(rows: list[UnifiedMonitoring], attr: str) -> Optional[float]:
    for r in rows:
        v = getattr(r, attr, None)
        if v is not None:
            return float(v)
    return None


async def _check_current_thresholds(
    db,
    ip: str,
    rows: list[UnifiedMonitoring],
    thresholds: dict,
) -> None:
    """Alert on current disk/memory/cpu above configured levels."""
    if not rows:
        return

    disk = _latest_value(rows, "disk_usage")
    memory = _latest_value(rows, "memory_usage")

    # disk_critical — any current reading above threshold is critical.
    disk_cfg = thresholds["disk"]
    existing_disk = await _existing_alert(db, ip, "disk_critical")
    if disk is not None and disk >= disk_cfg["warn"]:
        severity = "critical" if disk >= disk_cfg["critical"] else "warning"
        if existing_disk is None:
            await _create_alert(
                db,
                ip_address=ip,
                alert_type="disk_critical",
                severity=severity,
                message=f"Disk usage at {disk:.1f}% (threshold {disk_cfg['warn']:.0f}%)",
                details={
                    "current_pct": disk,
                    "warn_threshold": disk_cfg["warn"],
                    "critical_threshold": disk_cfg["critical"],
                },
            )
    elif existing_disk is not None:
        await _resolve_alert(db, existing_disk)

    # memory_critical
    mem_cfg = thresholds["memory"]
    existing_mem = await _existing_alert(db, ip, "memory_critical")
    if memory is not None and memory >= mem_cfg["warn"]:
        severity = "critical" if memory >= mem_cfg["critical"] else "warning"
        if existing_mem is None:
            await _create_alert(
                db,
                ip_address=ip,
                alert_type="memory_critical",
                severity=severity,
                message=f"Memory usage at {memory:.1f}% (threshold {mem_cfg['warn']:.0f}%)",
                details={
                    "current_pct": memory,
                    "warn_threshold": mem_cfg["warn"],
                    "critical_threshold": mem_cfg["critical"],
                },
            )
    elif existing_mem is not None:
        await _resolve_alert(db, existing_mem)

    # cpu_sustained_high — last 3 samples must all exceed the warn threshold.
    cpu_cfg = thresholds["cpu"]
    last_cpus = [float(r.cpu_usage) for r in rows[:_CPU_SUSTAIN_SAMPLES] if r.cpu_usage is not None]
    existing_cpu = await _existing_alert(db, ip, "cpu_sustained_high")
    is_sustained = (
        len(last_cpus) >= _CPU_SUSTAIN_SAMPLES
        and all(c >= cpu_cfg["warn"] for c in last_cpus)
    )
    if is_sustained:
        avg = sum(last_cpus) / len(last_cpus)
        severity = "critical" if avg >= cpu_cfg["critical"] else "warning"
        if existing_cpu is None:
            await _create_alert(
                db,
                ip_address=ip,
                alert_type="cpu_sustained_high",
                severity=severity,
                message=f"CPU sustained at {avg:.1f}% over {len(last_cpus)} samples (threshold {cpu_cfg['warn']:.0f}%)",
                details={
                    "avg_pct": round(avg, 2),
                    "samples": last_cpus,
                    "warn_threshold": cpu_cfg["warn"],
                    "critical_threshold": cpu_cfg["critical"],
                },
            )
    elif existing_cpu is not None and last_cpus and last_cpus[0] < cpu_cfg["warn"]:
        await _resolve_alert(db, existing_cpu)


async def _check_disk_forecast(db, ip: str, thresholds: dict) -> None:
    """Project disk usage forward and alert on expected breach."""
    disk_cfg = thresholds["disk"]

    # Need at least 30 days of history.
    first_ts_row = await db.execute(
        select(func.min(UnifiedMonitoring.timestamp))
        .where(
            and_(
                UnifiedMonitoring.ip_address == ip,
                UnifiedMonitoring.disk_usage.is_not(None),
            )
        )
    )
    first_ts = first_ts_row.scalar()
    if first_ts is None:
        return
    now = now_ist().replace(tzinfo=None) if now_ist().tzinfo else now_ist()
    age_days = (now - first_ts).total_seconds() / 86400.0
    if age_days < _FORECAST_MIN_DAYS:
        return

    # Pull the last 30 days of samples, newest-first, capped.
    cutoff = now - timedelta(days=_FORECAST_MIN_DAYS)
    q = (
        select(UnifiedMonitoring.timestamp, UnifiedMonitoring.disk_usage)
        .where(
            and_(
                UnifiedMonitoring.ip_address == ip,
                UnifiedMonitoring.timestamp >= cutoff,
                UnifiedMonitoring.disk_usage.is_not(None),
            )
        )
        .order_by(UnifiedMonitoring.timestamp.asc())
        .limit(_FORECAST_MAX_ROWS)
    )
    result = await db.execute(q)
    series = [(ts, float(pct)) for ts, pct in result.all()]

    existing = await _existing_alert(db, ip, "disk_forecast")
    forecast = forecast_breach(
        series=series,
        threshold=disk_cfg["warn"],
        horizon_days=disk_cfg["forecast_days"],
    )

    if forecast is None:
        if existing is not None:
            # Trend no longer predicts a breach — clear it.
            await _resolve_alert(db, existing)
        return

    if existing is None:
        await _create_alert(
            db,
            ip_address=ip,
            alert_type="disk_forecast",
            severity="warning",
            message=(
                f"Disk projected to reach {disk_cfg['warn']:.0f}% in "
                f"~{forecast['days_to_breach']} days"
            ),
            details=forecast,
        )


async def _check_agent_offline(
    db,
    agent: RegisteredAgent,
    threshold_minutes: int,
) -> None:
    existing = await _existing_alert(db, agent.ip_address, "agent_offline")
    # last_seen is stored as naive IST.  Compare against naive now.
    now_naive = now_ist().replace(tzinfo=None)
    offline = (
        agent.last_seen is not None
        and (now_naive - agent.last_seen) > timedelta(minutes=threshold_minutes)
    )
    if offline and existing is None:
        mins = int((now_naive - agent.last_seen).total_seconds() / 60)
        await _create_alert(
            db,
            ip_address=agent.ip_address,
            alert_type="agent_offline",
            severity="critical",
            message=f"Agent offline — no heartbeat for {mins} min",
            details={
                "hostname": agent.hostname,
                "last_seen": agent.last_seen.isoformat(),
                "minutes_offline": mins,
            },
        )
    elif not offline and existing is not None:
        await _resolve_alert(db, existing)


# ── loop ────────────────────────────────────────────────────────────────


async def _run_once() -> None:
    async with AsyncSessionLocal() as db:
        try:
            thresholds = await _load_thresholds(db)

            agents_result = await db.execute(
                select(RegisteredAgent).where(RegisteredAgent.is_blocked == False)  # noqa: E712
            )
            agents = agents_result.scalars().all()

            for agent in agents:
                ip = agent.ip_address
                try:
                    await _check_agent_offline(db, agent, settings.STALE_AGENT_MINUTES)

                    rows = await _recent_rows(db, ip, _CPU_SUSTAIN_SAMPLES)
                    if not rows:
                        continue

                    last_two = rows[:2]
                    await _check_services(db, ip, last_two)
                    await _check_current_thresholds(db, ip, rows, thresholds)
                    await _check_disk_forecast(db, ip, thresholds)
                except Exception:
                    logger.exception("Host alert check failed for %s", ip)

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Error in host alert scorer iteration")


async def _loop() -> None:
    logger.info("Host alert scorer background task started")
    while True:
        try:
            await _run_once()
        except Exception:
            logger.exception("Unhandled error in host alert scorer loop")
        await asyncio.sleep(_LOOP_SECONDS)


def start_host_alert_scorer() -> asyncio.Task:
    global _task
    _task = asyncio.create_task(_loop())
    return _task


def stop_host_alert_scorer() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        logger.info("Host alert scorer background task cancelled")
        _task = None


# ── seeding ─────────────────────────────────────────────────────────────


async def seed_default_thresholds(db) -> None:
    """Insert default rows if the alert_threshold table is empty."""
    existing = await db.execute(select(AlertThreshold.metric))
    have = {row[0] for row in existing.all()}
    for metric, cfg in _DEFAULT_THRESHOLDS.items():
        if metric in have:
            continue
        db.add(AlertThreshold(
            metric=metric,
            warn_threshold=cfg["warn"],
            critical_threshold=cfg["critical"],
            forecast_days=cfg["forecast_days"],
            updated_at=now_ist(),
        ))
    await db.commit()


async def ensure_alert_instance_nullable(db) -> None:
    """Migrate existing db_monitoring_alert.instance_name to NULLable.

    Safe to call on every startup — ignores errors if already nullable.
    """
    from sqlalchemy import text as _text
    try:
        await db.execute(_text(
            "ALTER TABLE db_monitoring_alert "
            "MODIFY COLUMN instance_name VARCHAR(128) NULL"
        ))
        await db.commit()
    except Exception:
        await db.rollback()
        # Already nullable or table not yet created — ignore.
