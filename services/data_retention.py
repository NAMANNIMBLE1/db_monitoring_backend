import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, text

from config import settings
from utils.timezone import now_ist
from database import AsyncSessionLocal
from models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None

PURGE_INTERVAL_SECONDS = 21600  # 6 hours


async def _get_retention_days() -> int:
    """Read retention days from DB, falling back to config default."""
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "data_retention_days")
        )
        setting = row.scalar_one_or_none()
        if setting is not None:
            return int(setting.value)
    return settings.DATA_RETENTION_DAYS


async def _purge_old_data() -> None:
    """
    One iteration of the data retention purge.

    Deletes rows older than DATA_RETENTION_DAYS from
    windows_port_monitoring, linux_port_monitoring, and agent_heartbeat.
    """
    retention_days = await _get_retention_days()

    if retention_days <= 0:
        return

    cutoff = now_ist() - timedelta(days=retention_days)
    cutoff_naive = cutoff.replace(tzinfo=None)

    tables = [
        "windows_port_monitoring",
        "linux_port_monitoring",
        "unified_monitoring",
        "db_monitoring",
        "agent_heartbeat",
    ]

    # Also purge old resolved alerts (use created_at column)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    "DELETE FROM db_monitoring_alert "
                    "WHERE created_at < :cutoff AND is_resolved = 1 LIMIT 5000"
                ),
                {"cutoff": cutoff_naive},
            )
            await db.commit()
            if result.rowcount:
                logger.info("Purged %d old resolved DB alerts", result.rowcount)
    except Exception:
        logger.exception("Error purging db_monitoring_alert")

    batch_limit = 5000  # delete in chunks to avoid long table locks

    try:
        total_deleted = 0
        for table in tables:
            table_deleted = 0
            while True:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        text(f"DELETE FROM {table} WHERE timestamp < :cutoff LIMIT :lim"),
                        {"cutoff": cutoff_naive, "lim": batch_limit},
                    )
                    await db.commit()
                    deleted = result.rowcount
                    table_deleted += deleted
                    if deleted < batch_limit:
                        break
                # Yield control between batches so API requests are served
                await asyncio.sleep(0.1)
            total_deleted += table_deleted
            if table_deleted > 0:
                logger.info(
                    "Data retention: purged %d rows from %s (cutoff: %s)",
                    table_deleted, table, cutoff_naive,
                )

        if total_deleted > 0:
            logger.info("Data retention: total %d rows purged", total_deleted)
        else:
            logger.debug("Data retention: no rows to purge")
    except Exception:
        logger.exception("Error during data retention purge")


async def _data_retention_loop() -> None:
    """Run the data retention purge in an infinite loop every 6 hours."""
    logger.info("Data retention background task started (interval=%ds, default_retention=%d days, reads DB override each cycle)",
                PURGE_INTERVAL_SECONDS, settings.DATA_RETENTION_DAYS)
    while True:
        try:
            await _purge_old_data()
        except Exception:
            logger.exception("Unhandled error in data retention loop")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


def start_data_retention() -> asyncio.Task:
    """Create and return the background task. Call on FastAPI startup."""
    global _task
    _task = asyncio.create_task(_data_retention_loop())
    return _task


def stop_data_retention() -> None:
    """Cancel the background task. Call on FastAPI shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        logger.info("Data retention background task cancelled")
        _task = None
