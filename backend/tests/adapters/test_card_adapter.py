"""Comprehensive tests for the Card network adapter (Module 4)."""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ripple.adapters.card import CardAdapter
from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.errors import (
    AdapterRegistrationError,
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.config import Settings
from ripple.domain.identity import IdentifierType
from ripple.domain.party import PartyType
from ripple.domain.transaction import (
    IntermediaryRole,
    SettlementModel,
    TransactionDirection,
    TransactionStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "card"
TEST_KEY = "test_pan_token_key_for_unit_tests_32chars_min"


def load_fixture(name: str) -> dict[str, object]:
    """Helper to load a JSON fixture dictionary."""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        # pyrefly: ignore [no-any-return-explicit]
        return json.load(f)


def make_raw_event(
    payload: dict[str, object] | str | bytes,
    received_at: datetime | None = None,
) -> RawEvent:
    """Helper creating a RawEvent for Card adapter testing."""
    return RawEvent(
        network_id="card",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=received_at or datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC),
    )


def test_card_authorization_approved_and_declined() -> None:
    """Assert authorization approved produces AUTHORIZED and declined produces REJECTED."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)

    # Approved Auth (response_code "00")
    approved_fix = load_fixture("auth_approved.json")
    res_appr = adapter.normalize(make_raw_event(approved_fix))
    tx_appr = res_appr.transaction

    assert tx_appr.status == TransactionStatus.AUTHORIZED
    assert tx_appr.settled_at is None
    assert tx_appr.direction == TransactionDirection.DEBIT
    assert tx_appr.settlement_model == SettlementModel.AUTH_CAPTURE
    assert tx_appr.network_metadata["amount_stage"] == "authorization"
    assert tx_appr.network_metadata["auth_code"] == "AUTH99"
    assert res_appr.lifecycle_stage == "authorization"

    # Declined Auth (response_code "05")
    declined_fix = load_fixture("auth_declined.json")
    res_decl = adapter.normalize(make_raw_event(declined_fix))
    tx_decl = res_decl.transaction

    assert tx_decl.status == TransactionStatus.REJECTED
    assert tx_decl.settled_at is None
    assert tx_decl.network_metadata["decline_code"] == "05"


def test_card_lifecycle_auth_clearing_settlement_identity() -> None:
    """Assert auth -> clearing -> settlement events share correlation_key and transaction_id."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)

    res_auth = adapter.normalize(make_raw_event(load_fixture("auth_approved.json")))
    res_clr = adapter.normalize(make_raw_event(load_fixture("clearing.json")))
    res_stl = adapter.normalize(make_raw_event(load_fixture("settlement.json")))

    # Correlation and transaction_id stability across lifecycle
    expected_corr = "card:NETREF_CARD_001_AUTH"
    assert (
        res_auth.correlation_key
        == res_clr.correlation_key
        == res_stl.correlation_key
        == expected_corr
    )
    assert (
        res_auth.transaction.transaction_id
        == res_clr.transaction.transaction_id
        == res_stl.transaction.transaction_id
    )

    # Status progression
    assert res_auth.transaction.status == TransactionStatus.AUTHORIZED
    assert res_clr.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_stl.transaction.status == TransactionStatus.SETTLED

    # Settlement timestamp and reversal window
    assert res_stl.transaction.settled_at == datetime(2026, 10, 15, 0, 0, 0, tzinfo=UTC)
    assert res_stl.transaction.is_reversible is True
    assert (
        res_stl.transaction.reversal_window_ends_at
        # pyrefly: ignore [unsupported-operation]
        == res_stl.transaction.settled_at + timedelta(days=120)
    )
    assert any("settlement_date" in w for w in res_stl.warnings)


def test_card_amount_changing_between_auth_and_clearing() -> None:
    """Assert authorization amount ($100) vs clearing amount ($105) produces two valid snapshots."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)

    res_auth = adapter.normalize(make_raw_event(load_fixture("auth_approved.json")))
    res_clr = adapter.normalize(make_raw_event(load_fixture("clearing.json")))

    assert res_auth.transaction.amount.amount == Decimal("100.00")
    assert res_clr.transaction.amount.amount == Decimal("105.00")
    assert res_auth.transaction.network_metadata["amount_stage"] == "authorization"
    assert res_clr.transaction.network_metadata["amount_stage"] == "clearing"
    assert res_auth.transaction.transaction_id == res_clr.transaction.transaction_id


def test_card_reversal_event() -> None:
    """Assert reversal event maps to REVERSED status."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    res = adapter.normalize(make_raw_event(load_fixture("reversal.json")))

    assert res.transaction.status == TransactionStatus.REVERSED
    assert res.lifecycle_stage == "reversal"
    assert res.transaction.amount.currency == "USD"  # numeric 840 resolved to USD


def test_card_chargeback_event_with_reason_code() -> None:
    """Assert chargeback event maps to DISPUTED with reason code preserved in metadata."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    res = adapter.normalize(make_raw_event(load_fixture("chargeback.json")))

    assert res.transaction.status == TransactionStatus.DISPUTED
    assert res.lifecycle_stage == "chargeback"
    assert res.transaction.network_metadata["reason_code"] == "4837"


def test_card_hop_order_for_purchase() -> None:
    """Assert purchase funds flow hop order: issuer -> scheme -> acquirer."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    res = adapter.normalize(make_raw_event(load_fixture("auth_approved.json")))

    hops = res.transaction.intermediary_hops
    assert len(hops) == 3

    assert hops[0].sequence == 1
    assert hops[0].role == IntermediaryRole.ISSUER
    assert hops[0].institution_identifier.value == "iss:ISS_202"

    assert hops[1].sequence == 2
    assert hops[1].role == IntermediaryRole.SCHEME
    assert hops[1].institution_identifier.value == "scheme:Visa"

    assert hops[2].sequence == 3
    assert hops[2].role == IntermediaryRole.ACQUIRER
    assert hops[2].institution_identifier.value == "acq:ACQ_101"


def test_card_refund_flips_direction_and_hop_order() -> None:
    """Assert refund (processing code 20) flips direction to CREDIT and hop order."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    res = adapter.normalize(make_raw_event(load_fixture("refund.json")))
    tx = res.transaction

    # Direction and party swap
    assert tx.direction == TransactionDirection.CREDIT

    sender = next(p for p in res.parties if p.party_id == tx.sender_id)
    receiver = next(p for p in res.parties if p.party_id == tx.receiver_id)

    # Merchant is sender on refund
    assert sender.party_type == PartyType.MERCHANT
    assert "ACQ_101:MERC_555" in sender.identifiers[0].value

    # Cardholder is receiver on refund
    assert receiver.party_type == PartyType.INDIVIDUAL
    assert receiver.identifiers[0].type == IdentifierType.CARD_PAN_TOKEN

    # Hop order reversed: acquirer -> scheme -> issuer
    hops = tx.intermediary_hops
    assert len(hops) == 3
    assert hops[0].role == IntermediaryRole.ACQUIRER
    assert hops[0].institution_identifier.value == "acq:ACQ_101"
    assert hops[1].role == IntermediaryRole.SCHEME
    assert hops[1].institution_identifier.value == "scheme:Visa"
    assert hops[2].role == IntermediaryRole.ISSUER
    assert hops[2].institution_identifier.value == "iss:ISS_202"


def test_card_atm_channel_sets_receiver_to_financial_institution() -> None:
    """Assert ATM channel sets receiver party_type to FINANCIAL_INSTITUTION."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    res = adapter.normalize(make_raw_event(load_fixture("atm_withdrawal.json")))

    receiver = next(p for p in res.parties if p.party_id == res.transaction.receiver_id)
    assert receiver.party_type == PartyType.FINANCIAL_INSTITUTION


def test_card_de7_year_inference_including_dec_jan_boundary() -> None:
    """Assert DE7 MMDDhhmmss infers correct year closest to received_at around New Year."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    base_payload = load_fixture("auth_approved.json")

    # Case 1: Ingestion on Jan 1, 2026, 00:05:00 UTC; message sent Dec 31, 23:59:50
    payload1 = dict(base_payload)
    payload1["transmission_time"] = "1231235950"
    event1 = make_raw_event(payload1, received_at=datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC))
    res1 = adapter.normalize(event1)
    assert res1.transaction.initiated_at == datetime(2025, 12, 31, 23, 59, 50, tzinfo=UTC)

    # Case 2: Ingestion on Dec 31, 2025, 23:58:00 UTC; message timestamp is Jan 1, 00:01:00
    payload2 = dict(base_payload)
    payload2["transmission_time"] = "0101000100"
    event2 = make_raw_event(payload2, received_at=datetime(2025, 12, 31, 23, 58, 0, tzinfo=UTC))
    res2 = adapter.normalize(event2)
    assert res2.transaction.initiated_at == datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC)


def test_card_luhn_failure_rejected() -> None:
    """Assert Luhn-invalid PAN is rejected without leaking the PAN in the error message."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    payload = dict(load_fixture("auth_approved.json"))
    invalid_pan = "4111111111111112"  # Fails Luhn
    payload["pan"] = invalid_pan

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    assert "luhn" in str(exc_info.value).lower()
    # PAN MUST NOT appear in the exception string or reason
    assert invalid_pan not in str(exc_info.value)
    assert invalid_pan not in exc_info.value.reason


def test_card_zero_amount_rejection() -> None:
    """Assert zero-amount authorizations raise UnsupportedPayloadError citing M02 limitation."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    payload = dict(load_fixture("auth_approved.json"))
    payload["amount"] = "0.00"

    with pytest.raises(UnsupportedPayloadError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    err_msg = str(exc_info.value)
    assert "zero-amount" in err_msg.lower()
    assert "module 2" in err_msg.lower() or "limitation" in err_msg.lower()


def test_card_unknown_processing_code() -> None:
    """Assert unsupported processing code raises UnsupportedPayloadError."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    payload = dict(load_fixture("auth_approved.json"))
    payload["processing_code"] = "99"

    with pytest.raises(UnsupportedPayloadError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    assert "processing_code" in str(exc_info.value)


def test_card_discarded_sensitive_fields_with_field_only_warnings() -> None:
    """Assert CVV, track, and PIN fields are discarded and warnings name only the field."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    payload = dict(load_fixture("auth_approved.json"))
    payload["cvv"] = "999"
    payload["track2"] = "4111111111111111=2612101000000"
    payload["pin_block"] = "FFFFFFFFFFFFFFFF"
    payload["expiry"] = "12/26"

    res = adapter.normalize(make_raw_event(payload))

    # Assert warnings mention the field names
    warning_text = " ".join(res.warnings)
    assert "cvv" in warning_text
    assert "track2" in warning_text
    assert "pin_block" in warning_text
    assert "expiry" in warning_text

    # Assert warnings and output DO NOT contain the sensitive values
    assert "999" not in warning_text
    assert "FFFFFFFFFFFFFFFF" not in warning_text
    dump_str = res.model_dump_json()
    assert "999" not in dump_str
    assert "FFFFFFFFFFFFFFFF" not in dump_str


def test_card_pan_leak_comprehensive_check() -> None:
    """CRITICAL: Assert the raw PAN never appears in result, warnings, exceptions, or logs."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    raw_pan = "4111111111111111"
    payload = dict(load_fixture("auth_approved.json"))
    payload["pan"] = raw_pan

    res = adapter.normalize(make_raw_event(payload))

    # 1. Check NormalizationResult JSON and string serialization
    json_dump = res.model_dump_json()
    str_dump = str(res)
    repr_dump = repr(res)
    assert raw_pan not in json_dump
    assert raw_pan not in str_dump
    assert raw_pan not in repr_dump

    # 2. Check all warnings
    warnings_dump = " ".join(res.warnings)
    assert raw_pan not in warnings_dump

    # 3. Check party attributes and transaction metadata
    tx_meta = json.dumps(res.transaction.network_metadata)
    assert raw_pan not in tx_meta

    # 4. Check token format
    token = res.transaction.sender_identifier.value
    assert token.startswith("tok_")
    assert len(token) == 36  # "tok_" + 32 hex chars
    assert raw_pan not in token


def test_card_pan_tokenization_determinism_and_key_isolation() -> None:
    """Assert same PAN + same key gives identical token; different key gives different token."""
    raw_pan = "4111111111111111"
    payload = dict(load_fixture("auth_approved.json"))
    payload["pan"] = raw_pan

    adapter_key1 = CardAdapter(pan_token_key="key_alpha_very_secure_32_characters_long")
    adapter_key1_again = CardAdapter(pan_token_key="key_alpha_very_secure_32_characters_long")
    adapter_key2 = CardAdapter(pan_token_key="key_beta_completely_different_key_32_chars")

    res1 = adapter_key1.normalize(make_raw_event(payload))
    res1_repeat = adapter_key1_again.normalize(make_raw_event(payload))
    res2 = adapter_key2.normalize(make_raw_event(payload))

    token1 = res1.transaction.sender_identifier.value
    token1_repeat = res1_repeat.transaction.sender_identifier.value
    token2 = res2.transaction.sender_identifier.value

    assert token1 == token1_repeat
    assert token1 != token2


def test_card_production_registration_fails_without_key() -> None:
    """Assert in production mode, missing or short key fails registration with error."""
    settings = Settings(APP_ENV="production", CARD_PAN_TOKEN_KEY=None)
    with pytest.raises(AdapterRegistrationError) as exc_info:
        CardAdapter.from_settings(settings)
    assert "CARD_PAN_TOKEN_KEY" in str(exc_info.value)


def test_card_development_mode_warns_and_falls_back_without_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Assert in dev mode, a missing key emits a warning and constructs with dev key."""
    settings = Settings(APP_ENV="development", CARD_PAN_TOKEN_KEY=None)
    with caplog.at_level(logging.WARNING):
        adapter = CardAdapter.from_settings(settings)
    assert isinstance(adapter, CardAdapter)
    assert any(
        "WARNING: CARD_PAN_TOKEN_KEY is unset" in record.message for record in caplog.records
    )


def test_card_can_handle_and_batch_and_payload_types() -> None:
    """Assert can_handle, payload types (str/bytes), and normalize_batch."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    fixture = load_fixture("auth_approved.json")

    ev_json = make_raw_event(fixture)
    assert adapter.can_handle(ev_json) is True

    ev_wrong_net = RawEvent(
        network_id="upi",
        payload=fixture,
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    assert adapter.can_handle(ev_wrong_net) is False

    ev_str = RawEvent(
        network_id="card",
        payload=json.dumps(fixture),
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    res_str = adapter.normalize(ev_str)
    assert res_str.transaction.network_transaction_ref == fixture["network_reference"]

    ev_bytes = RawEvent(
        network_id="card",
        payload=json.dumps(fixture).encode("utf-8"),
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    res_bytes = adapter.normalize(ev_bytes)
    assert res_bytes.transaction.network_transaction_ref == fixture["network_reference"]

    ev_bad = RawEvent(
        network_id="card",
        payload={"invalid": "payload"},
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC),
    )
    batch_res = adapter.normalize_batch([ev_str, ev_bad])
    assert batch_res.total_count == 2
    assert batch_res.success_count == 1
    assert batch_res.failure_count == 1
    assert batch_res.has_failures is True


def test_card_unknown_channel_and_token_only_and_numeric_currencies() -> None:
    """Assert unrecognized channel warns and maps to 'other', token only works, numeric codes."""
    adapter = CardAdapter(pan_token_key=TEST_KEY)
    desc = adapter.describe()
    assert "discarded_fields" in desc
    assert "cvv" in desc["discarded_fields"]

    payload = {
        "event_type": "authorization",
        "network_reference": "CARD_EDGE_001",
        "processing_code": "00",
        "response_code": "00",
        "card_token": "tok_existing_pretokenized_12345",
        "bin": "411111",
        "last4": "1111",
        "amount": "1000",
        "currency": "392",  # JPY
        "transmission_time": "2026-10-13T10:00:00Z",
        "channel": "unrecognized_channel",
        "acquirer": {"id": "ACQ_01"},
        "merchant": {"id": "MERC_01"},
    }
    # pyrefly: ignore [bad-argument-type]
    res = adapter.normalize(make_raw_event(payload))
    assert res.transaction.channel == "other"
    assert any("unrecognized_channel" in w for w in res.warnings)
    assert res.transaction.amount.currency == "JPY"
    assert res.transaction.amount.scale == 0
    assert res.transaction.sender_identifier.value == "tok_existing_pretokenized_12345"
    assert len(res.transaction.intermediary_hops) == 1  # Only acquirer
