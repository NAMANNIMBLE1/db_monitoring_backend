import logging
from typing import Optional, Set
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from database import get_nms_db

logger = logging.getLogger(__name__)

# Group mappings based on provided queries
USER_GROUP_MAPPING = {
    # APDCL groups: device_group_id IN (18,19,20,21)
    "APDCL": [18, 19, 20, 21],
    # DPGCL groups: device_group_id IN (1,3,4,5,6,7,8,9,16)
    "DPGCL": [1, 3, 4, 5, 6, 7, 8, 9, 16],
    # MGVCL groups: device_group_id IN (2,10,11,12,13,15,14,17)
    "MGVCL": [2, 10, 11, 12, 13, 15, 14, 17]
}

async def get_user_from_session(request: Request) -> Optional[int]:
    """Extract user_id from Laravel session cookie."""
    session_id = request.cookies.get("laravel_session")
    
    if not session_id:
        logger.warning("No laravel_session cookie found")
        return None
    
    try:
        async for session in get_nms_db():
            # Query sessions table to get user_id
            result = await session.execute(
                text("SELECT user_id FROM sessions WHERE id = :session_id"),
                {"session_id": session_id}
            )
            row = result.fetchone()
            
            if row and row[0]:
                return int(row[0])
            else:
                logger.warning(f"No valid user found for session: {session_id}")
                return None
                
    except Exception as e:
        logger.error(f"Error querying session: {e}")
        return None

async def get_user_allowed_ips(user_id: int) -> Set[str]:
    """Get allowed IP addresses for a user based on their group permissions."""
    try:
        async for session in get_nms_db():
            # First, check if user has any direct permissions or group permissions
            # We'll use the provided queries as the basis for our logic
            
            # For now, let's implement a simple approach based on user groups
            # In a real implementation, you might have a users table with group assignments
            
            # Get user's device groups (this would need to be adapted based on actual NMS schema)
            allowed_ips = set()
            
            # Try to determine user's group by checking permissions
            for group_name, group_ids in USER_GROUP_MAPPING.items():
                # Check if user has permissions for any device in this group
                placeholders = ",".join(str(gid) for gid in group_ids)
                query = f"""
                SELECT DISTINCT devices.hostname
                FROM device_group_device
                JOIN devices ON device_group_device.device_id = devices.device_id
                WHERE device_group_device.device_group_id IN ({placeholders})
                AND EXISTS (
                    SELECT 1 FROM devices_group_perms dgp 
                    WHERE dgp.device_group_id IN ({placeholders}) 
                    AND dgp.user_id = :user_id
                )
                """
                
                try:
                    result = await session.execute(text(query), {"user_id": user_id})
                    ips = [row[0] for row in result.fetchall()]
                    
                    if ips:  # If user has access to devices in this group
                        allowed_ips.update(ips)
                        logger.info(f"User {user_id} belongs to {group_name} group with {len(ips)} devices")
                        break  # Assume users belong to one main group
                except Exception as e:
                    logger.warning(f"Error querying {group_name} permissions: {e}")
                    continue
            
            # If no group permissions found, check direct device permissions
            if not allowed_ips:
                direct_query = """
                SELECT DISTINCT d.hostname
                FROM devices d
                JOIN devices_perms dp ON d.device_id = dp.device_id
                WHERE dp.user_id = :user_id
                """
                result = await session.execute(text(direct_query), {"user_id": user_id})
                direct_ips = [row[0] for row in result.fetchall()]
                allowed_ips.update(direct_ips)
            
            return allowed_ips
            
    except Exception as e:
        logger.error(f"Error getting user permissions: {e}")
        return set()

async def get_current_user(request: Request) -> dict:
    """FastAPI dependency to get current authenticated user and their permissions."""
    user_id = await get_user_from_session(request)
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized - No valid session")
    
    allowed_ips = await get_user_allowed_ips(user_id)
    
    # If no specific permissions, treat as admin (can see all)
    # This handles the edge case mentioned in the requirements
    is_admin = len(allowed_ips) == 0
    
    return {
        "user_id": user_id,
        "allowed_ips": allowed_ips,
        "is_admin": is_admin
    }

def filter_by_ip_permissions(data: list, allowed_ips: Set[str], is_admin: bool) -> list:
    """Filter monitoring data based on user's IP permissions."""
    if is_admin:
        return data
    
    # Filter data where the IP is in allowed_ips
    filtered_data = []
    for item in data:
        # Handle different data structures
        if isinstance(item, dict):
            # Check for common IP field names
            ip = item.get("ip") or item.get("hostname") or item.get("host") or item.get("address")
            if ip and ip in allowed_ips:
                filtered_data.append(item)
        elif hasattr(item, 'ip'):
            if item.ip in allowed_ips:
                filtered_data.append(item)
        elif hasattr(item, 'hostname'):
            if item.hostname in allowed_ips:
                filtered_data.append(item)
    
    return filtered_data
