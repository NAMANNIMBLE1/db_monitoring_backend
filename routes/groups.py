from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_, func
from datetime import datetime
from typing import List, Optional, Union
from models.device_group import DeviceGroup, DeviceGroupMember
from models.device import MonitoredDevice

from database import get_db
from pydantic import BaseModel

router = APIRouter()

class DeviceGroupCreate(BaseModel):
    name: str
    dynamic_query: Optional[str] = None  
    device_ids: Optional[List[Union[int, str]]] = None  

class DeviceGroupOut(BaseModel):
    id: int
    name: str
    dynamic_query: Optional[str]
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    class Config:
        orm_mode = True

class DeviceGroupMemberOut(BaseModel):
    id: int
    device_id: int
    class Config:
        orm_mode = True


async def _resolve_device_ids(
    db: AsyncSession, raw_ids: Optional[List[Union[int, str]]]
) -> List[int]:
    if not raw_ids:
        return []

    resolved_ids: List[int] = []
    unresolved_tokens: List[str] = []

    for raw in raw_ids:
        token = str(raw).strip()
        if not token:
            continue

        if token.isdigit():
            device = await db.get(MonitoredDevice, int(token))
        else:
            result = await db.execute(
                select(MonitoredDevice).where(
                    or_(
                        MonitoredDevice.ip_address == token,
                        MonitoredDevice.hostname == token,
                    )
                )
            )
            device = result.scalars().first()

        if device:
            resolved_ids.append(device.id)
        else:
            unresolved_tokens.append(token)

    if unresolved_tokens:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Unknown device identifiers",
                "unresolved": unresolved_tokens,
            },
        )

    # Keep insert/update deterministic and avoid duplicate rows.
    return sorted(set(resolved_ids))

@router.post("/groups/", response_model=DeviceGroupOut)
async def create_group(data: DeviceGroupCreate, db: AsyncSession = Depends(get_db)):
    # Groups are static for now;
    group = DeviceGroup(name=data.name, dynamic_query=None)
    db.add(group)
    await db.flush()
    # Add static members if provided
    resolved_device_ids = await _resolve_device_ids(db, data.device_ids)
    if resolved_device_ids:
        for device_id in resolved_device_ids:
            db.add(DeviceGroupMember(group_id=group.id, device_id=device_id))
    await db.commit()
    await db.refresh(group)
    return group

@router.get("/groups/", response_model=List[DeviceGroupOut])
async def list_groups(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            DeviceGroup.id,
            DeviceGroup.name,
            DeviceGroup.dynamic_query,
            DeviceGroup.created_at,
            DeviceGroup.updated_at,
            func.count(DeviceGroupMember.id).label("member_count"),
        )
        .outerjoin(DeviceGroupMember, DeviceGroupMember.group_id == DeviceGroup.id)
        .group_by(
            DeviceGroup.id,
            DeviceGroup.name,
            DeviceGroup.dynamic_query,
            DeviceGroup.created_at,
            DeviceGroup.updated_at,
        )
        .order_by(DeviceGroup.name.asc())
    )
    return [
        {
            "id": row.id,
            "name": row.name,
            "dynamic_query": row.dynamic_query,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "member_count": row.member_count,
        }
        for row in result.fetchall()
    ]

@router.get("/groups/{group_id}", response_model=DeviceGroupOut)
async def get_group(group_id: int, db: AsyncSession = Depends(get_db)):
    group = await db.get(DeviceGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group

@router.put("/groups/{group_id}", response_model=DeviceGroupOut)
async def update_group(group_id: int, data: DeviceGroupCreate, db: AsyncSession = Depends(get_db)):
    group = await db.get(DeviceGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    group.name = data.name
    group.dynamic_query = None
    # Update static members if provided
    if data.device_ids is not None:
        resolved_device_ids = await _resolve_device_ids(db, data.device_ids)
        await db.execute(
            DeviceGroupMember.__table__.delete().where(DeviceGroupMember.group_id == group_id)
        )
        for device_id in resolved_device_ids:
            db.add(DeviceGroupMember(group_id=group_id, device_id=device_id))
    await db.commit()
    await db.refresh(group)
    return group

@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)):
    group = await db.get(DeviceGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.delete(group)
    await db.commit()
    return {"ok": True}

@router.get("/groups/{group_id}/members", response_model=List[DeviceGroupMemberOut])
async def list_group_members(group_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DeviceGroupMember).where(DeviceGroupMember.group_id == group_id))
    return result.scalars().all()

@router.post("/groups/{group_id}/members")
async def add_group_members(group_id: int, device_ids: List[int], db: AsyncSession = Depends(get_db)):
    for device_id in device_ids:
        db.add(DeviceGroupMember(group_id=group_id, device_id=device_id))
    await db.commit()
    return {"ok": True}

@router.delete("/groups/{group_id}/members/{member_id}")
async def delete_group_member(group_id: int, member_id: int, db: AsyncSession = Depends(get_db)):
    member = await db.get(DeviceGroupMember, member_id)
    if not member or member.group_id != group_id:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.delete(member)
    await db.commit()
    return {"ok": True}
