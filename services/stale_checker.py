import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, text

from config import settings
from utils.timezone import now_ist
from database import AsyncSessionLocal
from models.registered_agent import RegisteredAgent

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def _round_to_5min(dt: datetime) -> datetime:
    """Round a datetime DOWN to the nearest 5-minute boundary."""
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


async def _batch_insert_null_rows(
    db, table: str, ips: list[str], timestamp: datetime
) -> int:
    """Bulk-insert NULL monitoring rows for a list of IPs in one statement."""
    if not ips:
        return 0
    # Build VALUES clause: (:ip0, :ts), (:ip1, :ts), ...
    placeholders = ", ".join(f"(:ip{i}, :ts)" for i in range(len(ips)))
    stmt = text(
        f"INSERT INTO {table} (ip_address, timestamp) "
        f"VALUES {placeholders} "
        f"ON DUPLICATE KEY UPDATE id=id"
    )
    params = {"ts": timestamp}
    for i, ip in enumerate(ips):
        params[f"ip{i}"] = ip
    result = await db.execute(stmt, params)
    await db.commit()
    return result.rowcount


async def _check_stale_agents() -> None:
    """
    One iteration of the stale-agent check.

    For every agent whose last_seen is older than STALE_AGENT_MINUTES, insert a
    NULL monitoring row for the current 5-minute window.  All inserts are batched
    into two bulk statements (one per OS type) to avoid holding connections.
    """
    threshold = now_ist() - timedelta(minutes=settings.STALE_AGENT_MINUTES)
    window_ts = _round_to_5min(now_ist())

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(RegisteredAgent).where(
                    RegisteredAgent.last_seen < threshold,
                    RegisteredAgent.is_blocked == False,
                )
            )
            stale_agents = result.scalars().all()

            if not stale_agents:
                return

            # Split by OS type
            win_ips = [a.ip_address for a in stale_agents if a.os_type != "linux"]
            linux_ips = [a.ip_address for a in stale_agents if a.os_type == "linux"]
            all_ips = [a.ip_address for a in stale_agents]

            win_inserted = await _batch_insert_null_rows(
                db, "windows_port_monitoring", win_ips, window_ts
            )
            linux_inserted = await _batch_insert_null_rows(
                db, "linux_port_monitoring", linux_ips, window_ts
            )
            # Also insert into unified_monitoring for agents using the new system
            unified_inserted = await _batch_insert_null_rows(
                db, "unified_monitoring", all_ips, window_ts
            )

            total = win_inserted + linux_inserted + unified_inserted
            if total:
                logger.info(
                    "Stale check: %d agent(s) stale — inserted %d win + %d linux + %d unified NULL rows at %s",
                    len(stale_agents), win_inserted, linux_inserted, unified_inserted, window_ts,
                )
        except Exception:
            logger.exception("Error during stale agent check")


async def _stale_checker_loop() -> None:
    """Run the stale-agent check in an infinite loop every 60 seconds."""
    logger.info("Stale checker background task started")
    while True:
        try:
            await _check_stale_agents()
        except Exception:
            logger.exception("Unhandled error in stale checker loop")
        await asyncio.sleep(60)


def start_stale_checker() -> asyncio.Task:
    """Create and return the background task. Call on FastAPI startup."""
    global _task
    _task = asyncio.create_task(_stale_checker_loop())
    return _task


def stop_stale_checker() -> None:
    """Cancel the background task. Call on FastAPI shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        logger.info("Stale checker background task cancelled")
        _task = None
