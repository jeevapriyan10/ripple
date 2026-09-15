FROM python:3.11-slim AS builder

ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    POETRY_NO_ANSI=1

WORKDIR /app
RUN pip install --no-cache-dir poetry
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY ripple ./ripple
RUN poetry install --only main

FROM python:3.11-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN groupadd --system ripple && useradd --system --gid ripple --create-home ripple
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/ripple /app/ripple
USER ripple
EXPOSE 8000
CMD ["sh", "-c", "uvicorn ripple.main:app --host ${API_HOST} --port ${API_PORT}"]
