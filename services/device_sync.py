"""Sync devices from nms.devices into port_monitoring.monitored_device.

- Runs once on backend startup
- Runs automatically once every 24 hours
- Never writes to the nms database
- Agents not in NMS are added with source='agent'
"""

import asyncio
import logging
from utils.timezone import now_ist

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, NmsAsyncSessionLocal
from models.device import MonitoredDevice

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None

SYNC_INTERVAL_SECONDS = 86400  # 24 hours


async def sync_devices_from_nms() -> int:
    """Pull hostname + ip_address from nms.devices and upsert into monitored_device.

    Returns the number of new devices inserted.
    """
    inserted = 0

    # 1. Read from nms DB
    nms_devices = []
    try:
        async with NmsAsyncSessionLocal() as nms_db:
            try:
                result = await nms_db.execute(
                    text("SELECT DISTINCT hostname, ip_address FROM devices")
                )
                nms_devices = result.fetchall()
            except Exception:
                # ip_address column might not exist
                await nms_db.rollback()
                try:
                    result = await nms_db.execute(
                        text("SELECT DISTINCT hostname FROM devices")
                    )
                    nms_devices = [(row[0], None) for row in result.fetchall()]
                except Exception:
                    logger.warning("Could not read nms.devices -- skipping sync")
                    return 0
    except Exception:
        logger.warning("Could not connect to nms DB -- skipping sync")
        return 0

    if not nms_devices:
        logger.info("No devices found in nms.devices")
        return 0

    # 2. Upsert into port_monitoring.monitored_device
    async with AsyncSessionLocal() as db:
        for row in nms_devices:
            hostname = row[0]
            ip_address = row[1] if len(row) > 1 else None

            if not hostname and not ip_address:
                continue

            # Use ip_address as the unique key; fall back to hostname if no IP
            lookup_ip = ip_address or hostname

            try:
                result = await db.execute(
                    select(MonitoredDevice).where(
                        MonitoredDevice.ip_address == lookup_ip
                    )
                )
                existing = result.scalars().first()

                if existing is None:
                    device = MonitoredDevice(
                        hostname=hostname or lookup_ip,
                        ip_address=lookup_ip,
                        source="nms",
                        is_active=True,
                        synced_at=now_ist(),
                        created_at=now_ist(),
                    )
                    db.add(device)
                    inserted += 1
                else:
                    # Update hostname and sync timestamp
                    existing.hostname = hostname or existing.hostname
                    existing.synced_at = now_ist()
                    existing.is_active = True
            except Exception:
                logger.exception("Error upserting device %s", hostname)

        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to commit device sync")
            return 0

    logger.info(
        "Device sync complete: %d from nms, %d new", len(nms_devices), inserted
    )
    return inserted


async def ensure_agent_device(ip_address: str, hostname: str) -> None:
    """Ensure a monitored_device row exists for an agent that registered.

    Called during agent registration. If the IP is not already in the table
    (neither from nms sync nor from a previous agent registration), insert
    it with source='agent'.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MonitoredDevice).where(
                MonitoredDevice.ip_address == ip_address
            )
        )
        existing = result.scalars().first()

        if existing is None:
            device = MonitoredDevice(
                hostname=hostname,
                ip_address=ip_address,
                source="agent",
                is_active=True,
                synced_at=now_ist(),
                created_at=now_ist(),
            )
            db.add(device)
            await db.commit()
            logger.info(
                "Added agent-registered device: %s (%s)", hostname, ip_address
            )


# ── Background loop ──


async def _sync_loop() -> None:
    """Run device sync on startup then every 24 hours."""
    # Initial sync on startup
    try:
        await sync_devices_from_nms()
    except Exception:
        logger.exception("Initial device sync failed")

    # Periodic sync
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        try:
            await sync_devices_from_nms()
        except Exception:
            logger.exception("Periodic device sync failed")


def start_device_sync() -> asyncio.Task:
    """Start the background sync task. Call on FastAPI startup."""
    global _task
    _task = asyncio.create_task(_sync_loop())
    return _task


def stop_device_sync() -> None:
    """Cancel the background sync task. Call on FastAPI shutdown."""
    global _task
    if _task is not None:
        _task.cancel()
        logger.info("Device sync background task cancelled")
        _task = None
