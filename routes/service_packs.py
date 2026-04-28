"""CRUD and assignment endpoints for Service Packs."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.registered_agent import RegisteredAgent
from models.service_definition import ServiceDefinition
from models.service_pack import ServicePack, ServicePackItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/service-packs", tags=["service-packs"])


# ── Schemas ──────────────────────────────────────────────────────


class PackCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    service_keys: List[str]


class PackUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    service_keys: Optional[List[str]] = None


class AssignRequest(BaseModel):
    agent_ids: List[str]


# ── Endpoints ────────────────────────────────────────────────────


@router.get("")
async def list_packs(db: AsyncSession = Depends(get_db)):
    """List all service packs with service count and assigned agent count."""
    result = await db.execute(
        select(ServicePack).order_by(ServicePack.is_master.desc(), ServicePack.name)
    )
    packs = result.scalars().all()

    items = []
    for pack in packs:
        # Service count
        svc_result = await db.execute(
            select(func.count()).where(ServicePackItem.pack_id == pack.id)
        )
        svc_count = svc_result.scalar() or 0

        # Agent count
        agent_result = await db.execute(
            select(func.count()).where(RegisteredAgent.service_pack_id == pack.id)
        )
        agent_count = agent_result.scalar() or 0

        d = pack.to_dict()
        d["services_count"] = svc_count
        d["agents_count"] = agent_count
        items.append(d)

    return items


@router.get("/{pack_id}")
async def get_pack(pack_id: int, db: AsyncSession = Depends(get_db)):
    """Get pack details with service keys and assigned agents."""
    result = await db.execute(
        select(ServicePack).where(ServicePack.id == pack_id)
    )
    pack = result.scalars().first()
    if not pack:
        raise HTTPException(status_code=404, detail="Service pack not found")

    # Get service keys
    svc_result = await db.execute(
        select(ServicePackItem.service_key).where(ServicePackItem.pack_id == pack_id)
    )
    service_keys = [r[0] for r in svc_result.fetchall()]

    # Get assigned agents
    agent_result = await db.execute(
        select(RegisteredAgent.agent_id, RegisteredAgent.hostname, RegisteredAgent.ip_address)
        .where(RegisteredAgent.service_pack_id == pack_id)
    )
    agents = [
        {"agent_id": r[0], "hostname": r[1], "ip_address": r[2]}
        for r in agent_result.fetchall()
    ]

    d = pack.to_dict()
    d["service_keys"] = service_keys
    d["services_count"] = len(service_keys)
    d["agents"] = agents
    d["agents_count"] = len(agents)
    return d


@router.post("", status_code=201)
async def create_pack(body: PackCreate, db: AsyncSession = Depends(get_db)):
    """Create a custom service pack."""
    # Check name uniqueness
    existing = await db.execute(
        select(ServicePack).where(ServicePack.name == body.name)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Pack '{body.name}' already exists")

    # Validate service keys exist
    if body.service_keys:
        result = await db.execute(
            select(ServiceDefinition.key).where(ServiceDefinition.key.in_(body.service_keys))
        )
        valid_keys = {r[0] for r in result.fetchall()}
        invalid = set(body.service_keys) - valid_keys
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown service keys: {', '.join(invalid)}")

    pack = ServicePack(name=body.name, is_master=False)
    db.add(pack)
    await db.flush()

    for key in body.service_keys:
        db.add(ServicePackItem(pack_id=pack.id, service_key=key))

    await db.commit()
    logger.info("Created service pack '%s' with %d services", body.name, len(body.service_keys))
    return {"id": pack.id, "name": pack.name, "services_count": len(body.service_keys)}


@router.put("/{pack_id}")
async def update_pack(pack_id: int, body: PackUpdate, db: AsyncSession = Depends(get_db)):
    """Update a custom service pack. Master pack cannot be modified."""
    result = await db.execute(
        select(ServicePack).where(ServicePack.id == pack_id)
    )
    pack = result.scalars().first()
    if not pack:
        raise HTTPException(status_code=404, detail="Service pack not found")
    if pack.is_master:
        raise HTTPException(status_code=403, detail="Master Service Pack cannot be modified")

    if body.name is not None:
        # Check uniqueness
        dup = await db.execute(
            select(ServicePack).where(ServicePack.name == body.name, ServicePack.id != pack_id)
        )
        if dup.scalars().first():
            raise HTTPException(status_code=409, detail=f"Pack '{body.name}' already exists")
        pack.name = body.name

    if body.service_keys is not None:
        # Validate keys
        if body.service_keys:
            result = await db.execute(
                select(ServiceDefinition.key).where(ServiceDefinition.key.in_(body.service_keys))
            )
            valid_keys = {r[0] for r in result.fetchall()}
            invalid = set(body.service_keys) - valid_keys
            if invalid:
                raise HTTPException(status_code=400, detail=f"Unknown service keys: {', '.join(invalid)}")

        # Replace items
        await db.execute(delete(ServicePackItem).where(ServicePackItem.pack_id == pack_id))
        for key in body.service_keys:
            db.add(ServicePackItem(pack_id=pack_id, service_key=key))

    await db.commit()
    logger.info("Updated service pack '%s' (id=%d)", pack.name, pack_id)
    return {"status": "updated", "id": pack_id}


@router.delete("/{pack_id}")
async def delete_pack(pack_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a custom service pack. Master pack cannot be deleted."""
    result = await db.execute(
        select(ServicePack).where(ServicePack.id == pack_id)
    )
    pack = result.scalars().first()
    if not pack:
        raise HTTPException(status_code=404, detail="Service pack not found")
    if pack.is_master:
        raise HTTPException(status_code=403, detail="Master Service Pack cannot be deleted")

    # Null out agent references (they'll fall back to defaults)
    await db.execute(
        update(RegisteredAgent)
        .where(RegisteredAgent.service_pack_id == pack_id)
        .values(service_pack_id=None)
    )

    # Delete items and pack
    await db.execute(delete(ServicePackItem).where(ServicePackItem.pack_id == pack_id))
    await db.delete(pack)
    await db.commit()

    logger.info("Deleted service pack '%s' (id=%d)", pack.name, pack_id)
    return {"status": "deleted", "id": pack_id}


@router.post("/{pack_id}/assign")
async def assign_pack(pack_id: int, body: AssignRequest, db: AsyncSession = Depends(get_db)):
    """Bulk assign a service pack to multiple agents."""
    # Verify pack exists
    result = await db.execute(
        select(ServicePack).where(ServicePack.id == pack_id)
    )
    pack = result.scalars().first()
    if not pack:
        raise HTTPException(status_code=404, detail="Service pack not found")

    assigned = 0
    not_found = []
    for agent_id in body.agent_ids:
        agent_result = await db.execute(
            select(RegisteredAgent).where(RegisteredAgent.agent_id == agent_id)
        )
        agent = agent_result.scalars().first()
        if not agent:
            not_found.append(agent_id)
            continue
        agent.service_pack_id = pack_id
        assigned += 1

    await db.commit()
    logger.info("Assigned pack '%s' to %d agent(s)", pack.name, assigned)

    result = {"status": "assigned", "pack": pack.name, "assigned": assigned}
    if not_found:
        result["not_found"] = not_found
    return result
