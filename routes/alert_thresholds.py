"""Read and update global alert thresholds (disk/cpu/memory + forecast horizon)."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.alert_threshold import AlertThreshold
from utils.timezone import now_ist

router = APIRouter(prefix="/alert-thresholds", tags=["alert-thresholds"])

_ALLOWED_METRICS = {"disk", "cpu", "memory"}


class ThresholdUpdate(BaseModel):
    warn_threshold: float = Field(..., ge=0, le=100)
    critical_threshold: float = Field(..., ge=0, le=100)
    forecast_days: int = Field(15, ge=1, le=365)


@router.get("")
async def list_thresholds(db: AsyncSession = Depends(get_db)) -> List[dict]:
    result = await db.execute(select(AlertThreshold))
    return [r.to_dict() for r in result.scalars().all()]


@router.put("/{metric}")
async def update_threshold(
    metric: str,
    payload: ThresholdUpdate,
    db: AsyncSession = Depends(get_db),
):
    metric = metric.lower()
    if metric not in _ALLOWED_METRICS:
        raise HTTPException(status_code=400, detail=f"metric must be one of {_ALLOWED_METRICS}")
    if payload.critical_threshold < payload.warn_threshold:
        raise HTTPException(
            status_code=400,
            detail="critical_threshold must be >= warn_threshold",
        )

    result = await db.execute(
        select(AlertThreshold).where(AlertThreshold.metric == metric)
    )
    row = result.scalars().first()
    if row is None:
        row = AlertThreshold(metric=metric)
        db.add(row)

    row.warn_threshold = payload.warn_threshold
    row.critical_threshold = payload.critical_threshold
    row.forecast_days = payload.forecast_days
    row.updated_at = now_ist()
    await db.commit()
    await db.refresh(row)
    return row.to_dict()
