"""
Debug endpoints to identify database connection issues
"""

import logging
from fastapi import APIRouter
from config import settings
from database import engine, nms_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["debug"])

@router.get("/debug/config")
async def debug_config():
    """Show current configuration (without sensitive data)"""
    return {
        "db_host": settings.DB_HOST,
        "db_port": settings.DB_PORT,
        "db_user": settings.DB_USER,
        "db_name": settings.DB_NAME,
        "nms_db_name": settings.NMS_DB_NAME,
        "server_host": settings.SERVER_HOST,
        "server_port": settings.SERVER_PORT,
        "password_set": bool(settings.DB_PASSWORD),
        "password_length": len(settings.DB_PASSWORD) if settings.DB_PASSWORD else 0
    }

@router.get("/debug/connection-test")
async def test_database_connections():
    """Test both database connections"""
    results = {}
    
    # Test main database
    try:
        async with engine.begin() as conn:
            result = await conn.execute("SELECT 1")
            results["main_db"] = {
                "status": "connected",
                "test_query": "SELECT 1 successful"
            }
    except Exception as e:
        results["main_db"] = {
            "status": "failed",
            "error": str(e)
        }
    
    # Test NMS database
    try:
        async with nms_engine.begin() as conn:
            result = await conn.execute("SELECT 1")
            results["nms_db"] = {
                "status": "connected", 
                "test_query": "SELECT 1 successful"
            }
    except Exception as e:
        results["nms_db"] = {
            "status": "failed",
            "error": str(e)
        }
    
    return results

@router.get("/debug/nms-tables")
async def check_nms_tables():
    """Check if NMS tables exist"""
    tables = ["sessions", "devices", "device_group_device", "devices_group_perms"]
    results = {}
    
    try:
        async with nms_engine.begin() as conn:
            for table in tables:
                try:
                    from sqlalchemy import text
                    result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = result.scalar()
                    results[table] = {
                        "exists": True,
                        "count": count
                    }
                except Exception as e:
                    results[table] = {
                        "exists": False,
                        "error": str(e)
                    }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "message": "Cannot connect to NMS database"
        }
    
    return results
