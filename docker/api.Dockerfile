# OpenEarth API — multi-stage uv build.
#
# Builder resolves the openearth-api dependency tree ONLY (--package openearth-api),
# so the offline ML stack (torch/smp, multi-GB) is never installed — the API never
# imports it. Workspace members install editable, so the runtime image carries both
# the .venv and packages/ source. Build context is the repo root.

# ── builder ──────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy the workspace manifests + lockfile first (cached unless deps change), then
# the source. All members' pyproject.toml are needed to resolve the locked graph.
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/

# --frozen: fail if uv.lock is stale; --no-dev: skip test/lint tooling;
# --package openearth-api: install only the API + its runtime deps (pulls in
# openearth-core, excludes openearth-ml).
RUN uv sync --frozen --no-dev --no-editable --package openearth-api

# ── runtime ──────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

# Non-root runtime user; the /data volume is chowned so SQLite/diskcache can write.
RUN useradd --create-home --uid 10001 openearth \
    && mkdir -p /data && chown openearth:openearth /data

WORKDIR /app
COPY --from=builder --chown=openearth:openearth /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    OPENEARTH_DATA_DIR=/data \
    PYTHONUNBUFFERED=1

USER openearth
EXPOSE 8000

# create_app() is EE/DB-free at import; the lifespan brings up the DB and a
# non-fatal EE init. A missing EE/Earthdata credential just 503s the EE routes.
CMD ["uvicorn", "openearth_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
