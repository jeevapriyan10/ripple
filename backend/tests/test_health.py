from typing import Any

import pytest
from fastapi.testclient import TestClient

from ripple import __version__
from ripple.api.health import READINESS_CHECKS, register_readiness_check
from ripple.config import get_settings
from ripple.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Fixture providing a FastAPI TestClient configured from the application factory."""
    app = create_app()
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    """Assert GET /health returns 200 and expected liveness keys."""
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "env" in data

    assert data["status"] == "ok"
    assert data["version"] == __version__
    assert data["env"] == get_settings().APP_ENV


def test_ready_endpoint(client: TestClient) -> None:
    """Assert GET /ready returns 200 and empty dependencies by default."""
    response = client.get("/ready")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "dependencies" in data

    assert data["status"] == "ready"
    assert data["dependencies"] == {}


def test_ready_with_registered_check(client: TestClient) -> None:
    """Assert registered dependency checks are evaluated and returned in /ready."""

    async def sample_db_check() -> tuple[str, dict[str, Any]]:
        return ("neo4j", {"connected": True, "latency_ms": 1.2})

    # Register check
    register_readiness_check(sample_db_check)
    try:
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "neo4j" in data["dependencies"]
        assert data["dependencies"]["neo4j"]["connected"] is True
    finally:
        # Reset check registry so subsequent tests are unaffected
        READINESS_CHECKS.remove(sample_db_check)
