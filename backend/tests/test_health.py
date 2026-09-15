from fastapi.testclient import TestClient

from ripple.main import create_app


def test_health_returns_liveness_metadata() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert {"status", "version", "env"} <= response.json().keys()


def test_ready_returns_dependency_results() -> None:
    response = TestClient(create_app()).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dependencies": {}}
