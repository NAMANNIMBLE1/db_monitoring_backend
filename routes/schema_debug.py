"""
Debug endpoints to check database schema
"""

import logging
from fastapi import APIRouter
from database import nms_engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schema-debug"])

@router.get("/schema-debug/devices-columns")
async def check_devices_columns():
    """Check the actual column names in devices table"""
    try:
        async with nms_engine.begin() as conn:
            result = await conn.execute(text("DESCRIBE devices"))
            columns = result.fetchall()
            
            column_info = []
            for row in columns:
                column_info.append({
                    "field": row[0],
                    "type": row[1],
                    "null": row[2],
                    "key": row[3],
                    "default": row[4],
                    "extra": row[5]
                })
            
            return {
                "table": "devices",
                "columns": column_info
            }
    except Exception as e:
        return {"error": str(e)}

@router.get("/schema-debug/sample-devices")
async def get_sample_devices():
    """Get sample devices to understand the data structure"""
    try:
        async with nms_engine.begin() as conn:
            # Only select text columns to avoid binary encoding issues
            result = await conn.execute(text("""
                SELECT device_id, hostname, sysName, display, ip, status, type 
                FROM devices LIMIT 5
            """))
            rows = result.fetchall()
            
            # Get column names
            columns = result.keys()
            
            sample_data = []
            for row in rows:
                sample_data.append(dict(zip(columns, row)))
            
            return {
                "table": "devices",
                "sample_data": sample_data,
                "columns": list(columns)
            }
    except Exception as e:
        return {"error": str(e)}

@router.get("/schema-debug/device-group-sample")
async def get_device_group_sample():
    """Get sample device_group_device data"""
    try:
        async with nms_engine.begin() as conn:
            result = await conn.execute(text("SELECT * FROM device_group_device LIMIT 3"))
            rows = result.fetchall()
            
            columns = result.keys()
            sample_data = []
            for row in rows:
                sample_data.append(dict(zip(columns, row)))
            
            return {
                "table": "device_group_device",
                "sample_data": sample_data,
                "columns": list(columns)
            }
    except Exception as e:
        return {"error": str(e)}
