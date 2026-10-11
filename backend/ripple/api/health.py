import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter

from ripple import __version__
from ripple.config import get_settings

router = APIRouter(tags=["health"])

# A readiness check is an async callable returning a tuple: (dependency_name, status_details)
ReadinessCheck = Callable[[], Awaitable[tuple[str, Any]]]

# Registry list of readiness check callables that later modules can append to
READINESS_CHECKS: list[ReadinessCheck] = []


def register_readiness_check(check: ReadinessCheck) -> None:
    """Register an asynchronous dependency check for the /ready endpoint."""
    READINESS_CHECKS.append(check)


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Liveness probe.

    Returns 200 with basic status, version, and environment.
    Must never depend on external services.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "env": settings.APP_ENV,
    }


@router.get("/ready", summary="Readiness probe")
async def ready() -> dict[str, Any]:
    """Readiness probe.

    Evaluates all registered dependency checks (e.g. Neo4j, Kafka).
    Returns 200 with status and dependencies mapping.
    """
    dependencies: dict[str, Any] = {}

    if READINESS_CHECKS:
        results = await asyncio.gather(
            *(check() for check in READINESS_CHECKS),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                dependencies["error"] = str(result)
            elif isinstance(result, tuple) and len(result) == 2:
                name, status = result
                dependencies[name] = status

    return {
        "status": "ready",
        "dependencies": dependencies,
    }
