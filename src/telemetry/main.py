import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telemetry.database import get_db, init_db
from telemetry.models import JobRun
from telemetry.schemas import JobRunIngest, JobRunResponse, RunStatus, ServiceOverview


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: Initialize DB and WAL mode
    await init_db()
    yield


app = FastAPI(
    title="Telemetry Dashboard API",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow React dev server (e.g. Vite default port 5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production if desired
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ingestion endpoint used by Python workers
@app.post("/api/v1/runs", response_model=JobRunResponse, status_code=status.HTTP_200_OK)
async def ingest_run(
    payload: JobRunIngest, db: AsyncSession = Depends(get_db)
) -> JobRun:
    # Check if run_id exists (e.g. update existing RUNNING job to SUCCESS/FAILED)
    stmt = select(JobRun).where(JobRun.run_id == payload.run_id)
    result = await db.execute(stmt)
    existing_run = result.scalar_one_or_none()

    if existing_run:
        # Update existing record
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(existing_run, key, value)

        started_at = _ensure_utc(payload.started_at)
        ended_at = _ensure_utc(payload.ended_at)
        if ended_at is not None and started_at is not None:
            existing_run.ended_at = ended_at
            existing_run.started_at = started_at
            existing_run.duration_seconds = (ended_at - started_at).total_seconds()

        await db.commit()
        await db.refresh(existing_run)
        normalized_started_at = _ensure_utc(existing_run.started_at)
        normalized_ended_at = _ensure_utc(existing_run.ended_at)
        if normalized_started_at is not None:
            existing_run.started_at = normalized_started_at
        if normalized_ended_at is not None:
            existing_run.ended_at = normalized_ended_at
        return existing_run

    new_run = JobRun(**payload.model_dump())
    started_at = _ensure_utc(payload.started_at)
    ended_at = _ensure_utc(payload.ended_at)
    if started_at is not None:
        new_run.started_at = started_at
    if ended_at is not None:
        new_run.ended_at = ended_at
    if ended_at is not None and started_at is not None:
        new_run.duration_seconds = (ended_at - started_at).total_seconds()

    db.add(new_run)
    await db.commit()
    await db.refresh(new_run)
    normalized_started_at = _ensure_utc(new_run.started_at)
    normalized_ended_at = _ensure_utc(new_run.ended_at)
    if normalized_started_at is not None:
        new_run.started_at = normalized_started_at
    if normalized_ended_at is not None:
        new_run.ended_at = normalized_ended_at
    return new_run


# Endpoint for React Dashboard: Get list of recent runs with optional filtering
@app.get("/api/v1/runs", response_model=list[JobRunResponse])
async def list_runs(
    service_name: str | None = None,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(le=200)] = 50,
    db: AsyncSession = Depends(get_db),
) -> list[JobRun]:
    if limit > 200:
        limit = 200
    stmt = select(JobRun).order_by(JobRun.created_at.desc()).limit(limit)

    if service_name:
        stmt = stmt.where(JobRun.service_name == service_name)
    if status_filter:
        stmt = stmt.where(JobRun.status == status_filter)

    result = await db.execute(stmt)
    runs = list(result.scalars().all())
    for run in runs:
        normalized_started_at = _ensure_utc(run.started_at)
        normalized_ended_at = _ensure_utc(run.ended_at)
        normalized_created_at = _ensure_utc(run.created_at)
        if normalized_started_at is not None:
            run.started_at = normalized_started_at
        if normalized_ended_at is not None:
            run.ended_at = normalized_ended_at
        if normalized_created_at is not None:
            run.created_at = normalized_created_at
    return runs


# Endpoint for React Dashboard: Get high-level summary per service
@app.get("/api/v1/overview", response_model=list[ServiceOverview])
async def get_services_overview(
    db: AsyncSession = Depends(get_db),
) -> list[ServiceOverview]:
    # Get distinct service names
    services_stmt = select(JobRun.service_name).distinct()
    services_res = await db.execute(services_stmt)
    service_names = services_res.scalars().all()

    overview_list: list[ServiceOverview] = []
    for name in service_names:
        # Get latest run for service
        latest_stmt = (
            select(JobRun)
            .where(JobRun.service_name == name)
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
        latest_run = (await db.execute(latest_stmt)).scalar_one()

        # Count total runs
        total_stmt = select(func.count(JobRun.id)).where(JobRun.service_name == name)
        total_runs = (await db.execute(total_stmt)).scalar_one()

        # Count failed runs
        failed_stmt = select(func.count(JobRun.id)).where(
            JobRun.service_name == name, JobRun.status == RunStatus.FAILED
        )
        failed_runs = (await db.execute(failed_stmt)).scalar_one()

        normalized_last_run_at = _ensure_utc(latest_run.started_at)
        last_run_at = (
            normalized_last_run_at
            if normalized_last_run_at is not None
            else latest_run.started_at
        )
        overview_list.append(
            ServiceOverview(
                service_name=name,
                last_status=latest_run.status,
                last_run_at=last_run_at,
                total_runs=total_runs,
                failed_runs=failed_runs,
            )
        )

    return overview_list


def start() -> None:
    """Entry point for project CLI script."""

    # Default to 127.0.0.1 locally; set HOST=0.0.0.0 in Docker Compose
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))

    uvicorn.run("telemetry.main:app", host=host, port=port, reload=True)
