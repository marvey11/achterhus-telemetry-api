from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from telemetry.database import Base
from telemetry.schemas import RunStatus


def get_enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [str(e.value) for e in enum_cls]


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)

    status: Mapped[RunStatus] = mapped_column(
        SQLEnum(
            RunStatus,
            native_enum=False,  # Use standard VARCHAR/String in SQLite
            values_callable=get_enum_values,
        ),
        index=True,
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)

    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
