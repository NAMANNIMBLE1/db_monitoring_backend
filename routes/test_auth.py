"""
Test endpoints for NMS authentication system.
These endpoints help developers test authentication without being NMS users.
"""

import logging
from typing import Dict, Any
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_nms_db
from services.auth_service import get_user_allowed_ips

logger = logging.getLogger(__name__)

router = APIRouter(tags=["test-auth"])

@router.get("/test-auth/setup-test-user")
async def setup_test_user():
    """
    Create a test user and session for development testing.
    This endpoint should only be used in development environments.
    """
    try:
        async for session in get_nms_db():
            # Create test session
            await session.execute(
                text("""
                    INSERT IGNORE INTO sessions (id, user_id, payload, ip_address) 
                    VALUES ('dev_test_session_123', 999, '{}', '127.0.0.1')
                """)
            )
            
            # Create test user permissions for APDCL group
            await session.execute(
                text("""
                    INSERT IGNORE INTO devices_group_perms (device_group_id, user_id) 
                    VALUES 
                    (18, 999), (19, 999), (20, 999), (21, 999)
                """)
            )
            
            await session.commit()
            
            return {
                "status": "success",
                "message": "Test user created successfully",
                "session_id": "dev_test_session_123",
                "user_id": 999,
                "group": "APDCL",
                "test_url": "Use cookie: laravel_session=dev_test_session_123"
            }
            
    except Exception as e:
        logger.error(f"Error setting up test user: {e}")
        return {
            "status": "error",
            "message": "Failed to setup test user",
            "error": str(e),
            "hint": "Check NMS database connection and table structure"
        }

@router.get("/test-auth/setup-test-user/{group}")
async def setup_test_user_group(group: str):
    """
    Create a test user for a specific group (APDCL, DPGCL, MGVCL).
    """
    group_mappings = {
        "APDCL": [18, 19, 20, 21],
        "DPGCL": [1, 3, 4, 5, 6, 7, 8, 9, 16],
        "MGVCL": [2, 10, 11, 12, 13, 15, 14, 17]
    }
    
    if group not in group_mappings:
        raise HTTPException(status_code=400, detail=f"Invalid group. Use: {list(group_mappings.keys())}")
    
    session_id = f"dev_test_{group.lower()}_123"
    user_id = 999 + list(group_mappings.keys()).index(group)
    
    try:
        async for session in get_nms_db():
            # Create test session
            await session.execute(
                text("""
                    INSERT IGNORE INTO sessions (id, user_id, payload, ip_address) 
                    VALUES (:session_id, :user_id, '{}', '127.0.0.1')
                """),
                {"session_id": session_id, "user_id": user_id}
            )
            
            # Create test user permissions
            placeholders = ",".join([f"({gid}, {user_id})" for gid in group_mappings[group]])
            await session.execute(
                text(f"""
                    INSERT IGNORE INTO devices_group_perms (device_group_id, user_id) 
                    VALUES {placeholders}
                """)
            )
            
            await session.commit()
            
            return {
                "status": "success",
                "message": f"Test user created for {group} group",
                "session_id": session_id,
                "user_id": user_id,
                "group": group,
                "device_groups": group_mappings[group],
                "test_url": f"Use cookie: laravel_session={session_id}"
            }
            
    except Exception as e:
        logger.error(f"Error setting up test user for {group}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to setup test user for {group}")

@router.get("/test-auth/cleanup")
async def cleanup_test_data():
    """
    Clean up all test data. Use this to reset the test environment.
    """
    try:
        async for session in get_nms_db():
            # Remove test sessions
            await session.execute(
                text("DELETE FROM sessions WHERE id LIKE 'dev_test_%'")
            )
            
            # Remove test user permissions
            await session.execute(
                text("DELETE FROM devices_group_perms WHERE user_id >= 999")
            )
            
            await session.commit()
            
            return {
                "status": "success",
                "message": "Test data cleaned up successfully"
            }
            
    except Exception as e:
        logger.error(f"Error cleaning up test data: {e}")
        raise HTTPException(status_code=500, detail="Failed to cleanup test data")

@router.get("/test-auth/show-permissions/{session_id}")
async def show_permissions(session_id: str):
    """
    Show what permissions a specific session would have.
    Useful for debugging authentication issues.
    """
    try:
        async for session in get_nms_db():
            # Get user from session
            result = await session.execute(
                text("SELECT user_id FROM sessions WHERE id = :session_id"),
                {"session_id": session_id}
            )
            row = result.fetchone()
            
            if not row:
                return {"error": "Session not found"}
            
            user_id = row[0]
            allowed_ips = await get_user_allowed_ips(user_id)
            
            return {
                "session_id": session_id,
                "user_id": user_id,
                "allowed_ips_count": len(allowed_ips),
                "allowed_ips": list(allowed_ips)[:10],  # Show first 10 IPs
                "is_admin": len(allowed_ips) == 0,
                "total_ips": len(allowed_ips)
            }
            
    except Exception as e:
        logger.error(f"Error showing permissions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get permissions")

@router.get("/test-auth/sample-ips/{group}")
async def get_sample_ips_for_group(group: str):
    """
    Get sample IP addresses for a specific group.
    Useful for understanding what each group can access.
    """
    group_mappings = {
        "APDCL": [18, 19, 20, 21],
        "DPGCL": [1, 3, 4, 5, 6, 7, 8, 9, 16],
        "MGVCL": [2, 10, 11, 12, 13, 15, 14, 17]
    }
    
    if group not in group_mappings:
        raise HTTPException(status_code=400, detail=f"Invalid group. Use: {list(group_mappings.keys())}")
    
    try:
        async for session in get_nms_db():
            placeholders = ",".join(str(gid) for gid in group_mappings[group])
            result = await session.execute(
                text(f"""
                    SELECT DISTINCT d.hostname 
                    FROM device_group_device
                    JOIN devices ON device_group_device.device_id = devices.device_id
                    WHERE device_group_device.device_group_id IN ({placeholders})
                    LIMIT 10
                """)
            )
            ips = [row[0] for row in result.fetchall()]
            
            return {
                "group": group,
                "device_groups": group_mappings[group],
                "sample_ips": ips,
                "total_count_query": f"""
                    SELECT COUNT(DISTINCT d.hostname) 
                    FROM device_group_device
                    JOIN devices ON device_group_device.device_id = devices.device_id
                    WHERE device_group_device.device_group_id IN ({placeholders})
                """
            }
            
    except Exception as e:
        logger.error(f"Error getting sample IPs for {group}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get sample IPs")

@router.get("/test-auth/database-check")
async def database_check():
    """
    Check if the NMS database has the required tables and structure.
    """
    required_tables = ["sessions", "devices", "device_group_device", "devices_group_perms"]
    
    try:
        async for session in get_nms_db():
            results = {}
            
            for table in required_tables:
                try:
                    result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
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
            
            return {
                "status": "success",
                "tables": results,
                "nms_database": "Connected successfully"
            }
            
    except Exception as e:
        logger.error(f"Database check failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "message": "Cannot connect to NMS database",
            "troubleshooting": {
                "check_env_vars": "Verify DB_HOST, DB_USER, DB_PASSWORD, NMS_DB_NAME",
                "check_database": "Ensure MySQL server is running and accessible",
                "check_permissions": "Verify database user has access to NMS database",
                "check_network": "Ensure firewall allows connection to database server"
            }
        }
