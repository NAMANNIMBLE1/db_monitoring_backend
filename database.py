import logging
from typing import AsyncGenerator
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

logger = logging.getLogger(__name__)

# URL-encode the password so special characters like # @ % don't break the URI
_encoded_password = quote_plus(settings.DB_PASSWORD)

# ── port_monitoring database engine (read-write) ──
_pm_url = (
    f"mysql+aiomysql://{settings.DB_USER}:{_encoded_password}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_async_engine(
    _pm_url,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
    connect_args={"connect_timeout": 10},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ── nms database engine (read-only) ──
_nms_url = (
    f"mysql+aiomysql://{settings.DB_USER}:{_encoded_password}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.NMS_DB_NAME}"
)

nms_engine = create_async_engine(
    _nms_url,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
    connect_args={"connect_timeout": 10},
)

NmsAsyncSessionLocal = async_sessionmaker(
    bind=nms_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a port_monitoring database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_nms_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a read-only nms database session."""
    async with NmsAsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables in the port_monitoring database if they don't exist.

    Called once on application startup so there is no need to run
    schema.sql manually.
    """
    from models import Base  # noqa: imported here to avoid circular imports

    logger.info("Opening connection to port_monitoring DB...")
    async with engine.begin() as conn:
        logger.info("Running create_all for tables...")
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified / created")


async def dispose_engines() -> None:
    """Dispose both database engines. Call on shutdown to avoid 'Event loop is closed' errors."""
    await engine.dispose()
    await nms_engine.dispose()
    logger.info("Database engines disposed")
