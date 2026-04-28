import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.device import MonitoredDevice
from models.registered_agent import RegisteredAgent
from models.system_setting import SystemSetting
from services.agent_service import register_agent, set_agent_blocked
from services.device_sync import ensure_agent_device

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


# ── Request / Response schemas ──


class RegisterRequest(BaseModel):
    agent_id: str = Field(..., max_length=64)
    ip_address: str = Field(..., max_length=45)
    hostname: str = Field(..., max_length=255)
    os_type: str = Field(default="windows", pattern="^(windows|linux)$")
    agent_version: str = Field(default="1.0.0", max_length=32)
    master_key: str


class RegisterResponse(BaseModel):
    status: str
    auth_token: str
    agent_id: str


class AgentInfo(BaseModel):
    agent_id: str
    ip_address: str
    hostname: str
    os_type: str
    agent_version: str
    registered_at: Optional[str] = None
    last_seen: Optional[str] = None
    status: str  # "online" or "offline"
    is_blocked: bool = False
    service_pack_id: Optional[int] = None
    service_pack_name: Optional[str] = None


class RetentionUpdate(BaseModel):
    days: int = Field(..., ge=0)


async def get_retention_days(db: AsyncSession) -> int:
    """Read data_retention_days from DB, falling back to config."""
    row = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "data_retention_days")
    )
    setting = row.scalar_one_or_none()
    if setting is not None:
        return int(setting.value)
    return settings.DATA_RETENTION_DAYS


# ── Endpoints ──


@router.post("/register", response_model=RegisterResponse)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new agent or refresh credentials for an existing agent.

    The caller must provide the correct master_key.
    """
    try:
        result = await register_agent(
            db=db,
            agent_id=body.agent_id,
            ip_address=body.ip_address,
            hostname=body.hostname,
            os_type=body.os_type,
            agent_version=body.agent_version,
            master_key=body.master_key,
            expected_master_key=settings.MASTER_KEY,
        )

        # Ensure this agent's IP is in monitored_device
        # (adds with source='agent' if not already synced from NMS)
        try:
            await ensure_agent_device(body.ip_address, body.hostname)
        except Exception:
            logger.warning("Failed to ensure device entry for %s", body.ip_address)

        return RegisterResponse(**result)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid master key")
    except Exception:
        logger.exception("Registration error")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/agents")
async def list_agents(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Return paginated registered agents with online/offline status.
    """
    try:
        # Total count
        count_result = await db.execute(
            select(func.count()).select_from(RegisteredAgent)
        )
        total = count_result.scalar() or 0

        # Paginated query
        offset = (page - 1) * page_size
        result = await db.execute(
            select(RegisteredAgent)
            .order_by(RegisteredAgent.hostname)
            .offset(offset)
            .limit(page_size)
        )
        agents = result.scalars().all()

        threshold = datetime.utcnow() - timedelta(
            minutes=settings.STALE_AGENT_MINUTES
        )

        # Build pack name lookup
        from models.service_pack import ServicePack
        pack_result = await db.execute(select(ServicePack))
        pack_map = {p.id: p.name for p in pack_result.scalars().all()}

        items = []
        for a in agents:
            status = "online" if a.last_seen and a.last_seen >= threshold else "offline"
            pack_id = getattr(a, "service_pack_id", None)
            items.append(
                AgentInfo(
                    agent_id=a.agent_id,
                    ip_address=a.ip_address,
                    hostname=a.hostname,
                    os_type=a.os_type,
                    agent_version=a.agent_version,
                    registered_at=a.registered_at.isoformat() if a.registered_at else None,
                    last_seen=a.last_seen.isoformat() if a.last_seen else None,
                    status=status,
                    is_blocked=a.is_blocked,
                    service_pack_id=pack_id,
                    service_pack_name=pack_map.get(pack_id) if pack_id else None,
                )
            )

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, -(-total // page_size)),  # ceil division
        }
    except Exception:
        logger.exception("Error listing agents")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/agents/{agent_id}/block")
async def block_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Block an agent — its heartbeats will be rejected with 403."""
    found = await set_agent_blocked(db, agent_id, blocked=True)
    if not found:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "blocked", "agent_id": agent_id}


@router.patch("/agents/{agent_id}/unblock")
async def unblock_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Unblock an agent — heartbeats will be accepted again."""
    found = await set_agent_blocked(db, agent_id, blocked=False)
    if not found:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "unblocked", "agent_id": agent_id}


# ── Per-Agent Service Configuration ──────────────────────────────


class ServiceOverrideItem(BaseModel):
    service_key: str
    is_enabled: bool


class ServiceOverrideRequest(BaseModel):
    overrides: List[ServiceOverrideItem]


@router.get("/agents/{agent_id}/services")
async def get_agent_services(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Return the service list for a specific agent with enabled/disabled flags."""
    # Look up agent to get OS type
    agent_result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.agent_id == agent_id)
    )
    agent = agent_result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Determine pack info
    pack_id = getattr(agent, "service_pack_id", None)
    pack_name = None
    pack_keys = None

    if pack_id:
        from models.service_pack import ServicePack, ServicePackItem
        pack_result = await db.execute(
            select(ServicePack).where(ServicePack.id == pack_id)
        )
        pack = pack_result.scalars().first()
        if pack:
            pack_name = pack.name
            # Get pack service keys
            pk_result = await db.execute(
                select(ServicePackItem.service_key).where(ServicePackItem.pack_id == pack_id)
            )
            pack_keys = {r[0] for r in pk_result.fetchall()}

    # Get services: if pack assigned, base = pack services; else all active
    from models.service_definition import ServiceDefinition
    svc_result = await db.execute(
        select(ServiceDefinition).where(
            ServiceDefinition.is_active == True,
            (ServiceDefinition.os_type == agent.os_type) | (ServiceDefinition.os_type == "both"),
        ).order_by(ServiceDefinition.category, ServiceDefinition.display_name)
    )
    all_services = svc_result.scalars().all()

    if pack_keys is not None:
        base_services = [s for s in all_services if s.key in pack_keys]
    else:
        base_services = all_services

    # Get overrides for this agent
    from models.agent_service_override import AgentServiceOverride
    override_result = await db.execute(
        select(AgentServiceOverride).where(AgentServiceOverride.agent_id == agent_id)
    )
    overrides = {o.service_key: o.is_enabled for o in override_result.scalars().all()}

    has_overrides = len(overrides) > 0

    items = []
    for svc in base_services:
        items.append({
            "key": svc.key,
            "display_name": svc.display_name,
            "category": svc.category,
            "os_type": svc.os_type,
            "check_type": svc.check_type,
            "is_enabled": overrides.get(svc.key, True),
        })

    return {
        "agent_id": agent_id,
        "hostname": agent.hostname,
        "os_type": agent.os_type,
        "has_overrides": has_overrides,
        "pack_id": pack_id,
        "pack_name": pack_name,
        "services": items,
    }


@router.put("/agents/{agent_id}/services")
async def set_agent_services(
    agent_id: str,
    body: ServiceOverrideRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set per-agent service overrides. Replaces all existing overrides."""
    # Verify agent exists
    agent_result = await db.execute(
        select(RegisteredAgent).where(RegisteredAgent.agent_id == agent_id)
    )
    if not agent_result.scalars().first():
        raise HTTPException(status_code=404, detail="Agent not found")

    from models.agent_service_override import AgentServiceOverride
    from sqlalchemy import delete

    # Delete all existing overrides for this agent
    await db.execute(
        delete(AgentServiceOverride).where(AgentServiceOverride.agent_id == agent_id)
    )

    # Insert new overrides (store all entries so we know the agent has been configured)
    inserted = 0
    for item in body.overrides:
        override = AgentServiceOverride(
            agent_id=agent_id,
            service_key=item.service_key,
            is_enabled=item.is_enabled,
        )
        db.add(override)
        inserted += 1

    await db.commit()
    logger.info("Service overrides saved for agent %s: %d entries", agent_id, inserted)
    return {"status": "ok", "agent_id": agent_id, "overrides_saved": inserted}


@router.delete("/agents/{agent_id}/services")
async def reset_agent_services(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Reset agent to default services (remove all overrides)."""
    from models.agent_service_override import AgentServiceOverride
    from sqlalchemy import delete

    await db.execute(
        delete(AgentServiceOverride).where(AgentServiceOverride.agent_id == agent_id)
    )
    await db.commit()
    logger.info("Service overrides reset to defaults for agent %s", agent_id)
    return {"status": "reset", "agent_id": agent_id}


@router.get("/settings/stats")
async def settings_stats(db: AsyncSession = Depends(get_db)):
    """Return dashboard statistics for the Settings tab."""
    try:
        threshold = datetime.utcnow() - timedelta(
            minutes=settings.STALE_AGENT_MINUTES
        )

        # Total active devices
        dev_result = await db.execute(
            select(func.count()).select_from(MonitoredDevice).where(
                MonitoredDevice.is_active == True
            )
        )
        total_devices = dev_result.scalar() or 0

        # Agent counts
        all_agents = await db.execute(select(RegisteredAgent))
        agents = all_agents.scalars().all()

        total_agents = len(agents)
        windows_agents = sum(1 for a in agents if a.os_type == "windows")
        linux_agents = sum(1 for a in agents if a.os_type == "linux")
        online_agents = sum(
            1 for a in agents
            if a.last_seen and a.last_seen >= threshold and not a.is_blocked
        )
        blocked_agents = sum(1 for a in agents if a.is_blocked)

        retention_days = await get_retention_days(db)

        return {
            "total_devices": total_devices,
            "total_agents": total_agents,
            "windows_agents": windows_agents,
            "linux_agents": linux_agents,
            "online_agents": online_agents,
            "blocked_agents": blocked_agents,
            "data_retention_days": retention_days,
        }
    except Exception:
        logger.exception("Error fetching settings stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/settings/retention")
async def update_retention(body: RetentionUpdate, db: AsyncSession = Depends(get_db)):
    """Set data retention days. 0 = keep forever."""
    try:
        row = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "data_retention_days")
        )
        setting = row.scalar_one_or_none()
        if setting is not None:
            setting.value = str(body.days)
        else:
            db.add(SystemSetting(key="data_retention_days", value=str(body.days)))
        await db.commit()
        return {"status": "ok", "data_retention_days": body.days}
    except Exception:
        logger.exception("Error updating retention setting")
        raise HTTPException(status_code=500, detail="Internal server error")
