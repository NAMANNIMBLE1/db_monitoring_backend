import logging
from typing import Callable
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from services.auth_service import get_current_user

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate users based on Laravel session."""
    
    # Paths that don't require authentication
    PUBLIC_PATHS = {
        "/health",
        "/docs", 
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/static/",
        "/api/v1/test-auth/",
        "/api/v1/debug/",
        "/api/v1/schema-debug/",
    }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        # Skip authentication for public paths
        request_path = request.url.path
        logger.debug(f"Request path: {request_path}")
        
        if request_path in self.PUBLIC_PATHS or any(request_path.startswith(path) for path in self.PUBLIC_PATHS if path.endswith('/')):
            logger.debug(f"Skipping authentication for public path: {request_path}")
            return await call_next(request)
        
        # Skip authentication for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)
        
        try:
            # Get user info and store it in request state
            user_info = await get_current_user(request)
            request.state.user = user_info
            logger.debug(f"Authenticated user {user_info['user_id']} with {len(user_info['allowed_ips'])} allowed IPs")
            
        except HTTPException as e:
            # Let authentication exceptions propagate properly
            logger.warning(f"Authentication failed: {e.detail}")
            # Don't re-raise - let FastAPI handle it properly
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail}
            )
        except Exception as e:
            logger.error(f"Unexpected authentication error: {e}")
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication failed"}
            )
        
        return await call_next(request)
