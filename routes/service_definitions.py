"""CRUD endpoints for service definitions.

Agents fetch their configuration from ``GET /services/agent-config``.
The Settings UI manages definitions via the remaining endpoints.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.service_definition import ServiceDefinition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/services", tags=["service-definitions"])


# ── Pydantic schemas ────────────────────────────────────────────


class ServiceDefinitionCreate(BaseModel):
    key: str = Field(..., max_length=64, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(..., max_length=128)
    category: str = Field(..., max_length=64)
    os_type: str = Field(default="both")
    check_type: str = Field(default="tcp")
    tcp_ports: Optional[List[int]] = None
    udp_ports: Optional[List[int]] = None
    win_service_names: Optional[List[str]] = None
    linux_service_names: Optional[List[str]] = None
    linux_process_names: Optional[List[str]] = None


class ServiceDefinitionUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)
    category: Optional[str] = Field(None, max_length=64)
    os_type: Optional[str] = None
    check_type: Optional[str] = None
    tcp_ports: Optional[List[int]] = None
    udp_ports: Optional[List[int]] = None
    win_service_names: Optional[List[str]] = None
    linux_service_names: Optional[List[str]] = None
    linux_process_names: Optional[List[str]] = None
    is_active: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────


def _jsonify(val) -> Optional[str]:
    if val is None:
        return None
    return json.dumps(val)


async def _get_by_key(db: AsyncSession, key: str) -> ServiceDefinition:
    result = await db.execute(
        select(ServiceDefinition).where(ServiceDefinition.key == key)
    )
    svc = result.scalars().first()
    if svc is None:
        raise HTTPException(status_code=404, detail=f"Service '{key}' not found")
    return svc


# ── Endpoints ────────────────────────────────────────────────────


@router.get("")
async def list_services(
    os_type: Optional[str] = Query(None, description="Filter by os_type (windows, linux, both)"),
    active_only: bool = Query(False, description="Only return active services"),
    db: AsyncSession = Depends(get_db),
):
    """List all service definitions, optionally filtered."""
    query = select(ServiceDefinition)
    conditions = []
    if active_only:
        conditions.append(ServiceDefinition.is_active == True)
    if os_type:
        conditions.append(
            (ServiceDefinition.os_type == os_type) | (ServiceDefinition.os_type == "both")
        )
    if conditions:
        query = query.where(*conditions)
    query = query.order_by(ServiceDefinition.category, ServiceDefinition.display_name)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [r.to_dict() for r in rows]


@router.get("/agent-config")
async def get_agent_config(
    os_type: str = Query(..., description="Agent OS type (windows or linux)"),
    agent_id: Optional[str] = Query(None, description="Agent ID for per-agent overrides"),
    db: AsyncSession = Depends(get_db),
):
    """Return compact service check specs for agents.

    Agents call this endpoint on startup and periodically to refresh
    their configuration. The ``version`` hash lets agents skip
    re-processing when nothing has changed.

    If ``agent_id`` is provided and has service overrides, only enabled
    services are returned. If no overrides exist, all active services
    are returned (default behavior).
    """
    # Get all active services for this OS type (full catalog)
    query = select(ServiceDefinition).where(
        ServiceDefinition.is_active == True,
        (ServiceDefinition.os_type == os_type) | (ServiceDefinition.os_type == "both"),
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    if agent_id:
        from models.registered_agent import RegisteredAgent
        from models.service_pack import ServicePackItem
        from models.agent_service_override import AgentServiceOverride

        # Step 1: Check if agent has a service pack assigned
        agent_result = await db.execute(
            select(RegisteredAgent.service_pack_id).where(RegisteredAgent.agent_id == agent_id)
        )
        agent_row = agent_result.first()
        pack_id = agent_row[0] if agent_row else None

        if pack_id:
            # Filter to only services in the assigned pack
            pack_result = await db.execute(
                select(ServicePackItem.service_key).where(ServicePackItem.pack_id == pack_id)
            )
            pack_keys = {r[0] for r in pack_result.fetchall()}
            rows = [r for r in rows if r.key in pack_keys]

        # Step 2: Apply per-agent overrides on top
        override_result = await db.execute(
            select(AgentServiceOverride).where(AgentServiceOverride.agent_id == agent_id)
        )
        overrides = {o.service_key: o.is_enabled for o in override_result.scalars().all()}

        if overrides:
            rows = [r for r in rows if overrides.get(r.key, True)]

    services = [r.to_agent_config() for r in rows]

    # Build a deterministic version hash from the config
    config_str = json.dumps(services, sort_keys=True)
    version = hashlib.md5(config_str.encode()).hexdigest()[:12]

    return {"version": version, "services": services}


@router.get("/{key}")
async def get_service(key: str, db: AsyncSession = Depends(get_db)):
    """Get a single service definition by key."""
    svc = await _get_by_key(db, key)
    return svc.to_dict()


@router.post("", status_code=201)
async def create_service(
    body: ServiceDefinitionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new custom service definition."""
    # Check for duplicate key
    existing = await db.execute(
        select(ServiceDefinition).where(ServiceDefinition.key == body.key)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Service key '{body.key}' already exists")

    svc = ServiceDefinition(
        key=body.key,
        display_name=body.display_name,
        category=body.category,
        os_type=body.os_type,
        check_type=body.check_type,
        tcp_ports=_jsonify(body.tcp_ports),
        udp_ports=_jsonify(body.udp_ports),
        win_service_names=_jsonify(body.win_service_names),
        linux_service_names=_jsonify(body.linux_service_names),
        linux_process_names=_jsonify(body.linux_process_names),
        is_default=False,
        is_active=True,
    )
    db.add(svc)
    await db.commit()
    await db.refresh(svc)
    logger.info("Created custom service definition: %s", body.key)
    return svc.to_dict()


@router.put("/{key}")
async def update_service(
    key: str,
    body: ServiceDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing service definition."""
    svc = await _get_by_key(db, key)

    if body.display_name is not None:
        svc.display_name = body.display_name
    if body.category is not None:
        svc.category = body.category
    if body.os_type is not None:
        svc.os_type = body.os_type
    if body.check_type is not None:
        svc.check_type = body.check_type
    if body.tcp_ports is not None:
        svc.tcp_ports = _jsonify(body.tcp_ports)
    if body.udp_ports is not None:
        svc.udp_ports = _jsonify(body.udp_ports)
    if body.win_service_names is not None:
        svc.win_service_names = _jsonify(body.win_service_names)
    if body.linux_service_names is not None:
        svc.linux_service_names = _jsonify(body.linux_service_names)
    if body.linux_process_names is not None:
        svc.linux_process_names = _jsonify(body.linux_process_names)
    if body.is_active is not None:
        svc.is_active = body.is_active

    svc.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(svc)
    logger.info("Updated service definition: %s", key)
    return svc.to_dict()


@router.delete("/{key}")
async def delete_service(key: str, db: AsyncSession = Depends(get_db)):
    """Soft-delete (deactivate) a default service; hard-delete custom services."""
    svc = await _get_by_key(db, key)

    if svc.is_default:
        # Soft-delete: deactivate only
        svc.is_active = False
        svc.updated_at = datetime.utcnow()
        await db.commit()
        logger.info("Soft-deleted (deactivated) default service: %s", key)
        return {"status": "deactivated", "key": key}
    else:
        await db.delete(svc)
        await db.commit()
        logger.info("Hard-deleted custom service: %s", key)
        return {"status": "deleted", "key": key}
