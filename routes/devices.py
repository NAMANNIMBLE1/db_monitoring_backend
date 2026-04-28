import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db, get_nms_db
from models.device import MonitoredDevice
from models.registered_agent import RegisteredAgent
from services.auth_service import filter_by_ip_permissions

logger = logging.getLogger(__name__)

router = APIRouter(tags=["devices"])


class DeviceInfo(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    source: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceResolveResponse(BaseModel):
    hostname: str
    ip_address: str
    os_type: str
    agent_status: str
    agent_installed: bool


@router.get("/devices")
async def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Return paginated monitored devices from the local synced table."""
    try:
        base = select(MonitoredDevice).where(
            MonitoredDevice.is_active == True  # noqa: E712
        )

        count_result = await db.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        result = await db.execute(
            base.order_by(MonitoredDevice.hostname)
            .offset(offset)
            .limit(page_size)
        )
        devices = result.scalars().all()

        items = [
            DeviceInfo(
                hostname=d.hostname,
                ip_address=d.ip_address,
                source=d.source,
                is_active=d.is_active,
            )
            for d in devices
        ]

        # Filter by user permissions
        user_info = getattr(request.state, 'user', None)
        if user_info:
            filtered_items = filter_by_ip_permissions(
                items, user_info['allowed_ips'], user_info['is_admin']
            )
            # Recalculate pagination for filtered results
            filtered_total = len(filtered_items)
            filtered_total_pages = max(1, -(-filtered_total // page_size))
            filtered_start = (page - 1) * page_size
            filtered_end = filtered_start + page_size
            paginated_items = filtered_items[filtered_start:filtered_end]
            
            return {
                "items": paginated_items,
                "total": filtered_total,
                "page": page,
                "page_size": page_size,
                "total_pages": filtered_total_pages,
            }

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, -(-total // page_size)),
        }
    except Exception:
        logger.exception("Error querying monitored_device")
        raise HTTPException(status_code=500, detail="Internal server error")


# ── Device resolution helpers ────────────────────────────────────


async def _resolve_agent_info(db: AsyncSession, ip_address: str) -> dict:
    """Look up agent info for a given IP."""
    result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.ip_address == ip_address)
    )
    agent = result.scalars().first()

    if not agent:
        return {
            "os_type": "windows",
            "agent_status": "offline",
            "agent_installed": False,
        }

    threshold = datetime.utcnow() - timedelta(minutes=settings.STALE_AGENT_MINUTES)
    status = "online" if agent.last_seen and agent.last_seen >= threshold else "offline"

    return {
        "os_type": agent.os_type or "windows",
        "agent_status": status,
        "agent_installed": True,
    }


@router.get("/device/by-device-id/{device_id}")
async def resolve_by_device_id(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    nms_db: AsyncSession = Depends(get_nms_db),
    request: Request = None,
):
    """Resolve an NMS device_id to IP address + agent info."""
    # Try NMS database first
    try:
        result = await nms_db.execute(
            text("SELECT hostname, ip FROM devices WHERE device_id = :did LIMIT 1"),
            {"did": device_id},
        )
        nms_row = result.fetchone()
    except Exception as exc:
        logger.warning("Failed to query nms.devices: %s", exc)
        nms_row = None

    if nms_row:
        ip = nms_row[1]
        hostname = nms_row[0]
        
        # Check user permissions
        user_info = getattr(request.state, 'user', None)
        if user_info and not user_info['is_admin'] and ip not in user_info['allowed_ips']:
            raise HTTPException(status_code=403, detail="Access denied to this device")
        
        agent_info = await _resolve_agent_info(db, ip)
        return DeviceResolveResponse(
            hostname=hostname or ip,
            ip_address=ip,
            **agent_info,
        )

    # Fallback: check monitored_device by id
    result = await db.execute(
        select(MonitoredDevice).where(MonitoredDevice.id == device_id)
    )
    device = result.scalars().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Check user permissions
    user_info = getattr(request.state, 'user', None)
    if user_info and not user_info['is_admin'] and device.ip_address not in user_info['allowed_ips']:
        raise HTTPException(status_code=403, detail="Access denied to this device")

    agent_info = await _resolve_agent_info(db, device.ip_address)
    return DeviceResolveResponse(
        hostname=device.hostname,
        ip_address=device.ip_address,
        **agent_info,
    )


@router.get("/device/by-hostname/{hostname}")
async def resolve_by_hostname(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    nms_db: AsyncSession = Depends(get_nms_db),
    request: Request = None,
):
    """Resolve a hostname or IP to device info + agent status."""
    # Search monitored_device by hostname
    result = await db.execute(
        select(MonitoredDevice).where(MonitoredDevice.hostname == hostname)
    )
    device = result.scalars().first()

    # Try by IP if hostname match failed
    if not device:
        result = await db.execute(
            select(MonitoredDevice).where(MonitoredDevice.ip_address == hostname)
        )
        device = result.scalars().first()

    if device:
        # Check user permissions
        user_info = getattr(request.state, 'user', None)
        if user_info and not user_info['is_admin'] and device.ip_address not in user_info['allowed_ips']:
            raise HTTPException(status_code=403, detail="Access denied to this device")
            
        agent_info = await _resolve_agent_info(db, device.ip_address)
        return DeviceResolveResponse(
            hostname=device.hostname,
            ip_address=device.ip_address,
            **agent_info,
        )

    # Try registered_agent directly
    result = await db.execute(
        select(RegisteredAgent).where(
            (RegisteredAgent.hostname == hostname) |
            (RegisteredAgent.ip_address == hostname)
        )
    )
    agent = result.scalars().first()
    if agent:
        # Check user permissions
        user_info = getattr(request.state, 'user', None)
        if user_info and not user_info['is_admin'] and agent.ip_address not in user_info['allowed_ips']:
            raise HTTPException(status_code=403, detail="Access denied to this device")
            
        threshold = datetime.utcnow() - timedelta(minutes=settings.STALE_AGENT_MINUTES)
        status = "online" if agent.last_seen and agent.last_seen >= threshold else "offline"
        return DeviceResolveResponse(
            hostname=agent.hostname,
            ip_address=agent.ip_address,
            os_type=agent.os_type or "windows",
            agent_status=status,
            agent_installed=True,
        )

    # Try NMS database
    try:
        result = await nms_db.execute(
            text("SELECT hostname, ip FROM devices WHERE hostname = :h OR ip = :h LIMIT 1"),
            {"h": hostname},
        )
        nms_row = result.fetchone()
        if nms_row:
            ip = nms_row[1]
            # Check user permissions
            user_info = getattr(request.state, 'user', None)
            if user_info and not user_info['is_admin'] and ip not in user_info['allowed_ips']:
                raise HTTPException(status_code=403, detail="Access denied to this device")
                
            agent_info = await _resolve_agent_info(db, ip)
            return DeviceResolveResponse(
                hostname=nms_row[0] or ip,
                ip_address=ip,
                **agent_info,
            )
    except Exception as exc:
        logger.warning("Failed to query nms.devices: %s", exc)

    raise HTTPException(status_code=404, detail="Device not found")
