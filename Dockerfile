# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATA_DIR=/var/lib/apis-agent

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev \
    && chmod +x /app/deploy/entrypoint.sh \
    && useradd --system --uid 10001 --create-home apis \
    && mkdir -p /var/lib/apis-agent \
    && chown -R apis:apis /app /var/lib/apis-agent

USER apis
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail http://127.0.0.1:8080/health/live || exit 1

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
