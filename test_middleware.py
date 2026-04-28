#!/usr/bin/env python3
"""
Test to identify middleware issues
"""

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# Simple middleware that doesn't cause issues
@app.middleware("http")
async def simple_middleware(request: Request, call_next):
    response = await call_next(request)
    return response

@app.get("/")
async def root():
    return {"message": "Test with middleware is working"}

@app.get("/test-auth")
async def test_auth():
    # Simulate what our auth service does
    session_id = "test_session"
    if not session_id:
        return {"error": "No session"}
    return {"user_id": 999, "session_id": session_id}

if __name__ == "__main__":
    import uvicorn
    print("Starting middleware test server")
    uvicorn.run(app, host="0.0.0.0", port=9001)
