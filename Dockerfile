FROM ghcr.io/astral-sh/uv:0.11.17 AS uv

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM base AS runtime
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
RUN chmod +x ./scripts/*.sh && useradd --uid 10001 --create-home visionpass
USER visionpass
ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "visionpass.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS cv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libopenblas-dev liblapack-dev && \
    rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --extra cv
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
RUN chmod +x ./scripts/*.sh && useradd --uid 10001 --create-home visionpass
USER visionpass
ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "visionpass.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
RUN uv sync --frozen --extra dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY tests ./tests
RUN chmod +x ./scripts/*.sh
ENTRYPOINT ["./scripts/test-entrypoint.sh"]
CMD ["pytest"]
