import os
from typing import AsyncGenerator
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://blackboard:blackboard_secret@postgres:5432/blackboard_db",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_recycle=DB_POOL_RECYCLE,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

redis_pool = aioredis.ConnectionPool.from_url(
    REDIS_URL,
    max_connections=25,
    decode_responses=True,
)


def get_redis() -> aioredis.Redis:
    """Return an async Redis client bound to the shared connection pool."""
    return aioredis.Redis(connection_pool=redis_pool)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Initialize PostgreSQL pgvector extension and create all tables with startup retry."""
    import asyncio
    from app import models  # noqa: F401

    for attempt in range(90):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception:
            if attempt == 89:
                raise
            await asyncio.sleep(2.0)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency providing an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
