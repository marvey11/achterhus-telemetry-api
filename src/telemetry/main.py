import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telemetry.database import close_db, get_db, init_db
from telemetry.models import JobRun, RunEvent
from telemetry.schemas import (
    EventSource,
    JobRunResponse,
    RunEventCreate,
    RunEventResponse,
    RunRegistration,
    RunStatus,
    RunStatusUpdate,
    ServiceOverview,
)

TERMINAL_STATUSES = frozenset(
    {
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.CREATE_FAILED,
        RunStatus.START_FAILED,
        RunStatus.OOM_KILLED,
        RunStatus.TIMEOUT,
        RunStatus.ORCHESTRATOR_ERROR,
    }
)

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.SCHEDULED: frozenset(
        {
            RunStatus.IMAGE_PULLING,
            RunStatus.STARTING,
            RunStatus.CREATE_FAILED,
            RunStatus.ORCHESTRATOR_ERROR,
        }
    ),
    RunStatus.IMAGE_PULLING: frozenset(
        {
            RunStatus.STARTING,
            RunStatus.CREATE_FAILED,
            RunStatus.ORCHESTRATOR_ERROR,
        }
    ),
    RunStatus.STARTING: frozenset(
        {
            RunStatus.INITIALIZING,
            RunStatus.RUNNING,
            RunStatus.START_FAILED,
            RunStatus.OOM_KILLED,
            RunStatus.ORCHESTRATOR_ERROR,
        }
    ),
    RunStatus.INITIALIZING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.SUCCESS,
            RunStatus.FAILED,
            RunStatus.OOM_KILLED,
            RunStatus.TIMEOUT,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUCCESS,
            RunStatus.FAILED,
            RunStatus.OOM_KILLED,
            RunStatus.TIMEOUT,
        }
    ),
}


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalise_run_dates(run: JobRun) -> JobRun:
    for field in ("started_at", "ended_at", "created_at", "updated_at"):
        value = getattr(run, field)
        if value is not None:
            setattr(run, field, _ensure_utc(value))
    return run


def _normalise_event_dates(event: RunEvent) -> RunEvent:
    event.timestamp = _ensure_utc(event.timestamp)
    event.created_at = _ensure_utc(event.created_at)
    return event


def _add_status_event(
    db: AsyncSession,
    run: JobRun,
    *,
    source: EventSource,
    timestamp: datetime,
    error_details: dict[str, Any] | None = None,
) -> None:
    details: dict[str, Any] = {"status": run.status.value}
    if error_details is not None:
        details["error_details"] = error_details
    db.add(
        RunEvent(
            run_id=run.run_id,
            event_type="status_changed",
            source=source,
            timestamp=timestamp,
            details=details,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _ = app
    await init_db()
    try:
        yield
    finally:
        await close_db()


app = FastAPI(
    title="Telemetry API",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post(
    "/api/v1/runs",
    response_model=JobRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_run(
    payload: RunRegistration,
    db: AsyncSession = Depends(get_db),
) -> JobRun:
    run_id = str(payload.run_id)
    existing = await db.scalar(select(JobRun).where(JobRun.run_id == run_id))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run {run_id} is already registered",
        )

    timestamp = _ensure_utc(payload.timestamp)
    run = JobRun(
        service_name=payload.service_name,
        run_id=run_id,
        status=payload.status,
        source=payload.source,
        started_at=None,
        ended_at=None,
        metrics={},
        error_message=None,
        error_details=None,
        logs_summary=None,
        updated_at=timestamp,
    )
    db.add(run)
    _add_status_event(
        db,
        run,
        source=payload.source,
        timestamp=timestamp,
    )
    await db.commit()
    await db.refresh(run)
    return _normalise_run_dates(run)


@app.patch(
    "/api/v1/runs/{run_id}/status",
    response_model=JobRunResponse,
)
async def update_run_status(
    run_id: str,
    payload: RunStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> JobRun:
    run = await db.scalar(select(JobRun).where(JobRun.run_id == run_id))
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} was not found",
        )
    if run.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Run {run_id} is terminal in state {run.status.value}",
        )
    if payload.status != run.status and payload.status not in ALLOWED_TRANSITIONS.get(
        run.status, frozenset()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Transition from {run.status.value} to {payload.status.value} "
            "is not allowed",
        )

    timestamp = _ensure_utc(payload.timestamp)

    if payload.status == RunStatus.RUNNING:
        previous_runs = await db.scalars(
            select(JobRun).where(
                JobRun.service_name == run.service_name,
                JobRun.run_id != run.run_id,
                JobRun.status == RunStatus.RUNNING,
            )
        )
        stale_error = {
            "reason": "StaleRunSuperseded",
            "message": f"Superseded by run {run.run_id}",
        }
        for previous in previous_runs:
            previous.status = RunStatus.TIMEOUT
            previous.source = EventSource.API
            previous.updated_at = timestamp
            previous.ended_at = timestamp
            previous.error_details = stale_error
            previous.error_message = stale_error["message"]
            if previous.started_at is not None:
                started_at = _ensure_utc(previous.started_at)
                previous.started_at = started_at
                previous.duration_seconds = (timestamp - started_at).total_seconds()
            _add_status_event(
                db,
                previous,
                source=EventSource.API,
                timestamp=timestamp,
                error_details=stale_error,
            )

    run.status = payload.status
    run.source = payload.source
    run.updated_at = timestamp
    if run.started_at is None and payload.status in {
        RunStatus.INITIALIZING,
        RunStatus.RUNNING,
    }:
        run.started_at = timestamp
    if payload.status in TERMINAL_STATUSES:
        run.ended_at = timestamp
        if run.started_at is not None:
            started_at = _ensure_utc(run.started_at)
            run.started_at = started_at
            run.duration_seconds = (timestamp - started_at).total_seconds()
    if payload.metrics is not None:
        run.metrics = payload.metrics
    if payload.logs_summary is not None:
        run.logs_summary = payload.logs_summary
    if payload.error_details is not None:
        run.error_details = payload.error_details
        message = payload.error_details.get("message")
        if isinstance(message, str):
            run.error_message = message

    _add_status_event(
        db,
        run,
        source=payload.source,
        timestamp=timestamp,
        error_details=payload.error_details,
    )
    await db.commit()
    await db.refresh(run)
    return _normalise_run_dates(run)


@app.post(
    "/api/v1/runs/{run_id}/events",
    response_model=RunEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_run_event(
    run_id: str,
    payload: RunEventCreate,
    db: AsyncSession = Depends(get_db),
) -> RunEvent:
    run = await db.scalar(select(JobRun).where(JobRun.run_id == run_id))
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} was not found",
        )

    event = RunEvent(
        run_id=run_id,
        event_type=payload.event_type,
        source=payload.source,
        timestamp=_ensure_utc(payload.timestamp),
        details=payload.details,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return _normalise_event_dates(event)


@app.get(
    "/api/v1/runs/{run_id}/events",
    response_model=list[RunEventResponse],
)
async def list_run_events(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[RunEvent]:
    run_exists = await db.scalar(select(JobRun.id).where(JobRun.run_id == run_id))
    if run_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} was not found",
        )
    result = await db.scalars(
        select(RunEvent)
        .where(RunEvent.run_id == run_id)
        .order_by(RunEvent.timestamp, RunEvent.id)
    )
    return [_normalise_event_dates(event) for event in result.all()]


@app.get("/api/v1/runs", response_model=list[JobRunResponse])
async def list_runs(
    service_name: str | None = None,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    page: Annotated[int, Query(ge=1)] = 1,
    db: AsyncSession = Depends(get_db),
) -> list[JobRun]:
    stmt = select(JobRun).order_by(JobRun.created_at.desc(), JobRun.id.desc())

    if service_name:
        stmt = stmt.where(JobRun.service_name == service_name)
    if status_filter:
        stmt = stmt.where(JobRun.status == status_filter)

    result = await db.scalars(stmt.limit(limit).offset((page - 1) * limit))
    return [_normalise_run_dates(run) for run in result.all()]


@app.get("/api/v1/overview", response_model=list[ServiceOverview])
async def get_services_overview(
    db: AsyncSession = Depends(get_db),
) -> list[ServiceOverview]:
    service_names = await db.scalars(select(JobRun.service_name).distinct())
    overview_list: list[ServiceOverview] = []
    failure_statuses = [
        RunStatus.FAILED,
        RunStatus.CREATE_FAILED,
        RunStatus.START_FAILED,
        RunStatus.OOM_KILLED,
        RunStatus.TIMEOUT,
        RunStatus.ORCHESTRATOR_ERROR,
    ]

    for name in service_names:
        latest_run = await db.scalar(
            select(JobRun)
            .where(JobRun.service_name == name)
            .order_by(JobRun.updated_at.desc(), JobRun.id.desc())
            .limit(1)
        )
        if latest_run is None:
            continue
        total_runs = await db.scalar(
            select(func.count(JobRun.id)).where(JobRun.service_name == name)
        )
        failed_runs = await db.scalar(
            select(func.count(JobRun.id)).where(
                JobRun.service_name == name,
                JobRun.status.in_(failure_statuses),
            )
        )
        last_run_at = latest_run.started_at or latest_run.created_at
        overview_list.append(
            ServiceOverview(
                service_name=name,
                last_status=latest_run.status,
                last_run_at=_ensure_utc(last_run_at),
                total_runs=total_runs or 0,
                failed_runs=failed_runs or 0,
            )
        )

    return overview_list


def start() -> None:
    """Entry point for project CLI script."""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("telemetry.main:app", host=host, port=port, reload=True)
