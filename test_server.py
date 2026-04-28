#!/usr/bin/env python3
"""
Minimal test server to identify the issue
"""

import asyncio
from fastapi import FastAPI
from config import settings

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Test server is working"}

@app.get("/config")
async def get_config():
    return {
        "db_host": settings.DB_HOST,
        "db_name": settings.DB_NAME,
        "nms_db_name": settings.NMS_DB_NAME,
        "server_port": settings.SERVER_PORT
    }

@app.get("/test-db")
async def test_db():
    try:
        from database import engine, nms_engine
        from sqlalchemy import text
        
        # Test main database
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            main_db_ok = True
    except Exception as e:
        main_db_ok = False
        main_db_error = str(e)
    
    try:
        # Test NMS database  
        async with nms_engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            nms_db_ok = True
    except Exception as e:
        nms_db_ok = False
        nms_db_error = str(e)
        
        return {
            "main_db": {
                "ok": main_db_ok,
                "error": main_db_error if not main_db_ok else None
            },
            "nms_db": {
                "ok": nms_db_ok,
                "error": nms_db_error if not nms_db_ok else None
            }
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    print(f"Starting test server on port {settings.SERVER_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=settings.SERVER_PORT)
