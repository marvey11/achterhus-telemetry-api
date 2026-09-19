import asyncio
import importlib
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import telemetry.database as database_module
from telemetry.database import Base
from telemetry.main import (
    app,
    get_services_overview,
    ingest_run,
    lifespan,
    list_runs,
    start,
)
from telemetry.schemas import JobRunIngest, RunStatus


def test_database_url_supports_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./tmp/test-telemetry.db")

    reloaded = importlib.reload(database_module)

    assert reloaded.DATABASE_URL == "sqlite+aiosqlite:///./tmp/test-telemetry.db"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.reload(database_module)


def test_ingest_and_overview_endpoints() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async def setup_db() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup_db())

    async def run_case() -> None:
        async with session_factory() as session:
            created = await ingest_run(
                JobRunIngest(
                    service_name="newsletter-worker",
                    run_id="a0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b",
                    status=RunStatus.RUNNING,
                    started_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                ),
                session,
            )
            assert created.status == RunStatus.RUNNING

            updated = await ingest_run(
                JobRunIngest(
                    service_name="newsletter-worker",
                    run_id="a0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b",
                    status=RunStatus.FAILED,
                    started_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    error_message="worker crashed",
                    duration_seconds=300.0,
                ),
                session,
            )
            assert updated.status == RunStatus.FAILED
            assert updated.duration_seconds == 300.0

            filtered = await list_runs(
                service_name="newsletter-worker",
                status_filter=RunStatus.FAILED,
                limit=10,
                db=session,
            )
            assert len(filtered) == 1
            assert filtered[0].run_id == "a0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b"

            all_runs = await list_runs(db=session)
            assert len(all_runs) == 1

            overview = await get_services_overview(db=session)
            assert len(overview) == 1
            assert overview[0].service_name == "newsletter-worker"
            assert overview[0].last_status == RunStatus.FAILED
            assert overview[0].total_runs == 1
            assert overview[0].failed_runs == 1
            assert overview[0].last_run_at.isoformat().startswith("2026-01-01T00:00:00")

    asyncio.run(run_case())
    asyncio.run(engine.dispose())

    app.dependency_overrides.clear()


def test_lifespan_initializes_database() -> None:
    async def run_case() -> None:
        async with lifespan(app):
            pass

    asyncio.run(run_case())


def test_start_invokes_uvicorn_with_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("PORT", "9000")

    with patch("telemetry.main.uvicorn.run") as mocked_run:
        start()

    mocked_run.assert_called_once_with(
        "telemetry.main:app",
        host="0.0.0.0",  # noqa: S104
        port=9000,
        reload=True,
    )

    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
