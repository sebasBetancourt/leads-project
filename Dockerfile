FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    LEADBOT_HOME=/app \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev

COPY config ./config
COPY knowledge ./knowledge
COPY alembic.ini ./
COPY migrations ./migrations

CMD ["leadbot", "--help"]
