import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from middleware.auth_middleware import AuthMiddleware

from routes.agent import router as agent_router
from routes.devices import router as devices_router
from routes.monitor import router as monitor_router
from routes.linux_monitor import router as linux_monitor_router
from routes.service_definitions import router as service_definitions_router
from routes.unified_monitor import router as unified_monitor_router
from routes.db_monitor import router as db_monitor_router
from routes.service_packs import router as service_packs_router
from routes.alert_thresholds import router as alert_thresholds_router
from routes.groups import router as groups_router
from routes.auth import router as auth_router
from routes.test_auth import router as test_auth_router
from routes.debug import router as debug_router
from routes.schema_debug import router as schema_debug_router
from services.stale_checker import start_stale_checker, stop_stale_checker
from services.db_health_scorer import start_db_health_scorer, stop_db_health_scorer
from services.device_sync import start_device_sync, stop_device_sync
from services.data_retention import start_data_retention, stop_data_retention
from services.host_alert_scorer import (
    ensure_alert_instance_nullable,
    seed_default_thresholds,
    start_host_alert_scorer,
    stop_host_alert_scorer,
)

# ── Logging configuration ──

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Port Monitoring NMS API")

    # Auto-create tables if they don't exist
    from database import init_db
    try:
        logger.info("Connecting to database...")
        await asyncio.wait_for(init_db(), timeout=30)
        logger.info("Database initialisation complete")

        # Seed default service definitions and service packs (idempotent)
        from database import AsyncSessionLocal
        from services.service_seeder import seed_default_services, ensure_agent_pack_column, seed_master_service_pack
        try:
            async with AsyncSessionLocal() as db:
                await seed_default_services(db)
            async with AsyncSessionLocal() as db:
                await ensure_agent_pack_column(db)
            async with AsyncSessionLocal() as db:
                await seed_master_service_pack(db)
            async with AsyncSessionLocal() as db:
                await ensure_alert_instance_nullable(db)
            async with AsyncSessionLocal() as db:
                await seed_default_thresholds(db)
        except Exception:
            logger.exception("Failed to seed defaults / service packs")
    except asyncio.TimeoutError:
        logger.error("Database initialisation timed out after 30s -- check MySQL connectivity")
    except Exception:
        logger.exception("Failed to initialise database -- tables may not exist")

    logger.info("Starting background tasks...")
    start_stale_checker()
    start_device_sync()
    start_data_retention()
    start_db_health_scorer()
    start_host_alert_scorer()
    logger.info("Startup complete -- serving requests")
    yield
    logger.info("Shutting down Port Monitoring NMS API")
    stop_stale_checker()
    stop_device_sync()
    stop_data_retention()
    stop_db_health_scorer()
    stop_host_alert_scorer()
    from database import dispose_engines
    await dispose_engines()


# ── Application factory ──

app = FastAPI(
    title="Port Monitoring NMS API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/redoc", include_in_schema=False)
async def custom_redoc():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_js_url="https://unpkg.com/redoc@2.1.5/bundles/redoc.standalone.js",
    )

# ── CORS middleware ──

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Authentication middleware ──
app.add_middleware(AuthMiddleware)

# ── Include routers ──

app.include_router(agent_router, prefix="/api/v1")
app.include_router(devices_router, prefix="/api/v1")
app.include_router(monitor_router, prefix="/api/v1")
app.include_router(linux_monitor_router, prefix="/api/v1")
app.include_router(service_definitions_router, prefix="/api/v1")
app.include_router(unified_monitor_router, prefix="/api/v1")
app.include_router(db_monitor_router, prefix="/api/v1")
app.include_router(service_packs_router, prefix="/api/v1")
app.include_router(alert_thresholds_router, prefix="/api/v1")
app.include_router(groups_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(test_auth_router, prefix="/api/v1")
app.include_router(debug_router, prefix="/api/v1")
app.include_router(schema_debug_router, prefix="/api/v1")


# ── Global exception handler ──

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ── Health check ──

@app.get("/health")
async def health_check():
    return {"status": "healthy"}


# ── Optional: serve the frontend SPA ──
_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring-front-end")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    from config import settings as _cfg

    uvicorn.run("app:app", host=_cfg.SERVER_HOST, port=_cfg.SERVER_PORT, reload=True)
