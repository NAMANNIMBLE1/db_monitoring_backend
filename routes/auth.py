import logging
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["authentication"])

@router.get("/auth/me")
async def get_current_user_info(request: Request):
    """Get current authenticated user information."""
    user_info = getattr(request.state, 'user', None)
    
    if not user_info:
        return {"error": "Not authenticated"}
    
    return {
        "user_id": user_info["user_id"],
        "allowed_ips_count": len(user_info["allowed_ips"]),
        "is_admin": user_info["is_admin"],
        "allowed_ips": list(user_info["allowed_ips"]) if not user_info["is_admin"] else "all"
    }
