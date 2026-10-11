import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ripple import __version__
from ripple.api.health import router as health_router
from ripple.config import get_settings


class JSONLogFormatter(logging.Formatter):
    """Structured JSON log formatter suitable for cloud log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        extra_data = getattr(record, "extra", None)
        if isinstance(extra_data, dict):
            log_entry["extra"] = extra_data

        return json.dumps(log_entry)


def setup_logging(log_level: str) -> None:
    """Configure root logger with structured JSON formatting and the specified log level."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONLogFormatter())

    root_logger = logging.getLogger()
    try:
        numeric_level = getattr(logging, log_level.upper())
    except AttributeError:
        numeric_level = logging.INFO

    root_logger.setLevel(numeric_level)
    root_logger.handlers = [handler]


def create_app() -> FastAPI:
    """FastAPI application factory.

    Configures metadata, structured logging, CORS middleware, and API routers
    using centralized settings.
    """
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)

    return app


# Default app instance for direct uvicorn invocations
app = create_app()
