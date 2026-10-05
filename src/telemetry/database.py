import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def build_engine(database_url: str | None = None) -> AsyncEngine:
    """Create an async PostgreSQL engine from the configured connection string."""
    configured_url = database_url or os.getenv("DATABASE_URL")
    if not configured_url:
        raise RuntimeError("DATABASE_URL must be configured")

    if configured_url.startswith("postgres://"):
        configured_url = configured_url.replace(
            "postgres://", "postgresql+asyncpg://", 1
        )
    elif configured_url.startswith("postgresql://"):
        configured_url = configured_url.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )

    if not configured_url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must use PostgreSQL with the asyncpg driver")

    return create_async_engine(
        configured_url,
        echo=False,
    )


def _get_engine() -> AsyncEngine:
    global engine
    if engine is None:
        engine = build_engine()
    return engine


class Base(DeclarativeBase):
    pass


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _get_session_factory()() as session:
        yield session


async def init_db() -> None:
    async with _get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    global engine, _session_factory
    if engine is not None:
        await engine.dispose()
        engine = None
        _session_factory = None
