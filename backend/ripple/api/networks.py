"""Read-only API endpoints for inspecting registered payment network profiles.

Provides introspection endpoints under /api/v1/networks for fraud analysts,
downstream services, and monitoring tools to query network capabilities and metadata.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from ripple.adapters.errors import UnknownNetworkError
from ripple.adapters.profile import NetworkProfile
from ripple.adapters.registry import get_registry

router = APIRouter(prefix="/networks", tags=["networks"])


async def check_adapters() -> tuple[str, dict[str, Any]]:
    """Readiness probe evaluation for loaded payment network adapters.

    Returns the count and IDs of active adapters. Fails readiness if zero adapters
    are loaded, preventing traffic ingestion into an unconfigured platform.
    """
    registry = get_registry()
    ids = registry.network_ids()
    count = len(ids)
    is_ready = count > 0

    return (
        "adapters",
        {
            "count": count,
            "ids": ids,
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
