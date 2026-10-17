"""Tests for the Universal pass-through adapter."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.errors import NormalizationError
from ripple.adapters.universal import UniversalAdapter
from ripple.domain.identity import IdentifierType


def make_valid_universal_payload() -> tuple[dict[str, Any], datetime]:
    """Helper creating a valid payload dictionary matching universal domain models."""
    sender_id = str(uuid4())
    receiver_id = str(uuid4())
    tx_id = str(uuid4())
    now = datetime.now(UTC)

    payload = {
        "transaction": {
            "transaction_id": tx_id,
            "network_transaction_ref": "univ_tx_999",
            "network": "universal",
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "sender_identifier": {
                "type": IdentifierType.EMAIL.value,
                "value": "alice@example.com",
                "network": "universal",
            },
            "receiver_identifier": {
                "type": IdentifierType.EMAIL.value,
                "value": "bob@example.com",
                "network": "universal",
            },
            "amount": {
                "amount": "150.00",
                "currency": "USD",
                "scale": 2,
            },
            "initiated_at": now.isoformat(),
            "settled_at": now.isoformat(),
            "observed_at": (now - timedelta(hours=5)).isoformat(),  # Stale timestamp
            "status": "SETTLED",
            "settlement_model": "INTERNAL_LEDGER",
        },
        "parties": [
            {
                "party_id": sender_id,
                "home_network": "universal",
                "identifiers": [
                    {
                        "type": IdentifierType.EMAIL.value,
                        "value": "alice@example.com",
                        "network": "universal",
                    }
                ],
            },
            {
                "party_id": receiver_id,
                "home_network": "universal",
                "identifiers": [
                    {
                        "type": IdentifierType.EMAIL.value,
                        "value": "bob@example.com",
                        "network": "universal",
                    }
                ],
            },
        ],
    }
    return payload, now


def test_universal_adapter_can_handle() -> None:
    """Assert can_handle accurately performs cheap structural checks."""
    adapter = UniversalAdapter()
    now = datetime.now(UTC)

    # Valid event
    ev_valid = RawEvent(
        network_id="universal",
        payload={"transaction": {}, "parties": []},
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )
    assert adapter.can_handle(ev_valid) is True

    # Wrong network ID
    ev_wrong_net = RawEvent(
        network_id="upi",
        payload={"transaction": {}},
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )
    assert adapter.can_handle(ev_wrong_net) is False

    # Non-dictionary payload
    ev_bytes = RawEvent(
        network_id="universal",
        payload=b"raw bytes",
        payload_format=PayloadFormat.OTHER,
        received_at=now,
    )
    assert adapter.can_handle(ev_bytes) is False


def test_universal_adapter_round_trip_and_observed_at_override() -> None:
    """Assert valid payload normalizes and observed_at is overridden with received_at."""
    adapter = UniversalAdapter()
    payload, now = make_valid_universal_payload()

    ingestion_time = now + timedelta(seconds=10)
    event = RawEvent(
        network_id="universal",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=ingestion_time,
    )

    result = adapter.normalize(event)

    # Assert valid round-trip
    assert result.transaction.network == "universal"
    assert result.transaction.amount.amount == Decimal("150.00")
    assert len(result.parties) == 2
    assert result.correlation_key == f"universal:{result.transaction.transaction_id}"
    assert result.lifecycle_stage == "settled"

    # CRITICAL: transaction.observed_at must be overridden with event.received_at
    assert result.transaction.observed_at == ingestion_time


def test_universal_adapter_deterministic_output() -> None:
    """Assert identical event inputs yield identical NormalizationResult across repeated calls."""
    adapter = UniversalAdapter()
    payload, now = make_valid_universal_payload()

    event = RawEvent(
        network_id="universal",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )

    res1 = adapter.normalize(event)
    res2 = adapter.normalize(event)

    assert res1.transaction.transaction_id == res2.transaction.transaction_id
    assert res1.correlation_key == res2.correlation_key
    assert res1.transaction.observed_at == res2.transaction.observed_at
    assert len(res1.parties) == len(res2.parties)


def test_universal_adapter_malformed_non_dict_payload() -> None:
    """Assert non-dict payload raises NormalizationError with field='payload'."""
    adapter = UniversalAdapter()
    event = RawEvent(
        network_id="universal",
        payload="invalid string payload",
        payload_format=PayloadFormat.JSON,
        received_at=datetime.now(UTC),
    )
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(event)
    assert exc_info.value.field == "payload"


def test_universal_adapter_missing_transaction_key() -> None:
    """Assert missing 'transaction' raises NormalizationError with field='transaction'."""
    adapter = UniversalAdapter()
    event = RawEvent(
        network_id="universal",
        payload={"parties": []},
        payload_format=PayloadFormat.JSON,
        received_at=datetime.now(UTC),
    )
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(event)
    assert exc_info.value.field == "transaction"


def test_universal_adapter_invalid_transaction_field() -> None:
    """Assert validation failure in transaction payload identifies the failing field."""
    adapter = UniversalAdapter()
    payload, now = make_valid_universal_payload()

    # Corrupt transaction: negative amount violates domain invariant
    tx_dict = payload["transaction"]
    assert isinstance(tx_dict, dict)
    tx_dict["amount"] = {
        "amount": "-50.00",
        "currency": "USD",
        "scale": 2,
    }

    event = RawEvent(
        network_id="universal",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(event)
    assert "transaction" in str(exc_info.value.field)


def test_universal_adapter_missing_sender_in_parties() -> None:
    """Assert missing sender party in parties list raises NormalizationError."""
    adapter = UniversalAdapter()
    payload, now = make_valid_universal_payload()

    # Retain only receiver party, removing sender
    parties_list = payload["parties"]
    assert isinstance(parties_list, list)
    payload["parties"] = [parties_list[1]]

    event = RawEvent(
        network_id="universal",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(event)
    assert exc_info.value.field == "parties"
    assert "sender_id" in str(exc_info.value.reason)
