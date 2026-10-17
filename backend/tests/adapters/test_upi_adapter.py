"""Comprehensive tests for the UPI instant-payment adapter (Module 4)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.errors import (
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.upi import UpiAdapter
from ripple.domain.identity import IdentifierType
from ripple.domain.party import PartyType
from ripple.domain.transaction import (
    SettlementModel,
    TransactionDirection,
    TransactionStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "upi"


def load_fixture(name: str) -> dict[str, object]:
    """Helper to load a JSON fixture dictionary."""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def make_raw_event(
    payload: dict[str, object] | str | bytes,
    received_at: datetime | None = None,
) -> RawEvent:
    """Helper creating a RawEvent for UPI adapter testing."""
    return RawEvent(
        network_id="upi",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=received_at or datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )


def test_upi_pay_success() -> None:
    """Assert successful PAY message normalizes to SETTLED with correct attributes."""
    adapter = UpiAdapter()
    fixture = load_fixture("pay_success.json")
    event = make_raw_event(fixture)

    res = adapter.normalize(event)

    tx = res.transaction
    assert tx.network == "UPI"
    assert tx.network_transaction_ref == "UPI_TXN_001_PAY_SUCCESS"
    assert tx.status == TransactionStatus.SETTLED
    assert tx.settlement_model == SettlementModel.INSTANT
    assert tx.direction == TransactionDirection.TRANSFER
    assert tx.amount.amount == Decimal("1500.50")
    assert tx.amount.currency == "INR"
    assert tx.amount.scale == 2
    assert tx.settled_at is not None
    assert tx.settled_at == datetime(2026, 10, 13, 4, 30, 5, tzinfo=UTC)
    assert tx.channel == "mobile_app"
    assert tx.purpose_code == "00"
    assert tx.device_id == "device_abc_123"
    assert tx.ip_address == "103.21.244.1"
    assert tx.network_metadata["rrn"] == "123456789012"
    assert tx.network_metadata["remarks"] == "Dinner split with friends"
    assert tx.network_metadata["response_code"] == "00"
    assert tx.network_metadata["response_code_label"] == "success"
    assert res.lifecycle_stage == "completed"

    # Parties verification
    assert len(res.parties) == 2
    payer = next(p for p in res.parties if p.party_id == tx.sender_id)
    payee = next(p for p in res.parties if p.party_id == tx.receiver_id)

    assert payer.party_type == PartyType.INDIVIDUAL
    assert payer.attributes["psp_handle"] == "oksbi"
    assert len(payer.identifiers) == 3  # VPA, Bank Account, Phone
    assert payer.identifiers[0].type == IdentifierType.VPA

    assert payee.party_type == PartyType.INDIVIDUAL
    assert payee.attributes["psp_handle"] == "okaxis"


def test_upi_pay_failure() -> None:
    """Assert failed PAY message maps to FAILED with decline code and merchant party."""
    adapter = UpiAdapter()
    fixture = load_fixture("pay_failure.json")
    event = make_raw_event(fixture)

    res = adapter.normalize(event)
    tx = res.transaction

    assert tx.status == TransactionStatus.FAILED
    assert tx.settled_at is None
    assert res.lifecycle_stage == "failed"
    assert tx.network_metadata["response_code"] == "U30"
    assert tx.network_metadata["response_code_label"] == "debit failed"

    # Payee merchant attributes
    payee = next(p for p in res.parties if p.party_id == tx.receiver_id)
    assert payee.party_type == PartyType.MERCHANT
    assert payee.attributes["mcc"] == "5411"
    assert payee.attributes["merchant_id"] == "MERC12345"


def test_upi_pending_then_success_shared_correlation_and_id() -> None:
    """Assert pending event followed by success share correlation_key and transaction_id."""
    adapter = UpiAdapter()
    collect_pending = load_fixture("collect_pending.json")
    collect_success = load_fixture("collect_success.json")

    res_pending = adapter.normalize(make_raw_event(collect_pending))
    res_success = adapter.normalize(make_raw_event(collect_success))

    assert res_pending.correlation_key == res_success.correlation_key == "upi:UPI_TXN_003_COLLECT"
    assert res_pending.transaction.transaction_id == res_success.transaction.transaction_id
    assert res_pending.transaction.status == TransactionStatus.PENDING_AUTHORIZATION
    assert res_success.transaction.status == TransactionStatus.SETTLED


def test_upi_full_collect_lifecycle_and_expired() -> None:
    """Assert full COLLECT progression (requested -> approved -> success) and an expired collect."""
    adapter = UpiAdapter()

    # Stage 1: Collect requested (pending approval)
    pending_payload = load_fixture("collect_pending.json")
    res1 = adapter.normalize(make_raw_event(pending_payload))
    assert res1.transaction.status == TransactionStatus.PENDING_AUTHORIZATION
    assert res1.lifecycle_stage == "collect_requested"

    # Stage 2: Collect approved by payer (initiated_at populated)
    approved_payload = load_fixture("collect_approved.json")
    res2 = adapter.normalize(make_raw_event(approved_payload))
    assert res2.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res2.lifecycle_stage == "pending"

    # Stage 3: Collect settled
    success_payload = load_fixture("collect_success.json")
    res3 = adapter.normalize(make_raw_event(success_payload))
    assert res3.transaction.status == TransactionStatus.SETTLED
    assert res3.lifecycle_stage == "completed"

    # All share identity
    assert (
        res1.transaction.transaction_id
        == res2.transaction.transaction_id
        == res3.transaction.transaction_id
    )

    # Stage 4: Expired collect
    expired_payload = load_fixture("collect_expired.json")
    res_expired = adapter.normalize(make_raw_event(expired_payload))
    assert res_expired.transaction.status == TransactionStatus.CANCELLED
    assert res_expired.lifecycle_stage == "expired"


def test_upi_money_direction_is_payer_to_payee_for_collect() -> None:
    """Assert direction of money flow is ALWAYS payer -> payee, even for COLLECT."""
    adapter = UpiAdapter()
    fixture = load_fixture("collect_success.json")
    res = adapter.normalize(make_raw_event(fixture))

    tx = res.transaction
    payer = next(p for p in res.parties if p.party_id == tx.sender_id)
    payee = next(p for p in res.parties if p.party_id == tx.receiver_id)

    assert tx.direction == TransactionDirection.TRANSFER
    assert payer.identifiers[0].value == "payer@oksbi"
    assert payee.identifiers[0].value == "payee@hdfc"


def test_upi_deemed_status() -> None:
    """Assert DEEMED status maps to PENDING_SETTLEMENT with deemed=True in metadata."""
    adapter = UpiAdapter()
    fixture = load_fixture("deemed.json")
    res = adapter.normalize(make_raw_event(fixture))

    tx = res.transaction
    assert tx.status == TransactionStatus.PENDING_SETTLEMENT
    assert tx.network_metadata.get("deemed") is True
    assert res.lifecycle_stage == "deemed"


def test_upi_reversed_status() -> None:
    """Assert REVERSED status maps to TransactionStatus.REVERSED."""
    adapter = UpiAdapter()
    fixture = load_fixture("reversed.json")
    res = adapter.normalize(make_raw_event(fixture))

    assert res.transaction.status == TransactionStatus.REVERSED
    assert res.lifecycle_stage == "reversed"


def test_upi_naive_timestamp_converted_from_ist_with_warning() -> None:
    """Assert naive timestamps are assumed Asia/Kolkata, converted to UTC, and warn."""
    adapter = UpiAdapter()
    payload = {
        "txn_id": "UPI_NAIVE_TZ_TEST",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": "100.00",
        "currency": "INR",
        "initiated_at": "2026-10-13 14:30:00",  # Naive
        "completed_at": "2026-10-13 14:30:05",  # Naive
        "payer": {"vpa": "alice@oksbi"},
        "payee": {"vpa": "bob@okaxis"},
    }
    res = adapter.normalize(make_raw_event(payload))

    # 14:30 IST is 09:00 UTC
    assert res.transaction.initiated_at == datetime(2026, 10, 13, 9, 0, 0, tzinfo=UTC)
    assert res.transaction.settled_at == datetime(2026, 10, 13, 9, 0, 5, tzinfo=UTC)
    assert any("Asia/Kolkata" in w for w in res.warnings)


def test_upi_float_amount_rejected() -> None:
    """Assert float amounts are strictly rejected with an informative NormalizationError."""
    adapter = UpiAdapter()
    # Note: When payload is dict, a float value tests raw rejection
    payload = {
        "txn_id": "UPI_FLOAT_TEST",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": 100.50,  # Float!
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "completed_at": "2026-10-13T10:00:05Z",
        "payer": {"vpa": "alice@oksbi"},
        "payee": {"vpa": "bob@okaxis"},
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    assert "float" in str(exc_info.value).lower()
    assert exc_info.value.field == "amount"


def test_upi_non_inr_currency_rejected() -> None:
    """Assert currency other than INR raises NormalizationError."""
    adapter = UpiAdapter()
    payload = {
        "txn_id": "UPI_CURR_TEST",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": "100.00",
        "currency": "USD",  # Not INR!
        "initiated_at": "2026-10-13T10:00:00Z",
        "completed_at": "2026-10-13T10:00:05Z",
        "payer": {"vpa": "alice@oksbi"},
        "payee": {"vpa": "bob@okaxis"},
    }
    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    assert "INR" in str(exc_info.value)
    assert exc_info.value.field == "currency"


def test_upi_mandate_unsupported() -> None:
    """Assert non-PAY/COLLECT transaction types (e.g. MANDATE) raise UnsupportedPayloadError."""
    adapter = UpiAdapter()
    payload = {
        "txn_id": "UPI_MANDATE_TEST",
        "txn_type": "MANDATE",
        "status": "SUCCESS",
        "amount": "100.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "completed_at": "2026-10-13T10:00:05Z",
        "payer": {"vpa": "alice@oksbi"},
        "payee": {"vpa": "bob@okaxis"},
    }
    with pytest.raises(UnsupportedPayloadError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    assert "MANDATE" in str(exc_info.value)


def test_upi_multi_identifier_party_preference_order() -> None:
    """Assert counterparty identifier preference order VPA > BANK_ACCOUNT > PHONE."""
    adapter = UpiAdapter()
    payload = {
        "txn_id": "UPI_IDENT_PREF_TEST",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": "50.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "completed_at": "2026-10-13T10:00:05Z",
        "payer": {
            "vpa": "primary@oksbi",
            "account": {"ifsc": "SBIN0001111", "number": "1234567890"},
            "phone": "+919876543210",
        },
        "payee": {
            # No VPA; bank account is primary
            "account": {"ifsc": "HDFC0002222", "number": "9876543210"},
            "phone": "+919876543211",
        },
    }
    res = adapter.normalize(make_raw_event(payload))
    tx = res.transaction

    # Payer primary is VPA
    assert tx.sender_identifier.type == IdentifierType.VPA
    assert tx.sender_identifier.value == "primary@oksbi"

    # Payee primary is BANK_ACCOUNT
    assert tx.receiver_identifier.type == IdentifierType.BANK_ACCOUNT
    assert tx.receiver_identifier.value == "HDFC0002222:9876543210"


def test_upi_same_input_twice_identical_output() -> None:
    """Assert determinism: normalizing identical events yields byte-identical output."""
    adapter = UpiAdapter()
    fixture = load_fixture("pay_success.json")
    event1 = make_raw_event(fixture)
    event2 = make_raw_event(fixture)

    res1 = adapter.normalize(event1)
    res2 = adapter.normalize(event2)

    assert res1.model_dump_json() == res2.model_dump_json()


def test_upi_data_minimization_and_discarded_fields() -> None:
    """Assert describe() lists discarded fields and personal names never enter output."""
    adapter = UpiAdapter()
    desc = adapter.describe()

    assert "discarded_fields" in desc
    assert "payer.name" in desc["discarded_fields"]
    assert "payee.name" in desc["discarded_fields"]

    payload = {
        "txn_id": "UPI_NAME_TEST",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": "100.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "completed_at": "2026-10-13T10:00:05Z",
        "payer": {"vpa": "alice@oksbi", "name": "Alice Wonderland"},
        "payee": {"vpa": "bob@okaxis", "name": "Bob Builder"},
    }
    res = adapter.normalize(make_raw_event(payload))
    dump_str = res.model_dump_json()

    assert "Alice Wonderland" not in dump_str
    assert "Bob Builder" not in dump_str


def test_upi_unknown_response_code_labelled_unknown() -> None:
    """Assert unknown response code is preserved verbatim and given label 'unknown'."""
    adapter = UpiAdapter()
    payload = {
        "txn_id": "UPI_UNKNOWN_CODE_TEST",
        "txn_type": "PAY",
        "status": "FAILURE",
        "response_code": "CUSTOM_999",
        "amount": "100.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "payer": {"vpa": "alice@oksbi"},
        "payee": {"vpa": "bob@okaxis"},
    }
    res = adapter.normalize(make_raw_event(payload))

    assert res.transaction.network_metadata["response_code"] == "CUSTOM_999"
    assert res.transaction.network_metadata["response_code_label"] == "unknown"


def test_upi_can_handle_and_batch_and_payload_types() -> None:
    """Assert can_handle, payload types (str/bytes), and normalize_batch."""
    adapter = UpiAdapter()
    fixture = load_fixture("pay_success.json")

    ev_json = make_raw_event(fixture)
    assert adapter.can_handle(ev_json) is True

    ev_wrong_net = RawEvent(
        network_id="card",
        payload=fixture,
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    assert adapter.can_handle(ev_wrong_net) is False

    ev_str = RawEvent(
        network_id="upi",
        payload=json.dumps(fixture),
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    res_str = adapter.normalize(ev_str)
    assert res_str.transaction.network_transaction_ref == fixture["txn_id"]

    ev_bytes = RawEvent(
        network_id="upi",
        payload=json.dumps(fixture).encode("utf-8"),
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    res_bytes = adapter.normalize(ev_bytes)
    assert res_bytes.transaction.network_transaction_ref == fixture["txn_id"]

    ev_bad = RawEvent(
        network_id="upi",
        payload={"invalid": "payload"},
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    batch_res = adapter.normalize_batch([ev_str, ev_bad])
    assert batch_res.total_count == 2
    assert batch_res.success_count == 1
    assert batch_res.failure_count == 1
    assert batch_res.has_failures is True


def test_upi_party_identifiers_fallback_and_attributes() -> None:
    """Assert party identifier fallback (bank account, phone) and remark truncation."""
    adapter = UpiAdapter()
    payload = {
        "txn_id": "UPI_ID_FALLBACK",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": "150.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "completed_at": "2026-10-13T10:00:05Z",
        "payer": {"account": {"ifsc": "HDFC0001234", "number": "9876543210"}},
        "payee": {"phone": "+919876543210"},
        "remarks": "A" * 150,
    }
    res = adapter.normalize(make_raw_event(payload))
    payer = next(p for p in res.parties if p.party_id == res.transaction.sender_id)
    payee = next(p for p in res.parties if p.party_id == res.transaction.receiver_id)
    assert payer.identifiers[0].type == IdentifierType.BANK_ACCOUNT
    assert payee.identifiers[0].type == IdentifierType.PHONE
    assert len(res.transaction.network_metadata["remarks"]) == 100


def test_upi_invalid_status_and_missing_completed_at() -> None:
    """Assert invalid status raises NormalizationError and missing completed_at raises."""
    adapter = UpiAdapter()
    payload_bad_status = {
        "txn_id": "UPI_BAD_STATUS",
        "txn_type": "PAY",
        "status": "UNKNOWN_STATUS",
        "amount": "100.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "payer": {"vpa": "a@ok"},
        "payee": {"vpa": "b@ok"},
    }
    with pytest.raises(NormalizationError):
        adapter.normalize(make_raw_event(payload_bad_status))

    payload_no_completed = {
        "txn_id": "UPI_NO_COMPLETED",
        "txn_type": "PAY",
        "status": "SUCCESS",
        "amount": "100.00",
        "currency": "INR",
        "initiated_at": "2026-10-13T10:00:00Z",
        "payer": {"vpa": "a@ok"},
        "payee": {"vpa": "b@ok"},
    }
    with pytest.raises(NormalizationError) as exc_comp:
        adapter.normalize(make_raw_event(payload_no_completed))
    assert exc_comp.value.field == "completed_at"
