# Backend image for the AI Quant Platform (FastAPI + uvicorn).
# Build context: ./backend  (set in docker-compose.yml)
#
# NOTE: The DB is an external managed MySQL (Aliyun RDS); this image does NOT
# containerize a database. Connection params arrive via env vars (.env).
# We use pure pandas (no TA-Lib), so only minimal build tooling is needed.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: gcc + mysql client headers for any compiled wheels (e.g.
# cryptography / pymysql fallbacks) and curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better layer caching). pyproject.toml + README
# are enough for `pip install -e .` since the project is a flat `app` package.
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && pip install -e ".[dev]"

# Alembic migrations (optional at runtime; included so the image can run
# `alembic upgrade head` without a rebuild).
COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000

# Non-root user for runtime safety.
RUN useradd --create-home --uid 1001 appuser && chown -R appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
