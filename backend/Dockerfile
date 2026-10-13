# Multi-stage build for Ripple Backend

# Stage 1: Dependency builder
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.4 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

# Install Poetry
RUN pip install --no-cache-dir poetry=="${POETRY_VERSION}"

# Copy dependency specifications
COPY pyproject.toml ./

# Install only production dependencies into /build/.venv
RUN poetry install --only main --no-root --no-ansi


# Stage 2: Test & lint runner (includes dev dependencies: pytest, ruff, black, mypy)
FROM builder AS tester

WORKDIR /app

COPY pyproject.toml ./
RUN poetry install --no-root --no-ansi

ENV PYTHONPATH=/app

COPY ripple ./ripple
COPY tests ./tests

CMD ["poetry", "run", "pytest", "tests"]


# Stage 3: Production runtime image (default final image, zero dev dependencies)
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    API_HOST=0.0.0.0 \
    API_PORT=8000

# Create non-root user and group
RUN groupadd -r ripple && useradd -r -g ripple -d /app -s /sbin/nologin ripple

# Copy virtual environment from builder stage
COPY --from=builder --chown=ripple:ripple /build/.venv /app/.venv

# Copy application source code
COPY --chown=ripple:ripple ripple ./ripple
COPY --chown=ripple:ripple pyproject.toml ./

# Switch to non-root user
USER ripple

# Expose default port
EXPOSE 8000

# Run uvicorn server respecting API_HOST and API_PORT
CMD ["sh", "-c", "exec uvicorn ripple.main:create_app --factory --host \"${API_HOST:-0.0.0.0}\" --port \"${API_PORT:-8000}\""]
