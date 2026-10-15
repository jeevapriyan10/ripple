"""Tests for the networks REST API, dry-run normalization, and readiness probes."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ripple.adapters.registry import get_registry, load_enabled_adapters
from ripple.adapters.universal import UniversalAdapter
from ripple.config import Settings
from ripple.main import create_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Test client fixture running within FastAPI lifespan context manager."""
    app = create_app()
    with TestClient(app) as test_client:
        # Load standard adapters for testing
        settings = Settings(
            APP_ENV="development",
            CARD_PAN_TOKEN_KEY="test_pan_token_key_for_unit_tests_32chars_min",
            ENABLE_ADAPTER_DRY_RUN=True,
        )
        load_enabled_adapters(settings=settings, registry=get_registry())
        yield test_client


def test_list_networks_endpoint(client: TestClient) -> None:
    """Assert GET /api/v1/networks returns 200 and lists universal, upi, and card."""
    response = client.get("/api/v1/networks")
    assert response.status_code == 200

    profiles = response.json()
    assert isinstance(profiles, list)
    assert len(profiles) >= 3

    network_ids = [p["network_id"] for p in profiles]
    assert "universal" in network_ids
    assert "upi" in network_ids
    assert "card" in network_ids
    assert "crypto" in network_ids
    assert "swift" in network_ids
    assert "wallet" in network_ids


def test_get_network_detail_success(client: TestClient) -> None:
    """Assert GET /api/v1/networks/{network_id} returns 200 with the requested profile."""
    response = client.get("/api/v1/networks/upi")
    assert response.status_code == 200

    profile = response.json()
    assert profile["network_id"] == "upi"
    assert profile["category"] == "INSTANT_PAYMENT"
    assert profile["settlement_model"] == "INSTANT"
    assert profile["operating_timezone"] == "Asia/Kolkata"
    assert "INR" in profile["supported_currencies"]


def test_get_network_detail_404_not_found(client: TestClient) -> None:
    """Assert GET /api/v1/networks/{network_id} returns 404 for an unregistered network ID."""
    response = client.get("/api/v1/networks/nonexistent_rail")
    assert response.status_code == 404

    detail = response.json()["detail"]
    assert "nonexistent_rail" in detail
    assert "not found" in detail


def test_dry_run_normalize_upi_success(client: TestClient) -> None:
    """Assert POST /api/v1/networks/upi/normalize returns 200 with NormalizationResult."""
    with open(FIXTURES_DIR / "upi" / "pay_success.json", encoding="utf-8") as f:
        payload = json.load(f)

    response = client.post(
        "/api/v1/networks/upi/normalize",
        json={"payload": payload, "payload_format": "JSON", "source": "test_queue"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "transaction" in data
    assert data["transaction"]["network"] == "UPI"
    assert data["transaction"]["status"] == "SETTLED"
    assert data["correlation_key"] == "upi:UPI_TXN_001_PAY_SUCCESS"
    assert len(data["parties"]) == 2


def test_dry_run_normalize_card_success(client: TestClient) -> None:
    """Assert POST /api/v1/networks/card/normalize returns 200 with NormalizationResult."""
    with open(FIXTURES_DIR / "card" / "auth_approved.json", encoding="utf-8") as f:
        payload = json.load(f)

    response = client.post(
        "/api/v1/networks/card/normalize",
        json={"payload": payload, "payload_format": "JSON"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "transaction" in data
    assert data["transaction"]["network"] == "CARD"
    assert data["transaction"]["status"] == "AUTHORIZED"
    assert data["correlation_key"] == "card:NETREF_CARD_001_AUTH"


def test_dry_run_normalize_malformed_payload_with_pan_never_leaks_pan(
    client: TestClient,
) -> None:
    """Assert malformed payload containing a PAN returns 422 and does NOT leak PAN in body."""
    raw_pan = "4111111111111112"  # Invalid Luhn
    payload = {
        "event_type": "authorization",
        "network_reference": "NETREF_TEST_422",
        "processing_code": "00",
        "pan": raw_pan,
        "amount": "100.00",
        "currency": "USD",
        "transmission_time": "2026-10-13T12:00:00Z",
        "acquirer": {"id": "ACQ_1"},
    }

    response = client.post(
        "/api/v1/networks/card/normalize",
        json={"payload": payload},
    )
    assert response.status_code == 422
    body_text = response.text

    assert raw_pan not in body_text
    data = response.json()
    assert "field" in data
    assert "reason" in data


def test_dry_run_normalize_crypto_success(client: TestClient) -> None:
    """Assert POST /api/v1/networks/crypto/normalize returns 200 with NormalizationResult."""
    with open(FIXTURES_DIR / "crypto" / "transfer_finalized.json", encoding="utf-8") as f:
        payload = json.load(f)

    response = client.post(
        "/api/v1/networks/crypto/normalize",
        json={"payload": payload, "payload_format": "JSON"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "transaction" in data
    assert data["transaction"]["network"] == "CRYPTO"
    assert data["transaction"]["status"] == "CONFIRMED"
    assert "crypto:ethereum" in data["correlation_key"]


def test_dry_run_normalize_swift_success(client: TestClient) -> None:
    """Assert POST /api/v1/networks/swift/normalize returns 200 with NormalizationResult."""
    with open(FIXTURES_DIR / "swift" / "payment_instruction.json", encoding="utf-8") as f:
        payload = json.load(f)

    response = client.post(
        "/api/v1/networks/swift/normalize",
        json={"payload": payload, "payload_format": "JSON"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "transaction" in data
    assert data["transaction"]["network"] == "SWIFT"
    assert data["transaction"]["status"] == "INITIATED"
    assert data["correlation_key"] == "swift:eb92b1a0-1234-4567-89ab-cdef01234567"


def test_dry_run_normalize_malformed_swift_payload_never_leaks_iban(client: TestClient) -> None:
    """Assert malformed SWIFT payload with invalid IBAN returns 422 without leaking IBAN."""
    invalid_iban = "DE89370400440532013099"  # Checksum corrupt
    with open(FIXTURES_DIR / "swift" / "payment_instruction.json", encoding="utf-8") as f:
        payload = json.load(f)
    payload["debtor"]["account"]["iban"] = invalid_iban

    response = client.post(
        "/api/v1/networks/swift/normalize",
        json={"payload": payload, "payload_format": "JSON"},
    )
    assert response.status_code == 422
    body_text = response.text

    assert invalid_iban not in body_text
    data = response.json()
    assert "field" in data
    assert "reason" in data


def test_enabled_networks_loads_crypto_and_swift_only() -> None:
    """Assert ENABLED_NETWORKS='crypto,swift' loads only those two networks."""
    registry = get_registry()
    registry.clear()

    settings = Settings(
        APP_ENV="development",
        ENABLED_NETWORKS="crypto,swift",
    )
    load_enabled_adapters(settings=settings, registry=registry)

    loaded_ids = registry.network_ids()
    assert loaded_ids == ["crypto", "swift"]
    assert registry.has("crypto")
    assert registry.has("swift")
    assert not registry.has("upi")
    assert not registry.has("card")
    assert not registry.has("universal")


def test_dry_run_normalize_unknown_network_returns_404(client: TestClient) -> None:
    """Assert dry-run normalization on an unknown network returns 404."""
    response = client.post(
        "/api/v1/networks/unknown_rail/normalize",
        json={"payload": {"foo": "bar"}},
    )
    assert response.status_code == 404


def test_dry_run_normalize_disabled_returns_404() -> None:
    """Assert dry-run normalization endpoint returns 404 when ENABLE_ADAPTER_DRY_RUN is false."""
    disabled_settings = Settings(
        APP_ENV="development",
        ENABLE_ADAPTER_DRY_RUN=False,
    )
    with patch("ripple.api.networks.get_settings", return_value=disabled_settings):
        app = create_app()
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/v1/networks/upi/normalize",
                json={"payload": {"txn_id": "test"}},
            )
            assert response.status_code == 404


def test_ready_probe_with_and_without_adapters(client: TestClient) -> None:
    """Assert /ready fails when registry is empty and passes when adapters are loaded."""
    registry = get_registry()

    # 1. Clear registry to simulate zero loaded adapters
    registry.clear()
    assert len(registry.network_ids()) == 0

    response_empty = client.get("/ready")
    assert response_empty.status_code == 503
    data_empty = response_empty.json()
    assert data_empty["status"] == "not_ready"
    assert data_empty["dependencies"]["adapters"]["count"] == 0
    assert data_empty["dependencies"]["adapters"]["ready"] is False

    # 2. Register universal adapter -> readiness passes
    registry.register(UniversalAdapter, replace=True)
    assert len(registry.network_ids()) == 1

    response_ready = client.get("/ready")
    assert response_ready.status_code == 200
    data_ready = response_ready.json()
    assert data_ready["status"] == "ready"
    assert data_ready["dependencies"]["adapters"]["ready"] is True


def test_readiness_reflects_card_absence_when_production_registration_fails() -> None:
    """Assert in production with no key, card registration fails and /ready reflects it."""
    registry = get_registry()
    registry.clear()

    prod_settings = Settings(
        APP_ENV="production",
        CARD_PAN_TOKEN_KEY=None,
        ENABLED_NETWORKS="all",
    )
    with patch("ripple.main.get_settings", return_value=prod_settings):
        with patch("ripple.api.networks.get_settings", return_value=prod_settings):
            app = create_app()
            with TestClient(app) as test_client:
                resp = test_client.get("/ready")
                assert resp.status_code == 200
                data = resp.json()
                adapters_dep = data["dependencies"]["adapters"]
                assert "card" not in adapters_dep["ids"]
                assert "card" in adapters_dep["failed"]


def test_dry_run_normalize_wallet_success(client: TestClient) -> None:
    """Assert POST /api/v1/networks/wallet/normalize returns 200 with NormalizationResult."""
    with open(FIXTURES_DIR / "wallet" / "p2p_send_completed.json", encoding="utf-8") as f:
        payload = json.load(f)

    response = client.post(
        "/api/v1/networks/wallet/normalize",
        json={"payload": payload},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["transaction"]["network"] == "wallet"
    assert data["transaction"]["status"] == "SETTLED"
    assert data["correlation_key"] == "wallet:WLT_TXN_001_P2P"


def test_dry_run_wallet_malformed_with_card_number_returns_422_without_leaking(
    client: TestClient,
) -> None:
    """Assert malformed wallet payload with card_number returns 422.

    Body must not contain the sensitive card number.
    """
    malformed_payload = {
        "event_type": "p2p",
        "txn_id": "WLT_LEAK_TEST_01",
        "status": "COMPLETED",
        "card_number": "4111111111111111",
        # Missing amount, sender, receiver, created_at to trigger 422
    }

    response = client.post(
        "/api/v1/networks/wallet/normalize",
        json={"payload": malformed_payload},
    )
    assert response.status_code == 422
    body_text = response.text
    # Sensitive card_number must NEVER appear in error response body
    assert "4111111111111111" not in body_text


def test_enabled_networks_wallet_only() -> None:
    """Assert ENABLED_NETWORKS=wallet loads only the wallet adapter."""
    registry = get_registry()
    registry.clear()

    wallet_only_settings = Settings(
        APP_ENV="development",
        ENABLED_NETWORKS="wallet",
    )
    load_enabled_adapters(settings=wallet_only_settings, registry=registry)

    assert registry.network_ids() == ["wallet"]
    assert registry.has("wallet") is True
    assert registry.has("upi") is False
    assert registry.has("card") is False
