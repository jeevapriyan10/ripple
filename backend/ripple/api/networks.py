"""API endpoints for inspecting registered payment network profiles and dry-run normalization.

Provides introspection endpoints under /api/v1/networks for fraud analysts,
downstream services, and monitoring tools to query network capabilities and metadata,
as well as a stateless normalization dry-run endpoint.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ripple.adapters.contract import (
    NormalizationResult,
    PayloadFormat,
    RawEvent,
)
from ripple.adapters.errors import (
    NormalizationError,
    UnknownNetworkError,
    UnsupportedPayloadError,
)
from ripple.adapters.profile import NetworkProfile
from ripple.adapters.registry import get_registry
from ripple.config import get_settings

router = APIRouter(prefix="/networks", tags=["networks"])


class NormalizeRequest(BaseModel):
    """Stateless dry-run event normalization request payload."""

    payload: dict[str, Any] | str = Field(
        ...,
        description="Rail-native message payload as JSON object or string.",
    )
    payload_format: PayloadFormat = Field(
        default=PayloadFormat.JSON,
        description="Serialization format of the payload (default JSON).",
    )
    source: str | None = Field(
        default=None,
        description="Optional ingestion queue or origin label.",
    )


async def check_adapters() -> tuple[str, dict[str, Any]]:
    """Readiness probe evaluation for loaded payment network adapters.

    Returns the count and IDs of active adapters. Fails readiness if zero adapters
    are loaded, preventing traffic ingestion into an unconfigured platform.
    """
    registry = get_registry()
    ids = registry.network_ids()
    count = len(ids)
    failed = list(registry.failed_registrations().keys())
    is_ready = count > 0

    return (
        "adapters",
        {
            "count": count,
            "ids": ids,
            "failed": failed,
            "ready": is_ready,
            "status": "ready" if is_ready else "failed",
        },
    )


@router.get(
    "",
    response_model=list[NetworkProfile],
    summary="List all registered payment network profiles",
)
def list_networks() -> list[NetworkProfile]:
    """Retrieve all registered payment network profiles, sorted alphabetically by network_id."""
    registry = get_registry()
    return registry.list_profiles()


@router.get(
    "/{network_id}",
    response_model=NetworkProfile,
    summary="Get payment network profile by ID",
)
def get_network(network_id: str) -> NetworkProfile:
    """Retrieve the static profile for a single registered network ID.

    Returns 404 with a helpful diagnostic message if the network ID is not recognized.
    """
    registry = get_registry()
    try:
        adapter = registry.get(network_id.lower())
        return adapter.profile
    except UnknownNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Payment network '{network_id}' not found. "
                f"Available networks: {registry.network_ids()}"
            ),
        ) from exc


@router.post(
    "/{network_id}/normalize",
    response_model=NormalizationResult,
    summary="Stateless dry-run payment event normalization",
)
def normalize_event(
    network_id: str,
    request: NormalizeRequest,
) -> Any:
    """Execute stateless normalization of a raw rail-native event against the specified adapter.

    Sets received_at to the current server time in UTC (adapters never read the clock).
    Returns 404 if dry-run is disabled or network is unknown.
    Returns 422 with {field, reason} on normalization or payload errors.
    Request payloads are never persisted or logged.
    """
    settings = get_settings()
    if not settings.ENABLE_ADAPTER_DRY_RUN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dry-run normalization endpoint is disabled.",
        )

    registry = get_registry()
    try:
        adapter = registry.get(network_id.lower())
    except UnknownNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Payment network '{network_id}' not found. "
                f"Available networks: {registry.network_ids()}"
            ),
        ) from exc

    raw_event = RawEvent(
        network_id=network_id.lower(),
        payload=request.payload,
        payload_format=request.payload_format,
        received_at=datetime.now(UTC),
        source=request.source,
    )

    try:
        return adapter.normalize(raw_event)
    except NormalizationError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"field": exc.field, "reason": exc.reason},
        )
    except UnsupportedPayloadError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"field": getattr(exc, "field", None), "reason": str(exc)},
        )
