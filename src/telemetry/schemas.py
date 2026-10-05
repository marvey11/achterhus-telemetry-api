from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    IMAGE_PULLING = "IMAGE_PULLING"
    STARTING = "STARTING"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CREATE_FAILED = "CREATE_FAILED"
    START_FAILED = "START_FAILED"
    OOM_KILLED = "OOM_KILLED"
    TIMEOUT = "TIMEOUT"
    ORCHESTRATOR_ERROR = "ORCHESTRATOR_ERROR"


class EventSource(StrEnum):
    ORCHESTRATOR = "orchestrator"
    APPLICATION = "application"
    WATCHGUARD = "watchguard"
    API = "api"


class RunRegistration(BaseModel):
    service_name: str = Field(min_length=1, max_length=100)
    run_id: UUID
    status: Literal[RunStatus.SCHEDULED] = RunStatus.SCHEDULED
    source: EventSource = EventSource.ORCHESTRATOR
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunStatusUpdate(BaseModel):
    status: RunStatus
    source: EventSource
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_details: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    logs_summary: str | None = None


class RunEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    source: EventSource
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class RunEventResponse(BaseModel):
    id: int
    run_id: str
    event_type: str
    source: EventSource
    timestamp: datetime
    details: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobRunResponse(BaseModel):
    id: int
    service_name: str
    run_id: str
    status: RunStatus
    source: EventSource
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    metrics: dict[str, Any]
    error_message: str | None
    error_details: dict[str, Any] | None
    logs_summary: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# High-level aggregated overview for service status cards
class ServiceOverview(BaseModel):
    service_name: str
    last_status: RunStatus
    last_run_at: datetime
    total_runs: int
    failed_runs: int
