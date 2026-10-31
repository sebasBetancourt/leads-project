FROM python:3.12-slim

# Misma versión de uv que generó uv.lock: así `--locked` instala exactamente lo que probó CI.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    LEADBOT_HOME=/app \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --no-dev --locked

COPY config ./config
COPY knowledge ./knowledge
COPY alembic.ini ./
COPY migrations ./migrations

# Sin root: el worker maneja credenciales de Gmail y de Google. `output/` se monta desde el host (propuestas PDF).
RUN useradd --create-home --uid 1000 leadbot && mkdir -p /app/output && chown leadbot /app/output
USER leadbot

CMD ["leadbot", "--help"]
