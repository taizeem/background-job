# Stage 1: Base Python 3.12 Slim image with uv binary
FROM python:3.12-slim-bookworm AS base

# Copy the official uv binary from Astral's image
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

# Prevent Python from writing .pyc files, buffer stdout/stderr, and disable dev dependencies
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Create a non-root group and user, and assign ownership of /app
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser && \
    chown -R appuser:appuser /app

# Switch to non-root user before installing dependencies so appuser owns .venv
USER appuser

# Install production dependencies (cached layer unless pyproject.toml or uv.lock changes)
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code
COPY --chown=appuser:appuser . .

EXPOSE 8000

# Default command runs migrations and starts the Gunicorn WSGI server
CMD ["sh", "-c", "uv run --no-dev python manage.py migrate --noinput && uv run --no-dev gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60"]