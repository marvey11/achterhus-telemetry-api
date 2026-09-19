# Achterhus Telemetry API

A FastAPI service for collecting job-run telemetry from containerized services and exposing that data through a lightweight dashboard API.

## Overview

This project provides a backend for service health and execution telemetry. Dockerized workers post run metadata to the API, and a separate frontend can query recent runs or aggregated service summaries without coupling directly to the database.

## Features

- Receive telemetry payloads from worker services via `/api/v1/runs`
- Store run metadata in SQLite using SQLAlchemy async models
- Query recent job runs with optional service and status filters
- Aggregate service-level summaries for dashboard cards via `/api/v1/overview`
- Support local development with FastAPI, Uvicorn, and pytest

## Project Layout

- `src/telemetry/` — application package
- `tests/` — pytest coverage for API and configuration behavior
- `pyproject.toml` — project metadata, dependency groups, and tool configuration
- `.github/workflows/ci.yml` — CI quality gate
- `.pre-commit-config.yaml` — local validation hooks

## Local Setup

Use `uv` from the repository root:

```bash
uv sync --locked --all-extras --dev
```

## Running the API

Start the API locally:

```bash
uv run uvicorn telemetry.main:app --host 127.0.0.1 --port 8000 --reload
```

The project also exposes a CLI entry point:

```bash
uv run telemetry-api
```

## API Endpoints

### POST `/api/v1/runs`

Ingest a job run payload.

Example body:

```json
{
  "service_name": "newsletter-worker",
  "run_id": "8d4b64d8-7e28-42f8-8cb8-3b4d202d3d83",
  "status": "RUNNING",
  "started_at": "2026-01-01T12:00:00Z",
  "metrics": {"duration_ms": 1234},
  "logs_summary": "Worker started successfully"
}
```

### GET `/api/v1/runs`

Returns recent job runs, with optional filters:

- `service_name`
- `status`
- `limit` (max 200)

### GET `/api/v1/overview`

Returns one record per service with the latest status, latest run time, total runs, and failed-runs count.

## Development and Validation

Run the repository checks before submitting changes:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```

## Configuration Notes

- The default database is SQLite at the project root: `telemetry.db`
- `DATABASE_URL` can be overridden via the environment for local or test configuration
- The application uses timezone-aware timestamps for `started_at`, `ended_at`, and `created_at`

## Change Log

See [CHANGELOG.md](CHANGELOG.md) for a high-level history of the project.
