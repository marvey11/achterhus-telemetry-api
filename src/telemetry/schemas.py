from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WARNING = "WARNING"


# Schema for worker POST requests
class JobRunIngest(BaseModel):
    service_name: str = Field(..., description="Identifier for the container/script")
    run_id: str = Field(..., description="UUID for the execution run")
    status: RunStatus = Field(default=RunStatus.RUNNING)
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    logs_summary: str | None = None


# Schema for API GET responses
class JobRunResponse(JobRunIngest):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# High-level aggregated overview for service status cards
class ServiceOverview(BaseModel):
    service_name: str
    last_status: RunStatus
    last_run_at: datetime
    total_runs: int
    failed_runs: int
