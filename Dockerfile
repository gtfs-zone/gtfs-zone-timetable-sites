# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# git fetches the gtfs-zone-vocab git dependency.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra pipeline --no-install-project

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra pipeline

# ---

# Debian rather than alpine: Dagster's dependencies ship manylinux wheels only.
FROM python:3.13-slim-bookworm

WORKDIR /app

RUN groupadd -r flier && useradd -r -g flier flier

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY sites.yaml ./

ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME=/app/dagster_home \
    TIMETABLE_SITES_CONFIG=/app/sites.yaml

# DAGSTER_HOME must exist and be writable by the runtime user.
RUN mkdir -p /app/dagster_home && chown flier:flier /app/dagster_home

USER flier

CMD ["dagster", "api", "grpc", "-h", "0.0.0.0", "-p", "4000", "-m", "gtfs_zone_timetable_sites.pipeline.definitions"]
