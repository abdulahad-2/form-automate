import os
from typing import AsyncGenerator
from sqlalchemy import create_engine, MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://username:password@localhost:5432/email_automation")
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Create async engine for FastAPI
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=True,  # Set to False in production
    future=True,
)

# Create sync engine for Alembic migrations
sync_engine = create_engine(
    DATABASE_URL,
    echo=True,  # Set to False in production
)

# Create session factories
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

SessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)

# Create base model class
Base = declarative_base()

# Metadata for Alembic
metadata = MetaData()

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

class RedisManager:
    def __init__(self):
        self.redis = None

    async def init_redis(self):
        self.redis = await redis.from_url(REDIS_URL, decode_responses=True)
        return self.redis

    async def close_redis(self):
        if self.redis:
            await self.redis.close()

redis_manager = RedisManager()

# Database dependency for FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# Redis dependency for FastAPI
async def get_redis():
    if not redis_manager.redis:
        await redis_manager.init_redis()
    return redis_manager.redis

# Utility function to get sync session (for migrations and scripts)
def get_sync_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def init_db():
    """Initialize database tables"""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def close_db():
    """Close database connections"""
    await async_engine.dispose()
    await redis_manager.close_redis()