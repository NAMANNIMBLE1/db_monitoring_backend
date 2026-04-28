#!/usr/bin/env python3
"""
Debug version of the main app with better error handling
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Try to import dependencies with error handling
try:
    from config import settings
    logger.info("✅ Config loaded successfully")
except Exception as e:
    logger.error(f"❌ Config failed: {e}")
    raise

try:
    from database import init_db, AsyncSessionLocal
    logger.info("✅ Database imported successfully")
except Exception as e:
    logger.error(f"❌ Database import failed: {e}")
    raise

@asynccontextmanager
async def debug_lifespan(app: FastAPI):
    logger.info("🚀 Starting debug server...")
    
    try:
        # Test database connection
        logger.info("🔍 Testing database connection...")
        await asyncio.wait_for(init_db(), timeout=30)
        logger.info("✅ Database connection successful")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        # Don't raise - allow server to start for debugging
    
    logger.info("✅ Debug server startup complete")
    yield
    logger.info("🛑 Debug server shutting down")

# Create debug app
app = FastAPI(
    title="Debug Port Monitoring API",
    version="1.0.0-debug",
    lifespan=debug_lifespan,
    docs_url=None,
    redoc_url=None,
)

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple debug middleware that doesn't crash
@app.middleware("http")
async def debug_middleware(request: Request, call_next):
    try:
        logger.info(f"📥 {request.method} {request.url.path}")
        response = await call_next(request)
        logger.info(f"📤 {request.method} {request.url.path} - {response.status_code}")
        return response
    except HTTPException as e:
        logger.warning(f"🚫 HTTP Exception: {e.status_code} - {e.detail}")
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    except Exception as e:
        logger.error(f"💥 Unexpected error: {e}")
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "error": str(e)})

# Debug endpoints
@app.get("/")
async def debug_root():
    return {"message": "Debug server is working", "status": "ok"}

@app.get("/debug/config")
async def debug_config():
    return {
        "db_host": settings.DB_HOST,
        "db_name": settings.DB_NAME,
        "nms_db_name": settings.NMS_DB_NAME,
        "server_port": settings.SERVER_PORT
    }

@app.get("/debug/db-test")
async def debug_db_test():
    try:
        from database import engine, nms_engine
        from sqlalchemy import text
        
        results = {}
        
        # Test main database
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                results["main_db"] = {"status": "ok"}
        except Exception as e:
            results["main_db"] = {"status": "error", "error": str(e)}
        
        # Test NMS database
        try:
            async with nms_engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                results["nms_db"] = {"status": "ok"}
        except Exception as e:
            results["nms_db"] = {"status": "error", "error": str(e)}
        
        return results
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/auth-test")
async def debug_auth_test():
    try:
        # Test auth service import
        from services.auth_service import get_current_user
        return {"auth_service": "ok"}
    except Exception as e:
        return {"auth_service": "error", "error": str(e)}

# Add a simple devices endpoint without auth
@app.get("/devices-simple")
async def devices_simple():
    try:
        from database import AsyncSessionLocal
        from models.device import MonitoredDevice
        from sqlalchemy import select
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MonitoredDevice).where(MonitoredDevice.is_active == True).limit(5)
            )
            devices = result.scalars().all()
            
            return {
                "devices": [
                    {"hostname": d.hostname, "ip_address": d.ip_address}
                    for d in devices
                ],
                "total": len(devices)
            }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    logger.info(f"🌐 Starting debug server on {settings.SERVER_HOST}:{settings.SERVER_PORT}")
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT, log_level="info")
