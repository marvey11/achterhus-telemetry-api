# ==========================================
# Stage 1: Build & Dependency Resolution
# ==========================================
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Copy lockfile and package manifests
COPY pyproject.toml uv.lock ./

# Pre-install third-party & Git dependencies (cached unless uv.lock / pyproject.toml changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
    --frozen \
    --no-dev \
    --no-install-project

# Copy full source code; .dockerignore is available
COPY . .

# Fast sync to install audiothek-downloader itself into .venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
    --frozen \
    --no-dev

# ==========================================
# Stage 2: Production Runtime
# ==========================================
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy virtualenv and app code from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["uvicorn", "telemetry.main:app", "--host", "0.0.0.0", "--port", "8000"]
