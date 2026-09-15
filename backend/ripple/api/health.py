"""Liveness and extensible readiness endpoints."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter

from ripple import __version__
from ripple.config import get_settings

DependencyCheck = Callable[[], Awaitable[dict[str, Any]]]
dependency_checks: list[DependencyCheck] = []

router = APIRouter(tags=["health"])


def register_dependency_check(check: DependencyCheck) -> None:
    """Register an async readiness check for a future external dependency."""
    dependency_checks.append(check)


@router.get("/health")
async def health() -> dict[str, str]:
    """Return process liveness without touching external services."""
    settings = get_settings()
    return {"status": "ok", "version": __version__, "env": settings.app_env}


@router.get("/ready")
async def ready() -> dict[str, object]:
    """Return readiness and results from any checks registered by later modules."""
    dependencies: dict[str, Any] = {}
    for check in dependency_checks:
        dependencies.update(await check())
    return {"status": "ready", "dependencies": dependencies}
