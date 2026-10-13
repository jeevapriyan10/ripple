import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ripple import __version__
from ripple.adapters.registry import load_enabled_adapters
from ripple.api.health import READINESS_CHECKS, register_readiness_check
from ripple.api.health import router as health_router
from ripple.api.networks import check_adapters
from ripple.api.networks import router as networks_router
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager for startup and shutdown lifecycle management."""
    settings = get_settings()
    load_enabled_adapters(settings)
    if check_adapters not in READINESS_CHECKS:
        register_readiness_check(check_adapters)
    try:
        yield
    finally:
        if check_adapters in READINESS_CHECKS:
            READINESS_CHECKS.remove(check_adapters)


def create_app() -> FastAPI:
    """FastAPI application factory.

    Configures metadata, structured logging, CORS middleware, lifespan events,
    and API routers using centralized settings.
    """
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Sanitize validation errors so raw input payloads are never echoed back."""
        errors: list[dict[str, Any]] = []
        for err in exc.errors():
            loc = err.get("loc", ())
            field_name = ".".join(str(item) for item in loc if item != "body")
            errors.append(
                {
                    "field": field_name or None,
                    "reason": err.get("msg", "Validation error"),
                }
            )
        first_err = errors[0] if errors else {"field": None, "reason": "Validation error"}
        return JSONResponse(
            status_code=422,
            content={
                "field": first_err["field"],
                "reason": first_err["reason"],
                "errors": errors,
            },
        )

    app.include_router(health_router)
    app.include_router(networks_router, prefix="/api/v1")

    return app


# Default app instance for direct uvicorn invocations
app = create_app()
