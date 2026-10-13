"""Tests for the networks REST API and readiness probe evaluation."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ripple.adapters.registry import get_registry
from ripple.adapters.universal import UniversalAdapter
from ripple.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Test client fixture running within FastAPI lifespan context manager."""
    app = create_app()
    with TestClient(app) as test_client:
        # Ensure universal adapter is registered for base tests
        get_registry().register(UniversalAdapter, replace=True)
        yield test_client


def test_list_networks_endpoint(client: TestClient) -> None:
    """Assert GET /api/v1/networks returns 200 and a list of registered profiles."""
    response = client.get("/api/v1/networks")
    assert response.status_code == 200

    profiles = response.json()
    assert isinstance(profiles, list)
    assert len(profiles) >= 1

    network_ids = [p["network_id"] for p in profiles]
    assert "universal" in network_ids


def test_get_network_detail_success(client: TestClient) -> None:
    """Assert GET /api/v1/networks/{network_id} returns 200 with the requested profile."""
    response = client.get("/api/v1/networks/universal")
    assert response.status_code == 200

    profile = response.json()
    assert profile["network_id"] == "universal"
    assert profile["category"] == "OTHER"
    assert profile["settlement_model"] == "INTERNAL_LEDGER"
    assert profile["schema_version"] == 1


def test_get_network_detail_404_not_found(client: TestClient) -> None:
    """Assert GET /api/v1/networks/{network_id} returns 404 for an unregistered network ID."""
    response = client.get("/api/v1/networks/nonexistent_rail")
    assert response.status_code == 404

    detail = response.json()["detail"]
    assert "nonexistent_rail" in detail
    assert "not found" in detail


def test_ready_probe_with_and_without_adapters(client: TestClient) -> None:
    """Assert /ready fails when registry is empty and passes when universal adapter is registered."""
    registry = get_registry()

    # 1. Clear registry to simulate zero loaded adapters
    registry.clear()
    assert len(registry.network_ids()) == 0

    response_empty = client.get("/ready")
    assert response_empty.status_code == 503
    data_empty = response_empty.json()
    assert data_empty["status"] == "not_ready"
    assert "adapters" in data_empty["dependencies"]
    assert data_empty["dependencies"]["adapters"]["count"] == 0
    assert data_empty["dependencies"]["adapters"]["ready"] is False
    assert data_empty["dependencies"]["adapters"]["status"] == "failed"

    # 2. Register universal adapter -> readiness should pass
    registry.register(UniversalAdapter, replace=True)
    assert len(registry.network_ids()) == 1

    response_ready = client.get("/ready")
    assert response_ready.status_code == 200
    data_ready = response_ready.json()
    assert data_ready["status"] == "ready"
    assert "adapters" in data_ready["dependencies"]
    assert data_ready["dependencies"]["adapters"]["count"] == 1
    assert "universal" in data_ready["dependencies"]["adapters"]["ids"]
    assert data_ready["dependencies"]["adapters"]["ready"] is True
    assert data_ready["dependencies"]["adapters"]["status"] == "ready"
