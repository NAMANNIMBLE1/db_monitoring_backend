#!/usr/bin/env python3
"""
Test the main app components one by one
"""

import asyncio
import logging

# Configure logging to see all errors
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

def test_imports():
    """Test all imports step by step"""
    logger.info("=== Testing Imports ===")
    
    try:
        from config import settings
        logger.info("✅ Config imported")
    except Exception as e:
        logger.error(f"❌ Config failed: {e}")
        return False
    
    try:
        from database import engine, nms_engine, init_db
        logger.info("✅ Database imported")
    except Exception as e:
        logger.error(f"❌ Database failed: {e}")
        return False
    
    try:
        from services.auth_service import get_current_user
        logger.info("✅ Auth service imported")
    except Exception as e:
        logger.error(f"❌ Auth service failed: {e}")
        return False
    
    try:
        from middleware.auth_middleware import AuthMiddleware
        logger.info("✅ Auth middleware imported")
    except Exception as e:
        logger.error(f"❌ Auth middleware failed: {e}")
        return False
    
    return True

async def test_database_init():
    """Test database initialization"""
    logger.info("=== Testing Database Init ===")
    
    try:
        from database import init_db
        await asyncio.wait_for(init_db(), timeout=10)
        logger.info("✅ Database init successful")
        return True
    except Exception as e:
        logger.error(f"❌ Database init failed: {e}")
        return False

def test_app_creation():
    """Test FastAPI app creation"""
    logger.info("=== Testing App Creation ===")
    
    try:
        from fastapi import FastAPI
        app = FastAPI(title="Test App")
        logger.info("✅ Basic FastAPI app created")
    except Exception as e:
        logger.error(f"❌ Basic FastAPI failed: {e}")
        return False
    
    try:
        from app import app
        logger.info("✅ Main app created")
        return True
    except Exception as e:
        logger.error(f"❌ Main app creation failed: {e}")
        return False

def test_middleware():
    """Test middleware creation"""
    logger.info("=== Testing Middleware ===")
    
    try:
        from middleware.auth_middleware import AuthMiddleware
        from fastapi import FastAPI
        
        app = FastAPI()
        middleware = AuthMiddleware(app)
        logger.info("✅ Middleware created")
        return True
    except Exception as e:
        logger.error(f"❌ Middleware creation failed: {e}")
        return False

async def main():
    """Run all tests"""
    logger.info("🔍 Starting Main App Diagnostic")
    
    success = True
    
    if not test_imports():
        success = False
    
    if not await test_database_init():
        success = False
    
    if not test_app_creation():
        success = False
    
    if not test_middleware():
        success = False
    
    logger.info(f"=== Result: {'✅ SUCCESS' if success else '❌ FAILURE'} ===")
    return success

if __name__ == "__main__":
    asyncio.run(main())
