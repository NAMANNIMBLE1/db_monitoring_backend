#!/usr/bin/env python3
"""
Basic test to identify configuration and startup issues
"""

import sys
import traceback

def test_imports():
    """Test if all modules can be imported"""
    print("=== Testing Imports ===")
    try:
        from config import settings
        print("✅ Config imported successfully")
        print(f"   DB_HOST: {settings.DB_HOST}")
        print(f"   DB_NAME: {settings.DB_NAME}")
        print(f"   NMS_DB_NAME: {settings.NMS_DB_NAME}")
    except Exception as e:
        print(f"❌ Config import failed: {e}")
        traceback.print_exc()
        return False
    
    try:
        from database import engine, nms_engine
        print("✅ Database engines imported successfully")
    except Exception as e:
        print(f"❌ Database engines import failed: {e}")
        traceback.print_exc()
        return False
    
    try:
        from services.auth_service import get_current_user
        print("✅ Auth service imported successfully")
    except Exception as e:
        print(f"❌ Auth service import failed: {e}")
        traceback.print_exc()
        return False
    
    return True

def test_database_connection():
    """Test database connections"""
    print("\n=== Testing Database Connections ===")
    import asyncio
    from database import engine, nms_engine
    
    async def test_connections():
        try:
            # Test main database
            async with engine.begin() as conn:
                from sqlalchemy import text
                result = await conn.execute(text("SELECT 1"))
                print("✅ Main database connection successful")
        except Exception as e:
            print(f"❌ Main database connection failed: {e}")
            return False
        
        try:
            # Test NMS database
            async with nms_engine.begin() as conn:
                from sqlalchemy import text
                result = await conn.execute(text("SELECT 1"))
                print("✅ NMS database connection successful")
        except Exception as e:
            print(f"❌ NMS database connection failed: {e}")
            return False
        
        return True
    
    return asyncio.run(test_connections())

def test_fastapi_app():
    """Test FastAPI app creation"""
    print("\n=== Testing FastAPI App ===")
    try:
        from app import app
        print("✅ FastAPI app created successfully")
        
        # Test app routes
        routes = [route.path for route in app.routes]
        print(f"   Routes registered: {len(routes)}")
        
        # Check if our routes are there
        auth_routes = [r for r in routes if 'auth' in r or 'debug' in r]
        print(f"   Auth/Debug routes: {auth_routes}")
        
    except Exception as e:
        print(f"❌ FastAPI app creation failed: {e}")
        traceback.print_exc()
        return False
    
    return True

def main():
    """Run all tests"""
    print("🔍 NMS Integration Basic Diagnostic\n")
    
    success = True
    
    # Test imports
    if not test_imports():
        success = False
    
    # Test database connections
    if not test_database_connection():
        success = False
    
    # Test FastAPI app
    if not test_fastapi_app():
        success = False
    
    print(f"\n=== Summary ===")
    if success:
        print("✅ All tests passed! The issue might be elsewhere.")
    else:
        print("❌ Some tests failed. This explains the 500 errors.")
    
    return success

if __name__ == "__main__":
    main()
