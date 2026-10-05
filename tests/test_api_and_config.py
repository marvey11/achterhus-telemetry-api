import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import telemetry.database as database_module
import telemetry.main as main_module
from telemetry.database import Base
from telemetry.main import (
    app,
    create_run_event,
    get_services_overview,
    list_run_events,
    list_runs,
    register_run,
    update_run_status,
)
from telemetry.schemas import (
    EventSource,
    RunEventCreate,
    RunRegistration,
    RunStatus,
    RunStatusUpdate,
)

RUN_ID = "a0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b"
SECOND_RUN_ID = "b0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b"


def test_database_url_comes_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql+asyncpg://localhost/telemetry"
    monkeypatch.setenv("DATABASE_URL", database_url)

    engine = database_module.build_engine()
    assert engine.url.drivername == "postgresql+asyncpg"
    asyncio.run(engine.dispose())


def test_database_requires_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL must be configured"):
        database_module.build_engine()
    with pytest.raises(ValueError, match="must use PostgreSQL"):
        database_module.build_engine("sqlite+aiosqlite:///:memory:")


def test_lifecycle_events_pagination_and_overview() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    timestamp = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    async def run_case() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            first = await register_run(
                RunRegistration(
                    service_name="newsletter-worker",
                    run_id=UUID(RUN_ID),
                    timestamp=timestamp,
                ),
                session,
            )
            assert first.status.value == RunStatus.SCHEDULED.value
            assert first.started_at is None

            for next_status in (
                RunStatus.IMAGE_PULLING,
                RunStatus.STARTING,
                RunStatus.INITIALIZING,
                RunStatus.RUNNING,
            ):
                updated = await update_run_status(
                    RUN_ID,
                    RunStatusUpdate(
                        status=next_status,
                        source=EventSource.ORCHESTRATOR
                        if next_status
                        in {
                            RunStatus.IMAGE_PULLING,
                            RunStatus.STARTING,
                        }
                        else EventSource.APPLICATION,
                        timestamp=timestamp,
                    ),
                    session,
                )
            assert updated.status.value == RunStatus.RUNNING.value
            assert updated.started_at == timestamp

            heartbeat = await update_run_status(
                RUN_ID,
                RunStatusUpdate(
                    status=RunStatus.RUNNING,
                    source=EventSource.APPLICATION,
                    timestamp=timestamp,
                    metrics={"heartbeat": 1},
                ),
                session,
            )
            assert heartbeat.metrics == {"heartbeat": 1}
            assert heartbeat.started_at == timestamp

            event = await create_run_event(
                RUN_ID,
                RunEventCreate(
                    event_type="checkpoint",
                    source=EventSource.APPLICATION,
                    timestamp=timestamp,
                    details={"step": "download"},
                ),
                session,
            )
            assert event.event_type == "checkpoint"
            events = await list_run_events(RUN_ID, session)
            assert len(events) == 7
            assert events[-1].details == {"step": "download"}

            second = await register_run(
                RunRegistration(
                    service_name="newsletter-worker",
                    run_id=UUID(SECOND_RUN_ID),
                    timestamp=timestamp,
                ),
                session,
            )
            assert second.status.value == RunStatus.SCHEDULED.value
            for next_status in (
                RunStatus.STARTING,
                RunStatus.RUNNING,
            ):
                second = await update_run_status(
                    SECOND_RUN_ID,
                    RunStatusUpdate(
                        status=next_status,
                        source=EventSource.ORCHESTRATOR,
                        timestamp=timestamp,
                    ),
                    session,
                )
            assert second.status.value == RunStatus.RUNNING.value

            await session.refresh(first)
            assert first.status.value == RunStatus.TIMEOUT.value
            assert first.source == EventSource.API
            stale_events = await list_run_events(RUN_ID, session)
            assert stale_events[-1].details["status"] == RunStatus.TIMEOUT.value

            second = await update_run_status(
                SECOND_RUN_ID,
                RunStatusUpdate(
                    status=RunStatus.SUCCESS,
                    source=EventSource.APPLICATION,
                    timestamp=timestamp,
                    metrics={"items_processed": 8},
                ),
                session,
            )
            assert second.status.value == RunStatus.SUCCESS.value
            assert second.metrics == {"items_processed": 8}
            assert second.duration_seconds == 0

            with pytest.raises(HTTPException) as terminal_error:
                await update_run_status(
                    SECOND_RUN_ID,
                    RunStatusUpdate(
                        status=RunStatus.SUCCESS,
                        source=EventSource.APPLICATION,
                        timestamp=timestamp,
                    ),
                    session,
                )
            assert terminal_error.value.status_code == 422

            with pytest.raises(HTTPException) as missing_run:
                await update_run_status(
                    "c0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b",
                    RunStatusUpdate(
                        status=RunStatus.RUNNING,
                        source=EventSource.APPLICATION,
                    ),
                    session,
                )
            assert missing_run.value.status_code == 404

            with pytest.raises(HTTPException) as missing_event:
                await create_run_event(
                    "c0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b",
                    RunEventCreate(
                        event_type="checkpoint",
                        source=EventSource.APPLICATION,
                    ),
                    session,
                )
            assert missing_event.value.status_code == 404
            with pytest.raises(HTTPException) as missing_event_list:
                await list_run_events(
                    "c0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b",
                    session,
                )
            assert missing_event_list.value.status_code == 404

            third_run_id = "d0eebc99-9c2b-4d02-8ec7-3bb7f6f01d2b"
            await register_run(
                RunRegistration(
                    service_name="other-worker",
                    run_id=UUID(third_run_id),
                    timestamp=timestamp,
                ),
                session,
            )
            with pytest.raises(HTTPException) as invalid_transition:
                await update_run_status(
                    third_run_id,
                    RunStatusUpdate(
                        status=RunStatus.SUCCESS,
                        source=EventSource.APPLICATION,
                    ),
                    session,
                )
            assert invalid_transition.value.status_code == 422

            page_one = await list_runs(limit=1, page=1, db=session)
            page_two = await list_runs(limit=1, page=2, db=session)
            assert len(page_one) == 1
            assert len(page_two) == 1
            filtered = await list_runs(
                service_name="newsletter-worker",
                status_filter=RunStatus.SUCCESS,
                limit=10,
                page=1,
                db=session,
            )
            assert [run.run_id for run in filtered] == [SECOND_RUN_ID]

            overview = await get_services_overview(db=session)
            newsletter_summary = next(
                item for item in overview if item.service_name == "newsletter-worker"
            )
            assert newsletter_summary.total_runs == 2
            assert newsletter_summary.failed_runs == 1
            assert newsletter_summary.last_status == RunStatus.SUCCESS

            with pytest.raises(HTTPException) as duplicate_registration:
                await register_run(
                    RunRegistration(
                        service_name="newsletter-worker",
                        run_id=UUID(SECOND_RUN_ID),
                        timestamp=timestamp,
                    ),
                    session,
                )
            assert duplicate_registration.value.status_code == 409

        await engine.dispose()

    asyncio.run(run_case())


def test_lifespan_initializes_and_closes_database() -> None:
    async def run_case() -> None:
        with (
            patch.object(main_module, "init_db", new_callable=AsyncMock) as init_db,
            patch.object(main_module, "close_db", new_callable=AsyncMock) as close_db,
        ):
            async with main_module.lifespan(app):
                pass
            init_db.assert_awaited_once()
            close_db.assert_awaited_once()

    asyncio.run(run_case())


def test_start_invokes_uvicorn_with_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("PORT", "9000")

    with patch("telemetry.main.uvicorn.run") as mocked_run:
        main_module.start()

    mocked_run.assert_called_once_with(
        "telemetry.main:app",
        host="0.0.0.0",  # noqa: S104
        port=9000,
        reload=True,
    )
